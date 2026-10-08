# Iteración 11 (final) — Documentación, entregables y verificación final

- **Fecha:** 2026-10-08 · **Rama:** `feat/qa-tablas` (no se pusheó ni se mergeó)
- **SHA:** ver `git log -1 --format=%H -- specs/qa-tablas-pdf-spec-plan-artifacts/iterations/11-docs-final.md` (D-003)
- **Commit:** `docs: README, informe de evaluación y verificación final`
- Retoma una corrida anterior interrumpida: se conservaron `README.md`, `EVALUATION.md`, `.env.example`, la spec (v0.9, `Implemented`), `scripts/mermaid_graphs.py`, `assets/` y D-042; se revisaron contra los criterios y se completó lo que faltaba (ver Archivos).

## Criterios de éxito

PowerShell desde la raíz salvo indicación. `$env:LLM_CACHE="true"` en todo lo que llama al LLM (no se re-corrió la evaluación completa).

| # | Criterio | Comando / evidencia | Resultado |
|---|---|---|---|
| 1 | README/EVALUATION con las secciones pedidas y sin referencias internas | revisión; `git grep -nE "qa-tablas-pdf-spec\|plan-artifacts\|VC-[0-9]\|Iteration [0-9]\|Iteración [0-9]" README.md EVALUATION.md` (y `--cached`); `Test-Path docs` | PASS: README = qué es, instalación y ejecución, uso de la app, arquitectura (general + 2 grafos + tablas de nodos + aristas + observabilidad), decisiones de diseño, PDFs de muestra con URL, preguntas/evaluación, limitaciones + troubleshooting, video demo, créditos. EVALUATION = metodología, corridas, tabla generada, análisis de errores, NFRs medidos, trazas representativas, limitaciones. Greps vacíos (exit 1); `docs/` no existe. Los rótulos `NFR-0xx` dentro de la sección que genera el runner no se tocaron (D-042) |
| 2 | VC-094: Mermaid del README == `draw_mermaid()` | `.venv\Scripts\python scripts\mermaid_graphs.py --check` + comparación estricta `readme_blocks(...) == current_diagrams()` | PASS: "OK: los diagramas…" (exit 0) y `{'ingestion': True, 'analyst': True}` byte a byte. También PASS en la copia limpia |
| 3 | VC-093: 3 trazas + errores y limitaciones en EVALUATION | revisión de "Trazas representativas" | **PENDIENTE (human gate)**: las 3 trazas (ingesta `5ff8d853-…`, q04 multitabla `answered` `fe3e594b-…`, q09 `partial` `27143e71-…`) están descriptas con su link privado de LangSmith y el marcador visible `TODO(trazas): reemplazar por links públicos` (×3). Análisis de errores y limitaciones: PASS |
| 4 | VC-090: README cita la URL de cada PDF | tabla "PDFs de muestra" vs `data/sample_pdfs/SOURCES.md` | PASS: 3/3 URLs (INDEC 2T2026, INDEC 2T2025, CEPAL) |
| 5 | DECISIONS reflejadas en el README o internas | ver lista abajo | PASS |
| 6 | VC-095: copia limpia + comandos del README | ver "Copia limpia" y "Comandos del README" | PASS (con nota de tiempos en frío) |
| 7 | Link del video | README "Video demo" | **PENDIENTE (human gate)**: `TODO(video): link al video` visible |
| 8 | Secretos | `git grep -nE "(sk-[A-Za-z0-9]{16,}\|pcsk[_]\|lsv2[_])"` y `--cached` tras el stage; `git ls-files .env` | PASS: vacíos (exit 1); `.env` no trackeado |
| 9 | Regresión completa | `.venv\Scripts\ruff check .`; `.venv\Scripts\python -m pytest -m "not integration"`; `$env:LLM_CACHE="true"; .venv\Scripts\python -m pytest -m integration` | PASS: "All checks passed!"; 337 passed / 20 deselected en 53,0 s; integración **20 passed** en 164,8 s tras D-043 (primera corrida: 19 passed, 1 failed, ver Problemas). Índice temporal `*-itest-*` creado y borrado: al final solo existe `mas-2026s2-tables`, con 33 vectores |
| 10 | Artefactos | revisión | PASS: este record, `CONTEXT.md` (sistema entregado), D-042 y D-043 |

### Copia limpia (VC-095)

Un `git clone` no incluiría los cambios sin commitear de esta iteración, y el commit va al final. Se copió a `%TEMP%\tp-clean` **exactamente la lista de `git ls-files`** (121 archivos del índice, con el contenido del árbol de trabajo = lo que se commitea): sin `.git`, `.venv`, `.env`, `data/tables/`, `.langchain.db`, cachés ni `.claude/settings.local.json`. Ahí, tal cual el README: `python -m venv .venv` (CPython 3.13.14) OK; `.venv\Scripts\python -m pip install -r requirements.txt` exit 0 (~5 min); `.venv\Scripts\ruff check .` "All checks passed!"; `.venv\Scripts\python -m pytest -m "not integration"` 337 passed, **80,8 s la primera corrida** (venv recién creado: carga en frío de pandas/pyarrow/streamlit), **28,7 s** y **27,8 s** las siguientes (la última tras copiar el test de D-043); `scripts\mermaid_graphs.py --check` OK. Sin `.env` en la copia: la suite no usa claves ni red. Se considera PASS de NFR-011 (régimen normal ≤ 60 s) y el README documenta el costo de la primera corrida.

### Comandos del README (PowerShell, tal cual)

- Entorno/instalación: ver copia limpia. `Copy-Item .env.example .env` / `notepad .env`: no se ejecutaron en el repo (pisarían el `.env` real); `.env.example` revisado.
- `.venv\Scripts\python -m agents.ingestion (Get-ChildItem data\sample_pdfs\*.pdf).FullName` con `$env:LLM_CACHE="true"`: exit 0, los 3 PDFs `ok` (5 + 12 + 16 tablas, 0 degradadas), 48 s; el índice siguió con 33 vectores y `data/tables/` con 33 `.parquet` + 33 `.json` (reingesta idempotente). Un primer intento con `| Select-Object -First 30` cortó el proceso a mitad (artefacto del pipe, no del sistema) y se repitió completo.
- `.venv\Scripts\streamlit run app.py` (con `--server.headless true`): `/_stcore/health` = `ok`, la página carga con las 3 pestañas (leído en el navegador integrado); proceso detenido.
- `.venv\Scripts\python -m agents.analyst "¿Cuál fue la tasa de desocupación … 2° trimestre de 2026?"`: exit 0, `status=answered`, 7,9 %.
- `.venv\Scripts\ruff check .` y `pytest -m "not integration"`: ver criterio 9. `pytest -m integration`: ver criterio 9.
- `.venv\Scripts\python -m eval.run_eval --render eval\results\latest.json`: exit 0, "aciertos 9/11", `EVALUATION.md` sin cambios respecto del stage. `python -m eval.run_eval`: no se re-corrió completo (≈ 45 llamadas); el punto de entrada se verificó con `--ids q10` (exit 0, `cannot_answer` correcto) y se borró el `subset-*.json` generado. `--ids q05 q07` usa el mismo camino.
- `.venv\Scripts\python scripts\inspect_pdf.py data\sample_pdfs\indec_eph_mercado_trabajo_2t2026.pdf --context`: exit 0. `scripts\mermaid_graphs.py --check`: OK.

## DECISIONS: reflejadas en el README / internas

Reflejadas: D-001 (Modelos), D-002 (requirements), D-004/D-025 (caché SQLite propia), D-005 (variables vacías), D-008/D-009/D-011 (normalización numérica), D-010 (PDFs), D-012 (encabezados), D-015 (excepción por epígrafe), D-017/D-018 (Parquet + JSON, metadata de Pinecone), D-019 (tests de integración aislados, troubleshooting), D-020/D-023 (ids idempotentes y orden de `index`), D-021 (reintentos por tabla, errores del proxy), D-022 (observabilidad), D-024 (validate/repair), D-026/D-027/D-028 (sandbox), D-029 (una llamada de embeddings, sin umbral), D-030/D-031 (estado determinístico, normalización de la salida del LLM), D-032/D-033 (codegen y feedback), D-034 (combinar tablas), D-035..D-038 (UI), D-039..D-041 (EVALUATION.md), D-042 (`mermaid_graphs.py`), D-043 (no cambia comportamiento; la evaluación ya explica tablas equivalentes).
**Internas** (no van al README): D-003 (SHA en records), D-006 (forma del regex de secretos), D-007 (alcance del forward-fill), D-013 (detalles de BR-001/BR-005), D-014 (filtros del contexto), D-016 (dataclasses y ubicación de `IngestionWarning`).

## Final Verification

Evidencia común: suite sin red 337 passed (repo y copia limpia) y `pytest -m integration` 20 passed en esta iteración, más el record de cada iteración.

| Requisito | VC | It. | Estado | Evidencia |
|---|---|---|---|---|
| FR-001 | VC-001 | 1 | PASS | `test_single_llm_client.py` |
| FR-002 | VC-002 | 1, 9 | PASS | `test_config.py`, `test_ui_errors.py` (AppTest sin `PINECONE_API_KEY`) |
| FR-003 | VC-003 | 4 | PASS | `test_pinecone.py::test_vc003…` (índice efímero real) |
| FR-004 | VC-004 | 4 | PASS | `test_vectorstore.py` |
| FR-005 | VC-005 | 1 | PASS | `test_llm.py` |
| FR-010, FR-011 | VC-010, VC-011 | 3 | PASS | `test_pdf_extract.py` |
| FR-012 / BR-002 | VC-012 | 2 | PASS | `test_table_parsing.py` |
| FR-013 / BR-003 / BR-004 | VC-013, VC-014 | 2 | PASS | `test_table_parsing.py` |
| FR-014 / BR-001 | VC-015, VC-016 | 3 | PASS | `test_pdf_extract.py`; `test_sample_pdfs.py` (CEPAL 1.1.1 en 2 págs.; cuadros 2.2/2.3 no unidos) |
| FR-015 | VC-017 | 3 | PASS | `test_pdf_extract.py` |
| FR-016 | VC-018 | 3 | PASS | `test_pdf_extract.py` |
| FR-017 / BR-005 | VC-019 | 2, 3 | PASS | `test_table_parsing.py`, `test_pdf_extract.py` |
| FR-018 / BR-006 | VC-020 | 3 | PASS | `test_pdf_context.py`; `test_sample_pdfs.py::test_context…` |
| FR-019 | VC-021 | 5 | PASS | `test_ingestion_real.py::test_vc021…` |
| FR-020 / BR-007 | VC-022 | 5 | PASS | `test_ingestion_validate.py` |
| FR-021 | VC-023 | 5 | PASS | `test_ingestion_graph.py` |
| FR-022 / BR-008 / BR-011 | VC-024 | 5 | PASS | `test_ingestion_graph.py` |
| FR-023 / BR-009 | VC-025 | 4, 5 | PASS | `test_vectorstore.py`, `test_ingestion_graph.py` |
| FR-024 / BR-010 | VC-027, VC-028 | 4, 5 | PASS | `test_vectorstore.py`; `test_ingestion_real.py::test_vc027…`, `test_pinecone.py::test_vc027…`; reingesta de producción por CLI: 33 → 33 |
| FR-025 | VC-029 | 9 | PASS | manual + `screenshots/vc029-detalle-tabla.jpg` (record 09) |
| FR-026 | VC-030 | 5 | PASS | `test_ingestion_graph.py` |
| FR-027 | VC-031 | 3, 5 | PASS | `test_pdf_extract.py`, `test_ingestion_graph.py` |
| FR-028 | VC-032 | 3, 5 | PASS | idem |
| FR-029 | VC-033 | 5 | PASS | `test_ingestion_graph.py` |
| FR-030 | VC-040 | 7 | PASS | `test_analyst_real.py::test_vc040…` |
| FR-031 | VC-041 | 7 | PASS | `test_analyst_graph.py` |
| FR-032 | VC-042 | 7 | PASS | `test_analyst_graph.py` |
| FR-033 | VC-043 | 8 | PASS | `test_analyst_real.py::test_vc043…` |
| FR-034 | VC-044 | 7 | PASS | `test_analyst_graph.py` |
| FR-035 / BR-013 | VC-045 | 8 | PASS | `test_analyst_graph.py` |
| FR-036 | VC-046 | 6 | PASS | `test_sandbox.py` |
| FR-037 | VC-047, VC-048 | 6, 8 | PASS | `test_sandbox.py`, `test_analyst_graph.py` |
| FR-038 / BR-011 | VC-049 | 8 | PASS | `test_analyst_graph.py` |
| FR-039 / BR-015 | VC-050 | 7 | PASS | `test_status.py` |
| FR-040 / BR-016 | VC-051 | 8 | PASS | `test_analyst_real.py::test_vc051…` |
| FR-041 | VC-052 | 7 | PASS | `test_analyst_graph.py` |
| FR-042 | VC-056 | 7 | PASS | `test_analyst_graph.py` |
| BR-014 | VC-053, VC-054, VC-055 | 10 | PASS | filas q06/q07, q08, q09 en `EVALUATION.md` (resultado reportado tal cual: q07 falla en la corrida 2; VC-054 con dato externo en lugar de pesos → USD, D-041) |
| BR-012 | VC-060..VC-065 | 6 | PASS | `test_sandbox.py` |
| FR-050 | VC-070 | 9 | PASS | manual + `vc070-*` |
| FR-051 | VC-071 | 9 | PASS | manual + `vc071-*` |
| FR-052 | VC-072 | 9 | PASS | manual + `vc072-*` |
| FR-053 | VC-073 | 9 | PASS | manual + `vc073-*`; AppTest en `test_ui_errors.py` |
| FR-054 | VC-074 | 9 | PASS | `test_ui_errors.py` |
| FR-055 | VC-075 | 9 | PASS | manual + `vc073-vc075-salida-y-traza.jpg` |
| FR-056 | VC-076 | 9 | PASS | `test_ui_errors.py` |
| FR-060 | VC-080 | 5 | PASS | `test_ingestion_real.py::test_vc080…` |
| FR-061 | VC-081 | 8 | PASS | `test_analyst_real.py::test_vc081…` |
| FR-070 | VC-090 | 3, 11 | PASS | `test_sample_pdfs.py::test_vc090…`; URLs en README |
| FR-071 | VC-091 | 10 | PASS | `test_questions_yaml.py` |
| FR-072 | VC-092 | 10 | PASS | `eval/results/latest.json` + tabla de `EVALUATION.md` (`--render` reproduce el archivo) |
| FR-073 | VC-093 | 11 | **PENDIENTE (human gate)** | 3 trazas descriptas con links privados; faltan los links públicos (`TODO(trazas)` ×3) |
| FR-074 | VC-094 | 11 | PASS (diagramas) / **PENDIENTE (human gate)** (video) | `mermaid_graphs.py --check` OK; `TODO(video)` en README |
| NFR-001..003, 010, 011 | VC-095 | 1, 11 | PASS | copia limpia: venv + install + ruff + 337 tests (28,7 s en régimen; 80,8 s la primera corrida en frío) + greps de secretos vacíos. NFR-001: solo Python 3.13 |
| NFR-004 | VC-063 | 6 | PASS | `test_sandbox.py::test_vc063…` (2,1 s, hijos muertos) |
| NFR-005 | VC-026 | 4 | PASS | `test_vectorstore.py`; `test_pinecone.py::test_nfr005…` ×3 |
| NFR-006..009, 012 | VC-092, VC-096 | 8, 10 | PASS | tests con fakes; corrida 2: ≤ 4 chat y 1 embeddings por pregunta, mediana 15,2 s / máx. 19,9 s, ingesta ≤ 34 s por PDF, 81,8 % ≥ 75 % |

**Resumen:** 57 filas → 55 PASS, 0 FAIL, 2 PENDIENTE (human gate: FR-073 links públicos; FR-074 video, cuyos diagramas sí pasan).
**Artifact check:** PASS (`CONTEXT.md` describe el sistema entregado, sin stubs; `DECISIONS.md` D-001..D-043; records 01–11). **Documentation check:** PASS (criterio 1).

## Archivos

Conservados de la corrida interrumpida (revisados): `README.md`, `EVALUATION.md`, `.env.example`, `specs/qa-tablas-pdf-spec.md` (v0.9, `Implemented`, changelog), `scripts/mermaid_graphs.py`, `assets/{explorar-tablas,ingesta-resumen,respuesta-answered}.jpg`, D-042. Cambiados en esta corrida: `README.md` (tiempos reales de la suite, captura `ingesta-resumen.jpg` que estaba sin usar), `tests/integration/test_analyst_real.py` (D-043), `CONTEXT.md`, `DECISIONS.md` (D-043), este record.

## Problemas encontrados

- `test_single_table_question_is_answered_with_the_pdf_value` falló en la primera corrida de integración: el selector eligió el resumen de tasas de la pág. 5 (mismos valores) y el test exigía el Cuadro 1.3. Regresión de test no detectada en la Iteración 10 (solo se re-corrió `-k plan` tras D-040). Corregido en D-043; segunda corrida 20/20.
- `| Select-Object -First N` en PowerShell corta el proceso nativo del pipeline: no usarlo con la CLI de ingesta.
- Primera corrida de la suite en un venv recién creado: 80,8 s (> 60 s); las siguientes ~28 s. Documentado en el README.

## Pendientes del usuario

1. **Trazas públicas:** compartir en LangSmith los runs `5ff8d853-72a8-43bd-a8eb-c96115929217`, `fe3e594b-53e3-4aef-aff1-cce7bc88439f`, `27143e71-94b8-4cb3-ae89-0ab8b5f21d3e` y reemplazar los 3 `TODO(trazas): reemplazar por links públicos` de `EVALUATION.md` (sección "Trazas representativas").
2. **Video demo (≤ 5 min):** ingesta, exploración y ≥ 3 preguntas; reemplazar `TODO(video): link al video` en `README.md` (sección "Video demo").
3. **Entrega:** decidir merge de `feat/qa-tablas` a `main`, push al remoto y SHA a entregar (`git rev-parse HEAD`). Opcional: probar a mano la subida por arrastrar y soltar y abrir un link de traza con sesión.
