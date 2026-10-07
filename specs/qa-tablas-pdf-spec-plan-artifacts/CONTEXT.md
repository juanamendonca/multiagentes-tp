# CONTEXT — estado actual del código

Última actualización: Iteración 4 (almacén Parquet/JSON y Pinecone). Rama de trabajo: `feat/qa-tablas`.

## Current state

- El proyecto se instala con `requirements.txt` en el venv `.venv` (Python 3.13) y pasa `ruff check .` y `pytest -m "not integration"` (147 tests, sin red, ~13 s). `pytest -m integration` tiene `test_sample_pdfs.py` (6 tests, sin red, ~20 s) y `test_pinecone.py` (5 tests, Pinecone + embeddings reales, ~90 s, índice efímero propio — D-019).
- `services/config.py` y `services/llm.py`: configuración validada (FR-002 lógico) y clientes LiteLLM con caché SQLite opcional (FR-001, FR-005). Modelos (D-001): `gpt-5.4-mini` / `text-embedding-3-small` (1536).
- `services/table_parsing.py` (BR-002..BR-005, D-011), `services/pdf_extract.py` (`load_pdf`, `extract_tables`: encabezados D-012, unión multipágina BR-001 con la excepción de epígrafe propio —spec v0.6—, FR-015/016/028) y `services/pdf_context.py` (`collect_context`, BR-006, D-014): de bytes de un PDF a tablas lógicas normalizadas + avisos + contexto.
- `agents/ingestion/schemas.py`: modelos de §7.3. `services/table_store.py`: almacén Parquet + JSON por tabla (D-017). `services/vectorstore.py`: `ensure_index` (FR-003/FR-004), texto de embedding (BR-009), metadata ≤ 35 KB (NFR-005), embeddings en lotes de 64 (NFR-007), upsert tabla por tabla con conteo de avance (FR-029), borrado de obsoletos por prefijo (BR-010), conteo con espera; todo sobre el `Protocol VectorStore` (D-018, D-020). Probado contra Pinecone real (VC-003, VC-027 parte vectorstore).
- `data/sample_pdfs/`: 3 PDFs reales (2 EPH de INDEC + extracto de CEPAL) con `SOURCES.md` (D-010). El índice de producción `mas-2026s2-tables` **todavía no existe** (la cuenta tiene 0 índices): lo crea `ensure_index` en la primera ingesta (Iteración 5). Todavía no hay grafos LangGraph, sandbox ni UI.

## File map

```
requirements.txt          dependencias directas fijadas con == (D-002); pinecone==10.0.0
ruff.toml / pytest.ini    lint (py311, 110 cols, E,F,I,UP,B) / tests (marker `integration`, pythonpath=.)
.env.example              variables de §7.1 sin valores
.gitignore                .env, .venv, caches, data/tables/, .langchain.db
assignment.es.md          enunciado del TP
notebooks/00_litellm_exploracion.ipynb   exploración del proxy (OQ-1); sin outputs
specs/                    spec (v0.6), plan y este directorio de artefactos
services/config.py        Settings, load_settings, ConfigError, REQUIRED_VARS
services/llm.py           get_chat_model, get_embeddings, SQLiteCache, enable_cache_if_configured
services/table_parsing.py flatten_header, parse_number, infer_decimal_separator, is_missing_marker,
                          clean_text, normalize_table, NormalizedTable, check_minimums
services/pdf_extract.py   load_pdf, extract_tables, LoadedPdf, PageText, TextBlock, RawTable,
                          IngestionWarning, PdfOpenError, is_page_furniture, CAPTION_RE, REASON_*
services/pdf_context.py   collect_context, TableContext
services/table_store.py   TableStore, make_table_id, doc_prefix, ensure_parquet_safe, TABLE_ID_RE
services/vectorstore.py   VectorStore (Protocol), PineconeVectorStore, ensure_index, make_pinecone_client,
                          build_embedding_text, build_metadata, decode_metadata, metadata_size, embed_texts,
                          build_vector_records, upsert_records, delete_stale, query, count_vectors,
                          VectorRecord, Match, IndexDimensionMismatch, IndexingError, MetadataTooLarge
agents/__init__.py, agents/ingestion/__init__.py   paquetes (sin lógica)
agents/ingestion/schemas.py  ColumnMeta, TableEnrichment, TableSource, TableRecord (+ IngestionWarning reexportado)
scripts/inspect_pdf.py    CLI de inspección de tablas extraídas (sin LLM ni red)
data/sample_pdfs/         indec_eph_mercado_trabajo_2t2026.pdf, indec_eph_mercado_trabajo_2t2025.pdf,
                          cepal_anuario_estadistico_2025_poblacion_trabajo.pdf, SOURCES.md (versionados)
tests/fakes.py            ScriptedChatModel, FakeEmbeddings, FakeVectorStore, FakePineconeClient, FakePineconeIndex
tests/tables.py           sample_df(), sample_record(...), DOC_HASH, OTHER_DOC_HASH (tablas/registros de ejemplo)
tests/fixtures/make_pdfs.py  generadores de PDFs de test con PyMuPDF (devuelven bytes)
tests/unit/test_config.py, test_llm.py, test_single_llm_client.py   VC-002, VC-005, VC-001
tests/unit/test_table_parsing.py     VC-012..VC-014, VC-019 (mínimos) + notas al pie (D-011)
tests/unit/test_pdf_extract.py       VC-010, VC-011, VC-015..VC-019, VC-031, VC-032 + D-012/D-015
tests/unit/test_pdf_context.py       VC-020 (+ truncado, multipágina, texto de otra tabla)
tests/unit/test_table_store.py       VC-025 (almacén), VC-028 (local), columna mixta → str, catálogo, borrado
tests/unit/test_vectorstore.py       VC-004, VC-025 (embedding text + upsert), VC-026, VC-028, FR-029, NFR-007
tests/integration/test_sample_pdfs.py  VC-090 (técnica) y VC-015 sobre los PDFs reales (sin red)
tests/integration/test_pinecone.py   VC-003, VC-027 (vectorstore) en Pinecone real; NFR-005 sobre tablas reales
```

Layout futuro fijado por el plan: `app.py`, `agents/ingestion/{state,prompts,nodes,graph,__main__}.py`, `agents/analyst/`, `services/{sandbox,tracing}.py`, `ui/`, `eval/`. `data/tables/` (gitignored) lo crea `TableStore.save_table`.

## Public interfaces

```python
# services/config.py / services/llm.py  (sin cambios desde la Iteración 1)
REQUIRED_VARS: tuple[str, ...]; class ConfigError(Exception): missing: list[str]; invalid: dict[str, str]
@dataclass(frozen=True) class Settings: ... pinecone_index_name="mas-2026s2-tables", pinecone_cloud="aws",
    pinecone_region="us-east-1", tables_dir=Path("data/tables"), max_tables_per_pdf=40 ...   # vars de §7.1
def load_settings(env: Mapping[str, str] | None = None) -> Settings
def get_chat_model(settings: Settings, **overrides: Any) -> ChatOpenAI     # temperature=0
def get_embeddings(settings: Settings) -> OpenAIEmbeddings
def enable_cache_if_configured(settings, database_path=Path(".langchain.db")) -> SQLiteCache | None
```

```python
# services/table_parsing.py  (Decimal = Literal[",", "."]; Cell = str | None)
@dataclass class NormalizedTable: df: pd.DataFrame; dtypes: dict[str, str]; unit_hints: dict[str, str | None]
def normalize_table(raw_rows, header_rows: int, default_decimal: Decimal = ".") -> NormalizedTable
def flatten_header(rows, n_cols=None) -> list[str]; def parse_number(cell, decimal) -> float | None
def infer_decimal_separator(cells, default=".") -> Decimal; def is_missing_marker(cell) -> bool
def clean_text(cell) -> str; def check_minimums(df) -> str | None   # motivo "tabla descartada: ..."

# services/pdf_extract.py
class IngestionWarning(BaseModel, frozen): page: int | None = None; table_ref: str | None = None; reason: str
class PdfOpenError(Exception): reason: str        # str(e) == "No se pudo abrir el PDF: <reason>"
@dataclass(frozen=True) class LoadedPdf: file_name; doc_hash (sha256 hex); pdf_bytes; pages: list[PageText];
                                         median_font_size: float; n_pages (property)
@dataclass(eq=False) class RawTable: df; dtypes; unit_hints; pages: list[int] (1-based); bbox_first; bbox_last;
                                     index_on_page: int (0-based); header: list[str]; has_header: bool; ref -> "p003-t0"
def load_pdf(file_name: str, pdf_bytes: bytes) -> LoadedPdf          # FR-010, FR-027
def extract_tables(loaded: LoadedPdf, max_tables: int = 40) -> tuple[list[RawTable], list[IngestionWarning]]
# services/pdf_context.py
@dataclass(frozen=True) class TableContext: above: list[str]; below: list[str]; section: str | None; text -> str
def collect_context(loaded: LoadedPdf, table: RawTable, tables: Sequence[RawTable] = ()) -> TableContext
```

```python
# agents/ingestion/schemas.py  (spec §7.3; Pydantic)
ColumnDtype = Literal["number","integer","percent","currency","year","date","text"]
class ColumnMeta: name: str; description: str; dtype: ColumnDtype; unit: str | None = None
class TableEnrichment: name: str; description: str; columns: list[ColumnMeta]; row_headers: list[str]
class TableSource: file_name: str; pages: list[int]
class TableRecord: table_id; doc_hash; name; description; columns: list[ColumnMeta]; row_headers: list[str] = [];
                   source: TableSource; section: str | None = None; n_rows: int = 0; n_cols: int = 0;
                   dtypes: dict[str, str] = {}; metadata_quality: Literal["ok","degraded"] = "ok";
                   validation_attempts: int = 1; data_path: str = ""
    # n_rows/n_cols/dtypes/data_path los completa TableStore.save_table desde el DataFrame guardado
IngestionWarning   # mismo objeto que services.pdf_extract.IngestionWarning (D-016)

# services/table_store.py  (D-017)
def make_table_id(doc_hash: str, first_page: int, index_on_page: int) -> str  # "<hash16>-p003-t0"; ValueError si inválido
def doc_prefix(doc_hash: str) -> str                                          # "<hash16>-"
def ensure_parquet_safe(df) -> pd.DataFrame   # nombres str únicos (si no, ValueError); object → "str"; assert de tipos
class TableStore(tables_dir: str | Path):
    parquet_path(table_id) -> Path; json_path(table_id) -> Path     # ValueError si el id no cumple TABLE_ID_RE
    save_table(record: TableRecord, df) -> TableRecord   # escribe atómico <id>.parquet + <id>.json y devuelve el registro
                                                         # guardado; ValueError si [c.name for c in record.columns] != df.columns
    load_table(table_id) -> pd.DataFrame; load_record(table_id) -> TableRecord   # FileNotFoundError si falta
    table_exists(table_id) -> bool                       # existe el Parquet (FR-042)
    list_records() -> list[TableRecord]                  # *.json ordenados por id; ilegibles se saltean con log
    list_table_ids(doc_hash_prefix="") -> list[str]
    delete_tables(ids) -> list[str]; delete_document(doc_hash_or_prefix16) -> list[str]
    delete_stale(doc_hash, keep_ids) -> list[str]        # BR-010, lado local

# services/vectorstore.py  (D-018, D-020)
@dataclass VectorRecord(id: str, values: list[float], metadata: dict); @dataclass Match(id, score: float, metadata: dict)
class VectorStore(Protocol): upsert(vectors: Sequence[VectorRecord]); list_ids(prefix) -> list[str];
                             delete(ids); query(vector, top_k) -> list[Match]; count() -> int
class PineconeVectorStore(index, namespace="", name="")           # VectorStore sobre el SDK pinecone 10
def make_pinecone_client(settings) -> pinecone.Pinecone
def ensure_index(settings, client=None, *, index_name=None, ready_timeout_s=300) -> PineconeVectorStore
    # crea serverless coseno con EMBEDDING_DIM si no existe (FR-003); si existe con otra dimensión lanza
    # IndexDimensionMismatch(index_name, index_dim, expected_dim) antes de cualquier escritura (FR-004)
def build_embedding_text(record) -> str        # BR-009 (formato en D-018)
def build_metadata(record, df, embedding_text=None, max_bytes=MAX_METADATA_BYTES) -> dict   # §7.4, ≤ 35 000 B
def decode_metadata(metadata) -> dict          # columns: list[ColumnMeta], pages: list[int], n_rows/n_cols int, section|None
def metadata_size(metadata) -> int             # bytes de json.dumps(ensure_ascii=False) en UTF-8
def embed_texts(embeddings, texts, *, batch_size=64, expected_dim=None) -> list[list[float]]   # NFR-007
def build_vector_records(items: Sequence[tuple[TableRecord, DataFrame]], vectors, embedding_texts=None) -> list[VectorRecord]
def upsert_records(store, items, vectors, embedding_texts=None) -> int
    # una tabla por llamada, en orden; IndexingError(indexed, total, cause): "<k> de <n> tablas indexadas; ..." (FR-029)
def delete_stale(store, doc_hash, keep_ids) -> list[str]     # BR-010: list_ids("<hash16>-") y borra los no conservados
def query(store, vector, top_k) -> list[Match]
def count_vectors(store, *, expected=None, timeout_s=30, poll_s=2, sleep=time.sleep) -> int
METADATA_KEYS = (table_id, doc_hash, name, description, columns_json, row_headers, file_name, pages, section,
                 n_rows, n_cols, preview_csv, metadata_quality, embedding_text)
MAX_METADATA_BYTES = 35_000; EMBEDDING_BATCH_SIZE = 64; EMBEDDING_ROW_HEADERS = 50; PREVIEW_ROWS = 5
```

CLI: `.venv\Scripts\python scripts/inspect_pdf.py <pdf> [--rows N] [--max-tables N] [--context]`.

Variables de entorno: spec §7.1 (todas en `.env.example`). Esta iteración usa `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_CLOUD`/`PINECONE_REGION`, `EMBEDDING_*` y `TABLES_DIR` (`Settings.tables_dir` → `TableStore`).

### Fakes y fixtures de test

- `tests/fakes.py::ScriptedChatModel(responses=[...])`: `invoke` y `with_structured_output(Schema)` devuelven el siguiente elemento guionado (`str`/`BaseMessage`/`BaseModel`/`dict`; una `Exception` se lanza); `calls` = prompts recibidos.
- `tests/fakes.py::FakeEmbeddings(dimension=8)`: vector unitario determinístico por texto (`vector(text)`); `calls` = lotes recibidos por `embed_documents`, `query_calls`.
- `tests/fakes.py::FakeVectorStore(fail_on_upsert_call=None, fail_on_ids=())`: `VectorStore` en memoria (`vectors: id -> VectorRecord`, query por coseno, `list_ids` por prefijo); registra `upsert_calls`/`delete_calls`/`query_calls`; la llamada N-ésima a `upsert` (o una que incluya un id de `fail_on_ids`) lanza `RuntimeError` (FR-029); se "sana" con `store.fail_on_upsert_call = None`.
- `tests/fakes.py::FakePineconeClient(indexes={name: {"dimension", "metric"}})` + `FakePineconeIndex`: forma del SDK para `ensure_index` (`created` registra creaciones, `index_objects` los índices entregados) y `PineconeVectorStore`.
- `tests/tables.py`: `sample_df()` (str con faltante, float64 con NaN, Int64), `sample_record(doc_hash=DOC_HASH, first_page=3, index_on_page=0, df=None, **overrides)` coherente con el df.
- `tests/fixtures/make_pdfs.py`: funciones que devuelven bytes de PDF: `two_tables_one_page`, `multipage_repeated_header` (págs. 4–6), `continuation_without_header`, `continuation_different_columns`, `continuation_not_first_on_next_page`, `continuation_with_own_caption`, `header_as_loose_text`, `image_only_page`, `page_with_image(fraction)`, `tables_below_minimums`, `table_with_context(long_paragraph=False)`, `no_tables`, `encrypted`, `five_tables`; helpers `draw_table(page, x, y, rows, col_widths)` y `write(page, x, baseline, text)`.

## Conventions & invariants

- `ChatOpenAI(`/`OpenAIEmbeddings(` solo en `services/llm.py` (VC-001); embeddings siempre con `get_embeddings` + `embed_texts`. Toda la config entra por `load_settings()`; nunca loguear `*_api_key`.
- Tests `[U]` sin red ni claves; fixtures PDF generadas en el test. Tests con PDFs reales o servicios reales llevan `@pytest.mark.integration`.
- Imports absolutos desde la raíz; idioma: mensajes/prompts/docs en español, identificadores en inglés. `services/table_store.py` y `services/vectorstore.py` importan los modelos de `agents/ingestion/schemas.py` (el paquete `agents` no debe importar grafos en su `__init__`, para no crear ciclos).
- pandas 3: columnas de texto con dtype `str`; los DataFrames normalizados solo tienen `float64`, `Int64` (años) o `str` (listos para Parquet); faltantes/marcadores → `NaN`, nunca 0. `save_table` lo vuelve a verificar (`ensure_parquet_safe`).
- Páginas 1-based en todo lo público (`PageText.number`, `RawTable.pages`, `IngestionWarning.page`, `TableSource.pages`); `index_on_page` 0-based.
- **Invariante de identidad (BR-010):** `table_id = make_table_id(doc_hash, pages[0], index_on_page)` = `f"{doc_hash[:16]}-p{pages[0]:03d}-t{index_on_page}"` == id del vector en Pinecone == nombre base de `<id>.parquet`/`<id>.json`. Prefijo de documento: `doc_hash[:16] + "-"`.
- `TABLES_DIR` (`data/tables/`): por tabla `<id>.parquet` (pyarrow, sin índice; fuente de verdad de datos y dtypes) + `<id>.json` (`TableRecord`); escrituras atómicas (tmp + `os.replace`).
- Pinecone: namespace por defecto (`""`), un vector por tabla, métrica coseno. Metadata solo con tipos aptos para Pinecone (texto, número, booleano, lista de textos; sin `null`): `pages` como lista de textos y `section` ausente como `""`; `decode_metadata` lo revierte. ≤ 35 000 bytes (NFR-005).
- Tests contra Pinecone real: índice efímero `<PINECONE_INDEX_NAME>-itest-<6 hex>`, borrado al final aunque falle (D-019); nunca escriben en el índice de producción.
- Encabezados (D-012): los nombres de columna salen de las palabras de la franja de encabezado; niveles unidos con `" - "`; después pasan por `flatten_header`. Filas de datos totalmente vacías se descartan antes de normalizar; BR-005 se aplica a la tabla lógica.
- Separador decimal: `infer_decimal_separator` sobre todas las celdas de datos del PDF → `default_decimal` (INDEC → `,`; CEPAL → `.`).
- Unión multipágina (BR-001 v0.6 + D-013/D-015): (a)–(d) sobre las tablas detectadas; una tabla con epígrafe propio encima en su página no continúa a la anterior.
- Contexto (D-014): se ignoran bloques dentro de tablas y en el 5 % superior/inferior de la página; la sección no puede ser un epígrafe "Cuadro/Gráfico N".

## Commands

```powershell
# instalar / actualizar dependencias
.venv\Scripts\python -m pip install -r requirements.txt
# tests unitarios (sin red) y lint
.venv\Scripts\python -m pytest -m "not integration"
.venv\Scripts\ruff check .
# tests de almacén y vectorstore (sin red)
.venv\Scripts\python -m pytest tests/unit/test_table_store.py tests/unit/test_vectorstore.py -v
# integración: PDFs reales (sin red, ~20 s)
.venv\Scripts\python -m pytest tests/integration/test_sample_pdfs.py -v
# integración: Pinecone real + embeddings LiteLLM (~90 s; crea y borra un índice de test propio)
.venv\Scripts\python -m pytest -m integration tests/integration/test_pinecone.py -v
# inspeccionar lo que se extrae de un PDF
.venv\Scripts\python scripts/inspect_pdf.py data/sample_pdfs/indec_eph_mercado_trabajo_2t2026.pdf --context
# chequeo de secretos (debe dar vacío; repetir con --cached tras el stage) y .env sin trackear
git grep -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"
git grep --cached -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"
git ls-files .env
```

## Known gaps

- Sin agentes todavía: el grafo de ingesta (enrich/validate/repair/index, trazas, `ingest_pdf`, CLI) es la Iteración 5 y usa `TableStore`, `ensure_index`, `embed_texts`, `upsert_records` y `delete_stale` de esta iteración. Orden recomendado en `index` (D-020): guardar archivos → embeddings en lote → upsert → `delete_stale` (Pinecone y local).
- El índice de producción no existe aún; el mensaje de FR-004 al arrancar la UI → Iteración 9 (llamar `ensure_index` y mostrar `IndexDimensionMismatch`).
- Spec §7.4 dice `pages (list[int])`, pero Pinecone solo admite listas de textos en metadata: se guarda como lista de textos (D-018). Pendiente: ajuste editorial de la spec si el usuario lo aprueba.
- UI de FR-002 → Iteración 9. NFR-001: solo probado en Python 3.13.
- Llamadas a nota pegadas a **texto** quedan en los rótulos de fila (CEPAL: `"Argentinab"`, `"Bahamas (Las)c"`, `"América Latinad"`): el enriquecimiento y el codegen deben usar los `row_headers`/valores reales.
- Encabezados largos en CEPAL (bilingües, con unidad y año) y con `*`/`°` en INDEC. Medido: la metadata de Pinecone de cada tabla real ocupa ≤ 5,8 KB (con descripciones de columna vacías), muy por debajo de 35 KB; el texto de embedding más largo, ~1,9 K caracteres.
- En INDEC, el rótulo de la primera fila de algunos cuadros queda en el nombre de la primera columna, y las filas de subtítulo (`"Sexo y grupos de edad"`) quedan como filas con `NaN`.
- `find_tables` no detecta grillas de 1 columna (el descarte por "< 2 columnas" de BR-005 solo se prueba inyectando un fragmento).
- Los cuadros de gráficos generan "tablas" que BR-005 descarta con aviso (CEPAL 21, INDEC 7 y 9) más el aviso de imagen grande de la portada; esperables, la UI los listará.
- NFR-008 (≤ 15 tablas por PDF): el informe EPH 2T2026 tiene 16 tablas lógicas y el de 2T2025, 12; se mide en la Iteración 10.
