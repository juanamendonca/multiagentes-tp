# CONTEXT — estado actual del código

Última actualización: Iteración 1 (bootstrap). Rama de trabajo: `feat/qa-tablas`.

## Current state

- El proyecto se instala con `requirements.txt` en el venv `.venv` (Python 3.13) y pasa `ruff check .` y `pytest -m "not integration"` (23 tests, sin red, ~3 s).
- `services/config.py` carga y valida la configuración de spec §7.1 (`load_settings`), con `ConfigError` que lista todas las variables faltantes/inválidas (FR-002, lado lógico).
- `services/llm.py` construye el chat model y los embeddings contra el proxy LiteLLM (FR-001) y ofrece caché SQLite opcional `.langchain.db` (FR-005).
- Modelos elegidos (D-001): `LLM_MODEL=gpt-5.4-mini` (`temperature=0`), `EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIM=1536`.
- Todavía no hay PDFs, Pinecone, agentes, sandbox ni UI.

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
tests/__init__.py
tests/fakes.py            ScriptedChatModel (LLM fake guionado con contador de llamadas)
tests/unit/__init__.py
tests/unit/test_config.py            VC-002
tests/unit/test_llm.py               FR-001 (construcción) + VC-005 (caché)
tests/unit/test_single_llm_client.py VC-001 (único lugar que construye clientes LLM)
```

Layout futuro fijado por el plan: `app.py`, `agents/{ingestion,analyst}/`, `services/{table_parsing,pdf_extract,pdf_context,table_store,vectorstore,sandbox,tracing}.py`, `ui/`, `eval/`, `scripts/`, `tests/{unit,integration,fixtures}/`, `data/{sample_pdfs,tables}/`.

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
- pandas 3 (copy-on-write, dtype `str` por defecto para texto).
- El notebook se versiona **sin outputs**.

## Commands

```powershell
# instalar / actualizar dependencias
.venv\Scripts\python -m pip install -r requirements.txt
# tests unitarios (sin red) y lint
.venv\Scripts\python -m pytest -m "not integration"
.venv\Scripts\ruff check .
# tests de esta iteración
.venv\Scripts\python -m pytest tests/unit/test_single_llm_client.py tests/unit/test_config.py tests/unit/test_llm.py -v
# chequeo de secretos (debe dar vacío) y que .env no esté trackeado
git grep -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"
git ls-files .env
# re-ejecutar la exploración de modelos (llamadas reales al proxy; outputs fuera del repo)
.venv\Scripts\jupyter nbconvert --to notebook --execute --output-dir $env:TEMP\nbrun notebooks/00_litellm_exploracion.ipynb
```

## Known gaps

- 🧑 **`.env` incompleto** (Human gate): hoy solo tiene `LITELLM_BASE_URL` y `LITELLM_API_KEY`. Faltan `LLM_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` (valores en D-001 / `.env.example`), `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` (opcional, default), `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`. `load_settings()` sobre el `.env` real hoy lanza `ConfigError`. Bloquea la Iteración 4 (Pinecone) y la 5 (LangSmith); no bloquea las 2 y 3.
- La parte de UI de FR-002 (mostrar el error y deshabilitar pestañas) → Iteración 9.
- NFR-001: solo probado en Python 3.13 (no hay 3.11/3.12 en la máquina).
- `FakeVectorStore` / `FakeEmbeddings` todavía no existen → Iteración 4 (en `tests/fakes.py`).
- Hay un PDF sin trackear en la raíz (`mercado_trabajo_eph_2trim26433FCBC5A8.pdf`) que apareció durante la Iteración 1; no se commiteó. Candidato para OQ-2 (Iteración 3), que debe moverlo a `data/sample_pdfs/` con permiso del usuario.
