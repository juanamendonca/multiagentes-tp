# Iteración 3 — Extracción desde PDF, continuación multipágina, contexto y PDFs reales

- **Fecha:** 2026-10-07 · **Rama:** `feat/qa-tablas`
- **SHA:** ver `git log -1 --format=%H -- specs/qa-tablas-pdf-spec-plan-artifacts/iterations/03-pdf-extraction.md` (D-003)
- **Commit:** `feat(ingesta): extracción de tablas con PyMuPDF, unión multipágina y contexto; PDFs de muestra`

## Criterios de éxito

Corrida completa con todo en stage, justo antes del commit (Git Bash desde la raíz; en PowerShell, mismos comandos con `\`):

| # | Criterio | Comando | Resultado |
|---|---|---|---|
| 1 | VC-010, 011, 015, 016, 017, 018, 019 (descartes con aviso), 020, 031, 032 | `.venv\Scripts\python -m pytest tests/unit/test_pdf_extract.py tests/unit/test_pdf_context.py -v` | PASS (27 passed). Por VC: `test_vc010_*` ×2, `test_vc011_*`, `test_vc015_*`, `test_vc016_*` ×3, `test_vc017_*`, `test_vc018_*` ×2 (30 % / 10 %), `test_vc019_*` ×2 (1 fila y 69 % vacía; 1 columna inyectada), `test_vc020_*` ×4, `test_vc031_*` ×4 (txt, truncado, encriptado, vacío), `test_vc032_*`; extra: D-012, D-015, decimal del documento, multipágina y otra tabla en el contexto |
| 2 | VC-090 (técnica) | `.venv\Scripts\python -m pytest tests/integration/test_sample_pdfs.py` | PASS (6 passed, ~17 s, sin red). `data/sample_pdfs/` tiene 3 PDFs con capa de texto en todas las páginas y `SOURCES.md` |
| 3 | `inspect_pdf.py` sobre cada PDF | `.venv\Scripts\python scripts/inspect_pdf.py data/sample_pdfs/<pdf>` | PASS (exit 0 en los 3) |
| 4 | Spec sin OQ-2 | `grep -c "OQ-2" specs/qa-tablas-pdf-spec.md` | PASS (0). Spec v0.5: FR-070 con archivos y URLs, §9 "Ninguna", A-4 validada, changelog |
| 5 | Suite sin red | `.venv\Scripts\python -m pytest -m "not integration"` | PASS (111 passed, 6 deselected, ~6 s) |
| 6 | Lint | `.venv\Scripts\ruff check .` | PASS ("All checks passed!") |
| 7 | Secretos | `git grep -nE "(sk-[A-Za-z0-9]{16,}\|pcsk[_]\|lsv2[_])"` y `git grep --cached -nE ...`; `git ls-files .env` | PASS (ambos vacíos, exit 1; `.env` no trackeado) |
| 8 | Artefactos | revisión | PASS: este record (comandos + inventario), `CONTEXT.md` reescrito, D-010..D-016 |

Corridas previas con FAIL (antes de la corrida final): ninguna en la checklist; los ajustes salieron de iterar con `inspect_pdf.py` sobre los PDFs reales (ver Problemas).

## PDFs de muestra (OQ-2) — qué se hizo con cada uno

| Original (raíz, sin trackear) | Qué es | Decisión |
|---|---|---|
| `mercado_trabajo_eph_2trim26433FCBC5A8.pdf` | INDEC, EPH "Mercado de trabajo", 2.º trim. 2026 (26 págs.) | Movido a `data/sample_pdfs/indec_eph_mercado_trabajo_2t2026.pdf` |
| `mercado_trabajo_eph_2trim25C42A813B2A.pdf` | INDEC, ídem, 2.º trim. 2025 (23 págs.) | Movido a `data/sample_pdfs/indec_eph_mercado_trabajo_2t2025.pdf` |
| `content.pdf` | CEPAL, Anuario Estadístico de América Latina y el Caribe 2025 (175 págs.) | Recortado a págs. 15–24 → `data/sample_pdfs/cepal_anuario_estadistico_2025_poblacion_trabajo.pdf`; texto verificado página por página; original borrado de la raíz |

Tabla multipágina: CEPAL cuadro 1.1.1, págs. 3–4 del extracto (17–18 del original), 49×7. Los informes de INDEC no tienen tablas que crucen página.

## Valores comparados a mano contra el texto del PDF

Todos coinciden: CEPAL 1.1.1 Argentina población 45 851.4 (pág. 3) → 45851.4; Uruguay 3 384.7 (pág. 4, continuación) → 3384.7; América Latina y el Caribe `667 888.6b` → 667888.6; CEPAL 1.2.1 `Argentinab` desocupación 7.2 → 7.2. EPH 2T2026 Cuadro 1.3 (pág. 8) tasa de actividad 2T2026 `48,9` → 48.9 y población total `30.158` → 30158; Cuadro 1.1 Empleo 3T2025 `45,4 (1)` → 45.4; Cuadro 3.4 (pág. 20) desocupación total `7,9` → 7.9. EPH 2T2025 Cuadro 3.2 (pág. 17) población total `29.909` → 29909 (= columna 2T2025 del Cuadro 1.3 de 2026); Cuadro 1.6 tasa de desocupación 2T2025 `7,6` → 7.6.

## Inventario de tablas lógicas (id provisorio = `RawTable.ref`; filas × columnas sin encabezado)

**`indec_eph_mercado_trabajo_2t2026.pdf`** (16 tablas; avisos: imagen grande pág. 1, 9 descartes de gráficos/portada)
p005-t0 [5] 9×6 Cuadro 1.1 Principales tasas · p007-t0 [7] 10×6 Cuadro 1.2 Grupos de PEA según presión · p008-t0 [8] 48×6 Cuadro 1.3 Resumen de principales indicadores · p009-t0 [9] 53×6 Cuadro 1.4 Composición de la tasa de empleo · p010-t0 [10] 57×6 Cuadro 1.5 Caracterización de ocupados · p012-t0 [12] 54×6 Cuadro 1.6 Composición de la desocupación · p014-t0 [14] 50×6 Cuadro 1.7 Caracterización de desocupados · p015-t0 [15] 16×6 Cuadro 2.1 Empleo por formalidad · p015-t1 [15] 10×6 Cuadro 2.2 Ocupados por formalidad y sector · p016-t0 [16] 9×6 Cuadro 2.3 Ocupados por sector y formalidad · p016-t1 [16] 8×6 Cuadro 2.4 Tasas de informalidad por categoría · p016-t2 [16] 7×6 Cuadro 2.5 Caracterización de la informalidad · p017-t0 [17] 11×8 Cuadro 3.1 Indicadores por región · p018-t0 [18] 43×8 Cuadro 3.2 Indicadores por aglomerado · p019-t0 [19] 43×7 Cuadro 3.3 Población (en miles) por aglomerado · p020-t0 [20] 43×5 Cuadro 3.4 Desocupación con CV e IC 90 %.

**`indec_eph_mercado_trabajo_2t2025.pdf`** (12 tablas; avisos: imagen grande pág. 1, 7 descartes)
p004-t0 [4] 4×6 resumen ejecutivo (tasa de empleo, ocupados ausentes…; sin epígrafe, primera columna `col_0`) · p005-t1 [5] 7×6 Cuadro 1.1 · p006-t0 [6] 10×6 Cuadro 1.2 · p008-t0 [8] 48×6 Cuadro 1.3 · p009-t0 [9] 76×6 Cuadro 1.4 · p011-t0 [11] 73×6 Cuadro 1.5 · p012-t0 [12] 58×6 Cuadro 1.6 · p014-t0 [14] 55×6 Cuadro 1.7 · p015-t0 [15] 11×8 Cuadro 2 Indicadores por región · p016-t0 [16] 43×8 Cuadro 3.1 por aglomerado · p017-t0 [17] 43×7 Cuadro 3.2 Población (en miles) · p018-t0 [18] 43×5 Cuadro 3.3 Desocupación con CV e IC.

**`cepal_anuario_estadistico_2025_poblacion_trabajo.pdf`** (5 tablas; 21 descartes de portadillas y gráficos)
p003-t0 [3,4] 49×7 1.1.1 Población: indicadores seleccionados (I) (multipágina) · p006-t0 [6] 49×7 1.1.4 Población: indicadores seleccionados (II) · p008-t0 [8] 24×5 1.2.1 Participación, ocupación y desocupación por país, 2024 · p009-t0 [9] 6×5 1.2.2 Participación por sexo y edad (América Latina) · p010-t0 [10] 10×5 1.2.5 Ocupados urbanos en baja productividad (1.ª y última columna `col_0`/`col_4`: el PDF no les pone encabezado).

## Archivos

Agregados: `services/pdf_extract.py`, `services/pdf_context.py`, `scripts/inspect_pdf.py`, `tests/fixtures/{__init__,make_pdfs}.py`, `tests/unit/test_pdf_extract.py`, `tests/unit/test_pdf_context.py`, `tests/integration/{__init__,test_sample_pdfs}.py`, `data/sample_pdfs/` (3 PDFs + `SOURCES.md`), este record.
Cambiados: `services/table_parsing.py` + `tests/unit/test_table_parsing.py` (D-011, +10 tests), spec (v0.5), `CONTEXT.md`, `DECISIONS.md` (D-010..D-016). Sin dependencias nuevas (`pymupdf` ya estaba).
Borrados: `content.pdf` de la raíz (sin trackear; reemplazado por el extracto verificado, autorizado por el usuario).

## Desvíos del plan

1. **Human gate de OQ-2:** el usuario ya había provisto los PDFs y URLs; no se propusieron candidatos ni se descargó nada.
2. **D-015:** condición extra a BR-001 (epígrafe propio) — la spec dice "Excepción: ninguna". Necesaria para no unir Cuadros 2.2 y 2.3 de INDEC. **Requiere ratificación del usuario** y, si se acepta, actualizar BR-001.
3. **Encabezados:** no se usa `table.header` (plan: "detección de header (`table.header`)"), sino la reconstrucción por palabras de D-012.
4. **`collect_context(loaded, table, tables=())`:** parámetro opcional extra para excluir texto de otras tablas (D-014), más filtros de sección.
5. **`table_parsing` extendido** (D-011) para notas al pie; `U$S` no aparece en los PDFs, no se agregó.
6. **VC-019 "1 columna"** se prueba inyectando el fragmento (`find_tables` no detecta grillas de 1 columna).

## Problemas encontrados / gotchas

- INDEC dibuja el encabezado de cada cuadro aparte de la grilla de datos y de tres formas distintas según la página; en las tablas regionales es texto suelto escalonado 14–19 pt sobre la grilla, con rayas 7 pt bajo los rótulos agrupadores.
- Todos los cuadros de INDEC comparten el mismo encabezado → BR-001 literal une cuadros consecutivos (D-015).
- Las negritas chicas de los gráficos vectoriales y los epígrafes "Cuadro N" se colaban como "sección" (D-014).
- El texto de los fixtures con fuente muy chica baja la mediana del documento y cambia qué es "título".
- PyMuPDF imprime "Consider using the pymupdf_layout package…" en `find_tables`; es inocuo.
- `find_tables` ~0,5 s/página: la extracción de los 3 PDFs tarda ~35 s en total.

## For the next iteration

- Usar `extract_tables(load_pdf(name, data), max_tables=settings.max_tables_per_pdf)` y `collect_context(loaded, t, tables)`; `table_id = f"{loaded.doc_hash[:16]}-p{t.pages[0]:03d}-t{t.index_on_page}"` (BR-010). `t.df` ya es apto para Parquet (`float64`/`Int64`/`str`).
- Reexportar `IngestionWarning` desde `agents/ingestion/schemas.py` (no duplicarlo, D-016); `PdfOpenError` → mensaje de FR-027 tal cual (`str(e)`).
- Los nombres de columna son largos (CEPAL) y con `*`/`°` (INDEC): Pinecone metadata (NFR-005) y prompts deben usarlos tal cual; medir el tamaño de `columns_json`.
- Rótulos de fila con notas pegadas en CEPAL (`"Argentinab"`) y filas de subtítulo con `NaN` en INDEC: tenerlo en cuenta en `row_headers` (BR-007 compara con `strip()`).
- Pendiente del usuario: ratificar D-015.
