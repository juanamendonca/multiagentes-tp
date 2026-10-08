# CONTEXT — estado actual del código

Última actualización: Iteración 6 (sandbox). Rama de trabajo: `feat/qa-tablas`.

## Current state

- El proyecto se instala con `requirements.txt` en `.venv` (Python 3.13) y pasa `ruff check .` y `pytest -m "not integration"` (256 tests, sin red, ~30–45 s: los 61 del sandbox lanzan ~11 subprocesos reales de ~1–2,5 s cada uno). `pytest -m integration` (14 tests): `test_sample_pdfs.py` (sin red, ~10 s), `test_pinecone.py` (Pinecone + embeddings, ~90 s) y `test_ingestion_real.py` (LLM + embeddings + Pinecone + LangSmith, ~35 s); los dos últimos usan un índice efímero propio (D-019).
- **Ingesta de punta a punta** (`agents/ingestion/`): `StateGraph` `load_pdf → extract_tables → collect_context → enrich ⇄ validate → (repair) → index` con reintentos por tabla (D-021), reparación BR-008, indexación idempotente (D-020/D-023) y trazas LangSmith `ingestion:<archivo>` con metadata final (D-022). CLI `python -m agents.ingestion <pdf>...`; interfaz `ingest_pdf` / `run_ingestion(..., on_update)`.
- **Índice de producción `mas-2026s2-tables` creado y cargado**: 33 vectores = 33 tablas de los 3 PDFs de muestra (INDEC 2T2026: 16, INDEC 2T2025: 12, CEPAL: 5), todas `metadata_quality="ok"` al primer intento; `data/tables/` tiene sus 33 `.parquet` + 33 `.json` (gitignored). Inventario en `iterations/05-ingestion-graph.md`.
- Servicios de base: `config`/`llm` (LiteLLM, caché SQLite que ahora sí funciona con salida estructurada, D-025), `table_parsing`, `pdf_extract`, `pdf_context`, `table_store`, `vectorstore`, `tracing`.
- **Sandbox de ejecución** (`services/sandbox.py`, independiente de los agentes): chequeo AST, subproceso `python -I -X utf8` con cwd temporal que solo tiene los Parquet seleccionados (`t1.parquet`…), entorno mínimo, preámbulo que carga `tables` y después bloquea `open`/sockets, timeout con muerte del árbol de procesos y salida acotada; `parse_result_line` valida el contrato §7.6 (D-026..D-028). Probado también a mano con un Parquet real de INDEC.
- Todavía no hay Analista (Iteraciones 7–8) ni UI.

## File map

```
requirements.txt          dependencias directas fijadas con == (D-002); langgraph==1.2.14, pinecone==10.0.0, psutil==7.2.2
ruff.toml / pytest.ini    lint (py311, 110 cols, E,F,I,UP,B) / tests (marker `integration`, pythonpath=.)
.env.example              variables de §7.1 sin valores
.gitignore                .env, .venv, caches, data/tables/, .langchain.db
assignment.es.md          enunciado del TP
notebooks/00_litellm_exploracion.ipynb   exploración del proxy (OQ-1); sin outputs
specs/                    spec (v0.7), plan y este directorio de artefactos
services/config.py        Settings, load_settings, ConfigError, REQUIRED_VARS
services/llm.py           get_chat_model, get_embeddings, SQLiteCache (+ D-025), enable_cache_if_configured
services/table_parsing.py flatten_header, parse_number, infer_decimal_separator, is_missing_marker,
                          clean_text, normalize_table, NormalizedTable, check_minimums
services/pdf_extract.py   load_pdf, extract_tables, LoadedPdf, PageText, TextBlock, RawTable,
                          IngestionWarning, PdfOpenError, is_page_furniture, CAPTION_RE, REASON_*
services/pdf_context.py   collect_context, TableContext
services/table_store.py   TableStore, make_table_id, doc_prefix, ensure_parquet_safe, TABLE_ID_RE
services/vectorstore.py   VectorStore (Protocol), PineconeVectorStore, ensure_index, make_pinecone_client,
                          build_embedding_text, build_metadata, decode_metadata, metadata_size, embed_texts,
                          build_vector_records, upsert_records, delete_stale, query, count_vectors, format_pages,
                          VectorRecord, Match, IndexDimensionMismatch, IndexingError, MetadataTooLarge
services/tracing.py       make_trace_setup, TraceSetup, RootMetadataTracer, make_langsmith_client, sanitize_for_trace
services/sandbox.py       check_code, build_preamble, run_in_sandbox, parse_result_line, ExecutionResult, ParsedOutput,
                          ResultItem, MissingItem, OutputContractError, summarize_stderr, ALLOWED_IMPORTS,
                          FORBIDDEN_NAMES, FORBIDDEN_ATTRIBUTES, MAX_OUTPUT_CHARS, PERMISSION_MESSAGE, ALIAS_RE
agents/__init__.py, agents/ingestion/__init__.py   paquetes (sin lógica; no importar grafos acá)
agents/ingestion/schemas.py     ColumnMeta, TableEnrichment, TableSource, TableRecord (+ IngestionWarning)
agents/ingestion/state.py       IngestionState (TypedDict), TableDraft, DraftStatus, IngestionStatus
agents/ingestion/validation.py  validate_enrichment (BR-007), repair_enrichment (BR-008), apply_unit_hints,
                                semantic_dtype, row_header_candidates, first_text_column, count_sentences, rule_tag
agents/ingestion/prompts.py     SYSTEM_PROMPT, build_enrich_messages, describe_columns
agents/ingestion/nodes.py       IngestionDeps, build_default_deps, *_node, route_after_validate, build_record,
                                NO_TABLES_MESSAGE, RECOVERABLE_LLM_ERRORS
agents/ingestion/graph.py       build_ingestion_graph, run_ingestion, ingest_pdf, IngestionResult, initial_state,
                                trace_metadata, result_from_state, NODE_NAMES, TRACE_PREFIX
agents/ingestion/__main__.py    CLI de ingesta
scripts/inspect_pdf.py    CLI de inspección de tablas extraídas (sin LLM ni red)
data/sample_pdfs/         3 PDFs reales + SOURCES.md (versionados)
data/tables/              Parquet + JSON de las tablas ingeridas (gitignored; hoy 33 tablas)
tests/fakes.py            ScriptedChatModel, FakeEmbeddings, FakeVectorStore, FakePineconeClient, FakePineconeIndex
tests/tables.py           sample_df, sample_record, DOC_HASH, OTHER_DOC_HASH, fake_settings, valid_enrichment
tests/fixtures/make_pdfs.py  generadores de PDFs de test con PyMuPDF (devuelven bytes)
tests/unit/test_config.py, test_llm.py, test_single_llm_client.py   VC-002, VC-005 (+ D-025), VC-001
tests/unit/test_table_parsing.py, test_pdf_extract.py, test_pdf_context.py   VC-010..VC-020, VC-031, VC-032
tests/unit/test_table_store.py, test_vectorstore.py   VC-004, VC-025, VC-026, VC-028, FR-029, NFR-007
tests/unit/test_ingestion_validate.py  VC-022 (BR-007) + BR-008
tests/unit/test_ingestion_graph.py     VC-023, VC-024, VC-025 (index), VC-030..VC-033, NFR-007, FR-024, stream
tests/unit/test_tracing.py             mecanismo de FR-060 con cliente LangSmith fake, saneado, trazas apagadas
tests/unit/test_sandbox.py             VC-046, VC-048, VC-060..VC-065, NFR-004 (subprocesos reales, Parquet generados)
tests/integration/test_sample_pdfs.py  VC-090 (técnica), VC-015 sobre PDFs reales (sin red)
tests/integration/test_pinecone.py     VC-003, VC-027 (vectorstore), NFR-005 reales
tests/integration/test_ingestion_real.py  VC-021, VC-027 (reingesta real), VC-080
```

Layout futuro fijado por el plan: `app.py`, `agents/analyst/`, `ui/`, `eval/`.

## Public interfaces

```python
# services/config.py / services/llm.py
REQUIRED_VARS: tuple[str, ...]; class ConfigError(Exception): missing: list[str]; invalid: dict[str, str]
@dataclass(frozen=True) class Settings: ... (vars de §7.1; tables_dir: Path, max_enrich_retries=2, langsmith_tracing=True ...)
def load_settings(env: Mapping[str, str] | None = None) -> Settings     # env=None: .env + os.environ (el proceso gana)
def get_chat_model(settings, **overrides) -> ChatOpenAI     # temperature=0
def get_embeddings(settings) -> OpenAIEmbeddings
def enable_cache_if_configured(settings, database_path=Path(".langchain.db")) -> SQLiteCache | None
```

```python
# services/table_parsing.py, services/pdf_extract.py, services/pdf_context.py  (sin cambios de firma)
def normalize_table(raw_rows, header_rows: int, default_decimal=".") -> NormalizedTable  # df, dtypes, unit_hints
class IngestionWarning(BaseModel, frozen): page: int | None; table_ref: str | None; reason: str
class PdfOpenError(Exception): reason        # str(e) == "No se pudo abrir el PDF: <reason>"
def load_pdf(file_name, pdf_bytes) -> LoadedPdf   # file_name, doc_hash, pdf_bytes, pages: list[PageText], median_font_size
def extract_tables(loaded, max_tables=40) -> tuple[list[RawTable], list[IngestionWarning]]
    # RawTable: df, dtypes, unit_hints, pages (1-based), bbox_first, bbox_last, index_on_page, header, has_header, ref
def collect_context(loaded, table, tables=()) -> TableContext   # above, below, section, text
# PageText.trace_summary() y RawTable.trace_summary(): resúmenes para LangSmith (sin texto ni datos)
```

```python
# agents/ingestion/schemas.py  (spec §7.3)
class ColumnMeta: name; description; dtype: "number"|"integer"|"percent"|"currency"|"year"|"date"|"text"; unit: str|None
class TableEnrichment: name; description; columns: list[ColumnMeta]; row_headers: list[str]
class TableRecord: table_id; doc_hash; name; description; columns; row_headers; source: TableSource(file_name, pages);
                   section; n_rows; n_cols; dtypes; metadata_quality: "ok"|"degraded"; validation_attempts; data_path

# services/table_store.py (D-017) / services/vectorstore.py (D-018, D-020)
def make_table_id(doc_hash, first_page, index_on_page) -> str     # "<hash16>-p003-t0"
class TableStore(tables_dir): save_table(record, df) -> TableRecord; load_table(id) -> DataFrame; load_record(id);
    table_exists(id); list_records(); list_table_ids(prefix=""); delete_tables(ids); delete_document(hash); delete_stale(hash, keep)
class VectorStore(Protocol): upsert(vectors); list_ids(prefix); delete(ids); query(vector, top_k) -> list[Match]; count()
def ensure_index(settings, client=None, *, index_name=None) -> PineconeVectorStore    # FR-003 / FR-004
def build_embedding_text(record) -> str; def build_metadata(record, df, embedding_text=None) -> dict
def decode_metadata(metadata) -> dict    # pages → list[int], columns → list[ColumnMeta], section "" → None
def embed_texts(embeddings, texts, *, batch_size=64, expected_dim=None) -> list[list[float]]
def upsert_records(store, items, vectors, texts=None) -> int      # IndexingError(indexed, total, cause)
def delete_stale(store, doc_hash, keep_ids) -> list[str]; def count_vectors(store, *, expected=None, timeout_s=30) -> int
```

```python
# services/tracing.py  (D-022)
def make_trace_setup(settings, *, run_name, metadata=None, tags=None, root_metadata=None, client=None) -> TraceSetup
@dataclass class TraceSetup: config: RunnableConfig (run_name, run_id, tags, metadata, callbacks); run_id: UUID;
    enabled: bool; tracer: RootMetadataTracer | None; project_name
    activate() -> contextmanager   # sin trazas: tracing_context(enabled=False)
    flush()                        # espera el envío (llamar al terminar)
class RootMetadataTracer(LangChainTracer): (root_metadata: Callable[[Mapping], Mapping]) — agrega metadata al run raíz al cerrar
def sanitize_for_trace(value) -> Any; def make_langsmith_client(settings) -> langsmith.Client (hide_inputs/outputs)
```

```python
# agents/ingestion  (D-021, D-023, D-024)
@dataclass(frozen) class TableDraft: table_id; raw: RawTable; context: TableContext|None; enrichment: TableEnrichment|None;
                                     errors: list[str]; attempts: int; status: "pending"|"valid"|"invalid"|"repaired"
class IngestionState(TypedDict): file_name, pdf_bytes, doc_hash, pages, median_font_size, tables: list[TableDraft],
    n_detected, enrich_round, llm_calls (suma), warnings (suma), records: list[TableRecord], n_indexed, deleted_ids,
    status: "running"|"ok"|"no_tables"|"pdf_error"|"index_error", message, error
@dataclass class IngestionDeps: settings; chat_model; embeddings; vector_store_factory: Callable[[], VectorStore];
                                table_store: TableStore; enrich_concurrency=4      # .vector_store() (perezoso), .max_attempts
def build_default_deps(settings, *, enrich_concurrency=4) -> IngestionDeps   # LiteLLM + ensure_index (perezoso) + TableStore
def build_ingestion_graph(deps) -> CompiledStateGraph     # nodos NODE_NAMES; .get_graph().draw_mermaid()
def run_ingestion(file_name, pdf_bytes, deps=None, *, on_update: Callable[[str, Mapping], None] | None = None) -> IngestionResult
def ingest_pdf(file_name, pdf_bytes, deps=None) -> IngestionResult      # spec §7.7
@dataclass class IngestionResult: file_name; doc_hash; status; message; records; warnings; n_detected; n_indexed;
    llm_calls; enrich_rounds; deleted_ids; run_id (run raíz LangSmith o None); elapsed_s; .ok (ok|no_tables); .n_degraded
def validate_enrichment(enrichment, df) -> list[str]     # BR-007, ≤ 1 error por regla, prefijo "[regla N]"
def repair_enrichment(last, df, *, file_name, first_page, index_on_page, context_above=(), unit_hints=None) -> TableEnrichment
def build_enrich_messages(draft, file_name, feedback=()) -> list[BaseMessage]
```

```python
# services/sandbox.py  (BR-012, BR-013, §7.6, §7.7; D-026..D-028)
def check_code(code: str) -> list[str]          # motivos de rechazo en español con "(línea N)"; [] = aceptado
def build_preamble(aliases: Mapping[str, str]) -> str   # {"t1": "t1.parquet"} → define pd, np, tables; luego bloquea
def run_in_sandbox(code, tables: Mapping[str, Path], timeout_s: int, static_check: bool = True, *,
                   covered_part_ids: Sequence[str] | None = None) -> ExecutionResult
    # tables = {"t1": store.parquet_path(id1), "t2": ...}; alias ^t[1-9]\d*$; Parquet inexistente → FileNotFoundError
class ExecutionResult(BaseModel, frozen): status: "ok"|"error"|"timeout"|"rejected"|"invalid_output"; stdout; stderr;
    returncode: int|None; duration_s; stdout_truncated; stderr_truncated; parsed: ParsedOutput|None; error: str|None;
    rejected_reasons: list[str]; pid; killed_pids      # .ok (status == "ok"), .truncated
def parse_result_line(stdout: str, covered_part_ids: Iterable[str]) -> ParsedOutput   # OutputContractError(ValueError)
class ParsedOutput: results: list[ResultItem]; missing: list[MissingItem]; conversions: list[str]
class ResultItem: part_id; label=""; value: float | list[float] | dict[str, float]; unit: str|None
class MissingItem: part_id; reason
def summarize_stderr(stderr, max_chars=300) -> str   # última línea no vacía (p. ej. "KeyError: 'x'")
```

`status`: `rejected` = no se ejecutó (AST; `pid`/`returncode` `None`); `error` = returncode ≠ 0 (`error` = `"returncode N: <última línea de stderr>"`); `timeout` (`returncode` `None`); `invalid_output` = returncode 0 pero falla §7.6 (solo si se pasó `covered_part_ids`); `ok`. `error` es el texto para el feedback del reintento (FR-037). stdout/stderr guardan los **últimos** 20 000 caracteres.

**Eventos de `stream` (`on_update(nodo, actualización)`; claves que devuelve cada nodo):**

| Nodo | Claves |
|---|---|
| `load_pdf` | `doc_hash`, `pages`, `median_font_size`, `status="running"` — o, si falla: `doc_hash`, `status="pdf_error"`, `error`, `message`, `records=[]`, `n_indexed=0` |
| `extract_tables` | `tables` (drafts `pending`), `n_detected`, `warnings` (nuevos); sin tablas además `status="no_tables"`, `message`, `records=[]`, `n_indexed=0` |
| `collect_context` | `tables` (con `context`) |
| `enrich` | `tables`, `enrich_round` (1, 2, …), `llm_calls` (llamadas de esta vuelta) |
| `validate` | `tables` (status `valid`/`invalid`, `errors`) |
| `repair` | `tables` (status `repaired`), `warnings` (uno por tabla degradada) |
| `index` | `records`, `n_indexed`, `status` (`ok`/`index_error`), `message`; si ok también `deleted_ids`, `warnings` |

Contadores para la UI: tablas detectadas = `len(update["tables"])` de `extract_tables`; intento de validación = `enrich_round`; inválidas = drafts con `status == "invalid"` en `validate`.

CLI: `.venv\Scripts\python -m agents.ingestion <pdf> [<pdf> ...] [--as-name NOMBRE] [--concurrency N]` (exit 1 si algún PDF no quedó `ok`/`no_tables`; 2 si falta config). `.venv\Scripts\python scripts/inspect_pdf.py <pdf> [--rows N] [--max-tables N] [--context]`.

### Fakes y helpers de test

- `ScriptedChatModel(responses=[...])`: `invoke` y `with_structured_output(Schema)` (también `.batch`) devuelven el siguiente elemento guionado (`BaseModel`/`dict`/`str`; una `Exception` se lanza); `calls` = prompts (texto). Con `enrich_concurrency=1` las respuestas van en el orden de las tablas.
- `FakeEmbeddings(dimension=8)`, `FakeVectorStore(fail_on_upsert_call=None, fail_on_ids=())` (se "sana" con `store.fail_on_upsert_call = None`), `FakePineconeClient`/`FakePineconeIndex`.
- `tests/tables.py`: `fake_settings(tables_dir, **overrides)` (dim 8, trazas apagadas), `valid_enrichment(df, **overrides)` (cumple BR-007), `sample_df()`, `sample_record(...)`.
- `tests/unit/test_ingestion_graph.py::Harness(tmp_path, responses, store=None, **settings)`: deps de test + `events`/`nodes` del stream + `factory_calls`.
- `tests/unit/test_tracing.py::FakeLangSmithClient` (`create_run`/`update_run`/`flush`) para probar trazas sin red.

## Conventions & invariants

- `ChatOpenAI(`/`OpenAIEmbeddings(` solo en `services/llm.py` (VC-001). Toda la config entra por `load_settings()`; nunca loguear `*_api_key`.
- Tests `[U]` sin red ni claves (las trazas se apagan con `fake_settings`, aunque el entorno tenga `LANGSMITH_TRACING=true`). Tests con PDFs reales o servicios reales llevan `@pytest.mark.integration`.
- Imports absolutos; mensajes/prompts/docs en español, identificadores en inglés. `agents/__init__.py` y `agents/ingestion/__init__.py` no importan grafos (los servicios importan `agents.ingestion.schemas`).
- pandas 3: columnas `float64`, `Int64` (años) o `str`; faltantes → `NaN`, nunca 0. Páginas 1-based; `index_on_page` 0-based.
- **Identidad (BR-010):** `table_id = make_table_id(doc_hash, pages[0], index_on_page)` == id del vector == nombre de `<id>.parquet`/`<id>.json`.
- Pinecone: namespace por defecto, un vector por tabla, coseno; metadata con `pages` lista de textos y `section` `""` (spec v0.7 §7.4); leer con `decode_metadata`.
- Ingesta: el LLM nunca asigna `table_id` ni `source`; `record.columns` siempre nombra exactamente `df.columns` (BR-007/BR-008), si no `save_table` falla. Un PDF sin tablas o inválido nunca abre Pinecone ni escribe en `TABLES_DIR`. Orden de `index`: VectorStore → archivos → embeddings → upsert → obsoletos (D-023).
- Reintentos de `enrich` por tabla, ≤ `1 + MAX_ENRICH_RETRIES` llamadas por tabla (NFR-007). Errores del LLM no recuperables se propagan (FR-054 los muestra en la UI).
- Trazas (D-022): run raíz `ingestion:<archivo>` con `run_id` propio; metadata final vía `RootMetadataTracer`; entradas/salidas saneadas (sin bytes ni DataFrames). Toda dataclass grande que entre al estado de un grafo debería tener `trace_summary()`.
- Tests contra servicios reales: índice efímero `<PINECONE_INDEX_NAME>-itest-<6 hex>` borrado en `finally`, `TABLES_DIR` temporal (D-019); nunca escriben en producción. Las trazas de los tests sí van al proyecto real.
- Encabezados (D-012), separador decimal por documento (D-008), unión multipágina (BR-001 + D-013/D-015), contexto (D-014): sin cambios.
- **Sandbox (D-026..D-028):** el código generado nunca lee archivos: usa `tables["tN"]` (alias `t1..tn` en el orden de las tablas elegidas), `pd` y `np` del preámbulo; nombres de columna exactos con `df["…"]`. El hijo corre `sys.executable -I -X utf8 <tmp>/main.py` con cwd `<tmp>/work/` (solo `tN.parquet`), entorno `PATH`+`SYSTEMROOT`+`SYSTEMDRIVE`+`TEMP`/`TMP` propios; al timeout se mata el árbol con `psutil` (en Windows el `python.exe` del venv es un lanzador). `static_check=False` solo en tests. No es una frontera de seguridad del SO (README final).

## Commands

```powershell
# instalar / actualizar dependencias
.venv\Scripts\python -m pip install -r requirements.txt
# tests unitarios (sin red) y lint
.venv\Scripts\python -m pytest -m "not integration"
.venv\Scripts\ruff check .
# tests del sandbox (sin red; lanza subprocesos reales, ~20–30 s)
.venv\Scripts\python -m pytest tests/unit/test_sandbox.py -v
# tests del grafo de ingesta (sin red)
.venv\Scripts\python -m pytest tests/unit/test_ingestion_validate.py tests/unit/test_ingestion_graph.py -v
# integración: ingesta real (LLM + Pinecone efímero + LangSmith, ~35 s; usar caché)
$env:LLM_CACHE="true"; .venv\Scripts\python -m pytest -m integration tests/integration/test_ingestion_real.py -v
# integración: PDFs reales (sin red) y Pinecone real
.venv\Scripts\python -m pytest tests/integration/test_sample_pdfs.py -v
.venv\Scripts\python -m pytest -m integration tests/integration/test_pinecone.py -v
# ingerir los PDFs de muestra en el índice de producción y data/tables/
$env:LLM_CACHE="true"; Get-ChildItem data\sample_pdfs\*.pdf | ForEach-Object { .venv\Scripts\python -m agents.ingestion $_.FullName }
# inspeccionar lo que se extrae de un PDF (sin LLM)
.venv\Scripts\python scripts/inspect_pdf.py data/sample_pdfs/indec_eph_mercado_trabajo_2t2026.pdf --context
# chequeo de secretos (debe dar vacío; repetir con --cached tras el stage) y .env sin trackear
git grep -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"
git grep --cached -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"
git ls-files .env
```

## Known gaps

- Sandbox listo pero sin consumidor: el prompt de codegen y el nodo `execute` llegan en la Iteración 8 (texto sugerido para el prompt en `iterations/06-sandbox.md`). Límites conocidos del sandbox: sin tope de memoria/CPU salvo el timeout; listas de bloqueo del AST incompletas; pyarrow lee/escribe sin pasar por `open` (solo lo frena el AST); NFR-001 solo probado en Windows + Python 3.13 (Store).
- Analista (Iteraciones 7–8), UI (9: mostrar `IngestionResult`, FR-025 con vista previa vía `TableStore.load_table`, FR-004 al arrancar con `ensure_index`), evaluación (10). `run_url` de LangSmith: hoy solo `run_id`.
- `.env` tiene `LLM_CACHE=false`; para desarrollo exportar `LLM_CACHE=true` (el proceso gana sobre `.env`). El nombre de archivo va en el prompt de `enrich`: reingerir con otro nombre no usa la caché. `IngestionResult.llm_calls` cuenta invocaciones (incluidas las servidas por la caché).
- Metadatos reales (revisados a mano, ver record 05): nombres a veces sin período (las dos EPH tienen "Principales tasas del mercado de trabajo"; el período está en la descripción, la sección y las columnas), abreviaturas ("ALC", "2T 2026"); columnas trimestrales de INDEC con mezcla de unidades por fila en el Cuadro 1.3 ("miles de personas o %"); filas de subtítulo de INDEC (`"Sexo y grupos de edad"`) aparecen en `row_headers`; CEPAL tiene notas pegadas a rótulos (`"Argentinab"`) y columnas `Country`/`Age` en inglés; tablas sin encabezado de primera columna quedan `col_0`/`col_4`.
- Sección errónea en EPH 2T2025 p004 (resumen ejecutivo): "Dentro de la población ocupada (44,5%) se destacó que:" (bloque en negrita, D-014) y una fila sin rótulo.
- NFR-008 (≤ 15 tablas, ≤ 5 min): medido 16 tablas en ~34 s; formal en la Iteración 10. NFR-001: solo probado en Python 3.13.
- `find_tables` no detecta grillas de 1 columna; los cuadros de gráficos generan avisos de tablas descartadas (esperables, la UI los lista).
- Si se mata un test de integración a mitad, puede quedar un índice `*-itest-*` en Pinecone (borrar a mano).
