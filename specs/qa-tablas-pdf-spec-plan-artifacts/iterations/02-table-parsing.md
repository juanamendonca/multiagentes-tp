# Iteración 2 — Normalización de tablas (funciones puras)

- **Fecha:** 2026-10-07
- **Rama:** `feat/qa-tablas`
- **SHA:** ver `git log -1 --format=%H -- specs/qa-tablas-pdf-spec-plan-artifacts/iterations/02-table-parsing.md` (D-003)
- **Commit:** `feat(ingesta): normalización de encabezados, números y celdas vacías`

## Criterios de éxito

Corrida completa desde el principio justo antes del commit (Git Bash desde la raíz; los mismos comandos con `\` en PowerShell):

| # | Criterio | Comando | Resultado |
|---|---|---|---|
| 1 | VC-012, VC-013, VC-014, VC-019 (mínimos) | `.venv\Scripts\python -m pytest tests/unit/test_table_parsing.py -v` | PASS (51 passed). Un test por ejemplo: VC-012 ×3 (`test_vc012_two_row_header_with_merged_cell`, `..._two_empty_header_names_become_col_i`, `..._duplicate_names_get_suffix`) + `test_vc012_normalized_table_has_single_row_of_unique_names` (FR-012); VC-013 ×8 (`test_vc013_es_ar_thousands_and_decimal_comma`, `..._en_us_thousands_and_decimal_point`, `..._parentheses_are_negative`, `..._percent_sets_unit_hint`, `..._us_dollar_amount`, `..._unicode_minus`, `..._column_with_70_percent_numeric_stays_text`, `..._year_column_is_integer`); VC-014 ×4 (`test_vc014_empty_string_is_nan`, `..._dash_is_nan`, `..._s_d_is_nan`, `..._ellipsis_is_nan`); VC-019 ×4 (`test_vc019_one_data_row_is_discarded`, `..._one_column_is_discarded`, `..._seventy_percent_empty_is_discarded`, `..._complete_2x2_table_is_kept`) |
| 2 | Casos borde del plan | idem | PASS: `test_none_cell_is_missing`, `test_all_empty_column_is_text_of_nan`, `test_zero_comma_five`, `test_lone_1234_in_es_ar_column_is_thousands` (→ 1234), `test_lone_1234_in_en_us_column_is_decimal` (→ 1.234), `test_lone_1234_without_signal_uses_default_decimal`, `test_negative_with_ascii_and_unicode_minus` |
| 3 | Suite sin red | `.venv\Scripts\python -m pytest -m "not integration"` | PASS (74 passed: 23 previos + 51 nuevos) |
| 4 | Lint | `.venv\Scripts\ruff check .` | PASS ("All checks passed!") |
| 5 | Secretos | `git grep -nE "(sk-[A-Za-z0-9]{16,}\|pcsk[_]\|lsv2[_])"` y, tras el stage, `git grep --cached -nE ...` | PASS (ambos vacíos, exit 1) |
| 6 | Artefactos | revisión | PASS: este record; `CONTEXT.md` lista las funciones públicas de `table_parsing` y `NormalizedTable`; la regla del separador por defecto está en D-008 |

Todos los criterios pasaron en la primera corrida completa; no hubo FAIL que reintentar.

## Archivos

Agregados: `services/table_parsing.py`, `tests/unit/test_table_parsing.py`, `specs/qa-tablas-pdf-spec-plan-artifacts/iterations/02-table-parsing.md`.
Cambiados: `specs/qa-tablas-pdf-spec-plan-artifacts/CONTEXT.md` (reescrito), `specs/qa-tablas-pdf-spec-plan-artifacts/DECISIONS.md` (D-007..D-009).
Borrados: ninguno. Sin dependencias nuevas. El PDF sin trackear de la raíz no se tocó ni se commiteó.

## Decisiones

- D-007: forward-fill de encabezados solo en filas no hoja y sin cruzar grupos de filas superiores.
- D-008: inferencia del separador decimal (señales es-AR de la spec + señales unívocas extra; señales en-US simétricas; `default_decimal="."` para columnas sin señal; la columna gana sobre el default).
- D-009: dtypes resultantes (`float64`; `Int64` solo años; `str`; columna toda vacía → `str`), `dtypes` = strings de dtype de pandas, marcadores sin distinguir mayúsculas + `…`.

## Desvíos del plan

1. Firmas ampliadas con parámetros opcionales compatibles: `flatten_header(rows, n_cols=None)` (para encabezados vacíos/filas cortas), `infer_decimal_separator(cells, default=".")` y `normalize_table(raw_rows, header_rows, default_decimal=".")` (pedido por el plan como salida a la ambigüedad de `"1.234"`). Los parámetros aceptan `Sequence` en vez de `list`.
2. Funciones públicas extra: `clean_text` (normalización de espacios que también va a necesitar la Iteración 3 para comparar encabezados en BR-001) y constantes de umbrales.
3. Señales de decimal adicionales a las dos de BR-003 (D-008): con `default_decimal="."` el resultado coincide con la spec en todos los casos de la spec; solo resuelven valores que la regla literal haría fallar (p. ej. `1.234.567`).

## Problemas encontrados / gotchas

- pandas 3: `pd.Series([...], dtype="str")` convierte `None` en `NaN` y `str(dtype)` es `"str"`; el round-trip a Parquet (`pyarrow` 25) preserva `str`, `float64` e `Int64` (test `test_columns_are_numeric_or_text_and_roundtrip_parquet`).
- `"−"` (U+2212) se normaliza a `-` antes de parsear; `"–"`/`"—"` solos son faltantes (BR-004), no signos.
- Un `"-"` solo es faltante, pero `"-7"` es negativo: `is_missing_marker` compara la celda entera.
- Grupos de miles se validan de a 3 dígitos: `"1.23"` con decimal `,` no es número (cuenta en contra del 80 %).
- `ruff format` reescribe docstrings que empiezan con comillas (`""""x"`→`""" "x"`); se formatearon los dos archivos nuevos (el formato no es criterio del plan).

## For the next iteration

- Usar `normalize_table(table.extract(), header_rows=..., default_decimal=...)` sobre cada tabla de `find_tables()`; `extract()` devuelve `list[list[str | None]]` (con `None` en celdas combinadas), que es exactamente la entrada esperada. `header_rows`: cantidad de filas iniciales que son encabezado (con `table.header.external` el encabezado está fuera de `extract()`: anteponer `table.header.names` como fila o pasar `header_rows=0` y renombrar).
- Señal decimal del documento: `infer_decimal_separator(<todas las celdas de todas las tablas del PDF>)` → pasarla como `default_decimal` (los PDFs de INDEC son es-AR).
- BR-005: llamar `check_minimums(normalized.df)` **después** de la unión multipágina (BR-001) y envolver el motivo en `IngestionWarning(page, table_ref, reason)`.
- Para BR-001 (d) "≥ 50 % de celdas numéricas" se puede reutilizar `parse_number(cell, decimal) is not None`, y para comparar encabezados normalizados `clean_text(...).casefold()`.
- Revisar con los PDFs reales si aparece `U$S` u otros símbolos/notas al pie pegadas a números (`12,3(a)`), que hoy no parsean (ver *Known gaps*).
- **PDF de muestra del usuario:** `mercado_trabajo_eph_2trim26433FCBC5A8.pdf` (INDEC, EPH "Mercado de trabajo", 2.4 MB) está sin trackear en la raíz. El usuario confirmó que es uno de los PDFs de muestra: moverlo a `data/sample_pdfs/` en la Iteración 3 (lo aportó el usuario, no requiere aprobación de descarga), registrar su fuente en `SOURCES.md`, y proponer el/los demás PDFs (OQ-2, eso sí con aprobación).
