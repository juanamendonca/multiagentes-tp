# Evaluación

Informe de la evaluación del sistema de preguntas y respuestas sobre tablas en PDFs: cómo se
evalúa, resultados de cada pregunta, análisis de los errores, requisitos no funcionales medidos,
trazas representativas de LangSmith y limitaciones. Cómo correrla: ver "Evaluación" en el
[README](README.md#6-evaluación).

## Metodología

**Set de preguntas.** [`eval/questions.yaml`](eval/questions.yaml) tiene 11 preguntas sobre los tres
PDFs de muestra (dos informes de la EPH de INDEC, 2.º trimestre de 2025 y de 2026, y un extracto del
Anuario Estadístico 2025 de CEPAL). Para cada una se anotan el estado esperado, los valores esperados
con su unidad, una tolerancia absoluta y las tablas (archivo + páginas) que contienen los datos. Los
valores esperados se calcularon **a mano desde el texto de los PDFs** (página, cuadro, fila y columna
en el campo `notes`), no desde la salida del sistema.

| Id | Qué ejercita | Estado esperado | Valor esperado | Fuente |
|---|---|---|---|---|
| q01 | una tabla, valor + variación | answered | 7,9 %; ±0,3 p.p. | EPH 2T2026 pág. 8 (Cuadro 1.3) |
| q02 | **multitabla**: dos ediciones de la EPH | answered | 8,7 %; 8,2 %; ±0,5 p.p. | EPH 2T2025 pág. 18 + EPH 2T2026 pág. 20 (o los cuadros regionales equivalentes) |
| q03 | cambio de escala (miles → millones) | answered | 1,164 millones | EPH 2T2026 pág. 8 |
| q04 | **multitabla**: CEPAL + INDEC | answered | 7,2 %; 7,6 %; ±0,4 p.p. | CEPAL pág. 8 (1.2.1) + EPH 2T2025 pág. 8 |
| q05 | **multitabla**, recuperación difícil | answered | 683; 656; ±27 miles | EPH 2T2025 pág. 17 + EPH 2T2026 pág. 19 |
| q06 | tabla relacionada, **falta el período** (2023) | cannot_answer | — | cuadros regionales 2T2025/2T2026 |
| q07 | un período presente y otro faltante | partial | 7,6 % (2T2024) | EPH 2T2025 pág. 8 |
| q08 | conversión que requiere un **dato externo** (población mundial) | cannot_answer | — | CEPAL págs. 3–4 (Argentina) |
| q09 | falta el **valor de comparación** (promedio del Caribe) | partial | 10,2 % | CEPAL pág. 8 |
| q10 | **ninguna tabla relacionada** | cannot_answer | — | — |
| q11 | escala sobre la tabla multipágina | answered | 3,3847 millones | CEPAL pág. 4 (continuación del 1.1.1) |

**Criterio de corrección.** Una pregunta es correcta si el estado obtenido es el esperado **y** los
valores coinciden. Con valor esperado nulo, la respuesta no debe tener resultados numéricos. Si no,
cada valor esperado tiene que aparecer entre todos los números calculados (listas y diccionarios
aplanados) a distancia menor o igual a la tolerancia; los valores marcados con `±` se comparan en
valor absoluto (una variación puede informarse como "+0,3" o "bajó 0,3"). Los valores extra no
penalizan y la unidad textual no se compara (la escala sí: 1,164 millones ≠ 1 164 miles). Se
compara por valor y no por parte porque la forma en que el Analista divide la pregunta varía. La
columna "Tablas esperadas" (si el Analista usó las tablas anotadas) es informativa y no afecta el
acierto.

**Condiciones.** Modelo `gpt-5.4-mini` con `temperature=0`, embeddings `text-embedding-3-small`,
`RETRIEVAL_TOP_K=5`, `MAX_CODE_RETRIES=3`, índice con las 33 tablas de los tres PDFs. Las corridas
medidas se hacen **sin caché del LLM** (`LLM_CACHE=false`), para que latencias y llamadas sean
reales. Las llamadas de chat se cuentan en el propio Analista y se cotejaron, pregunta por pregunta,
con los runs de tipo `llm` de cada traza en LangSmith (11/11 coinciden en la corrida 2).

## Corridas

Se hicieron dos corridas completas y una re-corrida de las preguntas que fallaron. Entre la primera
y la segunda hubo **una única ronda de ajuste** del prompt que divide la pregunta en partes (ver
análisis de errores); no se cambiaron las preguntas ni los valores esperados.

| | Corrida 1 | Corrida 2 (código entregado) | Re-corrida de q05 y q07 |
|---|---|---|---|
| Aciertos | 9/11 = 81,8 % | 9/11 = 81,8 % | q05 ✔, q07 ✘ |
| Estado correcto | 9/11 | 10/11 | 1/2 |
| Fallas | q05 (`cannot_answer`), q08 (`partial`) | q05 (valores incorrectos), q07 (`cannot_answer`) | q07 (`cannot_answer`) |
| Latencia mediana / máxima | 13,9 s / 19,0 s | 15,2 s / 19,9 s | 17,8 s / 19,1 s |
| Llamadas de chat: máximo por pregunta / total | 5 / 44 | 4 / 42 | 4 / 8 |
| Reintentos de código | 1 (q05) | 0 | 0 |

Resultados guardados: `eval/results/run1.json`, `eval/results/run2.json` (= `latest.json`) y
`eval/results/subset-20261007-230322.json`, con faltantes, conversiones, tablas usadas y la
respuesta completa de cada pregunta. La tabla siguiente corresponde a la corrida 2.

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

## Análisis de errores

**q05 — personas desocupadas en GBA en dos ediciones (falla en las dos corridas, por motivos
distintos; es el caso más grave del set).**

- *Corrida 1 (`cannot_answer`, 1 reintento):* las consultas de búsqueda ("personas desocupadas en
  GBA…") no recuperaron el cuadro de cantidades de la EPH 2T2026 ("Población de referencia por región
  y condición (en miles)", pág. 19), que quedaba en el puesto 5–6 o fuera del top-5 porque su nombre
  habla de "población" y no de "desocupados". El selector cubrió la pregunta con el cuadro de
  cantidades de 2025 y un cuadro de tasas de 2026, y el código, correctamente, reportó el dato de
  2026 como faltante. Resultado honesto, pero no el esperado.
- *Corrida 2 (respuesta incorrecta **sin aviso**):* tras el ajuste de prompt las consultas nombran
  "población desocupada (en miles de personas)", pero esta vez el selector eligió el Cuadro 1.3 de la
  EPH 2T2026, que es del **total de 31 aglomerados**, y lo marcó como cubriendo GBA. El código tomó la
  fila "Población desocupada" (1 091 → 1 164 miles) y la rotuló como GBA. Ni el selector ni el
  código verificaron que la entidad pedida estuviera en la tabla: el sistema respondió con datos que
  no son los pedidos.
- *Re-corrida:* correcta (683 → 656, −27 miles, con los cuadros de las págs. 17 y 19).
- *Lectura:* con `temperature=0` el selector igual no es determinístico, y la recuperación depende de
  cómo quedó nombrada la tabla. Mejoras posibles (no implementadas): exigirle al código que verifique
  que la fila o columna de la entidad existe antes de calcular, sumar al texto de embedding los
  rótulos agrupadores de fila ("Gran Buenos Aires"), o recuperar más candidatas para preguntas de
  cantidades.

**q07 — tasa del 2T2024 y del 2T2023 (regresión introducida por el ajuste).** En la corrida 1 el plan
separó los dos trimestres en dos partes: el 2T2024 está (7,6 %, EPH 2T2025 pág. 8) y el 2T2023 no →
`partial` correcto. El ajuste de prompt agregó la regla "los datos intermedios u operandos de una
cuenta no son partes"; desde entonces el plan junta los dos períodos en una sola parte, que queda
entera como faltante → `cannot_answer` (corrida 2 y re-corrida). El sistema no inventa el dato de
2023 en ningún caso: el error es de granularidad, no de veracidad.

**q08 — porcentaje de la población mundial (corregida por el ajuste).** En la corrida 1 el plan
convirtió el operando (población de Argentina, 45 851,4 miles) en una parte propia, que se respondió
→ `partial`. En la corrida 2 hay una sola parte, a la que le falta la población mundial (dato
externo que ninguna tabla trae) → `cannot_answer` correcto.

**El ajuste de prompt** (una sola ronda, sobre el planificador: consultas que nombran "población …
(en miles de personas)" cuando se pide una cantidad de personas, y "los operandos no son partes")
tuvo efecto neto cero en el acierto: corrigió q08, hizo que q05 recupere a veces las tablas
correctas y rompió q07. Se conserva porque es el código que se entrega y el que generó la tabla de
arriba. Cualquier regla de partición mejora un caso y empeora otro: la granularidad del plan decide
entre `partial` y `cannot_answer`.

**Observaciones sin falla.**

- q06 (período 2023 inexistente): el selector a veces marca la parte como cubierta con tablas de
  2025/2026; es el código el que detecta que falta 2023 y lo reporta, con lo que el estado final es
  correcto (`cannot_answer`, con el período en el motivo) a costa de una ejecución.
- q09: el sistema no calcula un "promedio del Caribe" a partir de los países listados; informa que
  ese agregado no está en la tabla (`partial`, con 10,2 % para Colombia).
- El selector a veces agrega una tabla que el código no usa, o cita como tabla relacionada un cuadro
  equivalente distinto del anotado (con los mismos valores).
- En la corrida 2 el código generado funcionó al primer intento en las 11 preguntas. El único
  reintento observado en la evaluación (q05, corrida 1) terminó en `cannot_answer`. La
  autocorrección sí se ejercita en los tests automatizados (código con `KeyError` que se corrige en el
  segundo intento) y se observó en pruebas manuales.

## Requisitos no funcionales medidos

| Requisito | Umbral | Medido | Cumple |
|---|---|---|---|
| Exactitud del set (estado y valor correctos) | ≥ 75 % | 81,8 % en ambas corridas | sí |
| Latencia del Analista | mediana ≤ 60 s, máximo ≤ 180 s | 15,2 s / 19,9 s (corrida 2); 13,9 s / 19,0 s (corrida 1) | sí |
| Llamadas de chat por pregunta | ≤ 4 + `MAX_CODE_RETRIES` = 7 | máximo 4 (corrida 2), 5 (corrida 1) | sí |
| Llamadas de embeddings por pregunta | ≤ 3 | 1 | sí |
| Llamadas de chat por tabla en la ingesta | ≤ 1 + `MAX_ENRICH_RETRIES` = 3 | 1 (las 33 tablas válidas al primer intento) | sí |
| Embeddings en la ingesta | ≤ 1 llamada cada 64 tablas | 1 por PDF | sí |
| Latencia de la ingesta | ≤ 5 min para ≤ 30 páginas y ≤ 15 tablas | EPH 2T2026 (26 págs., 16 tablas): 33,7 s incluida la creación del índice; EPH 2T2025 (23 págs., 12 tablas): 19,8 s; CEPAL (10 págs., 5 tablas): 11,9 s | sí |

Los tiempos de ingesta son los de la ingesta real (sin caché) que cargó el índice usado en la
evaluación; no se reingirió durante las corridas de preguntas para no cambiar el índice evaluado.

## Trazas representativas

Las tres trazas están compartidas públicamente desde el proyecto de LangSmith del autor: los links
de abajo se abren sin iniciar sesión.

1. **Ingesta** — `ingestion:indec_eph_mercado_trabajo_2t2026.pdf`
   (run `5ff8d853-72a8-43bd-a8eb-c96115929217`). Muestra el run raíz con un span por nodo
   (`load_pdf → extract_tables → collect_context → enrich → validate → index`), la metadata
   `doc_hash`, `n_tables=16`, `n_warnings`, `n_indexed`, `llm_calls`, y las 16 llamadas de
   enriquecimiento en paralelo con salida estructurada. Ninguna tabla de la ingesta real necesitó
   reintento de validación.
   [Ver traza (link público)](https://smith.langchain.com/public/367af2c4-b7d8-4758-ac52-fa24b2c0ebf7/r/5ff8d853-72a8-43bd-a8eb-c96115929217?start_time=2026-10-07T20%3A00%3A07.157478Z)
2. **Respuesta `answered` multitabla** — q04, corrida 2 (run `fe3e594b-53e3-4aef-aff1-cce7bc88439f`):
   la tasa de desocupación de Argentina en 2024 según CEPAL (7,2 %), la del total de 31 aglomerados
   del 2T2025 según INDEC (7,6 %) y su diferencia (0,4 p.p.). Muestra el plan en 3 partes, la
   recuperación sobre los tres documentos, la selección de una tabla de cada organismo (alias `t1` y
   `t2`), el código generado, la ejecución en el sandbox y la metadata `status=answered`,
   `selected_table_ids`, `code_attempts=1`. No hubo respuestas multitabla `answered` con reintento de
   código en las corridas.
   [Ver traza (link público)](https://smith.langchain.com/public/39f081b5-2548-4d49-8adc-4481941cecaa/r/fe3e594b-53e3-4aef-aff1-cce7bc88439f?start_time=2026-10-08T02%3A01%3A08.943283Z)
3. **Respuesta `partial`** — q09, corrida 2 (run `27143e71-94b8-4cb3-ae89-0ab8b5f21d3e`): se calcula
   la tasa de Colombia en 2024 (10,2 %), pero la comparación con "el promedio de los países del
   Caribe" queda como faltante porque ese agregado no está en la tabla (no se lo inventa promediando
   países). Muestra la cobertura por parte en la selección, el JSON del código con `results` y
   `missing`, y `decide_status` resolviendo `partial`.
   [Ver traza (link público)](https://smith.langchain.com/public/aa7af97d-e4c6-4385-b3de-d3f9d55e6d89/r/27143e71-94b8-4cb3-ae89-0ab8b5f21d3e?start_time=2026-10-08T02%3A02%3A20.076217Z)

Los links a la traza de cada pregunta de la corrida 2 están en la última columna de la tabla de
resultados.

## Limitaciones de la evaluación

- **Tamaño y no determinismo.** 11 preguntas y dos corridas: cada pregunta pesa 9 puntos
  porcentuales del acierto, y la misma pregunta puede pasar o fallar entre corridas (q05). Una sola
  corrida no alcanza para afirmar que una pregunta "pasa"; por eso se reportan las dos y la
  re-corrida.
- **Sesgo del autor.** Las preguntas y valores esperados los escribió el mismo autor del sistema,
  conociendo los PDFs; el ajuste de prompt se hizo mirando la corrida 1 (una sola ronda).
- **Casos que los PDFs no permiten.** Los informes no tienen montos en pesos ni columnas sin unidad:
  no hay pregunta de conversión de moneda (se usa en su lugar una conversión que requiere la
  población mundial, q08) ni de unidad ambigua.
- **Criterio por valor.** Un valor correcto con un rótulo equivocado contaría como acierto si el
  número coincide (riesgo bajo con valores distintos entre sí); a la inversa, una respuesta bien
  redactada con un número fuera de tolerancia es una falla.
- **Sin autocorrección en la corrida entregada.** Ninguna pregunta de la corrida 2 necesitó
  reintentos de código, así que la evaluación no mide la calidad de la autocorrección con datos
  reales.
- La cobertura de tablas esperadas es informativa: hay cuadros equivalentes entre ediciones de la EPH
  con los mismos valores.
