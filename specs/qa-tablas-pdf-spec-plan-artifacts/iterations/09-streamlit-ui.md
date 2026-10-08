# Iteración 9 — UI Streamlit

- **Fecha:** 2026-10-07 · **Rama:** `feat/qa-tablas`
- **SHA:** ver `git log -1 --format=%H -- specs/qa-tablas-pdf-spec-plan-artifacts/iterations/09-streamlit-ui.md` (D-003)
- **Commit:** `feat(ui): app Streamlit para subir, explorar y preguntar con streaming y manejo de errores`

## Criterios de éxito

Corrida final con el código definitivo (PowerShell/Git Bash desde la raíz). Manuales: app levantada con la vista previa del navegador integrado (`.claude/launch.json` → `streamlit`, `LLM_CACHE=true`), índice de **producción**.

| # | Criterio | Comando / evidencia | Resultado |
|---|---|---|---|
| 1 | VC-002 (UI), VC-074, VC-076 | `.venv\Scripts\python -m pytest tests/unit/test_ui_errors.py -v` | PASS (19 passed, 7,8 s). VC-002: `AppTest.from_file("app.py")` sin `PINECONE_API_KEY` (y sin leer `.env`) → `st.error` que nombra la variable, sin excepción ni "Traceback", 3 pestañas, chat deshabilitado. VC-074 ×4 (openai `APIConnectionError` → "LiteLLM…", `PineconeApiException` → "Pinecone", `IndexDimensionMismatch` → "Pinecone", genérica → "Analista"): `st.error` con el servicio, no propaga, historial previo intacto. VC-076: `"   "`, `""`, 1 001 → no se invoca el Analista + aviso; 1 000 (y 1 000 con espacios) → se invoca. Extra: VC-073 con `AppTest` (2 preguntas en el historial + una vacía), redacción de claves, causa encadenada, progreso de ingesta, borrado, `check_index`, `RunUrlBuilder` |
| 2 | VC-070 | Subir PDF: CEPAL cargado en el `<input type=file>` (fetch desde un servidor local + `DataTransfer`) y botón **Ingerir**; `get_page_text` durante la corrida | PASS: label del `st.status` en vivo "Leyendo el PDF…" → "Recolectando contexto · 5 tablas…" → "Validando metadatos · 5 tablas · intento 2…" → "Indexando en Pinecone · 5 tablas · intento 1…"; líneas por nodo `load_pdf: 10 páginas leídas`, `extract_tables: 5 tablas detectadas · 21 avisos`, `collect_context`, `enrich: intento 1 · 5 llamadas al LLM`, `validate: … 4 válidas · 1 inválidas`, `enrich: intento 2 · 1 llamadas`, `validate: … 5 válidas`, `index: 5 tablas indexadas`. Capturas `vc070-*` |
| 3 | VC-029 | Resumen tras la ingesta | PASS: "5 tablas indexadas en 10 s.", contadores, "Avisos (21)" (tabla página/tabla/motivo), por tabla: nombre, descripción, fuente + páginas + sección, badge "metadatos ok", columnas con tipo/dtype/unidad/descripción, encabezados de fila, "Vista previa (5 de 49 filas)". `vc029-detalle-tabla.jpg` |
| 4 | VC-071 | Explorar tablas | PASS: "33 tablas indexadas en 3 documentos (data/tables)", agrupadas por PDF, cada una con nombre, descripción, "Fuente: <archivo>, págs. …" y "Ver tabla completa" (datos completos, p. ej. 49 filas × 7 columnas). "Contar vectores en Pinecone" → 33 total; CEPAL 5, EPH 2T2025 12. `vc071-*` |
| 5 | VC-072 | Borrar documento CEPAL → Confirmar | PASS: "Se borró cepal_…pdf: 5 vectores y 5 tablas locales (vectores del documento que quedan en Pinecone: 0)."; Explorar pasa a 28 tablas en 2 documentos; recuento del índice 28. Reingerido desde Subir PDF (ver 2) → 33. `vc072-*` |
| 6 | VC-073 | Dos preguntas seguidas en Preguntar | PASS: (a) "¿Cuál fue la tasa de desocupación del total de 31 aglomerados en el 2° trimestre de 2026 y cuánto cambió respecto del 2° trimestre de 2025?" → badge "Respondida", 7,9 % y 0,3 p.p. (coincide con record 08), "Reintentos de código: 0 · intentos: 1 · llamadas al LLM: 4 · 9 s", tabla de valores, t1/t2 con datos, código, salida (returncode 0, 2,36 s, stdout JSON), pasos; (b) "¿Cuántos goles hizo Messi en el Mundial de Qatar 2022?" → "No se puede responder", sin bloque de valores, "Datos faltantes: p1: …". Ambas en el historial. Pregunta de solo espacios → aviso FR-056, no se ejecuta. `vc073-*` |
| 7 | VC-075 | Link de la traza | PASS: `href=https://smith.langchain.com/o/<tenant>/projects/p/<proyecto>/r/e0ed7841-bb8f-4aa1-bde6-f9f960d4f476?poll=true`; `read_run` de ese id → nombre `analyst:¿Cuál fue la tasa de desocupación del total de 31 aglomerado`, raíz (`parent_run_id=None`), del proyecto `LANGSMITH_PROJECT`, metadata `status=answered`. Abrirlo en el navegador requiere sesión de LangSmith: lo hace el usuario |
| 8 | Suite sin red, lint, secretos | `pytest -m "not integration"`; `ruff check .`; `git grep -nE "(sk-[A-Za-z0-9]{16,}\|pcsk[_]\|lsv2[_])"` y `--cached`; `git ls-files .env` | PASS: 323 passed, 20 deselected, 43,2 s (corridas previas 47–51 s según la carga; ≤ 60); "All checks passed!"; greps vacíos; `.env` no trackeado |
| 9 | Artefactos | revisión | PASS: este record, `CONTEXT.md`, D-035..D-038, `screenshots/` + `screenshots/README.md` |

**Estado final de producción:** 33 vectores (`count_vectors(expected=33)` = 33; CEPAL 5 con prefijo `105ddaa685cadeb9-`), 33 `.json` + 33 `.parquet` en `data/tables/`, ningún registro con el nombre de prueba `cepal_copia_renombrada.pdf` (se reingirió con el nombre original al final).

## Archivos

Agregados: `app.py`, `ui/{__init__,setup,errors,render,upload,explore,ask}.py`, `tests/unit/test_ui_errors.py`, `.claude/launch.json`, `screenshots/` (16 JPG + `README.md`), este record. Cambiados: `services/tracing.py` (`RunUrlBuilder`), `CONTEXT.md`, `DECISIONS.md` (D-035..D-038). Sin dependencias nuevas (Streamlit 1.65 ya estaba).

## Desvíos del plan

1. **FR-004 con `open_index` y no `ensure_index`** al arrancar (D-035): `ensure_index` crearía el índice sin ingesta (FR-003).
2. **Capturas:** el navegador integrado guarda cada screenshot como JPG de 800 px; se copiaron a `screenshots/`. Varias están recortadas/escaladas por la emulación de viewport (la evidencia principal es el texto de este record). `screenshots/README.md` lista las capturas a pantalla completa que conviene que tome el usuario.
3. **Subida de archivos:** VC-070 se ejercitó por la UI real, pero el archivo se cargó en el `<input type=file>` con JavaScript (fetch a un servidor local con CORS en el scratchpad + `DataTransfer`), no arrastrándolo. El arrastre lo verifica el usuario.
4. **Ingesta extra con otro nombre** (`cepal_copia_renombrada.pdf`) para comprobar el progreso en vivo tras un ajuste (status abierto): mismos 5 `table_id`, 33 vectores; se reingirió con el nombre original para que el catálogo quede igual que antes.
5. Test de UI único `tests/unit/test_ui_errors.py` (como pide el plan) que también cubre lógica de las otras pestañas.
6. `.claude/launch.json` agrega `env.LLM_CACHE=true` y `--browser.gatherUsageStats false`.

## Problemas encontrados / gotchas

- **Explorar pesada:** con 33 tablas, ~66 `st.dataframe` ocultos hacían que la página tardara en dibujarse (screenshots con timeout). Solución: `lazy_expander` (`on_change="rerun"` + `.open`, D-038).
- `st.status.update(label=...)` sin `expanded=True` colapsa el status: las líneas por nodo no se veían durante la ingesta. Se pasa `expanded=True` en cada evento.
- El total de vectores mostrado quedaba viejo tras borrar (33): ahora se descarta y se vuelve a contar con el botón.
- `AppTest.from_function` ejecuta el cuerpo de la función como script aparte: sin anotaciones de tipos que necesiten imports (`Any` no definido).
- `load_settings()` lee `.env` con `override=False`: en el `AppTest` de VC-002 se reemplaza `services.config.load_dotenv` por un no-op para no cargar las claves reales al proceso de tests.
- `ScriptControlException` de Streamlit hereda de `BaseException`: `run_safely` atrapa `Exception` sin romper `st.rerun()`.
- `st.dataframe` muestra `NaN` como "None" (cosmético).
- Pinecone `list` y `describe_index_stats` son eventualmente consistentes; `delete_document` espera hasta 20 s a que no queden ids del documento (en la prueba: 0 al primer intento).
- La suite unitaria sigue cerca del límite (43–51 s de 60 s según la carga de la máquina; la UI suma ~3 s). No se aceleraron tests existentes.

## UX conocidos

- Cada abrir/cerrar de una tabla en Explorar o en una respuesta hace un rerun (rápido).
- En una respuesta `answered`, las tablas que el selector agregó pero el código no usó aparecen igual entre las "Tablas seleccionadas" (gotcha del record 08).
- El arranque tarda ~10 s la primera vez (imports + 1 llamada a Pinecone).

## For the next iteration

- **Iteración 10 (eval):** para la columna de trazas usar `RunUrlBuilder(settings)(result.run_id)` (`services/tracing.py`); `AnalystResult.run_url` sigue `None`. Las dos preguntas probadas en la UI (7,9 % / 0,3 p.p. → `answered`; fútbol → `cannot_answer`) siguen dando lo mismo que en el record 08.
- **Iteración 11 (docs):** comando para levantar la app `.venv\Scripts\streamlit run app.py` (con `$env:LLM_CACHE="true"` en desarrollo); capturas en `screenshots/` y pendientes del usuario en `screenshots/README.md`; mencionar en el README que la pestaña Explorar permite borrar documentos (vectores + archivos) y que la app sin `.env` completo muestra qué variables faltan.
