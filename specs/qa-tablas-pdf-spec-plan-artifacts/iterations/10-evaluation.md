# Iteración 10 — Set de evaluación y corrida

- **Fecha:** 2026-10-07 · **Rama:** `feat/qa-tablas`
- **SHA:** ver `git log -1 --format=%H -- specs/qa-tablas-pdf-spec-plan-artifacts/iterations/10-evaluation.md` (D-003)
- **Commit:** `feat(eval): set de preguntas de prueba, runner de evaluación y primera corrida`

## Criterios de éxito

Git Bash desde la raíz (en PowerShell, mismos comandos con `\` y `$env:LLM_CACHE=...`). Corridas medidas con `LLM_CACHE=false` (latencias y llamadas reales), índice de **producción** en solo lectura.

| # | Criterio | Comando | Resultado |
|---|---|---|---|
| 1 | VC-091 + runner con `ask` fake | `.venv\Scripts\python -m pytest tests/unit/test_questions_yaml.py tests/unit/test_run_eval.py -v` | PASS (14 passed, ~2 s): composición FR-071 (11 preguntas, 4 con ≥ 2 tablas, q10 `cannot_answer` sin tablas, 4 `cannot_answer`/`partial` con tabla relacionada), causas de BR-014 + `partial`, PDFs existentes; comparación (tolerancia, `abs`, listas/dicts, `cannot_answer` sin números), validación del yaml, excepción → error sin cortar, resumen NFR-006/009/012, tabla entre marcadores (resto del archivo intacto, `\|` escapado), JSON, `--render` |
| 2 | VC-092 | `LLM_CACHE=false .venv\Scripts\python -m eval.run_eval --tag run1` y, tras D-040, `--tag run2` | PASS: ambas terminan (exit 0); `EVALUATION.md` tiene una fila por pregunta (11) con estado esperado/obtenido, valor esperado/obtenido, correcto, tablas (informativo), reintentos, latencia, llamadas LLM y link de traza; más mediana/máximo de latencia y % de aciertos. La sección refleja la run 2 (= `eval/results/latest.json`) |
| 3 | VC-053, VC-054, VC-055 en la tabla | revisión de `EVALUATION.md` / `run1.json` / `run2.json` | PASS (figuran con su resultado). VC-053: q06 `cannot_answer` ✔ en ambas ("falta el período 2023"); q07 `partial` ✔ en run 1 (7,6 %; "falta … 2° trimestre de 2023"), **FAIL** en run 2 (`cannot_answer`, ver fallas). VC-054: q08 (conversión con dato externo, D-041) **FAIL** en run 1 (`partial` con la población de Argentina como parte), ✔ en run 2 (`cannot_answer`: "falta la población mundial"). VC-055: q09 `partial` ✔ en ambas (10,2 %; "falta el promedio de los países del Caribe", no lo calculó con los países listados) |
| 4 | VC-096 | `run1.json`/`run2.json` + LangSmith (`list_runs(trace_id=…, run_type="llm")`) + record 05 | PASS: llamadas de chat por pregunta ≤ 5 (run 1) y ≤ 4 (run 2) ≤ 7 = 4 + `MAX_CODE_RETRIES` (NFR-006); embeddings 1 por pregunta (≤ 3). En run 2 el conteo de `AnalystResult.llm_calls` coincide pregunta por pregunta con los runs `llm` de la traza en LangSmith (11/11). Ingesta (NFR-007/008, de la ingesta real del record 05, D-041): EPH 2T2026 26 págs./16 tablas 33,7 s, EPH 2T2025 23 págs./12 tablas 19,8 s, CEPAL 10 págs./5 tablas 11,9 s (≤ 5 min), 1 llamada de chat por tabla (≤ 1 + `MAX_ENRICH_RETRIES`), 1 de embeddings por PDF |
| 5 | NFR-012 informado | idem | PASS (informado): 9/11 = **81,8 %** en run 1 y en run 2 (≥ 75 %, alcanzado) |
| 6 | Suite sin red, lint, secretos | `.venv\Scripts\python -m pytest -m "not integration"`; `.venv\Scripts\ruff check .`; `git grep -nE "(sk-[A-Za-z0-9]{16,}\|pcsk[_]\|lsv2[_])"` y `--cached`; `git ls-files .env` | PASS: 337 passed, 20 deselected, 51,3 s (≤ 60); "All checks passed!"; greps vacíos (exit 1); `.env` no trackeado |
| 7 | Artefactos | revisión | PASS: este record, `CONTEXT.md`, D-039..D-041 |

Regresión extra por el cambio de prompt: `LLM_CACHE=true .venv\Scripts\python -m pytest -m integration tests/integration/test_analyst_real.py -k plan` PASS (VC-040 sigue dando 2 partes). Ningún criterio dio FAIL; las preguntas que fallan son resultados de la evaluación, reportados abajo.

## Set de preguntas (`eval/questions.yaml`)

Valores esperados calculados a mano con `page.get_text()` de PyMuPDF sobre los PDFs (página, cuadro, fila y columna en `notes`).

| Id | Tipo | Estado esperado | Esperado | Fuente |
|---|---|---|---|---|
| q01 | una tabla | answered | 7,9 %; ±0,3 p.p. | EPH 2T2026 p. 8, Cuadro 1.3 |
| q02 | multitabla (2 ediciones) | answered | 8,7; 8,2; ±0,5 | EPH 2T2025 p. 15/18 + EPH 2T2026 p. 17/20 |
| q03 | escala | answered | 1,164 millones | EPH 2T2026 p. 8 (1.164 miles) |
| q04 | multitabla INDEC + CEPAL | answered | 7,2; 7,6; ±0,4 | CEPAL p. 8 (1.2.1, Argentina) + EPH 2T2025 p. 8 |
| q05 | multitabla, falla de recuperación conocida | answered | 683; 656; ±27 miles | EPH 2T2025 p. 17 + EPH 2T2026 p. 19 |
| q06 | período faltante (2023) | cannot_answer | — | regionales 2T2025/2T2026 |
| q07 | período faltante + partial | partial | 7,6 % (2T2024) | EPH 2T2025 p. 8 |
| q08 | conversión con dato externo (VC-054) | cannot_answer | — | CEPAL p. 3–4 (Argentina 45 851,4 miles; sin población mundial) |
| q09 | comparación faltante + partial (VC-055) | partial | 10,2 % | CEPAL p. 8 (sin promedio del Caribe) |
| q10 | sin tabla relacionada | cannot_answer | — | — |
| q11 | escala sobre tabla multipágina | answered | 3,3847 millones | CEPAL p. 4 (continuación del 1.1.1) |

## Resultados

| | Run 1 (prompts de la It. 8) | Run 2 (tras D-040) |
|---|---|---|
| Aciertos | 9/11 = 81,8 % (estado ok 9/11) | 9/11 = 81,8 % (estado ok 10/11) |
| Fallas | q05 `cannot_answer`; q08 `partial` | q05 `answered` con valores incorrectos; q07 `cannot_answer` |
| Latencia mediana / máx. | 13,9 s / 19,0 s | 15,2 s / 19,9 s |
| Llamadas de chat máx. / total | 5 / 44 | 4 / 42 |
| Reintentos de código | 1 (q05) | 0 |

Por pregunta (run 1 → run 2, estado/valor): q01 ✔→✔, q02 ✔→✔, q03 ✔→✔, q04 ✔→✔, q05 ✘ (cannot_answer, 1 reintento)→✘ (answered 1.091/1.164/73), q06 ✔→✔, q07 ✔ (partial 7,6)→✘ (cannot_answer), q08 ✘ (partial 45 851,4)→✔, q09 ✔→✔, q10 ✔→✔, q11 ✔→✔. Re-corrida de las fallas de run 2 (`--ids q05 q07`, `eval/results/subset-20261007-230322.json`): q05 ✔ (683/656/−27, eligió p017 + p019), q07 ✘ (`cannot_answer` otra vez).

### Fallas analizadas

- **q05 (no determinística, recuperación + selección):** run 1: las consultas ("personas desocupadas en GBA…") no traen los cuadros de cantidades de 2T2026 (p019, puesto ~5–6 por score 0,55–0,65); el selector cubrió con p017 (2T2025) + p018 (solo tasas) y el código reportó todo `missing` tras 1 reintento. Run 2 (con D-040): eligió solo el Cuadro 1.3 de 2T2026 (p008, total 31 aglomerados) y el código devolvió 1.091 → 1.164 rotulados como GBA: **respuesta incorrecta sin aviso** (el selector marcó `covered` una tabla sin la entidad GBA y el codegen no lo verificó). Re-corrida: correcta. Es el caso más grave del set.
- **q07 (regresión por D-040):** run 1 separó 2T2024 y 2T2023 en dos partes → `partial` correcto. Con la regla nueva de "operandos no son partes" (y la regla 1 existente) el plan junta los dos períodos en una sola parte, que queda entera `missing` → `cannot_answer`.
- **q08 (corregida por D-040):** run 1 hizo del operando (población de Argentina) una parte propia → `partial`; run 2: una sola parte → `cannot_answer` ("falta la población mundial").
- Observaciones sin falla: "tablas esperadas" ✘ en run 1 q02 antes de listar los cuadros equivalentes (el sistema usó p018/p020, mismos valores); el selector a veces cita como tabla relacionada un cuadro distinto del esperado (q07: p012 en lugar de p008).

## Trazas candidatas para FR-073 (LangSmith, proyecto `LANGSMITH_PROJECT`)

URL: `https://smith.langchain.com/o/<tenant>/projects/p/3a42a222-5725-4e62-92b0-09000d335246/r/<run_id>?poll=true` (la arma `RunUrlBuilder`; las de las preguntas están en `EVALUATION.md`).

1. **Ingesta:** `5ff8d853-72a8-43bd-a8eb-c96115929217` — `ingestion:indec_eph_mercado_trabajo_2t2026.pdf` (record 05; raíz, metadata `n_tables=16`). Sin reintentos de `enrich` (ninguna tabla de producción los necesitó).
2. **Multitabla `answered`:** `fe3e594b-53e3-4aef-aff1-cce7bc88439f` — q04 run 2 (CEPAL + INDEC, 7,2 / 7,6 / 0,4). Alternativa: `9f9a86f6-ddb0-4ab3-a7dd-85ab85ecfd0e` (q02 run 2, dos ediciones de la EPH). **No hubo** una multitabla `answered` con reintento en ninguna corrida (el único reintento, q05 run 1 `d66a2519-679f-4d0e-adb3-c08a181c6e83`, terminó `cannot_answer`).
3. **`partial` / `cannot_answer`:** `27143e71-94b8-4cb3-ae89-0ab8b5f21d3e` — q09 run 2 (`partial`, falta el promedio del Caribe). Alternativa `cannot_answer`: `99fe74fc-f8c0-4198-9d48-7754e38db948` (q06 run 2, falta 2023).

Las 5 se leyeron con `read_run` (raíz, nombre y `status` correctos). Compartirlas públicamente es un human gate de la Iteración 11.

## Archivos

Agregados: `eval/__init__.py`, `eval/questions.yaml`, `eval/run_eval.py`, `eval/results/{latest,run1,run2,subset-20261007-230322}.json`, `EVALUATION.md` (solo la sección generada), `tests/unit/test_questions_yaml.py`, `tests/unit/test_run_eval.py`, este record. Cambiados: `agents/analyst/prompts.py` (D-040), `CONTEXT.md`, `DECISIONS.md` (D-039..D-041). Sin dependencias nuevas (`pyyaml` ya estaba).

## Desvíos del plan

1. **VC-054 sin "pesos → USD"** y sin caso de unidad ambigua: los PDFs no lo permiten (D-041); q08 usa un dato externo (población mundial).
2. **Tiempo de ingesta no re-medido** en esta iteración: se reusan los de la ingesta real del record 05 (D-041).
3. Extras del runner: `--tag` (guarda cada corrida), `--render` (reescribe la sección desde un JSON, sin LLM; se usó tras ajustar el formato de `±` y la columna informativa de tablas, que no cambian aciertos) y la columna "Tablas esperadas". `run1.json` se re-renderizó con la misma lógica (sus aciertos no cambian).
4. Los JSON de resultados incluyen la URL de la traza (contiene los ids de organización y proyecto de LangSmith, no claves).

## Problemas encontrados / gotchas

- El mismo prompt con `temperature=0` da planes/selecciones distintos entre corridas (q05): una sola corrida no alcanza para afirmar que una pregunta "pasa"; conviene reportar re-corridas.
- La granularidad del plan decide `partial` vs `cannot_answer` (q07, q08): cualquier regla de partición mejora un caso y empeora otro.
- `AnalystResult.llm_calls` cuenta también las respuestas de la caché: con `LLM_CACHE=true` las latencias y llamadas no son reales (por eso las corridas medidas son sin caché). Las llamadas de embeddings no aparecen como runs en LangSmith.

## For the next iteration

- **Iteración 11 (docs):** `EVALUATION.md` hoy solo tiene la sección generada (run 2); agregar fuera de los marcadores la metodología (D-039), las dos corridas y la re-corrida (tabla "Resultados" de arriba), el análisis de fallas (q05 respuesta incorrecta silenciosa, q07, q08), las limitaciones (no determinismo, recuperación por nombre de tabla, sin casos de pesos/unidad ambigua) y NFR-006..009/012 medidos. Las 3 trazas de FR-073 están arriba (human gate: compartirlas o usar capturas). Para regenerar la tabla sin gastar LLM: `.venv\Scripts\python -m eval.run_eval --render eval/results/latest.json`. No re-correr la evaluación completa sin necesidad (≈ 45 llamadas por corrida).
