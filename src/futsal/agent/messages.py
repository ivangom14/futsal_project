"""Mensajes para el usuario (Fase 24): fórmulas de «no hay información» según el MOTIVO y errores técnicos en lenguaje llano.

El detalle técnico nunca se pierde: va a los logs y al panel de diagnóstico, no al chat.
"""

from dataclasses import dataclass

# Motivos de «no hay información» (el LLM las usa según el caso; el prompt las enumera).
NO_DATA = "No he encontrado ese dato en las fuentes que tengo disponibles."  # el dato no está en lo ingerido
NO_INFO = "No tengo información suficiente para responder a esa pregunta."  # normativa/fuente sin información
UNCONFIRMED = "No puedo confirmar esa información con los datos actuales."  # dato dudoso, parcial o desactualizado
UNSUPPORTED = ("Todavía no puedo consultar ese tipo de información, pero sí puedo ayudarte con resultados, clasificaciones, "
               "goleadores, actas y normativa.")  # herramienta no compatible con la consulta
NEEDS_DETAIL = "¿Puedes concretarme un poco más qué competición o equipo quieres consultar?"  # consulta ambigua
NOT_IN_REPORT = "Ese dato no consta en el acta del partido: el acta que publica la RFFM no recoge {qué}."  # dato que el acta no recoge (solo v2)
LIMITATION_REPLIES = (NO_DATA, NO_INFO, UNCONFIRMED, UNSUPPORTED, NEEDS_DETAIL)


@dataclass(frozen=True)
class FriendlyError:
    code: str  # rate_limit | timeout | connection | internal
    status: int  # HTTP que devuelve el servicio de chat
    message: str


RATE_LIMIT = FriendlyError("rate_limit", 429, "El servicio está recibiendo demasiadas consultas en este momento. "
                                                "Espera un poco y vuelve a intentarlo.")
CONNECTION = FriendlyError("connection", 502, "No he podido conectar con el servicio. Inténtalo de nuevo dentro de unos instantes.")
TIMEOUT = FriendlyError("timeout", 504, "La consulta está tardando más de lo esperado. Puedes volver a intentarlo.")
INTERNAL = FriendlyError("internal", 500, "Ha ocurrido un problema al procesar tu consulta.")


def classify_error(technical: str) -> FriendlyError:
    """Traduce el texto técnico de un fallo (LLM, red, MCP) a un mensaje para el usuario."""
    low = technical.lower()
    if "http 429" in low or "429" in low.split() or "cuota" in low or "rate limit" in low or "quota" in low:
        return RATE_LIMIT
    if "timeout" in low or "timed out" in low or "tardó" in low or "tardo" in low or "readtimeout" in low:
        return TIMEOUT
    if any(w in low for w in ("fallo de red", "connecterror", "connection", "conectar", "no responde", "http 502", "http 503", "http 504")):
        return CONNECTION
    return INTERNAL
