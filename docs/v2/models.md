# v2 · Modelos LLM (verificado en documentación oficial el 2026-11-08)

Precios en USD por millón de tokens (entrada / salida), nivel estándar de pago y sin descuentos de lote. **Volver a verificar
antes de cada ejecución de pago** y anotarlo en `config/llm_prices.json` (fecha y URL).

## Google Gemini — [modelos](https://aistudio.google.com/docs/models) · [precios](https://aistudio.google.com/docs/pricing) · [límites](https://aistudio.google.com/docs/rate-limits)

| ID | Estado | Entrada / salida | Entrada cacheada | Plan gratuito | Notas |
|---|---|---|---|---|---|
| `gemini-3.5-flash-lite` | estable | 0,30 / 2,50 | 0,03 | sí | **modelo actual del proyecto**; 1M de contexto, llamada de funciones, razonamiento y caché |
| `gemini-3.1-flash-lite` | estable | 0,25 / 1,50 | 0,025 | sí | |
| `gemini-3.8-flash` | estable («nuevo») | 0,75 / 3,75 → **1,50 / 7,50 desde el 1-1-2027** | 0,075 → 0,15 | sí | razonamiento low/medium/high (no admite `minimal`); la caché explícita cobra almacenamiento por hora |
| `gemini-3.6-flash` | estable | igual que 3.8 Flash | | sí | |
| `gemini-3-flash-preview` | preview | 0,50 / 3,00 | 0,05 | sí | preview: no apto para producción |
| `gemini-3.1-pro-preview` | preview | 2 / 12 (≤ 200k) | 0,20 | no | |
| `gemini-4-argon` | publicado | 2 / 10 → **4 / 20 desde el 1-12-2026** | 0,10 → 0,20 | no | modelo de razonamiento insignia |

Los límites del plan gratuito (RPM, TPM y RPD) **no están en la documentación**: se consultan por cuenta en AI Studio. Datos:
en el plan gratuito Google puede usar las entradas y salidas para mejorar sus productos, salvo en el EEE, Suiza y el Reino
Unido ([términos](https://ai.google.dev/gemini-api/terms)).

## Anthropic Claude — [modelos](https://platform.claude.com/docs/en/about-claude/models/overview) · [precios](https://platform.claude.com/docs/en/about-claude/pricing)

| ID | Entrada / salida | Lectura de caché | Notas |
|---|---|---|---|
| `claude-haiku-5-5` | 0,10 / 0,50 (prompts ≤ 100k) | 0,01 | el más rápido; pensado para clasificación, extracción y enrutado; retirada no antes de oct. 2027 |
| `claude-sonnet-5-5` | 2 / 10 | 0,10 | equilibrio de velocidad e inteligencia |
| `claude-opus-5-5` | 4 / 20 | 0,20 | |
| `claude-fable-5-1` | 10 / 50 | 0,25 | razonamiento exigente; desproporcionado para Futsito |
| `claude-haiku-4-5` | 1 / 5 | 0,10 | **es el predeterminado actual de `AnthropicLLM`: obsoleto y 10 veces más caro que Haiku 5.5** |

Todos tienen 1M de contexto, uso de herramientas, razonamiento adaptativo controlado por `effort` y lote al 50 %. No hay plan gratuito de API.

## OpenAI — [modelos](https://developers.openai.com/api/docs/models) · [precios](https://developers.openai.com/api/docs/pricing)

| ID | Entrada / salida | Entrada cacheada | Notas |
|---|---|---|---|
| `gpt-6-luna` | 0,10 / 0,50 | 0,01 | «el más eficiente»; razonamiento desde `none` |
| `gpt-6.1-terra` | 1 / 5 | 0,05 | rápido y eficiente |
| `gpt-6.1-sol` | 2 / 10 | 0,10 | casi como Astra a menor coste |
| `gpt-6-astra` | 10 / 50 | 1,00 | desproporcionado para Futsito |

Todos tienen 1,05M de contexto, llamada de funciones y niveles de razonamiento. El proyecto **no tiene adaptador OpenAI**
(tarea T04).

## Pesos abiertos

No hay una fuente independiente y actual que compare la llamada de herramientas en español (los rankings publicados se
contradicen: GLM-5.1, Kimi K2.6, DeepSeek V4, Qwen 3.5). **No entran en la primera comparativa.** Uso local recomendado
solo para embeddings del RAG (A8). Si en el futuro hiciera falta un modelo local, se evaluaría con esta misma batería.

## Preselección por fases (propuesta para A5)

Coste por ejecución de la batería (≈ 570 preguntas: 6,2 M tokens de entrada y ≈ 0,5 M de salida con razonamiento, sin caché;
estimación conservadora [E]):

| Fase | Modelo | Smoke (40 casos) | Completa (×3) |
|---|---|---|---|
| Referencia | `gemini-3.5-flash-lite` | gratis* | gratis* (≈ 3,1 si fuera de pago) |
| **F-A económicos** | `gemini-3.1-flash-lite` | gratis* | gratis* (≈ 2,3) |
| | `claude-haiku-5-5` | 0,08 | 0,87 |
| | `gpt-6-luna` | 0,08 | 0,87 |
| **F-B medios** | `gemini-3.8-flash` | gratis* | gratis* (≈ 6,5 en 2026; 13 desde 2027) |
| | `gpt-6.1-terra` | 0,85 | 8,70 |
| | `claude-sonnet-5-5` | 1,70 | 17,40 |
| F-C (solo si F-B no llega a los umbrales) | `gemini-4-argon`, `gpt-6.1-sol`, `claude-opus-5-5` | 1,7–3,4 | 17–35 cada uno |

\* Dentro de la cuota gratuita; puede requerir varios días.

**Total previsto en F-A + F-B: ≈ 28 USD de pago**, más un margen del 30 % para reintentos y para la medición de ruido: ≈ 35 USD.
F-C solo se ejecuta con una nueva aprobación.

## Hipótesis que la comparativa debe confirmar o descartar

1. Un modelo económico (Haiku 5.5, Luna o Flash-Lite) basta para T1 si el resolutor fija el ámbito y la evidencia llega precargada.
2. La diferencia entre modelos se concentra en S4 (complejas), S6 (adversariales) y S3 (encadenadas); ahí puede compensar un
   modelo medio solo para T2.
3. La caché de prompts reduce el coste de entrada más que cualquier compresión de texto (el prefijo fijo es el 83 % de la
   entrada [M]).

## Costes de producción (mezcla multimodelo, estimación [E])

Supuestos: 70 % de las preguntas T0/T1 con modelo económico (≈ 5k tokens de entrada y 300 de salida), 25 % T1 más largas
(≈ 12k y 400) y 5 % T2 con modelo medio (≈ 30k y 1.500). Con Haiku 5.5 o Luna más Sonnet 5.5: ≈ 0,0046 USD por pregunta,
**unos 46 USD al mes con 10.000 preguntas y 460 USD con 100.000**: el mismo coste que hoy con Flash-Lite para todo, pero con
un modelo medio para lo difícil. Con caché de prompts, del orden del 30–50 % menos [H].
