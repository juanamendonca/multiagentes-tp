# Iteración 1 — Bootstrap, configuración y elección de modelos

- **Fecha:** 2026-10-07
- **Rama:** `feat/qa-tablas`
- **SHA:** ver `git log -1 --format=%H -- specs/qa-tablas-pdf-spec-plan-artifacts/iterations/01-bootstrap.md` (D-003)
- **Commit:** `chore: bootstrap del proyecto, configuración y clientes LiteLLM`

## Criterios de éxito

Corrida completa desde el principio, con todo en stage, justo antes del commit (PowerShell o Git Bash desde la raíz):

| # | Criterio | Comando | Resultado |
|---|---|---|---|
| 1 | Install | `.venv\Scripts\python -m pip install -r requirements.txt` | PASS (exit 0; `pip check`: "No broken requirements found") |
| 2 | VC-001 | `.venv\Scripts\python -m pytest tests/unit/test_single_llm_client.py` | PASS (4 passed). Sanity: un `.py` temporal con `ChatOpenAI(` y `from openai import OpenAI` fuera de `services/llm.py` hace fallar 2 tests (archivo borrado después) |
| 3 | VC-002 | `.venv\Scripts\python -m pytest tests/unit/test_config.py` | PASS (11 passed; incluye sin `PINECONE_API_KEY` y dos faltantes) |
| 4 | VC-005 | `.venv\Scripts\python -m pytest tests/unit/test_llm.py` | PASS (8 passed; contador = 1 tras dos llamadas iguales con caché) |
| 5 | Suite sin red | `.venv\Scripts\python -m pytest -m "not integration"` | PASS (23 passed, ~2.5 s) |
| 6 | Lint | `.venv\Scripts\ruff check .` | PASS ("All checks passed!") |
| 7 | Secretos | `git grep -nE "(sk-[A-Za-z0-9]{16,}\|pcsk[_]\|lsv2[_])"` y `git grep --cached -nE ...` (además, la forma original del plan con los prefijos literales) | PASS (las tres vacías, exit 1) tras D-006. Primera corrida: FAIL por 5 falsos positivos (el texto del comando en spec/plan/CONTEXT) |
| 8 | `.env` no trackeado | `git ls-files .env` | PASS (vacío; `.env` está en `.gitignore`) |
| 9 | Spec sin OQ-1, §7.1 con modelos | `Select-String -Pattern "OQ-1" specs\qa-tablas-pdf-spec.md` | PASS (0 coincidencias; §7.1 nombra `gpt-5.4-mini`, `text-embedding-3-small`, `1536`; versión 0.4) |
| 10 | Artefactos | revisión | PASS: este record; `CONTEXT.md` (6 secciones, `Settings`, `load_settings`, `get_chat_model`, `get_embeddings`, comandos); D-001..D-003 (+D-004..D-006) en `DECISIONS.md` |

## OQ-1 — corrida del notebook

`jupyter nbconvert --to notebook --execute --output-dir %TEMP%\nbrun notebooks/00_litellm_exploracion.ipynb` (exit 0, salida fuera del repo; el notebook versionado no tiene outputs). Resumen en D-001: 6/6 candidatos pasan salida estructurada (`json_schema` y `function_calling`), tool calling y codegen; `gpt-5.4-mini` fue el más rápido. Embeddings: `text-embedding-3-small` = 1536 dims. `/model/info` responde 403 (no hay datos de costo).

## Archivos

Agregados: `requirements.txt`, `ruff.toml`, `pytest.ini`, `.env.example`, `services/__init__.py`, `services/config.py`, `services/llm.py`, `tests/__init__.py`, `tests/fakes.py`, `tests/unit/__init__.py`, `tests/unit/test_config.py`, `tests/unit/test_llm.py`, `tests/unit/test_single_llm_client.py`, `specs/qa-tablas-pdf-spec-plan-artifacts/{CONTEXT.md,DECISIONS.md,iterations/01-bootstrap.md}`.
Versionados por primera vez (existían sin trackear): `.gitignore`, `assignment.es.md`, `notebooks/00_litellm_exploracion.ipynb` (sin outputs), `specs/qa-tablas-pdf-spec.md`, `specs/qa-tablas-pdf-spec-plan.md`.
Cambiados: spec → v0.4 (§7.1 modelos, §9 sin OQ-1, A-5, NFR-003 regex, changelog); plan → solo la regex de secretos (D-006).
Borrados: ninguno.

## Desvíos del plan

1. **Caché SQLite propia** (D-004): el plan dice `SQLiteCache(".langchain.db")`, que vive en `langchain-community` (no está en la lista de dependencias y arrastra ~13 paquetes). Se implementó `services.llm.SQLiteCache` sobre `sqlite3` con la misma semántica.
2. **Regex de secretos** (D-006): el comando literal coincidía consigo mismo en spec/plan; se reescribió con `[_]` (equivalente). Edición editorial del plan.
3. **`numpy`** agregado a `requirements.txt` (no estaba en la lista del plan): el código generado puede importarlo (allowlist BR-012).
4. **`LANGSMITH_PROJECT` obligatoria sin default** (D-005): el default de la spec es una plantilla.
5. **Modelo de chat**: el notebook sugiere `gpt-4.1-mini` (primero que pasa en orden de costo); se eligió `gpt-5.4-mini` por el desempate de latencia que fija el plan (D-001).
6. **`test_single_llm_client.py`** además prohíbe clientes crudos (`openai.OpenAI(`, `anthropic`, `litellm`, `init_chat_model`...) pero permite `import openai` (FR-054 necesita sus excepciones).

## Problemas encontrados / gotchas

- El escaneo con `Path.rglob` recorría `.venv` (~8 s por test); se usa `os.walk` con poda de directorios.
- `langchain_core.load.loads` emite `LangChainBetaWarning`; se silencia solo dentro de `SQLiteCache.lookup`.
- `check_embedding_ctx_length=True` (default) usa `tiktoken`, que descarga su encoding la primera vez (la primera llamada de `-3-large` tardó 10.7 s). Si se corre sin internet fuera del proxy, considerar `False`.
- `load_dotenv()` sin argumentos busca el `.env` relativo al archivo que llama; se usa `find_dotenv(usecwd=True)` para que dependa del cwd (y los tests puedan aislarse con `chdir`).
- Apareció un PDF sin trackear en la raíz (`mercado_trabajo_eph_2trim26433FCBC5A8.pdf`, 2.4 MB) durante la iteración; no es de esta iteración y no se commiteó.

## For the next iteration

- Iteración 2 no necesita red ni claves: solo `pandas`/`numpy` y los comandos de *Commands* en `CONTEXT.md`.
- Fakes: agregar nuevos fakes a `tests/fakes.py`; `ScriptedChatModel` ya sirve para `with_structured_output` guionado (ver `CONTEXT.md`).
- 🧑 Pendiente del usuario: completar `.env` con `LLM_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` (valores en `.env.example`) y crear cuentas/claves de Pinecone y LangSmith (`PINECONE_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, opcional `PINECONE_INDEX_NAME`, `LANGSMITH_TRACING`). Bloquea la Iteración 4.
- En archivos versionados no escribir literalmente los prefijos de claves (D-006).
