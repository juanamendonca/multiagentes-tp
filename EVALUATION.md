# Evaluación

11 preguntas de prueba sobre los tres PDFs de muestra, definidas en
[`eval/questions.yaml`](eval/questions.yaml) con su estado y respuesta esperados (calculados a mano
desde los PDFs). Incluyen preguntas de una tabla, multitabla (dos ediciones de la EPH, e INDEC +
CEPAL), conversión de escala, una sin tabla relacionada y casos con un dato faltante (período,
conversión externa y valor de comparación). Una pregunta es correcta si el estado coincide y cada
valor esperado aparece en la respuesta dentro de la tolerancia.

Se reproduce con `python -m eval.run_eval` (sin caché, para medir latencias y llamadas reales). La
tabla de abajo es la de la última corrida; una corrida anterior también dio 9/11.

<!-- EVAL:START -->
## Resultados de la corrida

_Sección generada por `python -m eval.run_eval`; no editar a mano._

- Fecha: 2026-10-07 23:00 · modelo: `gpt-5.4-mini` · `LLM_CACHE=false` · `MAX_CODE_RETRIES=3` · preguntas: 11
- **Aciertos (estado y valor correctos): 9/11 = 81.8 %** (objetivo NFR-012: ≥ 75 %, alcanzado); estado correcto en 10/11.
- Latencia por pregunta: mediana 15.2 s, máximo 19.9 s (NFR-009: mediana ≤ 60 s y máximo ≤ 180 s, cumple).
- Llamadas de chat por pregunta: máximo 4 (límite 7), total 42; embeddings: máximo 1 (límite 3); NFR-006 cumple. Reintentos de código en total: 0.

| Id | Pregunta | Estado esperado | Estado obtenido | Valor esperado | Valor obtenido | Correcto | Tablas esperadas | Reintentos | Latencia (s) | Llamadas LLM | Traza |
|---|---|---|---|---|---|---|---|---|---|---|---|
| q01 | ¿Cuál fue la tasa de desocupación del total de 31 aglomerados urbanos en el 2° trimestre de 2026 y cuánto cambió, en puntos porcentuales, respecto del 2° trimestre de 2025? | answered | answered ✔ | 7,9 %; ±0,3 p.p. | 7,9 %; 0,3 p.p. ✔ | **sí** | ✔ | 0 | 19.2 | 4 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/6b3f1f7b-0778-44cb-871b-c15317defda3?poll=true) |
| q02 | ¿Cuál fue la tasa de desocupación del Gran Buenos Aires en el 2° trimestre de 2025 y en el 2° trimestre de 2026, y cuántos puntos porcentuales varió entre ambos? | answered | answered ✔ | 8,7 %; 8,2 %; ±0,5 p.p. | 8,7, 8,2 %; -0,5 p.p. ✔ | **sí** | ✔ | 0 | 16.4 | 4 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/9f9a86f6-ddb0-4ab3-a7dd-85ab85ecfd0e?poll=true) |
| q03 | ¿Cuántas personas desocupadas había en el total de 31 aglomerados urbanos en el 2° trimestre de 2026? Expresalo en millones de personas. | answered | answered ✔ | 1,164 millones de personas | 1,164 millones de personas ✔ | **sí** | ✔ | 0 | 12.0 | 4 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/984e24c8-6507-45a2-bb63-b92c3df99765?poll=true) |
| q04 | Según la CEPAL, ¿cuál fue la tasa media anual de desocupación abierta de Argentina en 2024? Según el INDEC, ¿cuál fue la tasa de desocupación del total de 31 aglomerados urbanos en el 2° trimestre de 2025? ¿Cuántos puntos porcentuales de diferencia hay entre ambas? | answered | answered ✔ | 7,2 %; 7,6 %; ±0,4 p.p. | 7,2 %; 7,6 %; 0,4 p.p. ✔ | **sí** | ✔ | 0 | 19.9 | 4 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/fe3e594b-53e3-4aef-aff1-cce7bc88439f?poll=true) |
| q05 | ¿Cuántas personas desocupadas había en el Gran Buenos Aires en el 2° trimestre de 2025 y en el 2° trimestre de 2026, y cuál fue la diferencia? | answered | answered ✔ | 683 miles de personas; 656 miles de personas; ±27 miles de personas | 1091, 1164 miles de personas; 73 miles de personas ✘ | no | ✘ | 0 | 15.2 | 4 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/c6793253-5fd1-4c9d-88ea-07d9e9e839e5?poll=true) |
| q06 | ¿Qué región tuvo el mayor desempleo en 2023 y cuánto superó al promedio nacional? | cannot_answer | cannot_answer ✔ | — | — ✔ | **sí** | — | 0 | 15.3 | 4 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/99fe74fc-f8c0-4198-9d48-7754e38db948?poll=true) |
| q07 | ¿Cuál fue la tasa de desocupación del total de 31 aglomerados urbanos en el 2° trimestre de 2024 y en el 2° trimestre de 2023? | partial | cannot_answer ✘ | 7,6 % | — ✘ | no | — | 0 | 12.6 | 4 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/314dcdba-c949-4997-a431-4361ae1b88f0?poll=true) |
| q08 | ¿Qué porcentaje de la población mundial representaba la población total de Argentina en 2025? | cannot_answer | cannot_answer ✔ | — | — ✔ | **sí** | — | 0 | 8.3 | 3 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/063b96b0-b1fc-4c33-85d6-5395ddc6e8f5?poll=true) |
| q09 | ¿Cuál fue la tasa media anual de desocupación abierta de Colombia en 2024 y cuántos puntos porcentuales superó al promedio de los países del Caribe? | partial | partial ✔ | 10,2 % | 10,2 % ✔ | **sí** | ✔ | 0 | 15.9 | 4 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/27143e71-94b8-4cb3-ae89-0ab8b5f21d3e?poll=true) |
| q10 | ¿Cuántos goles hizo Messi en el Mundial de Qatar 2022? | cannot_answer | cannot_answer ✔ | — | — ✔ | **sí** | — | 0 | 8.0 | 3 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/1481b6c5-90a4-4c73-a707-143774556ab0?poll=true) |
| q11 | Según la CEPAL, ¿cuál era la población total de Uruguay en 2025, en millones de personas? | answered | answered ✔ | 3,3847 millones de personas | 3,3847 millones de personas ✔ | **sí** | ✔ | 0 | 12.0 | 4 | [traza](https://smith.langchain.com/o/5e77de68-d657-4492-92a7-0c1085d69b26/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/72c7ba6c-abb5-408b-82c5-445adf45edc1?poll=true) |

Valor esperado con `±`: se compara el valor absoluto (variaciones). "Tablas esperadas": las tablas de `expected_tables` están entre las que usó el Analista (informativo, no afecta el acierto; "—" si no hubo cálculo). Detalle por pregunta (faltantes, conversiones, tablas, respuesta) en `eval/results/latest.json`.
<!-- EVAL:END -->

## Fallas

- **q05** (multitabla, desocupados de GBA): la búsqueda no trajo la tabla de 2T2026 que tiene el
  dato y el código usó la fila del total de 31 aglomerados. En otra corrida respondió bien, así que
  el resultado no es estable.
- **q07** (un período existe y el otro no): se esperaba `partial`, pero el plan juntó los dos
  períodos en una sola parte y la respuesta fue `cannot_answer`.

## Trazas representativas

1. [Ingesta de la EPH 2T2026](https://smith.langchain.com/public/367af2c4-b7d8-4758-ac52-fa24b2c0ebf7/r/5ff8d853-72a8-43bd-a8eb-c96115929217?start_time=2026-10-07T20%3A00%3A07.157478Z):
   un span por nodo del grafo y 16 tablas enriquecidas.
2. [Respuesta `answered` multitabla (q04)](https://smith.langchain.com/public/39f081b5-2548-4d49-8adc-4481941cecaa/r/fe3e594b-53e3-4aef-aff1-cce7bc88439f?start_time=2026-10-08T02%3A01%3A08.943283Z):
   combina una tabla de CEPAL y una de INDEC.
3. [Respuesta `partial` (q09)](https://smith.langchain.com/public/aa7af97d-e4c6-4385-b3de-d3f9d55e6d89/r/27143e71-94b8-4cb3-ae89-0ab8b5f21d3e?start_time=2026-10-08T02%3A02%3A20.076217Z):
   calcula la tasa de Colombia e informa que falta el promedio del Caribe.
