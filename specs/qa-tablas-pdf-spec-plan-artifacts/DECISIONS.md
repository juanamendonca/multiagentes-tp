# Decisiones

Registro append-only. Formato: Context / Decision / Rejected / Consequences.

## D-001: Modelos de chat y embeddings  (Iteration 1)
**Context**: OQ-1. Se corrió `notebooks/00_litellm_exploracion.ipynb` contra el proxy (2026-10-07). `/models` lista 30 modelos; `/model/info` da 403 (sin costos). 21 modelos de chat responden. Los 6 candidatos (`gpt-4.1-mini`, `gpt-4.1`, `gpt-5.4-mini`, `gpt-5.4`, `claude-haiku-4-5`, `claude-sonnet-4-5`) pasan todo: `json_schema`, `function_calling`, tool calling, codegen (valor 22.5 correcto) y `temperature=0`. Latencias (json_schema / function_calling / codegen, s): gpt-5.4-mini 1.5/1.3/1.2; gpt-4.1 1.9/3.0/1.9; claude-haiku-4-5 3.2/2.5/1.7; gpt-5.4 3.1/3.2/2.5; gpt-4.1-mini 3.4/2.3/2.9; claude-sonnet-4-5 3.9/4.1/3.5. Embeddings: `text-embedding-3-small` 1536 (coseno relacionada 0.784 / no relacionada 0.245), `-3-large` 3072 (0.707/0.268), `ada-002` 1536 (0.91/0.751, separa peor). Ninguno necesita `check_embedding_ctx_length=False`.
**Decision**: `LLM_MODEL=gpt-5.4-mini` con `temperature=0` (pasa todo; desempate por menor latencia en las tres pruebas; tier "mini" de costo bajo). `EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIM=1536` (mejor separación, mitad de tamaño que `-large`). `OpenAIEmbeddings` con el default `check_embedding_ctx_length=True`.
**Rejected**: `gpt-4.1-mini` (sugerencia por orden del notebook; pasa todo pero ~2× más lento en esta corrida; queda como respaldo). Modelos grandes/pro (más caros, sin necesidad). `claude-opus-4-7/4-8/5` (no aceptan `temperature=0`). `text-embedding-3-large` (3072 dims, sin mejora visible), `ada-002` (separa peor).
**Consequences**: el índice de Pinecone se crea con dimensión 1536. Latencias de una sola corrida (ruidosas); si `gpt-5.4-mini` falla en salida estructurada con prompts reales, cambiar a `gpt-4.1-mini` solo requiere editar `.env`. Valores en spec §7.1 (v0.4) y como comentario en `.env.example`.

## D-002: Dependencias en `requirements.txt` y config de herramientas en `ruff.toml` / `pytest.ini`  (Iteration 1)
**Context**: el usuario pidió `requirements.txt`; el plan fija versiones con `==` y no usar `pyproject.toml`.
**Decision**: un único `requirements.txt` con las dependencias directas (runtime + `pytest` + `ruff`) fijadas con `==` a lo instalado en `.venv` (Python 3.13). `numpy` se fija explícitamente porque el código generado lo puede importar (allowlist BR-012). `ruff.toml`: py311, line-length 110, reglas `E,F,I,UP,B`, excluye `notebooks/`. `pytest.ini`: `testpaths=tests`, `pythonpath=.`, `--strict-markers`, marker `integration`.
**Rejected**: `pyproject.toml` / uv / lockfile completo (`pip freeze` con transitivas): ambiguo con el gestor pedido y frágil entre plataformas. `jupyter`/`requests` no se incluyen: solo los usa el notebook de exploración.
**Consequences**: las transitivas no están fijadas (posible drift menor). Dependencias nuevas de iteraciones futuras (p. ej. `psutil` en la 6) se agregan a `requirements.txt` con `==`. NFR-001 (3.11/3.12) no se probó localmente: solo hay 3.13.

## D-003: Cómo se referencia el SHA del commit en los records  (Iteration 1)
**Context**: el plan permite completar el SHA con un commit de seguimiento o referenciarlo por `git log`.
**Decision**: cada record dice "SHA: ver `git log -1 --format=%H -- specs/qa-tablas-pdf-spec-plan-artifacts/iterations/NN-<slug>.md`". Los records son inmutables una vez commiteados.
**Rejected**: commit de seguimiento con el SHA: rompe la regla de un commit por iteración y ensucia la historia.
**Consequences**: el SHA se obtiene con un comando; el record no se edita después del commit.

## D-004: Caché SQLite propia en lugar de `langchain_community.cache.SQLiteCache`  (Iteration 1)
**Context**: FR-005 pide caché SQLite local (`.langchain.db`). `langchain-core` 1.x solo trae `InMemoryCache`; `SQLiteCache` vive en `langchain-community`, que arrastra `langchain-classic`, SQLAlchemy, aiohttp y otras ~10 dependencias.
**Decision**: `services.llm.SQLiteCache(BaseCache)` con `sqlite3` de la stdlib (tabla `llm_cache`, clave `(prompt, llm_string)`, valor serializado con `langchain_core.load.dumps/loads(allowed_objects="core")`). `enable_cache_if_configured` la instala con `set_llm_cache`.
**Rejected**: agregar `langchain-community` (dependencia pesada y en modo mantenimiento para una clase de ~40 líneas).
**Consequences**: misma semántica que la caché global de LangChain: aplica a toda generación de un chat model sin `cache` propio (probado con `invoke`; `with_structured_output` de `ChatOpenAI` debería pasar por ahí también, no verificado contra el proxy). Los fakes que reemplazan `with_structured_output` no pasan por la caché. Entradas ilegibles se tratan como miss.

## D-005: Semántica de variables obligatorias con default  (Iteration 1)
**Context**: spec §7.1 marca `PINECONE_INDEX_NAME`, `LANGSMITH_TRACING` y `LANGSMITH_PROJECT` como obligatorias pero con default; el de `LANGSMITH_PROJECT` es una plantilla (`mas-2026s2-<legajo>`).
**Decision**: una variable vacía cuenta como no definida. Obligatorias con default concreto (`PINECONE_INDEX_NAME`, `LANGSMITH_TRACING`) toman el default y nunca faltan. `LANGSMITH_PROJECT` se exige (su default no es usable). `ConfigError` lista todas las faltantes y todos los valores inválidos (enteros, booleanos) en un solo mensaje.
**Rejected**: usar literalmente `mas-2026s2-<legajo>` como proyecto.
**Consequences**: `REQUIRED_VARS` en `services/config.py` es la lista que la UI (Iteración 9) debe usar para decidir qué pestañas deshabilitar.

## D-006: Regex de secretos no autorreferente  (Iteration 1)
**Context**: el chequeo de NFR-003 con prefijos literales `pcsk` + guion bajo y `lsv2` + guion bajo coincidía con el propio texto del comando escrito en la spec y el plan (5 falsos positivos, ninguna clave real), así que nunca podía dar vacío.
**Decision**: en spec, plan y artefactos el comando se escribe `git grep -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"`: `[_]` coincide con el mismo carácter, pero el texto del comando ya no coincide consigo mismo. Con este cambio, tanto la forma original como la nueva dan vacío sobre el repo.
**Rejected**: excluir `specs/` del grep (dejaría sin revisar los artefactos); aceptar el chequeo como FAIL permanente.
**Consequences**: en archivos versionados no escribir los prefijos de claves de Pinecone/LangSmith en forma literal (usar la forma con `[_]` o describirlos). Cambio editorial en el plan (3 líneas) y en NFR-003 de la spec (changelog 0.4).

## D-007: Alcance del forward-fill en encabezados de varias filas  (Iteration 2)
**Context**: BR-002 pide propagar hacia la derecha las celdas combinadas antes de unir niveles, sin decir en qué filas ni hasta dónde. Propagar en la fila hoja convierte columnas con encabezado realmente vacío en duplicados (`X`, `X_2`), y propagar sin límites mezcla grupos (`2022` invadiendo columnas de `2023`).
**Decision**: `flatten_header` propaga solo en las filas que no son la última (niveles superiores), y una celda vacía no recibe el valor de su izquierda si en alguna fila superior empieza un grupo nuevo en esa columna. Las celdas se normalizan con `clean_text` (espacios y saltos de línea colapsados). Luego: unión con `" - "`, `col_<i>` para vacíos, deduplicación con `_2`, `_3`… (si el sufijo ya existe, se sigue incrementando).
**Rejected**: forward-fill en todas las filas (incluida la hoja); forward-fill sin respetar grupos superiores.
**Consequences**: con una sola fila de encabezado nunca se propaga (los vacíos son `col_<i>`). VC-012 pasa tal como está escrito. Si un PDF real tiene una celda combinada en la fila hoja, sale como `col_<i>`; Iteración 3 lo ve con `scripts/inspect_pdf.py`.

## D-008: Inferencia del separador decimal y `default_decimal`  (Iteration 2)
**Context**: BR-003 infiere el decimal por columna con dos señales es-AR (`\d{1,3}(\.\d{3})+,\d+`, `\d+,\d{1,2}` sin otro separador); si no, `.`. Un `"1.234"` aislado es ambiguo (plan, Iteración 2).
**Decision**: `infer_decimal_separator(cells, default=".")` mira el núcleo numérico (sin signo, unidades, paréntesis ni espacios). (1) `,` si hay señal es-AR: las dos de la spec + `\d+,\d{4,}` + `\d{1,3}(\.\d{3}){2,}`; (2) si no, `.` si hay señal en-US simétrica (`1,234.5`, `12.5`, `0.1234`, `1,234,567`); (3) si no, `default`. `normalize_table(..., default_decimal=".")` lo pasa a cada columna. El parseo exige grupos de miles de 3 dígitos (`"12.34.5"` → no es número).
**Rejected**: estado global/documental dentro de `table_parsing` (rompe la pureza); tratar `"1.234"` siempre como miles.
**Consequences**: con el default `.` el comportamiento es exactamente el de la spec. Iteración 3 puede calcular la señal del documento con `infer_decimal_separator(<todas las celdas de todas las tablas>)` y pasarla como `default_decimal` (recomendado para PDFs de INDEC, que son es-AR). La señal de la columna siempre gana sobre el default.

## D-009: Dtypes resultantes de `normalize_table`  (Iteration 2)
**Context**: el plan exige columnas numéricas (`float64`/`Int64`) o texto (`str`), nunca `object`, para Parquet; la spec no fija cuándo usar enteros ni qué hacer con columnas vacías.
**Decision**: años (todas las celdas no vacías `\d{4}` en 1900–2100) → `Int64` (nulos `<NA>`); demás numéricas (≥ 80 %) → `float64`, y las celdas no vacías que no parsean quedan `NaN`; resto → `str`. Columna toda vacía → `str` (todo `NaN`). `NormalizedTable.dtypes` = `str(df[c].dtype)` (`"float64"`, `"Int64"`, `"str"`), que es el "dtype real" de BR-007. Marcadores de BR-004 se comparan sin mayúsculas (`S/D`, `X`) y se acepta `…` (U+2026) como `...`; aplican también en columnas de texto.
**Rejected**: inferir `Int64` para cualquier columna entera (cambia dtypes entre tablas parecidas sin beneficio); `object` para columnas vacías.
**Consequences**: una columna de importes entre 1900 y 2100 se tipará como `Int64` (los valores no cambian). En columnas numéricas se pierde el texto de las celdas no numéricas (p. ej. notas); el 80 % lo acota.

## D-010: PDFs de muestra (OQ-2) y recorte del Anuario de CEPAL  (Iteration 3)
**Context**: OQ-2. El usuario proveyó 3 PDFs públicos con sus URLs (no se descargó nada): EPH "Mercado de trabajo" de INDEC 2T2026 (26 págs.) y 2T2025 (23 págs.), y el Anuario Estadístico de América Latina y el Caribe 2025 de CEPAL (175 págs.). Inspeccionados con `scripts/inspect_pdf.py`: los tres tienen capa de texto y tablas que `find_tables` detecta; los de INDEC no tienen tablas que crucen página (cada cuadro ocupa una); CEPAL sí (p. ej. cuadros 1.1.1, 1.4.1, 3.2.2).
**Decision**: se usan los tres. INDEC completos (≤ 30 págs.). CEPAL recortado a las págs. 15–24 (capítulo 1: 1.1 Población y 1.2 Trabajo): contiene la tabla multipágina 1.1.1 (págs. 3–4 del extracto) y datos por país de población y trabajo relacionados con la EPH (preguntas multitabla). Nombres: `indec_eph_mercado_trabajo_2t2026.pdf`, `indec_eph_mercado_trabajo_2t2025.pdf`, `cepal_anuario_estadistico_2025_poblacion_trabajo.pdf`. Fuentes, rango y comando de recorte en `data/sample_pdfs/SOURCES.md`; spec v0.5 (FR-070, §9, A-4).
**Rejected**: otros capítulos de CEPAL (cuentas nacionales, ambiente: menos relacionados con la EPH); `pdfplumber` como respaldo (A-4 no lo necesitó).
**Consequences**: el original de CEPAL (`content.pdf`) se borró de la raíz tras verificar que el texto de cada página del extracto es idéntico; su SHA-256 queda en `SOURCES.md`. Las páginas de CEPAL en `source.pages` son las del extracto (original = extracto + 14).

## D-011: Llamadas a nota al pie pegadas a números  (Iteration 3)
**Context**: CEPAL marca totales regionales con letras (`667 888.6b`, `-0.6c`) e INDEC con números de nota (`45,4 (1)`, `25,2(2)`); con BR-003 literal esas celdas no parsean (totales perdidos como `NaN`, y columnas enteras del Cuadro 1.1 de INDEC quedaban como texto). No hay `U$S` en los PDFs elegidos.
**Decision**: `table_parsing._decompose` quita al final de la celda, solo detrás de un dígito o `%`: 1–2 letras minúsculas, `(letras)`, `(1-2 dígitos)` o 1–3 `*`. `"(12)"` solo sigue siendo negativo; `"x"` solo sigue siendo faltante.
**Rejected**: quitar cualquier sufijo no numérico (convertiría textos en números); dejar los totales como `NaN`.
**Consequences**: la referencia a la nota se pierde de la celda (la nota queda en el contexto `below`). Una celda tipo `"12a"` pasa a ser 12. Las notas pegadas a texto (`"Argentinab"`) no se tocan.

## D-012: Encabezados reconstruidos desde las palabras de la página  (Iteration 3)
**Context**: `table.header` y las filas de encabezado de `extract()` vienen rotos en los PDFs reales: INDEC dibuja el encabezado aparte (`find_tables` lo da como una "tabla" 2×2 con textos mezclados, como una sola celda con todo, o no lo detecta y queda como texto suelto); CEPAL tiene 4 filas de encabezado con celdas combinadas.
**Decision**: filas de encabezado = la primera fila (si no es numérica) + las siguientes con la primera celda vacía y sin números que no sean años. La franja de encabezado (esas filas; más una "tabla" sin datos ≤ 15 pt encima, que se absorbe; o, si la tabla no tiene encabezado propio, texto suelto ≤ 22 pt encima con líneas a ≤ 9 pt, aceptado solo si nombra ≥ 2 columnas) se lee con `get_text("words")`: frases por línea, asignadas a la columna (límites de las celdas del cuerpo) que contiene su centro; una frase centrada sobre una raya horizontal (≤ 9 pt debajo, no de ancho completo, con subencabezados debajo y una sola frase subrayada) abarca las columnas bajo la raya. Por columna, las líneas pegadas (≤ 3 pt) se unen con espacio y los niveles con `" - "`; después `flatten_header`.
**Rejected**: `table.header.names` (mezcla títulos de cuadro); forward-fill de `flatten_header` sobre `extract()` (no distingue celdas vacías reales de combinadas); `pdfplumber`.
**Consequences**: nombres exactos como `"Total 31 aglomerados urbanos - Año 2026 - 2° trimestre*"` o `"Tasas generales - Desocupación"`. Heurística con umbrales en puntos (constantes en `pdf_extract.py`); un PDF con otro diseño puede pedir ajustarlos (`inspect_pdf.py` lo muestra).

## D-013: Detalles de BR-001 y BR-005 sobre las tablas detectadas  (Iteration 3)
**Context**: BR-001 habla de "primera/última tabla de la página" y de "≥ 50 % de celdas numéricas" sin precisar; las celdas vacías y los marcadores (`…`) son frecuentes.
**Decision**: BR-001 se evalúa sobre las tablas detectadas después de absorber fragmentos de encabezado (D-012) y antes de BR-005; `index_on_page` es la posición en esa lista (orden por y, x). (d) "fila numérica" = ≥ 50 % de las celdas con contenido (no vacías ni marcadores) parsean como número es-AR o en-US. Encabezados iguales = nombres reconstruidos iguales con `clean_text().casefold()`. Las filas de datos totalmente vacías se descartan; BR-005 se aplica a la tabla lógica unida.
**Rejected**: contar celdas vacías en el denominador (una fila de CEPAL con `...` dejaría de contar como numérica).
**Consequences**: una tabla basura al pie de una página impediría unir con la siguiente (no pasa en los PDFs elegidos).

## D-014: Contexto: bloques excluidos y sección  (Iteration 3)
**Context**: BR-006 literal tomaba como contexto el texto del encabezado de la tabla, celdas de otras tablas y pies de página, y como "sección" epígrafes de cuadros ("Cuadro 1.4 …") o rótulos de gráficos en negrita ("Ocupados no demandantes…", "de empleo").
**Decision**: `collect_context(loaded, table, tables=())` ignora bloques cuyo centro cae dentro de alguna tabla (bbox con franja de encabezado) y bloques en el 5 % superior o inferior de la página. La sección además debe empezar con mayúscula o dígito, no ser un epígrafe "Cuadro/Gráfico/Tabla/Figura N" y, si califica solo por negrita, tener fuente ≥ la mediana del documento. El truncado a 2 000 caracteres prioriza sección, después arriba[0], abajo[0], arriba[1]…
**Rejected**: aplicar BR-006 sin filtros (contexto ruidoso para el LLM).
**Consequences**: en los PDFs elegidos la sección es el título numerado del capítulo ("1. Resultados del total…", "1.2 Trabajo/Labour") y el epígrafe del cuadro queda en `above[0]`.

## D-015: Una tabla con epígrafe propio no es continuación (condición extra a BR-001)  (Iteration 3)
**Context**: en EPH 2T2026, el Cuadro 2.2 (última tabla de la pág. 15) y el Cuadro 2.3 (primera de la pág. 16) tienen las mismas 6 columnas y el encabezado estándar de INDEC: BR-001 literal los une en una sola tabla, lo que es incorrecto. La pág. 16 abre con su epígrafe "Cuadro 2.3 …".
**Decision**: si la primera tabla de la página n+1 tiene encima, en esa página (fuera de la franja de encabezado/pie), un bloque que empieza como epígrafe (`Cuadro|Tabla|Table|Gráfico|Figura|Figure|Mapa` + número, o numeración tipo `1.1.4 `), no se une. Las continuaciones reales (CEPAL 1.1.1, fixtures) no tienen epígrafe.
**Rejected**: BR-001 literal (tabla mezclada en los datos de demo); frenar la iteración (la spec no es ambigua, pero falla con datos reales).
**Consequences**: desvío de la spec (BR-001 dice "Excepción: ninguna"). **Ratificada por el usuario (2026-10-07)**: incorporada como excepción de BR-001 en la spec v0.6. Tests: `test_table_with_its_own_caption_is_not_a_continuation` y el de integración de las págs. 15/16.

## D-016: Modelos de la extracción y ubicación de `IngestionWarning`  (Iteration 3)
**Context**: el plan pone `IngestionWarning` en `agents/ingestion/schemas.py` (Iteración 4), pero la extracción ya lo necesita.
**Decision**: `IngestionWarning` (Pydantic, frozen) vive en `services/pdf_extract.py`; la Iteración 4 lo reexporta. `LoadedPdf`, `PageText`, `TextBlock`, `RawTable` y `TableContext` son dataclasses (llevan `bytes`/`DataFrame`); `RawTable` con `eq=False`. `LoadedPdf` guarda `pdf_bytes` y `extract_tables` reabre el documento. `RawTable.ref` (`p003-t0`) es el id provisorio de los avisos.
**Rejected**: Pydantic para estructuras con DataFrames; mantener abierto el `Document` de PyMuPDF en el estado del grafo (no serializable).
**Consequences**: el PDF se abre dos veces (load + extract); `find_tables` cuesta ~0,5 s por página.

## D-017: Almacén de tablas como clase `TableStore` que completa el registro  (Iteration 4)
**Context**: el plan lista funciones sueltas (`save_table(record, df)`, `load_table(table_id)`, …) pero todas dependen de `TABLES_DIR`, y `TableRecord` tiene campos (`n_rows`, `n_cols`, `dtypes`, `data_path`) que solo se conocen al escribir el DataFrame.
**Decision**: `make_table_id`/`doc_prefix` son funciones de módulo; el resto son métodos de `TableStore(tables_dir)` con los nombres del plan (+ `load_record`, `list_table_ids`, `delete_tables`, `delete_stale`). `save_table` pasa el df por `ensure_parquet_safe` (nombres `str` únicos o `ValueError`; columnas `object` → `str` con `NaN`; `assert` de tipos), exige `record.columns` == columnas del df, escribe Parquet y JSON de forma atómica (tmp + `os.replace`) y devuelve una copia del registro con `n_rows`, `n_cols`, `dtypes` (del df escrito) y `data_path` (`<TABLES_DIR>/<id>.parquet` en formato posix, informativo: se carga siempre por id). Los ids se validan con `TABLE_ID_RE` antes de armar rutas.
**Rejected**: funciones con `tables_dir` en cada llamada (más ruido en nodos y UI); confiar en los valores de `n_rows`/`dtypes` que trae el registro.
**Consequences**: la UI y el Analista leen con `TableStore(settings.tables_dir)`. Reemplazar una tabla es reescribir sus dos archivos; nunca quedan archivos a medias.

## D-018: Formato de metadata y texto de embedding en Pinecone  (Iteration 4)
**Context**: §7.4 pide `pages (list[int])` y `section` posiblemente `None`, pero la metadata de Pinecone solo admite texto, número, booleano y **listas de textos**, sin `null`. BR-009 no fija separadores. NFR-005 dice "se truncan `preview_csv` y `row_headers` en ese orden" sin detallar cómo.
**Decision**: `pages` se guarda como lista de textos (`["3","4"]`) y `section=None` como `""`; `decode_metadata` devuelve `pages: list[int]`, `section: None`, `columns: list[ColumnMeta]` (desde `columns_json`) y `n_rows`/`n_cols` `int`. Namespace por defecto. Texto BR-009: líneas `name`, `description`, `Columnas: ` + `nombre (unidad): descripción` unidas con `"; "` (sin unidad/descr. se omiten esas partes), `Filas: ` + primeros 50 rótulos con `"; "`, `Fuente: <archivo>, pág. N` / `págs. N, M`, y `Sección: …` solo si hay sección. Tope: 35 000 bytes de `json.dumps(ensure_ascii=False)` en UTF-8; truncado: `preview_csv` de 5 a 0 filas enteras, luego por caracteres; después `row_headers` desde el final; si aún no entra, la copia de `embedding_text` en metadata (el vector usa el texto completo); si no, `MetadataTooLarge`.
**Rejected**: `pages` como número (pierde la multipágina); omitir la clave `section` (rompe "claves fijas" de §7.4).
**Consequences**: desvío de tipo respecto de §7.4 (`pages`); quien lea metadata cruda (Analista, Iteración 7) debe usar `decode_metadata`. Las tablas reales ocupan ≤ 5,8 KB.

## D-019: Aislamiento de los tests de integración de Pinecone  (Iteration 4)
**Context**: VC-003 necesita un índice inexistente; VC-027 escribe vectores. No hay que ensuciar `PINECONE_INDEX_NAME`, y el plan Starter limita índices (hasta 5) y crear uno tarda.
**Decision**: `tests/integration/test_pinecone.py` crea **un** índice efímero por corrida, `<PINECONE_INDEX_NAME>-itest-<6 hex>` (fixture de módulo, verifica que no exista antes), con `ensure_index(settings, client, index_name=...)` (que espera a que esté listo), y lo borra en un `finally` aunque fallen los tests. El índice de producción no se lee ni se escribe. Los conteos se leen con `count_vectors(expected=..., timeout_s=30)`.
**Rejected**: reusar el índice de producción con un prefijo de ids de test (no permitiría probar la creación de VC-003 y deja riesgo de restos si el proceso muere); un índice de test permanente (ocupa cupo y acumula estado entre corridas).
**Consequences**: cada corrida tarda ~90 s (creación + borrado + extracción de un PDF + 2 llamadas de embeddings) y usa 1 índice temporal. Si el proceso se mata a mitad, puede quedar un índice `*-itest-*` a borrar a mano desde la consola de Pinecone.

## D-020: Upsert tabla por tabla y borrado de obsoletos por prefijo  (Iteration 4)
**Context**: FR-029 pide informar cuántas tablas quedaron indexadas si Pinecone falla a mitad; BR-010 pide borrar los ids del `doc_hash` que ya no aparecen; NFR-007 pide embeddings en lotes.
**Decision**: embeddings con `embed_texts` (≤ 64 textos por `embed_documents`); `upsert_records` hace un upsert por tabla, en orden, y ante una falla lanza `IndexingError(indexed, total, cause)` ("k de n tablas indexadas…"). `delete_stale` usa `index.list(prefix="<hash16>-")` (listado por prefijo, soportado en serverless), y borra los ids no conservados en lotes de 1000; `TableStore.delete_stale` hace lo mismo con los archivos locales. Orden recomendado para el nodo `index` (Iteración 5): guardar archivos → embeddings → upsert → `delete_stale` (Pinecone y local), así una falla nunca borra lo que estaba indexado.
**Rejected**: upsert en lote (≤ 40 tablas no lo justifica y el conteo de avance sería grueso); borrar todo el documento antes de reindexar (una falla dejaría el documento sin vectores); filtrar por metadata `doc_hash` en `delete` (no soportado igual en serverless).
**Consequences**: hasta `MAX_TABLES_PER_PDF` llamadas de upsert por PDF (aceptable). Reingerir el mismo PDF reemplaza vectores con los mismos ids, sin duplicar (probado con Pinecone real).
