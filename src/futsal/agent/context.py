"""Motor de comprensión de contexto de Futsito (modos `deterministic` y `llm`; `legacy` no lo usa).

Un mensaje + el estado conversacional → una `Understanding`: intención, competición(es), grupos, equipo y qué hacer (política de ambigüedad):

- `act`: ámbito claro → se consulta. `assume`: se resolvió una referencia indirecta con el contexto (categoría, «la otra», «¿y la senior?», opción ofrecida…) → se consulta
  y se DECLARA la suposición una vez. `ask`: varias interpretaciones razonables → se pregunta con opciones reales y sin consultar datos. `missing`: lo pedido no existe en el
  catálogo. `nodata`: otra temporada (hoy solo existe la actual). `none`: nada de competición reconocido (puede ser ajeno: decide el resto del flujo).
- El contexto solo DESEMPATA entre candidatas que el usuario ya dijo; nunca filtra una competición o un equipo nuevo.
- Varios grupos: ≤3 → se consultan todos; >3 → se pregunta, salvo que el contexto o el equipo lo determinen.

Módulo puro (sin E/S): el agente le da el catálogo, los equipos y, si procede, la interpretación del LLM (`interpreter.Interpretation`) ya validada.
"""

import difflib
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from futsal.agent.convstate import ConversationState, turn_hash
from futsal.agent.entities import GENERIC, TeamCatalog, _matches, normalize, scan
from futsal.agent.intent import LABEL, classify
from futsal.agent.interpreter import Interpretation
from futsal.agent.memory import Turn, asks_for_input, resolve_pending
from futsal.domain.competitions import Competition, fold
from futsal.domain.resolver import Parsed, Resolver, SeasonScope

FANOUT_MAX_GROUPS = 3
MAX_OPTIONS = 8
TEAM_INTENTS = ("next_match", "last_result")
NOTE_HEAD = "\n\n[Nota de la aplicación, no escrita por el usuario; verificada con el catálogo]"
INFERRED = frozenset({"age", "kind", "sibling", "other", "ordinal", "interpreter", "team_context", "narrowed"})
ORDINAL_FILLER = frozenset({"la", "el", "lo", "las", "los", "de", "esa", "ese", "opcion", "opciones", "primera", "primero", "segunda", "segundo", "tercera", "tercero",
                            "cuarta", "cuarto", "ultima", "ultimo", "otra", "otro", "y", "quiero", "dame", "mejor", "pues", "ah"})


@dataclass(frozen=True)
class Target:
    competition: str
    group_id: int
    group: str


@dataclass
class Understanding:
    status: str = "none"  # act | assume | ask | missing | nodata | none
    intent: str | None = None
    intent_sub: str | None = None
    intent_confidence: str = "none"
    intent_inherited: bool = False
    competitions: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    group_names: list[str] = field(default_factory=list)  # grupos concretos elegidos (vacío = todos los de la competición)
    team: str | None = None
    team_entries: list[tuple[str, str]] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    option_kind: str | None = None  # competition | group | team
    ask_about: str | None = None  # scope | team | group | competition
    missing: list[str] = field(default_factory=list)
    season: SeasonScope = field(default_factory=SeasonScope)
    assumption: str | None = None
    provenance: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    trigger: str | None = None  # por qué convendría el intérprete LLM
    interpreter: dict[str, Any] | None = None
    resolved_question: str = ""
    offtopic_confirmed: bool = False
    stated_age: str | None = None
    stated_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "intent": self.intent, "intent_confidence": self.intent_confidence, "intent_inherited": self.intent_inherited,
                "competitions": self.competitions, "groups": [t.group_id for t in self.targets], "team": self.team, "options": self.options,
                "option_kind": self.option_kind, "missing": self.missing, "season": self.season.requested, "assumption": self.assumption,
                "provenance": self.provenance, "reason": self.reason, "trigger": self.trigger, "interpreter": self.interpreter}


@dataclass
class TeamHit:
    names: list[str] = field(default_factory=list)  # equipos inequívocos nombrados
    ambiguous: list[str] = field(default_factory=list)  # nombres oficiales que una misma forma escrita podría ser
    generic: bool = False


class ContextEngine:
    def __init__(self, competitions: list[Competition], aliases: dict[str, Any], dims: dict[str, Any], teams: TeamCatalog | None = None) -> None:
        self.competitions = competitions
        self.resolver = Resolver(competitions, aliases, dims)
        self.by_name = {c.name: c for c in competitions}
        self.teams = teams
        self._team_tokens = sorted(teams.token_names) if teams is not None else []

    # ---------------------------------------------------------------------------------------------------------- equipos
    def team_entries(self, name: str) -> list[tuple[str, str]]:
        return sorted({(e.competition, e.group) for e in self.teams.by_name.get(name, [])}) if self.teams is not None else []

    def team_hit(self, text: str, state: ConversationState, raw: str = "") -> TeamHit:
        if self.teams is None:
            return TeamHit()
        tokens = normalize(text).split()
        fixed = []
        for t in tokens:  # erratas evidentes en un nombre de equipo («Valdemoru»): solo hacia una palabra del catálogo y nunca sobre vocabulario de competición
            near = difflib.get_close_matches(t, [w for w in self._team_tokens if len(w) >= 6 and w[0] == t[0]], 1, 0.88) if (
                len(t) >= 6 and t not in self.teams.token_names and t not in self.resolver.words and t not in self.resolver.protected) else []
            fixed.append(near[0] if near else t)
        chosen, _, generic = scan(fixed, self.teams)
        ambiguous = sorted({n for _, _, form, names in _matches(fixed, self.teams) if len(names) > 1 for n in names})
        if not chosen and not ambiguous:  # una sola palabra distintiva compartida por varios equipos («el Pinto»): hay que preguntar cuál
            shared = [t for t in fixed if len(t) >= 4 and t not in GENERIC and t not in self.resolver.words and len(self.teams.token_names.get(t, ())) > 1]
            if len(shared) == 1:
                ambiguous = sorted(self.teams.token_names[shared[0]])
        hit = TeamHit(list(chosen), ambiguous if not chosen else [], generic)
        if not hit.names and state.offered_kind == "team" and state.offered:  # respuesta a «¿a qué equipo?»: «el atlético» → una de las ofrecidas
            reply = {t for t in normalize(raw or text).split() if len(t) >= 3 and t not in self.resolver.stop}
            reply -= {"el", "la", "los", "las", "de", "del", "equipo", "quiero", "dame", "pues"}
            options = [o for o in state.offered if reply and reply <= set(normalize(o).split())]
            if len(options) == 1:
                hit = TeamHit([options[0]])
        return hit

    def team_candidates(self, text: str, state: ConversationState, limit: int = 30) -> list[str]:
        """Nombres de equipo que el intérprete LLM puede devolver: los del contexto y los que comparten alguna palabra distintiva con la pregunta."""
        if self.teams is None:
            return list(state.teams)
        words = {t for t in normalize(text).split() if len(t) >= 4 and t not in GENERIC}
        found = [n for n in self.teams.by_name if words & set(normalize(n).split())]
        return list(dict.fromkeys([*state.teams, *sorted(found)]))[:limit]

    # ---------------------------------------------------------------------------------------------------------- ámbito
    def _prev(self, state: ConversationState) -> list[Competition]:
        return [self.by_name[n] for n in state.competitions if n in self.by_name]

    def _prefer(self, comps: list[Competition], p: Parsed, state: ConversationState, prev: list[Competition]) -> tuple[list[Competition], str]:
        """Varias familias candidatas que el usuario dijo: el contexto desempata (sin añadir ni quitar nada que no esté ya entre ellas)."""
        r = self.resolver
        if len(r.families(comps)) <= 1:
            return comps, "explicit"
        inter = [c for c in comps if c in prev]
        if inter and len(r.families(inter)) == 1 and len(inter) < len(comps):
            return inter, "context"
        for dim, want, how in (("age", p.age is None and state.age, "age"), ("kind", p.kind is None and state.kind, "kind")):
            if want:
                sub = [c for c in comps if (r.age_of(c) if dim == "age" else r.kind_of(c)) == want]
                if sub and len(sub) < len(comps):
                    return (sub, how) if len(r.families(sub)) == 1 else (sub, "narrowed")
        return comps, "explicit"

    def _ordinal(self, raw: str, state: ConversationState) -> tuple[str, str] | None:
        """«la segunda» / «la otra» sobre las opciones que Futsito acaba de ofrecer (solo mensajes cortos sin otra pista)."""
        if not state.offered:
            return None
        pr = self.resolver.parse(raw)
        words = [w for w in pr.content if w not in ORDINAL_FILLER]
        if pr.ordinal is None and not pr.other or words or len(pr.content) > 4:
            return None
        if pr.ordinal is not None and abs(pr.ordinal) < len(state.offered) or pr.ordinal == -1:
            return state.offered_kind or "competition", state.offered[pr.ordinal if pr.ordinal is not None else 0]
        if pr.other and len(state.offered) == 2:
            current = next(iter(state.competitions or state.groups), None)
            rest = [o for o in state.offered if o != current]
            return (state.offered_kind or "competition", rest[0]) if len(rest) == 1 else None
        return None

    def _scope(self, p: Parsed, state: ConversationState, prev: list[Competition], interp: Interpretation | None, ordinal: tuple[str, str] | None,
               follow_up: bool) -> tuple[list[Competition], str]:
        r = self.resolver
        if ordinal and ordinal[0] == "competition" and ordinal[1] in self.by_name:
            return [self.by_name[ordinal[1]]], "ordinal"
        comps: list[Competition] = []
        how = "none"
        if p.strong:
            comps = r.match(p)
            how = "explicit"
            if comps:
                comps, how = self._prefer(comps, p, state, prev)
        elif p.kind or p.age:
            pool = r.match(p)
            if p.age and not p.kind and prev:
                sib = r.siblings(prev, p.age)
                if sib:
                    return sib, "sibling"
            if p.other and prev:
                pool = [c for c in pool if c not in prev]
            how = "other" if p.other else "explicit"
            comps = pool
            if len(r.families(comps)) > 1:
                comps, how2 = self._prefer(comps, p, state, prev)
                how = how2 if how2 != "explicit" else how
        elif prev and follow_up:  # sin ámbito propio: continúa el de la conversación, pero solo si el mensaje parece un seguimiento (no «cuéntame un chiste»)
            comps, how = prev, "context"
        if interp and interp.competitions and (not comps or len(r.families(comps)) > 1 and how in ("explicit", "narrowed", "other")):
            named = [self.by_name[n] for n in interp.competitions if n in self.by_name]
            # El LLM solo puede ELEGIR entre las candidatas que el usuario ya dijo; si no había ninguna, se acepta lo que exista en el catálogo.
            if named and (len(r.families(named)) == 1 or not comps) and (not comps or all(c in comps for c in named)):
                return named, "interpreter"
        return comps, how

    def _targets(self, comps: list[Competition], p: Parsed, state: ConversationState, how: str, interp: Interpretation | None
                 ) -> tuple[list[Target], list[str], str | None]:
        """(grupos a consultar, nombres de grupo elegidos, motivo de pregunta de grupo). ≤3 grupos → todos; >3 → pregunta salvo que el contexto lo determine."""
        wanted: list[str] = []
        if p.group:
            wanted = [g for c in comps for _, g in c.groups if fold(g) == p.group]
        elif interp and interp.groups:
            wanted = [g for c in comps for _, g in c.groups if g in interp.groups]
        elif how == "context" and state.groups and {c.name for c in comps} == set(state.competitions):
            wanted = [g for c in comps for _, g in c.groups if g in state.groups]
        elif p.other and len(comps) == 1 and state.groups and {c.name for c in comps} == set(state.competitions):
            wanted = [g for _, g in comps[0].groups if g not in state.groups]
        pairs = [(c, gid, g) for c in comps for gid, g in c.groups if not wanted or g in wanted]
        if len(pairs) > FANOUT_MAX_GROUPS and not wanted:
            return [], [], "group"
        return [Target(c.name, gid, g) for c, gid, g in pairs], list(dict.fromkeys(wanted)), None

    # ---------------------------------------------------------------------------------------------------------- comprensión
    def understand(self, effective: str, raw: str, state: ConversationState, *, has_history: bool = False, interp: Interpretation | None = None) -> Understanding:
        r = self.resolver
        u = Understanding()
        p = r.parse(effective)
        u.stated_age, u.stated_kind = p.age, p.kind
        prev = self._prev(state)
        guess = classify(effective, state.intent)
        u.intent, u.intent_confidence, u.intent_inherited, u.intent_sub = guess.intent, guess.confidence, guess.inherited, guess.sub
        if interp and interp.intent and (guess.intent is None or guess.confidence == "low" or guess.inherited):
            u.intent, u.intent_confidence, u.intent_inherited = interp.intent, "medium", False
        if interp:
            u.resolved_question = interp.resolved_question
        hit = self.team_hit(effective, state, raw)
        if (p.season or (interp and interp.season)) and (p.scoped or u.intent or hit.names or hit.ambiguous):
            u.season = SeasonScope("previous")
            u.status, u.reason = "nodata", "otra temporada: solo existe la actual"
            u.competitions = [c.name for c in r.match(p)] if p.scoped else []
            return u
        ordinal = self._ordinal(raw, state)
        if p.absent and not ordinal:
            u.status, u.missing, u.reason = "missing", list(p.absent), "término de competición/categoría que no existe en el catálogo"
            return u
        if u.intent == "rules":
            u.status, u.reason = "none", "reglamento"
            return u
        follow_up = u.intent is not None or p.pronoun or p.possessive or p.other or ordinal is not None
        comps, how = self._scope(p, state, prev, interp, ordinal, follow_up)
        team = hit.names[0] if len(hit.names) == 1 else None
        if ordinal and ordinal[0] == "team" and ordinal[1] in (self.teams.by_name if self.teams is not None else {}):
            team, hit = ordinal[1], TeamHit([ordinal[1]])
            u.provenance["team"] = "ordinal"
        if ordinal and ordinal[0] == "group":
            p = Parsed(**{**p.__dict__, "group": fold(ordinal[1])})
        if interp and interp.team and team is None and self.teams is not None and interp.team in self.teams.by_name:
            team, hit = interp.team, TeamHit([interp.team])
        if hit.ambiguous and not hit.names and comps and p.scoped:  # «el Pinto en primera»: el ámbito dicho puede dejar un solo equipo
            allowed = {c.name for c in comps}
            left = [n for n in hit.ambiguous if any(c in allowed for c, _ in self.team_entries(n))]
            if len(left) == 1:
                hit = TeamHit(left)
                team = left[0]
        if len(hit.names) > 1 or (hit.ambiguous and team is None):
            u.status, u.options, u.option_kind, u.ask_about = "ask", (hit.names or hit.ambiguous)[:MAX_OPTIONS], "team", "team"
            u.reason, u.trigger = "varios equipos posibles", ("team_ambiguous" if has_history else None)
            return u
        explicit_scope = p.scoped
        if team is None and (p.pronoun or p.possessive or (u.intent in TEAM_INTENTS and not explicit_scope)) and state.teams and not (p.strong and not p.pronoun) or team is None and u.intent in TEAM_INTENTS and state.teams and not p.strong:
            team = state.teams[0]
            u.provenance["team"] = "context"
        if team:
            entries = [(c, g) for c, g in self.team_entries(team)]
            scope_comps = r.match(p) if (p.scoped and how in ("age", "kind", "narrowed", "explicit", "none")) else comps  # con un equipo nombrado el contexto no desempata por categoría/tipo: se ofrecen todas sus competiciones
            if explicit_scope and scope_comps:
                ok = [(c, g) for c, g in entries if c in {x.name for x in scope_comps}]
                if not ok:
                    where = sorted({c for c, _ in entries})
                    u.status, u.team, u.team_entries = "missing", team, entries
                    u.missing, u.options, u.option_kind, u.reason = [f"{team} en ese ámbito"], where, "competition", "equipo fuera del ámbito pedido"
                    return u
                entries = ok
            elif not explicit_scope and entries:
                inter = [(c, g) for c, g in entries if c in state.competitions]
                if inter and len({c for c, _ in inter}) < len({c for c, _ in entries}):
                    entries, how = inter, "team_context"
            names = sorted({c for c, _ in entries})
            fam = r.families([self.by_name[n] for n in names if n in self.by_name])
            if len(fam) > 1:
                u.status, u.team, u.team_entries = "ask", team, entries
                u.options, u.option_kind, u.ask_about, u.reason = names, "competition", "competition", "el equipo juega en varias competiciones"
                u.trigger = "ambiguous_scope" if has_history or state.competitions else None
                return u
            if entries:
                comps = [self.by_name[n] for n in names if n in self.by_name]
                u.team, u.team_entries = team, entries
                group_names = sorted({g for _, g in entries})
                u.targets = [Target(c, gid, g) for c, g in entries for x in comps if x.name == c for gid, gname in x.groups if gname == g]
                u.group_names, u.competitions = group_names, names
                how = "ordinal" if u.provenance.get("team") == "ordinal" else how if how in INFERRED else "explicit"
                return self._finish(u, how, state, has_history)
            u.team = team
        if not comps:
            if p.scoped or ordinal:
                u.status, u.missing, u.reason = "missing", [" ".join(sorted(p.level)) or p.kind or p.age or "esa competición"], "ninguna competición cumple lo pedido"
                return u
            u.reason = "sin ámbito ni equipo"
            if u.intent and u.intent_confidence in ("high", "medium") and (u.intent in ("standings", "scorers", "matches", "teams_list", "stats", "next_match", "last_result")):
                u.status, u.ask_about, u.reason = "ask", "scope", "intención clara sin competición ni equipo en el contexto"
                u.trigger = "ambiguous_scope"  # también sin historial: puede ser una errata o un nombre coloquial que solo el intérprete entiende
            else:
                u.trigger = "no_signal"
            return u
        fam = r.families(comps)
        if len(fam) > 1:
            u.status, u.options, u.option_kind, u.ask_about = "ask", [c.name for c in comps][:MAX_OPTIONS], "competition", "competition"
            u.reason = "varias competiciones razonables"
            u.trigger = "ambiguous_scope" if (has_history or state.competitions) else None
            u.competitions = []
            return u
        targets, group_names, ask_group = self._targets(comps, p, state, how, interp)
        u.competitions = [c.name for c in comps]
        if ask_group:
            u.status, u.options, u.option_kind, u.ask_about = "ask", [g for _, g in comps[0].groups], "group", "group"
            u.reason = f"{len(comps[0].groups)} grupos y ninguno indicado"
            return u
        u.targets, u.group_names = targets, group_names
        if ordinal and ordinal[0] == "group":
            how = "ordinal"
        return self._finish(u, how, state, has_history)

    def _finish(self, u: Understanding, how: str, state: ConversationState, has_history: bool) -> Understanding:
        u.provenance["scope"] = how
        inferred = how in INFERRED
        already = bool(u.competitions) and set(u.competitions) <= set(state.declared) and set(u.competitions) == set(state.competitions)
        u.status = "assume" if inferred and not already else "act"
        if u.status == "assume":
            names = " y ".join(u.competitions[:3])
            where = f" ({', '.join(u.group_names)})" if how == "ordinal" and u.group_names else ""
            u.assumption = f"Entiendo que te refieres a {names}{where}."
        if u.intent is None or u.intent_confidence == "low":
            u.trigger = "intent_unknown"
        u.reason = f"ámbito {how}"
        return u

    # ---------------------------------------------------------------------------------------------------------- textos
    def clarification_text(self, u: Understanding) -> str:
        opts = "; ".join(f"«{o}»" for o in u.options)
        if u.option_kind == "team":
            return f"¿A qué equipo te refieres: {opts} (indica también la competición si la conoces)?"
        if u.option_kind == "group":
            return f"¿De qué grupo quieres consultarlo: {opts}?"
        if u.option_kind == "competition":
            who = f"«{u.team}» juega en varias competiciones. " if u.team else ""
            return f"{who}¿A cuál de estas competiciones te refieres: {opts}, o a otra?"
        return "¿De qué competición o equipo quieres consultarlo (por ejemplo, la competición y el grupo, o el nombre del equipo)?"

    def missing_text(self, u: Understanding) -> str:
        if u.team and u.options:
            return f"«{u.team}» no figura en ese ámbito; sí juega en: {'; '.join(u.options)}. ¿Quieres que lo consulte en alguna de ellas?"
        what = "; ".join(f"«{m}»" for m in u.missing)
        return (f"No he encontrado {what} entre las competiciones de la base de datos. Las que existen son: "
                f"{'; '.join(self.resolver.existing_terms())}. Dime cuál quieres consultar.")

    @staticmethod
    def nodata_text(u: Understanding) -> str:
        return ("No dispongo de datos de temporadas anteriores: la base solo contiene la temporada en curso" + (
            f" ({'; '.join(u.competitions)})" if u.competitions else "") + ". Si quieres, consulto la temporada actual.")

    def note(self, u: Understanding) -> str:
        if u.status == "none":
            return ""
        if u.status == "missing":
            return (NOTE_HEAD + f" No existe en el catálogo: {'; '.join(f'«{m}»' for m in u.missing)}. Dile con claridad qué has identificado y qué competiciones existen "
                    f"(no elijas otra ni inventes datos): {'; '.join(f'«{n}»' for n in self.resolver.existing_terms())}.")
        if u.status == "nodata":
            return (NOTE_HEAD + " Temporadas anteriores: la base solo contiene la temporada en curso; no hay datos de otras. Dilo con claridad, sin inventar, "
                    "y ofrece consultar la temporada actual.")
        if u.status == "ask":
            what = "las opciones reales" if u.options else "la competición, el grupo o el equipo"
            return (NOTE_HEAD + f" Aclaración necesaria: hay varias interpretaciones razonables; opciones: {'; '.join(f'«{o}»' for o in u.options) or 'ninguna identificada'}. "
                    f"Pregunta al usuario cuál (solo {what}) y no consultes datos hasta que responda.")
        lines = [NOTE_HEAD]
        if u.intent:
            lines.append(f" Intención identificada: {u.intent} ({LABEL.get(u.intent, u.intent)}).")
        for name in u.competitions:
            c = self.by_name[name]
            lines.append(f" Competición identificada: «{c.name}» (competition_id={c.id}).")
        if u.team:
            lines.append(f" Equipo identificado: «{u.team}» (juega en: {'; '.join(f'{c} · {g}' for c, g in u.team_entries)}).")
        if u.targets:
            lines.append("\nConsulta estos grupos" + (" y presenta los resultados separados por grupo" if len(u.targets) > 1 else "") + ": "
                         + ", ".join(f"group_id={t.group_id} («{t.group}» de {t.competition})" for t in u.targets) + ".\n")
        if u.assumption:
            lines.append(f" Empieza tu respuesta con esta frase exacta: «{u.assumption}»")
        lines.append(" Estas pistas están verificadas con el catálogo: úsalas sin volver a preguntar el ámbito; si el usuario cambia de tema, equipo o competición, "
                     "prevalece lo que diga el usuario.")
        return "".join(lines)

    def revision(self, u: Understanding) -> str:
        """Mensaje de recuperación (no repite la llamada: entrega la interpretación verificada y el plan)."""
        plan = "; ".join(f"{n}({', '.join(f'{k}={v}' for k, v in a.items())})" for n, a in self.plan(u)) or "las herramientas adecuadas"
        intent = (f"Intención identificada: {u.intent}" if u.intent else
                  "Intención no determinada (si el usuario pregunta cómo va una competición o un equipo, quiere la clasificación: get_standings)")
        return ("Revisión de la aplicación: tu respuesta anterior no consultó datos, pero la pregunta SÍ trata de la competición de fútbol sala de la RFFM: no uses el aviso de "
                f"alcance. Interpretación verificada con el catálogo → {intent}; competiciones: "
                f"{'; '.join(u.competitions) or 'las del contexto'}.\nConsulta estos grupos: "
                f"{', '.join(f'group_id={t.group_id}' for t in u.targets)}.\nLlama ahora a: {plan}.")

    def plan(self, u: Understanding) -> list[tuple[str, dict[str, Any]]]:
        """Consultas MCP que resuelven la intención con los grupos identificados (recuperación determinista). El equipo se resuelve con find_teams primero."""
        out: list[tuple[str, dict[str, Any]]] = []
        if not u.targets or u.intent in (None, "rules"):
            return out
        if u.team and u.intent in TEAM_INTENTS + ("matches",):
            return [("find_teams", {"name": u.team})]
        for t in u.targets:
            if u.intent in ("standings", "stats"):
                out.append(("get_standings", {"group_id": t.group_id}))
            elif u.intent == "scorers":
                out.append(("get_scorer_table", {"group_id": t.group_id, "limit": 10}))
            elif u.intent == "teams_list":
                out.append(("list_teams", {"group_id": t.group_id}))
            elif u.intent in TEAM_INTENTS + ("matches",):
                status = {"next_match": {"status": "scheduled", "order": "date_asc"}, "last_result": {"status": "finished", "order": "desc"}}.get(u.intent, {})
                out.append(("list_matches", {"group_id": t.group_id, **status, "limit": 10}))
        return out

    # ---------------------------------------------------------------------------------------------------------- estado
    def next_state(self, state: ConversationState, u: Understanding, question: str, answer: str) -> ConversationState:
        s = deepcopy(state)
        s.version = state.version
        s.turns += 1
        if u.stated_age:
            s.age = u.stated_age
        if u.stated_kind:
            s.kind = u.stated_kind
        if u.status in ("act", "assume") and u.competitions:
            s.competitions, s.groups = list(u.competitions), list(u.group_names) if len(u.competitions) == 1 else []
            ages = {self.resolver.age_of(self.by_name[n]) for n in u.competitions}
            kinds = {self.resolver.kind_of(self.by_name[n]) for n in u.competitions}
            s.age, s.kind = (ages.pop() if len(ages) == 1 else s.age), (kinds.pop() if len(kinds) == 1 else s.kind)
            for n in u.competitions:
                s.remember("competition", n)
            if u.team:
                s.remember_team(u.team)
            s.intent = u.intent or s.intent
            s.offered, s.offered_kind, s.pending = [], None, None
            if u.assumption:
                s.declared = list(dict.fromkeys([*s.declared, *u.competitions]))[-12:]
        elif u.status == "ask":
            s.offered, s.offered_kind = list(u.options), u.option_kind
            s.pending = {"question": question[:200], "kind": u.option_kind or "scope"}
            s.intent = u.intent or s.intent
            if u.team:
                s.remember_team(u.team)
            if u.option_kind == "group" and u.competitions:  # se pregunta el grupo de una competición ya identificada: la respuesta («el tercero») se refiere a ella
                s.competitions, s.groups = list(u.competitions), []
        elif u.status in ("missing", "nodata"):
            s.offered, s.offered_kind = [], None
        s.last_turn = turn_hash(question, answer)
        return s

    def rebuild(self, turns: Sequence[Turn], focus: dict[str, Any] | None = None) -> ConversationState:
        """Estado derivado del historial (lo que el servidor guardó o el navegador reenvió): repite la comprensión determinista turno a turno."""
        s = ConversationState()
        for i, t in enumerate(turns):
            _, effective, _ = resolve_pending(list(turns[:i]), t.question)
            u = self.understand(effective, t.question, s, has_history=i > 0)
            s = self.next_state(s, u, t.question, t.answer)
            if asks_for_input(t.answer):  # lo que el asistente ofreció de verdad (el usuario lo vio así) manda sobre lo que se habría deducido
                low = fold(t.answer)
                found = sorted((low.find(fold(c.name)), c.name) for c in self.competitions if fold(c.name) in low)
                if found:
                    s.offered, s.offered_kind = [n for _, n in found], "competition"
        if turns:
            s.last_turn = turn_hash(turns[-1].question, turns[-1].answer)
        focus = focus or {}
        if not s.competitions and focus.get("competition") in self.by_name:
            s.competitions = [str(focus["competition"])]
            s.groups = [str(focus["group"])] if focus.get("group") else []
        if focus.get("team") and (not s.teams or s.teams[0] != focus["team"]):
            s.remember_team(str(focus["team"]))
        return s
