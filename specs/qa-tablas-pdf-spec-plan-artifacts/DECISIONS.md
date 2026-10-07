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
