# CONTEXT — estado actual del código

Última actualización: Iteración 2 (normalización de tablas). Rama de trabajo: `feat/qa-tablas`.

## Current state

- El proyecto se instala con `requirements.txt` en el venv `.venv` (Python 3.13) y pasa `ruff check .` y `pytest -m "not integration"` (74 tests, sin red, ~3 s).
- `services/config.py` carga y valida la configuración de spec §7.1 (`load_settings`), con `ConfigError` que lista todas las variables faltantes/inválidas (FR-002, lado lógico).
- `services/llm.py` construye el chat model y los embeddings contra el proxy LiteLLM (FR-001) y ofrece caché SQLite opcional `.langchain.db` (FR-005).
- Modelos elegidos (D-001): `LLM_MODEL=gpt-5.4-mini` (`temperature=0`), `EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIM=1536`.
- `services/table_parsing.py` normaliza tablas crudas (filas de celdas `str | None`) a un `DataFrame` listo para Parquet: aplanado de encabezados (BR-002), números es-AR/en-US con unidades (BR-003), faltantes → `NaN` (BR-004) y mínimos de tabla (BR-005). Funciones puras, sin PDFs.
- Todavía no hay extracción de PDFs, Pinecone, agentes, sandbox ni UI.

## File map

```
requirements.txt          dependencias directas fijadas con == (D-002)
ruff.toml                 lint: py311, line-length 110, E,F,I,UP,B; excluye notebooks/
pytest.ini                testpaths=tests, pythonpath=., marker `integration`
.env.example              todas las variables de §7.1 sin valores + modelos elegidos en comentarios
.gitignore                .env, .venv, caches, data/tables/, .langchain.db
assignment.es.md          enunciado del TP
notebooks/00_litellm_exploracion.ipynb   exploración del proxy (OQ-1); se versiona sin outputs
specs/                    spec, plan y este directorio de artefactos
services/__init__.py
services/config.py        Settings, load_settings, ConfigError, REQUIRED_VARS
services/llm.py           get_chat_model, get_embeddings, SQLiteCache, enable_cache_if_configured
services/table_parsing.py flatten_header, parse_number, infer_decimal_separator, is_missing_marker,
                          clean_text, normalize_table, NormalizedTable, check_minimums (BR-002..BR-005)
tests/__init__.py
tests/fakes.py            ScriptedChatModel (LLM fake guionado con contador de llamadas)
tests/unit/__init__.py
tests/unit/test_config.py            VC-002
tests/unit/test_llm.py               FR-001 (construcción) + VC-005 (caché)
tests/unit/test_single_llm_client.py VC-001 (único lugar que construye clientes LLM)
tests/unit/test_table_parsing.py     VC-012, VC-013, VC-014, VC-019 (mínimos) + casos borde; tests test_vcNNN_*
```

Layout futuro fijado por el plan: `app.py`, `agents/{ingestion,analyst}/`, `services/{pdf_extract,pdf_context,table_store,vectorstore,sandbox,tracing}.py`, `ui/`, `eval/`, `scripts/`, `tests/{unit,integration,fixtures}/`, `data/{sample_pdfs,tables}/`.

## Public interfaces

```python
# services/config.py
REQUIRED_VARS: tuple[str, ...]   # LITELLM_BASE_URL, LITELLM_API_KEY, LLM_MODEL, EMBEDDING_MODEL,
                                 # EMBEDDING_DIM, PINECONE_API_KEY, LANGSMITH_API_KEY, LANGSMITH_PROJECT
class ConfigError(Exception):
    missing: list[str]           # en el orden de REQUIRED_VARS
    invalid: dict[str, str]      # variable -> motivo (p. ej. "se esperaba un entero")
@dataclass(frozen=True)
class Settings:
    litellm_base_url: str; litellm_api_key: str; llm_model: str; embedding_model: str
    embedding_dim: int; pinecone_api_key: str; langsmith_api_key: str; langsmith_project: str
    pinecone_index_name: str = "mas-2026s2-tables"; pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"; langsmith_tracing: bool = True
    tables_dir: Path = Path("data/tables"); max_enrich_retries: int = 2; max_code_retries: int = 3
    retrieval_top_k: int = 5; sandbox_timeout_s: int = 30; llm_cache: bool = False
    max_tables_per_pdf: int = 40
def load_settings(env: Mapping[str, str] | None = None) -> Settings
    # env=None: load_dotenv(find_dotenv(usecwd=True), override=False) + os.environ

# services/llm.py
def get_chat_model(settings: Settings, **overrides: Any) -> ChatOpenAI
    # base_url=settings.litellm_base_url, model=settings.llm_model, temperature=0, timeout=120, max_retries=2
def get_embeddings(settings: Settings) -> OpenAIEmbeddings
    # base_url=settings.litellm_base_url, model=settings.embedding_model, max_retries=2
class SQLiteCache(BaseCache):
    def __init__(self, database_path: str | Path = Path(".langchain.db")) -> None
def enable_cache_if_configured(settings: Settings, database_path: str | Path = Path(".langchain.db")) -> SQLiteCache | None
    # LLM_CACHE=true -> set_llm_cache(SQLiteCache(...)) y lo devuelve; false -> None, no toca la caché global
```

```python
# services/table_parsing.py  (funciones puras; Decimal = Literal[",", "."]; Cell = str | None)
@dataclass
class NormalizedTable:
    df: pd.DataFrame                 # columnas str únicas no vacías; cada una float64 | Int64 | str; índice RangeIndex
    dtypes: dict[str, str]           # col -> str(df[col].dtype): "float64" | "Int64" (años) | "str"
    unit_hints: dict[str, str | None]  # col -> "%" | "$" | "US$" | "€" | None (unidad removida al parsear)
def normalize_table(raw_rows: Sequence[Sequence[Cell]], header_rows: int,
                    default_decimal: Decimal = ".") -> NormalizedTable
    # raw_rows = encabezado + datos (como find_tables().extract()); las primeras header_rows filas son
    # el encabezado (0 -> col_<i>); filas cortas se completan con vacíos; ValueError si header_rows fuera de rango.
    # Por columna: marcadores/vacíos -> NaN; años 1900-2100 -> Int64; >= 80 % parsean -> float64 (+unit_hint,
    # las no numéricas -> NaN); si no -> str. default_decimal solo aplica a columnas sin señal (D-008).
def flatten_header(rows: Sequence[Sequence[Cell]], n_cols: int | None = None) -> list[str]   # BR-002, D-007
def parse_number(cell: str | None, decimal: Decimal) -> float | None    # None si no es número o es faltante
def infer_decimal_separator(cells: Sequence[Cell], default: Decimal = ".") -> Decimal     # D-008
def is_missing_marker(cell: object) -> bool      # "", None, -, –, —, s/d, n/d, ..., …, ///, x (sin mayúsculas)
def clean_text(cell: object) -> str              # colapsa espacios/saltos de línea; None -> ""
def check_minimums(df: pd.DataFrame) -> str | None
    # BR-005 sobre el df ya normalizado: motivo en español ("tabla descartada: ...") si < 2 filas de datos,
    # < 2 columnas o > 60 % de celdas NaN (60 % exacto se conserva); None si cumple.
# Constantes: NUMERIC_THRESHOLD=0.8, YEAR_MIN/YEAR_MAX=1900/2100, MIN_DATA_ROWS=2, MIN_COLS=2,
#             MAX_EMPTY_FRACTION=0.6, MISSING_MARKERS, HEADER_JOINER=" - "
```

Variables de entorno: spec §7.1 (todas en `.env.example`). Semántica (D-005): vacío = no definida; opcionales y obligatorias con default concreto toman el default; `LANGSMITH_PROJECT` es obligatoria.

### Fakes de test (`tests/fakes.py`)

`ScriptedChatModel(responses=[...])` — `BaseChatModel` sin red:
- `invoke(prompt)` devuelve el siguiente elemento: `str` → `AIMessage(content=str)`, `BaseMessage` → tal cual.
- `with_structured_output(Schema)` → runnable que devuelve el siguiente elemento (instancia `BaseModel` tal cual, `dict` validado con `Schema`).
- Un elemento `Exception` se lanza (para simular fallas del proxy).
- `calls: list[str]` = prompts recibidos en orden (mensajes unidos por `\n`); `len(calls)` = cantidad de llamadas. Más llamadas que respuestas → `AssertionError`.
- Usarlo inyectándolo donde iría `get_chat_model(settings)` (los grafos reciben el modelo por `deps`).

## Conventions & invariants

- `ChatOpenAI(` y `OpenAIEmbeddings(` se construyen **solo** en `services/llm.py` (test VC-001). Tampoco se permiten clientes crudos (`openai.OpenAI(`, `anthropic`, `litellm`, `init_chat_model`, etc.). Importar `openai` para atrapar sus excepciones sí está permitido.
- Toda la config entra por `load_settings()`; el resto del código recibe `Settings`, no lee `os.environ`.
- `Settings.__repr__` no muestra claves. Nunca loguear `settings.*_api_key`.
- Tests `[U]` sin red ni claves: usan `load_settings(dict)` con valores de prueba (`tests/unit/test_config.py::FULL_ENV`) y fakes de `tests/fakes.py`. Tests con servicios reales llevan `@pytest.mark.integration` (`--strict-markers` está activo).
- Imports absolutos desde la raíz (`from services.config import ...`, `from tests.fakes import ...`); `pytest.ini` agrega `.` al path.
- Idioma: mensajes/prompts/docs en español; identificadores en inglés.
- pandas 3 (copy-on-write, dtype `str` por defecto para texto). Las columnas de texto se crean con `dtype="str"` (None → `NaN`).
- Los DataFrames normalizados nunca tienen 0 donde había una celda vacía o un marcador de faltante (BR-004): quedan `NaN`/`<NA>`. Nunca hay columnas `object`: cada una es `float64`, `Int64` o `str`, así que se escriben en Parquet tal cual (test de round-trip en `test_table_parsing.py`).
- Nombres de columna de tablas: únicos, no vacíos, `str`, según BR-002/D-007; los años del encabezado quedan como texto (`"2023"`).
- Separador decimal: se infiere por columna; `default_decimal` (por defecto `.`) solo decide columnas sin señal (D-008). Iteración 3 debería calcular la señal del documento y pasarla.
- Motivos de descarte/avisos en español; los genera `check_minimums` y la Iteración 3 los envuelve en `IngestionWarning`.
- El notebook se versiona **sin outputs**.

## Commands

```powershell
# instalar / actualizar dependencias
.venv\Scripts\python -m pip install -r requirements.txt
# tests unitarios (sin red) y lint
.venv\Scripts\python -m pytest -m "not integration"
.venv\Scripts\ruff check .
# tests por módulo
.venv\Scripts\python -m pytest tests/unit/test_single_llm_client.py tests/unit/test_config.py tests/unit/test_llm.py -v
.venv\Scripts\python -m pytest tests/unit/test_table_parsing.py -v
# chequeo de secretos (debe dar vacío; repetir con --cached tras el stage) y que .env no esté trackeado
git grep -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"
git grep --cached -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"
git ls-files .env
# re-ejecutar la exploración de modelos (llamadas reales al proxy; outputs fuera del repo)
.venv\Scripts\jupyter nbconvert --to notebook --execute --output-dir $env:TEMP\nbrun notebooks/00_litellm_exploracion.ipynb
```

## Known gaps

- `.env` completo: el usuario confirmó (antes de la Iteración 2) que tiene todas las variables obligatorias. Pinecone/LangSmith se usan recién desde la Iteración 4/5; no se verificó todavía contra los servicios.
- La parte de UI de FR-002 (mostrar el error y deshabilitar pestañas) → Iteración 9.
- NFR-001: solo probado en Python 3.13 (no hay 3.11/3.12 en la máquina).
- `FakeVectorStore` / `FakeEmbeddings` todavía no existen → Iteración 4 (en `tests/fakes.py`).
- PDF de muestra provisto por el usuario, todavía sin trackear en la raíz: `mercado_trabajo_eph_2trim26433FCBC5A8.pdf` (INDEC, EPH "Mercado de trabajo", 2.4 MB). El usuario confirmó que es uno de los PDFs de muestra que quiere usar. → Iteración 3: moverlo a `data/sample_pdfs/` (lo aportó el usuario: no hace falta pedir aprobación de descarga para él), citar su URL en `SOURCES.md` y proponer el/los otros PDFs (OQ-2).
- `table_parsing` no conoce PDFs: la detección de cuántas filas son encabezado (`header_rows`), la señal decimal del documento (`default_decimal`), los avisos `IngestionWarning` y la unión multipágina (BR-001) son de la Iteración 3.
- `U$S` (variante argentina de `US$`) no está en la lista de símbolos de BR-003: una celda `U$S 100` no parsea como número. Revisar con los PDFs reales en la Iteración 3.
