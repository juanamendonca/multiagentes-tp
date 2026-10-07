# CONTEXT — estado actual del código

Última actualización: Iteración 3 (extracción desde PDF y PDFs de muestra). Rama de trabajo: `feat/qa-tablas`.

## Current state

- El proyecto se instala con `requirements.txt` en el venv `.venv` (Python 3.13) y pasa `ruff check .` y `pytest -m "not integration"` (111 tests, sin red, ~7 s). `pytest -m integration tests/integration/test_sample_pdfs.py` (6 tests, sin red, ~20 s) corre la extracción sobre los PDFs reales.
- `services/config.py` y `services/llm.py`: configuración validada (FR-002 lógico) y clientes LiteLLM con caché SQLite opcional (FR-001, FR-005). Modelos (D-001): `gpt-5.4-mini` / `text-embedding-3-small` (1536).
- `services/table_parsing.py`: normalización pura de tablas (BR-002..BR-005), ahora también ignora llamadas a nota pegadas a números (`667 888.6b`, `45,4 (1)`; D-011).
- `services/pdf_extract.py`: de bytes de un PDF a tablas lógicas normalizadas + avisos: `load_pdf` (hash, bloques de texto con bbox/fuente/negrita; `PdfOpenError` para no-PDF/dañado/encriptado), `extract_tables` (`find_tables`, encabezado reconstruido desde palabras y rayas — D-012, unión multipágina BR-001 + guarda de epígrafe D-015, decimal del documento, BR-005 después de unir, FR-015/016/028). `services/pdf_context.py`: `collect_context` (BR-006, D-014).
- `data/sample_pdfs/`: 3 PDFs reales (2 informes EPH de INDEC + extracto de 10 págs. del Anuario CEPAL 2025) con `SOURCES.md` (D-010). Tabla multipágina: CEPAL cuadro 1.1.1, págs. 3–4 del extracto. `scripts/inspect_pdf.py` muestra lo que se extraería de cada PDF.
- Todavía no hay almacén Parquet/JSON, Pinecone, agentes LangGraph, sandbox ni UI.

## File map

```
requirements.txt          dependencias directas fijadas con == (D-002)
ruff.toml / pytest.ini    lint (py311, 110 cols, E,F,I,UP,B) / tests (marker `integration`, pythonpath=.)
.env.example              variables de §7.1 sin valores
.gitignore                .env, .venv, caches, data/tables/, .langchain.db
assignment.es.md          enunciado del TP
notebooks/00_litellm_exploracion.ipynb   exploración del proxy (OQ-1); sin outputs
specs/                    spec (v0.5), plan y este directorio de artefactos
services/config.py        Settings, load_settings, ConfigError, REQUIRED_VARS
services/llm.py           get_chat_model, get_embeddings, SQLiteCache, enable_cache_if_configured
services/table_parsing.py flatten_header, parse_number, infer_decimal_separator, is_missing_marker,
                          clean_text, normalize_table, NormalizedTable, check_minimums
services/pdf_extract.py   load_pdf, extract_tables, LoadedPdf, PageText, TextBlock, RawTable,
                          IngestionWarning, PdfOpenError, is_page_furniture, CAPTION_RE, REASON_*
services/pdf_context.py   collect_context, TableContext
scripts/inspect_pdf.py    CLI de inspección de tablas extraídas (sin LLM ni red)
data/sample_pdfs/         indec_eph_mercado_trabajo_2t2026.pdf, indec_eph_mercado_trabajo_2t2025.pdf,
                          cepal_anuario_estadistico_2025_poblacion_trabajo.pdf, SOURCES.md (versionados)
tests/fakes.py            ScriptedChatModel (LLM fake guionado con contador de llamadas)
tests/fixtures/make_pdfs.py  generadores de PDFs de test con PyMuPDF (devuelven bytes)
tests/unit/test_config.py, test_llm.py, test_single_llm_client.py   VC-002, VC-005, VC-001
tests/unit/test_table_parsing.py     VC-012..VC-014, VC-019 (mínimos) + notas al pie (D-011)
tests/unit/test_pdf_extract.py       VC-010, VC-011, VC-015..VC-019, VC-031, VC-032 + D-012/D-015
tests/unit/test_pdf_context.py       VC-020 (+ truncado, multipágina, texto de otra tabla)
tests/integration/test_sample_pdfs.py  VC-090 (técnica) y VC-015 sobre los PDFs reales (sin red)
```

Layout futuro fijado por el plan: `app.py`, `agents/{ingestion,analyst}/`, `services/{table_store,vectorstore,sandbox,tracing}.py`, `ui/`, `eval/`, `data/tables/` (gitignored).

## Public interfaces

```python
# services/config.py  (sin cambios desde la Iteración 1)
REQUIRED_VARS: tuple[str, ...]; class ConfigError(Exception): missing: list[str]; invalid: dict[str, str]
@dataclass(frozen=True) class Settings: ... max_tables_per_pdf: int = 40 ...   # todas las vars de §7.1
def load_settings(env: Mapping[str, str] | None = None) -> Settings

# services/llm.py
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
```

```python
# services/pdf_extract.py
BBox = tuple[float, float, float, float]          # puntos PDF, origen arriba a la izquierda
class IngestionWarning(BaseModel, frozen):        # spec §7.3 (D-016: vive acá; Iteración 4 lo reexporta)
    page: int | None = None; table_ref: str | None = None; reason: str
class PdfOpenError(Exception): reason: str        # str(e) == "No se pudo abrir el PDF: <reason>"
@dataclass(frozen=True) class TextBlock: bbox: BBox; text: str; font_size: float; bold: bool
@dataclass(frozen=True) class PageText: number: int (1-based); width; height; blocks: list[TextBlock];
                                        has_text: bool; large_image_fraction: float
@dataclass(frozen=True) class LoadedPdf: file_name; doc_hash (sha256 hex); pdf_bytes; pages: list[PageText];
                                         median_font_size: float; n_pages (property)
@dataclass(eq=False) class RawTable:
    df: pd.DataFrame; dtypes: dict[str, str]; unit_hints: dict[str, str | None]
    pages: list[int]            # 1-based; > 1 si hubo continuación
    bbox_first: BBox; bbox_last: BBox   # incluyen la franja de encabezado
    index_on_page: int          # 0-based entre las tablas de su primera página (BR-010)
    header: list[str]           # == list(df.columns)
    has_header: bool            # False -> nombres col_<i>
    ref -> str                  # property "p<pág:03d>-t<índice>", p. ej. "p003-t0" (id provisorio)
def load_pdf(file_name: str, pdf_bytes: bytes) -> LoadedPdf          # FR-010, FR-027
def extract_tables(loaded: LoadedPdf, max_tables: int = 40) -> tuple[list[RawTable], list[IngestionWarning]]
REASON_NO_TEXT = "sin capa de texto"; REASON_LARGE_IMAGE = "imagen grande no procesada (posible tabla en imagen)"
# Avisos de BR-005: page=<primera pág>, table_ref=<ref>, reason=check_minimums(...)
# Aviso de FR-028: page=None, reason="<k> tablas omitidas: el PDF tiene <n> tablas lógicas y se procesan las primeras <m> (MAX_TABLES_PER_PDF)"

# services/pdf_context.py
@dataclass(frozen=True) class TableContext:
    above: list[str]   # ≤ 3 bloques, el más cercano primero (≤ 120 pt sobre la tabla, primera página)
    below: list[str]   # ≤ 2 bloques, el más cercano primero (≤ 80 pt bajo la tabla, última página)
    section: str | None
    text -> str        # property: sección + above + below unidos con "\n"; ≤ 2 000 caracteres
def collect_context(loaded: LoadedPdf, table: RawTable, tables: Sequence[RawTable] = ()) -> TableContext
    # pasar `tables` = todas las tablas del PDF para que el texto de otras tablas no sea contexto
```

CLI: `.venv\Scripts\python scripts/inspect_pdf.py <pdf> [--rows N] [--max-tables N] [--context]`.

Variables de entorno: spec §7.1 (todas en `.env.example`). `MAX_TABLES_PER_PDF` → `extract_tables(max_tables=...)`.

### Fakes y fixtures de test

- `tests/fakes.py::ScriptedChatModel(responses=[...])`: `invoke` y `with_structured_output(Schema)` devuelven el siguiente elemento guionado (`str`/`BaseMessage`/`BaseModel`/`dict`; una `Exception` se lanza); `calls` = prompts recibidos.
- `tests/fixtures/make_pdfs.py`: funciones que devuelven bytes de PDF: `two_tables_one_page`, `multipage_repeated_header` (págs. 4–6), `continuation_without_header`, `continuation_different_columns`, `continuation_not_first_on_next_page`, `continuation_with_own_caption`, `header_as_loose_text`, `image_only_page`, `page_with_image(fraction)`, `tables_below_minimums`, `table_with_context(long_paragraph=False)`, `no_tables`, `encrypted`, `five_tables`; helpers `draw_table(page, x, y, rows, col_widths)` (grilla con líneas que `find_tables` detecta) y `write(page, x, baseline, text)`. Útiles para tests del grafo de ingesta (Iteración 5).

## Conventions & invariants

- `ChatOpenAI(`/`OpenAIEmbeddings(` solo en `services/llm.py` (VC-001). Toda la config entra por `load_settings()`; nunca loguear `*_api_key`.
- Tests `[U]` sin red ni claves; fixtures PDF generadas en el test (la exclusión de PDFs sintéticos de la spec aplica solo a datos de demo). Tests que usan los PDFs reales llevan `@pytest.mark.integration`.
- Imports absolutos desde la raíz; idioma: mensajes/prompts/docs en español, identificadores en inglés.
- pandas 3: columnas de texto con dtype `str`; los DataFrames normalizados solo tienen `float64`, `Int64` (años) o `str` (listos para Parquet); faltantes/marcadores → `NaN`, nunca 0.
- Páginas 1-based en todo lo público (`PageText.number`, `RawTable.pages`, `IngestionWarning.page`); `index_on_page` 0-based. `table_id` (Iteración 4) = `f"{doc_hash[:16]}-p{pages[0]:03d}-t{index_on_page}"`.
- Encabezados (D-012): los nombres de columna salen de las palabras de la franja de encabezado (dentro de la tabla, de una "tabla" de solo encabezado dibujada encima, o de texto suelto ≤ 22 pt encima), no de `table.header`. Niveles unidos con `" - "`; rótulos subrayados por una raya se repiten en las columnas que abarcan (p. ej. `"Total 31 aglomerados urbanos - Año 2026 - 2° trimestre*"`). Después pasan por `flatten_header` (únicos, `col_<i>`).
- Filas de datos totalmente vacías se descartan antes de normalizar; BR-005 se aplica a la tabla lógica (después de unir).
- Separador decimal: `infer_decimal_separator` sobre todas las celdas de datos del PDF → `default_decimal` de cada tabla (INDEC → `,`; CEPAL → `.`).
- Unión multipágina (BR-001 + D-013/D-015): (a)–(d) sobre las tablas detectadas (tras absorber fragmentos de encabezado); (d) "fila numérica" = ≥ 50 % de las celdas con contenido parsean como número; una tabla con epígrafe propio ("Cuadro N", "1.1.4 …") encima en su página no continúa a la anterior.
- Contexto (D-014): se ignoran bloques dentro de tablas y en el 5 % superior/inferior de la página; como sección no cuentan epígrafes "Cuadro/Gráfico N", textos que no empiezan con mayúscula/dígito ni negritas más chicas que la mediana.

## Commands

```powershell
# instalar / actualizar dependencias
.venv\Scripts\python -m pip install -r requirements.txt
# tests unitarios (sin red) y lint
.venv\Scripts\python -m pytest -m "not integration"
.venv\Scripts\ruff check .
# tests de esta iteración
.venv\Scripts\python -m pytest tests/unit/test_pdf_extract.py tests/unit/test_pdf_context.py -v
.venv\Scripts\python -m pytest tests/integration/test_sample_pdfs.py -v     # PDFs reales, sin red, ~20 s
# inspeccionar lo que se extrae de un PDF
.venv\Scripts\python scripts/inspect_pdf.py data/sample_pdfs/indec_eph_mercado_trabajo_2t2026.pdf --context
# chequeo de secretos (debe dar vacío; repetir con --cached tras el stage) y .env sin trackear
git grep -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"
git grep --cached -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"
git ls-files .env
```

## Known gaps

- `.env`: el usuario confirmó que tiene todas las variables obligatorias; Pinecone/LangSmith todavía no se usaron (Iteración 4/5).
- UI de FR-002 → Iteración 9. NFR-001: solo probado en Python 3.13.
- `FakeVectorStore` / `FakeEmbeddings` → Iteración 4. `IngestionWarning` vive en `services/pdf_extract.py`; la Iteración 4 lo reexporta desde `agents/ingestion/schemas.py` (no duplicar).
- Llamadas a nota pegadas a **texto** quedan en los rótulos de fila (CEPAL: `"Argentinab"`, `"Bahamas (Las)c"`, `"América Latinad"`): el enriquecimiento y el codegen deben usar los `row_headers`/valores reales, no asumir `"Argentina"`.
- Encabezados largos en CEPAL (bilingües, con unidad y año: p. ej. `"Población total[A]/ Total population[A] - (En miles de personas a mitad de año/ Thousands of persons, at mid-year) - 2025"`); son exactos pero largos para los prompts.
- En los informes de INDEC, el rótulo de la primera fila de algunos cuadros queda en el nombre de la primera columna (`"Indicador - Grupos de población (en miles) y condición de actividad"`), y las filas de subtítulo (`"Sexo y grupos de edad"`) quedan como filas con `NaN`.
- D-015 (epígrafe propio corta la continuación) agrega una condición a BR-001 que la spec no tiene: pendiente de que el usuario la ratifique y, si corresponde, se agregue a BR-001 en una próxima versión de la spec.
- `find_tables` no detecta grillas de 1 columna, así que el descarte por "< 2 columnas" de BR-005 solo se prueba inyectando un fragmento.
- Los cuadros de gráficos (páginas con gráficos vectoriales) generan "tablas" que BR-005 descarta con aviso: el extracto de CEPAL da 21 avisos de descarte y los de INDEC 7 (2T2025) y 9 (2T2026), más el aviso de imagen grande de la portada. Son esperables; la UI los listará.
- NFR-008 (≤ 15 tablas por PDF): el informe EPH 2T2026 tiene 16 tablas lógicas y el de 2T2025, 12; se mide en la Iteración 10.
