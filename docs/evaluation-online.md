# Procedimiento de validación online (Windows CMD)

Todo lo de esta página se ejecuta **en tu equipo**, en `cmd.exe`, desde la carpeta del proyecto. Claude Code no tiene tu
clave de Gemini ni acceso a tu PostgreSQL: lo que hizo fue implementar y probar **offline** (tests, bases temporales,
LLM falso). La validación con Gemini real queda **pendiente hasta que ejecutes esto y compartas el informe**.

| Dónde | Qué | Consume cuota de Gemini |
|---|---|---|
| Claude (ya hecho) | `pytest`, Ruff, mypy, evaluación offline sobre bases temporales con fixtures | No |
| Tú, CMD | Pasos 0 a 3 (preparar, preflight, plan, tests) | **No** |
| Tú, CMD | Paso 4 (`--mode live`, suite `smoke`) | **Sí** (≈10 llamadas previstas) |
| Tú, CMD | Paso 7 (`--suite all`) | **Sí** (decenas de llamadas; solo tras revisar el smoke) |

## 0. Preparar (una vez por sesión)
```bat
cd ruta\a\futsal_project
.venv\Scripts\activate.bat
git pull origin claude/tender-rubin-rsz9uv
pip install -e ".[dev]"
docker compose up -d --wait db
```
No hay migraciones nuevas en la Fase 10. En **otra ventana** CMD (con el entorno activado) deja la API en marcha:
```bat
uvicorn futsal.api.app:app_factory --factory --port 8000
```
La clave se configura **solo** en `.env` (ábrelo con `notepad .env`):
```
LLM_PROVIDER=gemini
GEMINI_API_KEY=tu_clave
GEMINI_MODEL=gemini-3.5-flash-lite
```
No uses `type .env` ni `findstr GEMINI_API_KEY .env`: mostrarían la clave en pantalla.

## 1. Preflight: configuración, servicios y "misma base" (sin Gemini, solo lectura)
```bat
python -m futsal.agent.preflight --for-live
```
Comprueba, **sin imprimir secretos** (de cada clave solo dice "definida"/"NO definida"): `.env`, proveedor y modelo, host/base
de `DATABASE_URL` (sin contraseña), Docker, que existen las 10 tools MCP, que la API responde y que PostgreSQL se lee. Hace una
auditoría de datos (actas, finalizados sin acta, actas sin cerrar, marcadores que difieren, jugadores con nombre repetido) y
**demuestra que la API y la evaluación usan la misma base** comparando una huella de los partidos (id, estado, marcador) vista
por PostgreSQL y por la API. Código de salida: `0` todo bien, `1` avisos, `2` **bloqueado**.

Si dice `NO DEMOSTRADO` o `DIFERENTES`, la evaluación online queda **bloqueada por defecto**: casi siempre es que la API se
arrancó contra otra base o está desactualizada. Reinícala (`uvicorn ...`) tras `git pull`.

## 2. Plan de cuota (sin Gemini, no necesita clave)
```bat
python -m futsal.agent.evaluate --plan --suite smoke
```
Imprime: casos, **llamadas previstas** al LLM (ruta de referencia), **máximo teórico** (= casos × (`MAX_TOOL_CALLS`+1)), los
**reintentos** permitidos, las **peticiones HTTP en el peor caso**, el presupuesto duro (`--max-requests`) y los tokens
**estimados** (≈3,5 caracteres/token). Los números exactos son los límites; las llamadas y los tokens son estimaciones.
Gemini informará los tokens reales en el informe.

## 3. Tests offline (sin Gemini)
```bat
python -m pytest -q
ruff check .
python -m mypy
```

## 4. Evaluación online mínima: suite `smoke` (CONSUME CUOTA)
```bat
python -m futsal.agent.evaluate --mode live --confirm-live --suite smoke --max-requests 25
```
Son 5 preguntas: resultados de un equipo, goleadores del último partido, un partido **futuro** (no debe inventar marcador),
un apellido **ambiguo** (no debe elegir jugador) y una pregunta fuera de alcance (1 llamada). `--confirm-live` es obligatorio;
`--max-requests` es un tope duro (llamadas + reintentos): al alcanzarlo, los casos restantes quedan como `not_run`. Antes de
empezar muestra el plan y la comprobación de "misma base". Reintentos: `LLM_MAX_RETRIES` (por defecto 2, solo 429/5xx/red) y
`LLM_RETRY_MAX_WAIT` (20 s); ponlos a `0` para desactivarlos.

## 5. Qué se genera y qué compartir
Cada ejecución crea `data\agent\eval\<fecha>-<modo>-<suite>\` con:
- `report.md`: por caso, pregunta, criterio de corrección, herramientas usadas, **respuesta del agente**, veredicto por capa
  (datos, herramientas, respuesta, invención, advertencia, presentación), tokens (reales/estimados/no informados), reintentos y latencia.
- `results.json`: lo mismo, estructurado (sirve para `--compare`).
- `traces.sanitized.jsonl`: trazas sin resultados de tools y con claves/cadenas de conexión enmascaradas.
- `traces.raw.jsonl`: traza cruda. **No la compartas.** Está en `data\`, que Git ignora.

**Comparte `report.md` y `traces.sanitized.jsonl`.** Comprobación final antes de enviarlos (no debe salir nada):
```bat
findstr /I /C:"AIza" /C:"postgresql://" /C:"GEMINI_API_KEY=" report.md traces.sanitized.jsonl
```
Para sanear una traza de uso normal del agente: `python -m futsal.agent.evaluate --sanitize-traces data\agent\traces.jsonl data\agent\traces.sanitized.jsonl`.

## 6. Comparar antes y después
```bat
python -m futsal.agent.evaluate --compare data\agent\eval\ANTES\results.json data\agent\eval\DESPUES\results.json
```
Muestra cambios de estado por caso, **regresiones** (pass→fail) y diferencias de tokens, llamadas y latencia. Avisa si las dos
ejecuciones no son comparables (p. ej. tokens estimados de `offline` frente a reales de `live`).

## 7. Batería completa (solo si el smoke es satisfactorio)
```bat
python -m futsal.agent.evaluate --plan --suite all
python -m futsal.agent.evaluate --mode live --confirm-live --suite all --max-requests 120
```
Revisa primero el plan: los casos que no existen en tu base (p. ej. no hay equipos con nombre repetido) salen `not_evaluable`
y **no consumen cuota**. Una pregunta suelta: `python -m futsal.agent "tu pregunta" --trace-results`.

## Cómo leer el resultado
- `✔` cumple, `✘` falla, `—` no aplica o **no se pudo evaluar de forma fiable** (nunca se aprueba por defecto).
- **datos**: las tools devolvieron la cifra correcta. **herramientas**: usó las obligatorias, ninguna ajena, sin repetir llamadas ni
  pasarse del máximo. **respuesta**: contiene los hechos esperados. **invención**: no nombra jugadores que las tools no devolvieron,
  no da marcador de un partido no jugado, no traduce códigos de tarjeta a color, no inventa minutos de cambios.
  **advertencia**: avisa si la cobertura de actas es parcial, el acta falta o los marcadores se contradicen.
- Estos criterios son léxicos y deterministas: reducen la invención, **no la descartan**. Lee las respuestas del informe.
