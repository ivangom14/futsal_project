"""Intención de la pregunta (qué OPERACIÓN quiere el usuario), puntuada por rasgos léxicos con peso, no por frases hechas.

No es un conjunto de expresiones regulares para preguntas concretas: cada intención tiene familias de raíces con un peso y gana la que más suma. Sin señal
propia, una pregunta de seguimiento («¿Y la copa?») hereda la intención de la conversación; con señales débiles o empatadas la confianza baja y el
intérprete LLM (modo `llm`) decide. El resultado es una PISTA para el agente (no sustituye al LLM, que puede razonar otra cosa).
"""

import re
from dataclasses import dataclass

from futsal.agent.criteria import mentions_rules, norm

INTENTS = ("standings", "last_result", "next_match", "matches", "scorers", "teams_list", "stats", "rules")
LABEL = {"standings": "clasificación (get_standings)", "last_result": "último resultado (list_matches status=finished, order=desc, limit=1)",
         "next_match": "próximo partido (list_matches status=scheduled, order=date_asc, limit=1)", "matches": "partidos (list_matches)",
         "scorers": "goleadores (get_scorer_table)", "teams_list": "equipos del grupo (list_teams)",
         "stats": "estadística de la clasificación (get_standings: ganados, perdidos, goles…)", "rules": "reglamento (search_rules)"}

# (patrón sobre texto normalizado, peso). Raíces y combinaciones de verbo + objeto, no preguntas completas.
_FEATURES: dict[str, tuple[tuple[str, float], ...]] = {
    "standings": ((r"\bclasific\w*", 3), (r"\b(posicion|puesto|lugar|tabla|liderato|colista|farolillo)\b", 3), (r"\b(lider|lidera|lideran|manda|mandan|domina)\b", 2.5),
                  (r"\bpuntos?\b", 2), (r"\b(arriba|cabeza|top)\b", 2.5), (r"\bva (primer\w*|segund\w*|tercer\w*|cuart\w*|ultim\w*|penultim\w*)\b", 3),
                  (r"\bcomo (va|vais|van|anda|andan|esta|estan|marcha|marchan|le va|les va|lo hace|lo hacen)\b", 2), (r"\bquien (va|esta|anda|lidera)\b", 1.5)),
    "last_result": ((r"\bresultados?\b", 2.5), (r"\b(quedo|quedaron|acabo|acabaron|termino|terminaron|fue el partido)\b", 3), (r"\b(otro dia|ayer|anoche|el sabado pasado)\b", 3),
                    (r"\bultim[oa]s? (partido|resultado|jornada|encuentro)\b", 3), (r"\bcomo le fue\b", 2.5)),
    "next_match": ((r"\bproxim\w+\b", 3), (r"\bsiguiente (partido|jornada|encuentro|rival)\b", 3), (r"\bcuando (juega|jugara|juegan|vuelve|toca|se enfrenta|debuta)\b", 3),
                   (r"\bvuelve a jugar\b", 3), (r"\bcontra quien\b", 3), (r"\ba que hora\b", 2.5), (r"\bva a jugar\b", 2.5)),
    "matches": ((r"\bpartidos?\b", 2), (r"\bjornadas?\b", 1.5), (r"\b(fin de semana|calendario|enfrentamientos|encuentros)\b", 2.5)),
    "scorers": ((r"\bgoleador\w*", 3), (r"\bpichichi\w*", 3), (r"\banotador\w*", 3), (r"\bgoles\b", 2), (r"\b(lleva|llevan|ha marcado|han marcado|marca|marcan)\b", 1), (r"\bmaximo\w* (anotador|goleador)\b", 1)),
    "teams_list": ((r"\bque equipos\b", 3), (r"\bequipos (hay|participan|forman|juegan|componen|tiene)\b", 3), (r"\blista de equipos\b", 3), (r"\bcuantos equipos\b", 3),
                    (r"\b(los )?equipos (de|del|en|que (hay|juegan|participan))\b", 3)),
    "rules": ((r"\b(dura|duran|duracion|mitad|mitades|minutos|cronometro|sustitucion\w*|dimensiones|porteria|infraccion\w*|penalti|saque|reglamento|reglas?)\b", 3.0),),
    "stats": ((r"\bmas (partidos )?(ganados|ganado|perdidos|empatados|victorias|derrotas|empates)\b", 4), (r"\b(ha|han) (ganado|perdido|empatado) mas\b", 4),
              (r"\b(mejor|peor) (ataque|defensa)\b", 4), (r"\bmenos goles (encajados|recibidos)\b", 4), (r"\bmas goles (encajados|recibidos|marcados|a favor|en contra)\b", 3)),
}
_COMPILED = {k: tuple((re.compile(p), w) for p, w in v) for k, v in _FEATURES.items()}


@dataclass(frozen=True)
class IntentGuess:
    intent: str | None = None
    confidence: str = "none"  # high | medium | low | none
    inherited: bool = False  # sin señal propia: viene de la conversación
    also: tuple[str, ...] = ()  # otras intenciones con señal fuerte (pregunta compuesta)
    sub: str | None = None  # standings: top | bottom
    signals: tuple[str, ...] = ()


def classify(question: str, previous: str | None = None) -> IntentGuess:
    text = norm(question)
    if mentions_rules(question):
        return IntentGuess("rules", "high", signals=("rules",))
    scores: dict[str, float] = {}
    signals: list[str] = []
    for intent, features in _COMPILED.items():
        for pattern, weight in features:
            if pattern.search(text):
                scores[intent] = scores.get(intent, 0.0) + weight
                signals.append(f"{intent}:{pattern.pattern[:18]}")
    if "stats" in scores:  # «más partidos» no es solo «partidos»
        scores.pop("matches", None)
    if "next_match" in scores or "last_result" in scores:  # «próximo partido»: el objeto no es otra consulta
        scores.pop("matches", None)
    if not scores:
        ellipsis = text.startswith(("y ", "pero ", "entonces ", "y,")) or text == "y"  # «¿Y la copa?»: solo un seguimiento elíptico hereda la intención
        if previous and previous in INTENTS and ellipsis:
            return IntentGuess(previous, "medium", inherited=True)
        return IntentGuess()
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, top_score = ranked[0]
    strong = tuple(i for i, s in ranked[1:] if s >= 3)
    margin = top_score - (ranked[1][1] if len(ranked) > 1 else 0)
    confidence = "high" if top_score >= 3 and margin >= 1 else "medium" if top_score >= 2 and margin > 0 else "low"
    sub = None
    if top == "standings":
        sub = "bottom" if re.search(r"\b(ultim\w*|penultim\w*|colista|farolillo)\b", text) and not scores.get("last_result") else "top" if re.search(
            r"\b(primer\w*|arriba|cabeza|lider\w*|manda\w*)\b", text) else None
    return IntentGuess(top, confidence, False, strong, sub, tuple(signals[:6]))
