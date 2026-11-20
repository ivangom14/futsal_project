"""Cliente de pruebas de la API para las pruebas de FÚTBOL SALA anteriores a la Fase 1 multideporte.

La API exige `sport` en todos los endpoints de datos. Estas pruebas verifican contratos de fútbol sala, así que su cliente
añade `sport=futsal` de forma EXPLÍCITA cuando la petición no lo trae (nunca sustituye uno dado). Las pruebas de validación del
deporte (`test_multisport_pg.py`) y los clientes que pasan por MCP o preflight usan el `TestClient` normal: ahí el deporte lo
envía el propio cliente y se valida de verdad."""

from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

NO_SPORT = ("/health",)


class FutsalClient(TestClient):
    def request(self, method: str, url: Any, *args: Any, params: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        path = urlsplit(str(url))
        query = parse_qs(path.query, keep_blank_values=True)
        given = dict(params or {}) if not isinstance(params, (list, tuple)) else dict(params)
        if path.path in NO_SPORT or "sport" in given or "sport" in query:
            return super().request(method, url, *args, params=params, **kwargs)
        # httpx sustituye la query de la URL si se pasan `params`: se conservan los dos y se añade el deporte
        merged: dict[str, Any] = {k: v if len(v) > 1 else v[0] for k, v in query.items()}
        merged.update(given)
        merged["sport"] = "futsal"
        return super().request(method, path._replace(query="").geturl(), *args, params=merged, **kwargs)
