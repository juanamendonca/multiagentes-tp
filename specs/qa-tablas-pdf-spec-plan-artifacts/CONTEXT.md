# CONTEXT — estado actual del código

Última actualización: Iteración 8 (Analista: generación, ejecución y autocorrección de código). Rama de trabajo: `feat/qa-tablas`.

## Current state

- El proyecto se instala con `requirements.txt` en `.venv` (Python 3.13) y pasa `ruff check .` y `pytest -m "not integration"` (304 tests, sin red, ~44 s: los 61 del sandbox lanzan ~11 subprocesos reales de ~1–2,5 s cada uno y 2 tests del grafo Analista otros 3). `pytest -m integration` (20 tests): `test_sample_pdfs.py` (sin red, ~10 s), `test_pinecone.py` (Pinecone + embeddings, ~90 s) `test_ingestion_real.py` (LLM + embeddings + Pinecone + LangSmith, ~35 s) —estos dos usan un índice efímero propio (D-019)— y `test_analyst_real.py` (LLM real + sandbox; lee el índice de **producción** en solo lectura, ~55–70 s con caché).
- **Ingesta de punta a punta** (`agents/ingestion/`): `StateGraph` `load_pdf → extract_tables → collect_context → enrich ⇄ validate → (repair) → index` con reintentos por tabla (D-021), reparación BR-008, indexación idempotente (D-020/D-023) y trazas LangSmith `ingestion:<archivo>` con metadata final (D-022). CLI `python -m agents.ingestion <pdf>...`; interfaz `ingest_pdf` / `run_ingestion(..., on_update)`.
- **Índice de producción `mas-2026s2-tables` creado y cargado**: 33 vectores = 33 tablas de los 3 PDFs de muestra (INDEC 2T2026: 16, INDEC 2T2025: 12, CEPAL: 5), todas `metadata_quality="ok"` al primer intento; `data/tables/` tiene sus 33 `.parquet` + 33 `.json` (gitignored). Inventario en `iterations/05-ingestion-graph.md`.
- Servicios de base: `config`/`llm` (LiteLLM, caché SQLite que ahora sí funciona con salida estructurada, D-025), `table_parsing`, `pdf_extract`, `pdf_context`, `table_store`, `vectorstore`, `tracing`.
- **Sandbox de ejecución** (`services/sandbox.py`, independiente de los agentes): chequeo AST, subproceso `python -I -X utf8` con cwd temporal que solo tiene los Parquet seleccionados (`t1.parquet`…), entorno mínimo, preámbulo que carga `tables` y después bloquea `open`/sockets, timeout con muerte del árbol de procesos y salida acotada; `parse_result_line` valida el contrato §7.6 (D-026..D-028). Probado también a mano con un Parquet real de INDEC.
- **Analista de punta a punta** (`agents/analyst/`): `StateGraph` `understand_query → retrieve → select_tables → generate_code → execute → decide_status → respond` con atajos `retrieve → decide_status` (sin candidatas, FR-032), `select_tables → decide_status` (ninguna parte `covered`, FR-034) y el ciclo `execute → generate_code` mientras la ejecución falle y queden intentos (FR-037, ≤ `1 + MAX_CODE_RETRIES`; al agotar, FR-038). Plan estructurado (FR-030), recuperación con 1 llamada de embeddings y dedupe por score máximo (D-029), descarte de candidatas sin Parquet (FR-042), selección con metadatos + `preview_csv` (D-030; puede combinar tablas, D-034), codegen con columnas exactas/dtypes/unidades/primera columna fila por fila/`df.head(5)` (FR-035, D-033), ejecución en el sandbox real con validación §7.6 (FR-036), feedback de error en reintentos (D-032), estado determinístico `decide_status` (BR-015) y respuesta en español con resultados, tablas + páginas, conversiones y explicación del cálculo (FR-040/041). Trazas `analyst:<60 chars>` con `status`, `selected_table_ids`, `code_attempts` y un span por cada `generate_code`/`execute` (VC-081, verificado en LangSmith real). CLI `python -m agents.analyst "<pregunta>"` (muestra también código y ejecución); interfaz `ask` / `run_analyst(..., on_update)`.
- Preguntas reales verificadas a mano (record 08): tasa de desocupación 31 aglomerados 2T2026 = 7,9 % y +0,3 p.p. vs 2T2025 (1 tabla); GBA 8,7 % (2T2025) → 8,2 % (2T2026), −0,5 p.p. (2 tablas, 2 PDFs); 1.164 miles = 1,164 millones de desocupados (conversión declarada). Todavía no hay UI.

## File map

```
requirements.txt          dependencias directas fijadas con == (D-002); langgraph==1.2.14, pinecone==10.0.0, psutil==7.2.2
ruff.toml / pytest.ini    lint (py311, 110 cols, E,F,I,UP,B) / tests (marker `integration`, pythonpath=.)
.env.example              variables de §7.1 sin valores
.gitignore                .env, .venv, caches, data/tables/, .langchain.db
assignment.es.md          enunciado del TP
notebooks/00_litellm_exploracion.ipynb   exploración del proxy (OQ-1); sin outputs
specs/                    spec (v0.8), plan y este directorio de artefactos
services/config.py        Settings, load_settings, ConfigError, REQUIRED_VARS
services/llm.py           get_chat_model, get_embeddings, SQLiteCache (+ D-025), enable_cache_if_configured
services/table_parsing.py flatten_header, parse_number, infer_decimal_separator, is_missing_marker,
                          clean_text, normalize_table, NormalizedTable, check_minimums
services/pdf_extract.py   load_pdf, extract_tables, LoadedPdf, PageText, TextBlock, RawTable,
                          IngestionWarning, PdfOpenError, is_page_furniture, CAPTION_RE, REASON_*
services/pdf_context.py   collect_context, TableContext
services/table_store.py   TableStore, make_table_id, doc_prefix, ensure_parquet_safe, TABLE_ID_RE
services/vectorstore.py   VectorStore (Protocol), PineconeVectorStore, ensure_index, open_index, make_pinecone_client,
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
agents/analyst/__init__.py      paquete (sin lógica; no importar grafos acá)
agents/analyst/schemas.py       QueryPart, QueryPlan, PartCoverage, TableSelection, Candidate, MissingPart, AnalystResult,
                                Status, Coverage, MAX_PARTS, MAX_SEARCH_QUERIES
agents/analyst/status.py        decide_status, answered_part_ids (BR-015, funciones puras)
agents/analyst/state.py         AnalystState (TypedDict) + contrato de los nodos de código (docstring)
agents/analyst/prompts.py       UNDERSTAND/SELECT/CODEGEN/RESPOND_SYSTEM_PROMPT, build_*_messages (incl. build_codegen_messages),
                                describe_candidate, describe_codegen_table, describe_feedback, fallback_answer
agents/analyst/nodes.py         AnalystDeps (+ sandbox), build_default_deps, *_node, route_after_retrieve/select/execute,
                                merge_matches, normalize_selection, build_missing, fallback_plan, extract_code,
                                table_aliases, SandboxRunner, EXHAUSTED_REASON, NO_CODE_ERROR, *_REASON
agents/analyst/graph.py         build_analyst_graph, run_analyst, ask, initial_state, trace_run_name, trace_metadata,
                                result_from_state, NODE_NAMES, TRACE_PREFIX
agents/analyst/__main__.py      CLI del Analista
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
tests/unit/test_status.py              VC-050 (tabla de verdad de decide_status)
tests/unit/test_analyst_graph.py       VC-041, 042, 044, 045, 047, 048 (grafo), 049, 052, 056, NFR-006, extract_code, traza (FakeSandbox;
                                       VC-045/047 con el sandbox real)
tests/integration/test_analyst_real.py VC-040 (`-k plan`), sin relación → cannot_answer, 1 tabla (7,9 / 0,3), VC-043, VC-051, VC-081
                                       (índice de producción, solo lectura)
```

Layout futuro fijado por el plan: `app.py`, `ui/`, `eval/`.

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
def open_index(settings, client=None, *, index_name=None) -> PineconeVectorStore | None   # solo lectura (Analista): None si no existe
@dataclass(frozen) class Match: id; score; metadata (cruda: leer con decode_metadata)
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

```python
# agents/analyst  (spec §7.5, §7.7; D-029..D-031)
Status = Literal["answered", "partial", "cannot_answer"]
class QueryPart: id "p1".."pn" (renumerado en código); text; metrics; entities; periods; operation; search_queries (1..3, limpiadas)
class QueryPlan: parts (1..5); .part_ids; .part(id)
class PartCoverage: part_id; coverage "covered"|"missing"; table_ids=[]; missing_reason=None; related_table_note=None
class TableSelection: selected_table_ids; coverage; rationale=""; .covered_part_ids; .coverage_of(id)
class Candidate: table_id; score (máx.); name; description; columns: list[ColumnMeta]; row_headers; file_name; pages: list[int];
                 section; n_rows; n_cols; preview_csv; metadata_quality; matched_parts; matched_queries
class MissingPart: part_id; reason; related_table_note=None; table_ids=[]
class AnalystResult(BaseModel): question; status; answer; plan; candidates; selection; results: list[ResultItem]; missing: list[MissingPart];
    conversions; code; code_attempts; execution: ExecutionResult|None; warnings; llm_calls; embedding_calls; run_id; run_url (None); elapsed_s
    .selected_tables -> list[Candidate]   # en el orden de selected_table_ids = orden de los alias t1..tn
def decide_status(plan, selection, execution) -> Status          # BR-015; cualquiera puede ser None
def answered_part_ids(plan, selection, execution) -> set[str]   # A = covered ∩ results de una ejecución ok − missing del código
@dataclass class AnalystDeps: settings; chat_model; embeddings; vector_store_factory: Callable[[], VectorStore | None]; table_store;
    sandbox: SandboxRunner = run_in_sandbox      # .vector_store() (perezoso, una vez); .max_code_attempts (= 1 + MAX_CODE_RETRIES)
def build_default_deps(settings) -> AnalystDeps          # LiteLLM + open_index (perezoso, solo lectura) + TableStore
def build_analyst_graph(deps) -> CompiledStateGraph       # nodos NODE_NAMES
def run_analyst(question, deps=None, *, on_update=None) -> AnalystResult;  def ask(question, deps=None) -> AnalystResult
class AnalystState(TypedDict): question, plan, candidates, selection, code, code_attempts, execution, results, missing, conversions,
    status, answer, run_url, warnings (suma), llm_calls (suma), embedding_calls (suma)
def extract_code(text) -> str          # primer bloque ```python``` (o sin lenguaje; si no hay, el texto)
def table_aliases(selection) -> dict[str, str]   # {"t1": selected_table_ids[0], ...}
def build_codegen_messages(question, plan, covered_part_ids, tables: [(alias, Candidate, df)], *, timeout_s, feedback=None)
def describe_feedback(attempt, max_attempts, code, execution) -> str   # D-032
def build_respond_messages(..., conversions=(), code=None)   # code solo si status != cannot_answer
```

**Contrato de los nodos de código (FR-035..FR-038):** `generate_code` devuelve `{"code": str (sin preámbulo), "code_attempts": n + 1, "llm_calls": 1}` (en reintentos el prompt lleva el código anterior y su error); `execute` devuelve `{"execution": ExecutionResult, "missing": [...]}`: `missing` vacío salvo al agotar intentos (FR-038: `MissingPart(part_id, reason="falló la ejecución tras N intentos: <error ≤ 300 chars>", table_ids)` por parte `covered`). Código vacío → `ExecutionResult(status="rejected", error=NO_CODE_ERROR)` sin lanzar el sandbox. `route_after_execute`: `generate_code` si `not execution.ok` y `code_attempts < max_code_attempts`, si no `decide_status`. `decide_status` fija `status`, `results`/`conversions` (solo partes respondidas) y reemplaza `missing` por la lista final (motivo de la selección, del código o del error). Alias: `t{i}` = `selection.selected_table_ids[i-1]` (= tablas de las partes `covered`), igual en prompt y sandbox.

**`AnalystResult` completo (lo que muestran UI y eval):** `question`, `status`, `answer`, `plan`, `candidates`, `selection` (+ `.selected_tables`: candidatas elegidas con `file_name`/`pages`, en orden de alias), `results: list[ResultItem]` (`part_id`, `label`, `value` número/lista/dict, `unit`), `missing: list[MissingPart]` (`reason`, `related_table_note`, `table_ids`), `conversions`, `code` (último programa), `code_attempts` (reintentos = `code_attempts - 1`), `execution` (`status`, `stdout`, `stderr`, `returncode`, `duration_s`, `parsed`, `error`, `*_truncated`), `warnings`, `llm_calls`, `embedding_calls`, `run_id` (run raíz LangSmith), `run_url` (`None`: la arma la UI, Iteración 9), `elapsed_s`.

**Eventos de `stream` del Analista:** `understand_query` → `plan`, `llm_calls`, `warnings`; `retrieve` → `candidates`, `warnings` (FR-042, índice inexistente), `embedding_calls`; `select_tables` → `selection`, `llm_calls`, `warnings`; `generate_code` → `code`, `code_attempts`, `llm_calls` (se repite por intento); `execute` → `execution`, `missing` (se repite por intento); `decide_status` → `status`, `results`, `conversions`, `missing`; `respond` → `answer`, `llm_calls`.

**Eventos de `stream` de la Ingesta (`on_update(nodo, actualización)`; claves que devuelve cada nodo):**

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
- `tests/unit/test_analyst_graph.py::Harness(tmp_path, responses, store=None, *, no_index=False, **settings)`: deps del Analista; `.index(record, df, with_parquet=True)` mete la tabla en el Pinecone fake (+ archivos); `ScriptedVectorStore(scripted)` devuelve `Match` guionados por consulta.

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
- **Analista (D-029..D-034):** el codegen recibe solo las tablas elegidas por alias y nunca rutas; el sandbox (inyectable en `AnalystDeps.sandbox`) valida §7.6 contra las partes `covered`; ≤ `1 + MAX_CODE_RETRIES` llamadas de codegen (NFR-006: ≤ `4 + MAX_CODE_RETRIES` de chat por pregunta). Tests `[U]` usan `FakeSandbox` salvo 2 (sandbox real, ~1–2 s por ejecución). Nunca escribe en Pinecone (`open_index`, sin crear); una sola llamada de embeddings por pregunta; el LLM no decide el estado (BR-015, `decide_status` puro); la salida del LLM se normaliza en código (ids de partes, consultas, ids de tablas que no son candidatas, partes sin evaluar); una salida que no parsea degrada (plan de una parte / todo `missing`) con aviso, otros errores del proxy se propagan. Con `cannot_answer`, `results == []` siempre.
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
# tests del Analista (sin red)
.venv\Scripts\python -m pytest tests/unit/test_status.py tests/unit/test_analyst_graph.py -v
# integración del Analista (LLM real + índice de producción en solo lectura; usar caché)
$env:LLM_CACHE="true"; .venv\Scripts\python -m pytest -m integration tests/integration/test_analyst_real.py -v
# preguntar desde la consola
$env:LLM_CACHE="true"; .venv\Scripts\python -m agents.analyst "¿Cuál fue la tasa de desocupación del total de 31 aglomerados en el 2° trimestre de 2026 y cuánto cambió respecto del 2° trimestre de 2025?"
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

- Límites conocidos del sandbox (D-026): sin tope de memoria/CPU salvo el timeout; listas de bloqueo del AST incompletas; pyarrow lee/escribe sin pasar por `open` (solo lo frena el AST); NFR-001 solo probado en Windows + Python 3.13 (Store). Cada ejecución real cuesta ~1–8 s según la carga del equipo (arranque + `import pandas`).
- Analista con datos reales (record 08): (1) la recuperación no siempre trae todas las tablas útiles (p. ej. "personas desocupadas en GBA 2T2026" no recupera `b0a700bac4db8192-p019-t0`, cuyo nombre es "Población de referencia por región"): esas preguntas terminan `cannot_answer` honesto; (2) el selector, tras D-034, a veces marca `covered` partes con un período que no está (p. ej. 2023): la segunda barrera es el código, que las reporta en `missing` con el motivo (resultado correcto, gasta 1 llamada de codegen y 1 ejecución); (3) el selector a veces agrega una tabla extra no usada por el código (aparece en "tablas usadas"). Medir en la Iteración 10.
- UI (9: FR-056 (pregunta vacía o > 1 000 caracteres) no se valida en `ask`; mostrar `IngestionResult`, FR-025 con vista previa vía `TableStore.load_table`, FR-004 al arrancar con `ensure_index`), evaluación (10). `run_url` de LangSmith: hoy solo `run_id` (`AnalystResult.run_url` y `state.run_url` quedan `None`; FR-055 → Iteración 9, p. ej. `langsmith.Client().get_run_url(run=..., project_name=...)` o la URL del proyecto + `run_id`).
- `.env` tiene `LLM_CACHE=false`; para desarrollo exportar `LLM_CACHE=true` (el proceso gana sobre `.env`). El nombre de archivo va en el prompt de `enrich`: reingerir con otro nombre no usa la caché. `IngestionResult.llm_calls` cuenta invocaciones (incluidas las servidas por la caché).
- Metadatos reales (revisados a mano, ver record 05): nombres a veces sin período (las dos EPH tienen "Principales tasas del mercado de trabajo"; el período está en la descripción, la sección y las columnas), abreviaturas ("ALC", "2T 2026"); columnas trimestrales de INDEC con mezcla de unidades por fila en el Cuadro 1.3 ("miles de personas o %"); filas de subtítulo de INDEC (`"Sexo y grupos de edad"`) aparecen en `row_headers`; CEPAL tiene notas pegadas a rótulos (`"Argentinab"`) y columnas `Country`/`Age` en inglés; tablas sin encabezado de primera columna quedan `col_0`/`col_4`.
- Sección errónea en EPH 2T2025 p004 (resumen ejecutivo): "Dentro de la población ocupada (44,5%) se destacó que:" (bloque en negrita, D-014) y una fila sin rótulo.
- NFR-008 (≤ 15 tablas, ≤ 5 min): medido 16 tablas en ~34 s; formal en la Iteración 10. NFR-001: solo probado en Python 3.13.
- `find_tables` no detecta grillas de 1 columna; los cuadros de gráficos generan avisos de tablas descartadas (esperables, la UI los lista).
- Si se mata un test de integración a mitad, puede quedar un índice `*-itest-*` en Pinecone (borrar a mano).
- Analista: con preguntas sin relación igual hay candidatas (Pinecone siempre devuelve top-k; scores ~0,3–0,46 vs ~0,7 en preguntas relacionadas): el descarte lo hace el selector (FR-034), no un umbral. Sin tope de candidatas (con 33 tablas, ≤ ~15 por pregunta). La pregunta de VC-040 (2023) termina `cannot_answer` por período: los PDFs cubren 2T2024–2T2026.
