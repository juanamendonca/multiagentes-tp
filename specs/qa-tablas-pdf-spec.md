# Spec — QA multiagente sobre tablas en PDFs

| Campo | Valor |
|---|---|
| Versión | 0.8 |
| Última actualización | 2026-10-07 |
| Estado | Draft |
| Owner | jmendoncag |
| Fuente | `assignment.es.md` (TP Sistemas Multiagente de IA, 2026s2) |

---

## 1. Purpose

Los datos más útiles de informes públicos (estados contables, anuarios, publicaciones estadísticas) suelen estar en tablas dentro de PDFs, donde son difíciles de encontrar y de usar para hacer cuentas. Este sistema le permite a un usuario **subir PDFs legibles por máquina**, que un **Agente de Ingesta** extraiga, describa semánticamente e indexe sus tablas, y luego **hacer preguntas en lenguaje natural** que un **Agente Analista** responde escribiendo y ejecutando código Python sobre esas tablas, sin inventar datos: si la información no está, lo dice (`partial` / `cannot_answer`). Beneficia al usuario final (consulta datos sin cuentas manuales) y, como entrega académica, demuestra diseño de grafos LangGraph, RAG sobre Pinecone, generación/ejecución/autocorrección de código y observabilidad con LangSmith.

## 2. Scope

### In

- Agente de Ingesta como `StateGraph` de LangGraph: carga de PDF, extracción de tablas (PyMuPDF), unión de tablas multipágina, recolección de contexto, enriquecimiento con LLM (salida estructurada Pydantic), validación con ciclo de reintento, indexación en Pinecone y guardado de datos en Parquet.
- Agente Analista como `StateGraph`: entendimiento de la consulta, recuperación en Pinecone, selección de una o más tablas, generación de código, ejecución en sandbox (subproceso + timeout + sin red ni FS), autocorrección con límite, respuesta con estado `answered` / `partial` / `cannot_answer`.
- UI Streamlit con tres pestañas: Subir PDF, Explorar tablas, Preguntar (chat de sesión).
- Todas las llamadas a LLM/embeddings vía el proxy LiteLLM de la universidad; trazas LangSmith en toda ejecución.
- Configuración por variables de entorno (`.env` + `.env.example` versionado), setup con `requirements.txt` pineado.
- ≥ 2 PDFs **reales y públicos** en `data/sample_pdfs/` (≥ 1 con tabla multipágina), ≥ 8 preguntas de prueba con estado y respuesta esperados, script de evaluación local, `EVALUATION.md`, README completo, link a video demo.
- Tests automatizados (pytest) de la lógica determinística.

### Out

- OCR / PDFs escaneados (las páginas sin capa de texto se informan y se saltean).
- Todos los bonus de la Sección 10 del enunciado: human-in-the-loop (`interrupt`), visualizaciones/gráficos, OCR, evaluadores automáticos en LangSmith. *(Se pueden agregar en una versión posterior del spec.)*
- Autenticación, multiusuario, persistencia del historial de chat entre sesiones.
- Deploy en la nube; el sistema corre localmente.
- Sandbox basado en contenedores (Docker/VM); el aislamiento es a nivel de subproceso (ver BR-012 y Limitaciones).
- Edición manual de metadatos desde la UI.
- Uso de conocimiento general del LLM, fuentes externas o estimaciones para responder.
- PDFs generados sintéticamente como datos de demo.

## 3. Actors

| Actor | Descripción | Permisos |
|---|---|---|
| Usuario | Persona que usa la app Streamlit localmente. | Subir PDFs, explorar tablas indexadas, hacer preguntas, ver código/salida/trazas. |
| Agente de Ingesta | Grafo LangGraph que procesa un PDF. | Leer el PDF subido; llamar LLM/embeddings vía LiteLLM; escribir en Pinecone y en `data/tables/`. |
| Agente Analista | Grafo LangGraph que responde preguntas. | Llamar LLM/embeddings vía LiteLLM; leer Pinecone y `data/tables/`; lanzar el sandbox. **No** escribe en Pinecone. |
| Sandbox | Subproceso que ejecuta el código generado. | Leer solo las tablas seleccionadas (ya cargadas por el preámbulo); sin red, sin variables de entorno con secretos, sin escritura fuera de su directorio temporal. |
| Equipo docente | Corrige la entrega. | Leer el repo en el commit entregado, correr el setup con sus propias claves, ver trazas compartidas y video. |

## 4. Functional Requirements

Prioridad MoSCoW: **M** = Must, **S** = Should, **C** = Could.

### 4.1 Configuración y servicios

- **FR-001 (M)** — *Given* un `.env` con todas las variables de la tabla §7.1, *when* arranca la app, *then* se construyen el chat model y el modelo de embeddings con `ChatOpenAI`/`OpenAIEmbeddings` apuntando a `LITELLM_BASE_URL`, y no existe ningún otro cliente de LLM en el código.
- **FR-002 (M)** — *Given* que falta una variable obligatoria, *when* arranca la app, *then* la UI muestra un error que nombra cada variable faltante y deshabilita las pestañas que dependen de ella, sin traceback.
- **FR-003 (M)** — *Given* que el índice `PINECONE_INDEX_NAME` no existe, *when* se ejecuta la primera ingesta, *then* el sistema crea un índice serverless con dimensión `EMBEDDING_DIM` y métrica coseno.
- **FR-004 (M)** — *Given* que el índice existe con una dimensión distinta de `EMBEDDING_DIM`, *when* arranca la app, *then* se muestra un error que indica ambas dimensiones y no se escribe en el índice.
- **FR-005 (S)** — *Given* `LLM_CACHE=true`, *when* se repite una llamada al LLM con el mismo prompt, *then* la respuesta sale de un caché SQLite local (`.langchain.db`) sin pegarle al proxy.

### 4.2 Agente de Ingesta

- **FR-010 (M)** — *Given* un PDF subido, *when* corre el nodo `load_pdf`, *then* el estado contiene el `doc_hash` (SHA-256 de los bytes), el nombre del archivo y, por página, sus bloques de texto con bounding boxes.
- **FR-011 (M)** — *Given* una página con capa de texto que contiene tablas, *when* corre `extract_tables`, *then* cada tabla detectada queda en el estado como un `DataFrame` con su página, bbox e índice dentro de la página.
- **FR-012 (M)** — *Given* una tabla con encabezado de varias filas o celdas combinadas, *when* se extrae, *then* el `DataFrame` resultante tiene una sola fila de nombres de columna, únicos y no vacíos, construidos según BR-002.
- **FR-013 (M)** — *Given* celdas con números formateados como texto, *when* se extrae la tabla, *then* las columnas numéricas quedan con dtype numérico según BR-003, y las celdas vacías o marcadores de dato faltante quedan como `NaN` según BR-004.
- **FR-014 (M)** — *Given* una tabla que continúa en una o más páginas siguientes (BR-001), *when* corre `extract_tables`, *then* queda **una** tabla lógica con todas las filas en orden, sin filas de encabezado repetidas, y con todas las páginas en `source.pages`.
- **FR-015 (M)** — *Given* una página sin capa de texto, *when* corre `extract_tables`, *then* la página se saltea y se agrega un aviso `{page, reason: "sin capa de texto"}` al estado, que la UI muestra.
- **FR-016 (M)** — *Given* una página con una imagen que ocupa ≥ 20 % del área de la página, *when* corre `extract_tables`, *then* se agrega un aviso `{page, reason: "imagen grande no procesada (posible tabla en imagen)"}` y no se inventa contenido para ella.
- **FR-017 (M)** — *Given* una tabla detectada que no cumple los mínimos de BR-005, *when* corre `extract_tables`, *then* se descarta y se agrega un aviso con página y motivo.
- **FR-018 (M)** — *Given* una tabla extraída, *when* corre `collect_context`, *then* el estado guarda para esa tabla: texto inmediatamente arriba (epígrafe/título), texto inmediatamente abajo (notas al pie / fuente), y el título de sección más cercano, según BR-006.
- **FR-019 (M)** — *Given* una tabla con su contexto, *when* corre `enrich`, *then* el LLM devuelve, vía `with_structured_output` y el modelo Pydantic `TableEnrichment` (§7.3), `name`, `description`, `columns` y `row_headers`.
- **FR-020 (M)** — *Given* la salida de `enrich`, *when* corre `validate`, *then* se aplican todas las reglas de BR-007 y se guarda por tabla la lista de errores de validación.
- **FR-021 (M)** — *Given* una tabla con errores de validación y `intentos < MAX_ENRICH_RETRIES + 1`, *when* termina `validate`, *then* una arista condicional vuelve a `enrich` solo para las tablas inválidas, incluyendo en el prompt los errores como feedback.
- **FR-022 (M)** — *Given* una tabla que sigue inválida tras agotar los reintentos, *when* termina `validate`, *then* se aplica la reparación determinística de BR-008, la tabla se marca `metadata_quality="degraded"` y la UI lo muestra como aviso.
- **FR-023 (M)** — *Given* tablas validadas, *when* corre `index`, *then* por cada tabla se escriben `data/tables/<table_id>.parquet` y `data/tables/<table_id>.json` (TableRecord), se genera un embedding del texto de BR-009 y se hace upsert en Pinecone con el id `table_id` y la metadata de §7.4.
- **FR-024 (M)** — *Given* un PDF ya ingerido, *when* se vuelve a ingerir (aunque tenga otro nombre de archivo), *then* los `table_id` son los mismos (BR-010), los registros de ese `doc_hash` que ya no aparecen se borran, y la cantidad total de vectores del índice es la misma que tras la primera ingesta.
- **FR-025 (M)** — *Given* una ingesta terminada, *when* se muestra el resultado, *then* la UI lista por tabla: nombre, descripción, columnas (con descripción y unidad), encabezados de fila, vista previa de 5 filas, páginas de origen y `metadata_quality`, más la lista de avisos.
- **FR-026 (M)** — *Given* un PDF sin ninguna tabla legible, *when* termina la ingesta, *then* no se escribe nada en Pinecone y la UI muestra "No se encontraron tablas legibles" más los avisos.
- **FR-027 (M)** — *Given* un archivo que no es un PDF válido, está corrupto o está encriptado con contraseña, *when* corre `load_pdf`, *then* el grafo termina sin ejecutar los nodos siguientes, no se escribe nada en Pinecone ni en `TABLES_DIR`, y la UI muestra "No se pudo abrir el PDF: <motivo>".
- **FR-028 (S)** — *Given* un PDF con más de `MAX_TABLES_PER_PDF` tablas lógicas (default 40), *when* termina `extract_tables`, *then* solo se procesan las primeras `MAX_TABLES_PER_PDF` en orden de página y se agrega un aviso con la cantidad omitida.
- **FR-029 (S)** — *Given* una falla de Pinecone o de embeddings a mitad de `index`, *when* ocurre, *then* la UI informa cuántas tablas quedaron indexadas y que reingerir el mismo PDF completa la ingesta sin duplicados (FR-024).

### 4.3 Agente Analista

- **FR-030 (M)** — *Given* una pregunta, *when* corre `understand_query`, *then* el estado contiene un `QueryPlan` (§7.5) con la pregunta dividida en partes (`p1…pn`), y por parte: métricas, entidades, períodos, operación y 1–3 consultas de búsqueda.
- **FR-031 (M)** — *Given* un `QueryPlan`, *when* corre `retrieve`, *then* se consulta Pinecone con cada consulta de búsqueda (top-`RETRIEVAL_TOP_K`), se deduplican los resultados por `table_id` y se guardan como candidatas con su score.
- **FR-032 (M)** — *Given* que Pinecone no devuelve candidatas, *when* termina `retrieve`, *then* el grafo va directo a `respond` con estado `cannot_answer` y motivo "no hay tablas indexadas relacionadas".
- **FR-033 (M)** — *Given* candidatas, *when* corre `select_tables`, *then* el LLM recibe metadatos completos + vista previa de cada candidata y devuelve un `TableSelection` (§7.5): tablas elegidas (una o más) y, por cada parte, `coverage = covered | missing` con el motivo y la tabla relacionada si existe.
- **FR-034 (M)** — *Given* un `TableSelection` donde ninguna parte está `covered`, *when* termina `select_tables`, *then* el grafo va a `respond` sin generar código y el estado es `cannot_answer`.
- **FR-035 (M)** — *Given* partes `covered`, *when* corre `generate_code`, *then* el LLM recibe para cada tabla elegida su alias (`t1`, `t2`, …), los nombres **exactos** de columnas, dtypes, unidades, `df.head(5)` y las partes a calcular, y devuelve un programa que usa el diccionario `tables` provisto por el preámbulo (BR-013) e imprime como última línea el JSON de §7.6.
- **FR-036 (M)** — *Given* código generado, *when* corre `execute`, *then* se ejecuta en el sandbox (BR-012) y el estado guarda `stdout`, `stderr`, `returncode`, duración y el JSON parseado (o el error de parseo).
- **FR-037 (M)** — *Given* una ejecución con `returncode ≠ 0`, timeout, código rechazado por BR-012, o salida que no cumple §7.6, *when* termina `execute` y `intentos < MAX_CODE_RETRIES + 1`, *then* una arista condicional vuelve a `generate_code` con el código anterior y el error como feedback, e incrementa `code_attempts`.
- **FR-038 (M)** — *Given* que se agotaron los reintentos de código, *when* termina `execute`, *then* las partes que debía calcular pasan a `missing` con motivo "falló la ejecución tras N intentos: <último error resumido>" y el grafo va a `respond`.
- **FR-039 (M)** — *Given* el estado final de selección y ejecución, *when* corre `decide_status`, *then* el campo `status` del estado se calcula con la función determinística de BR-015, sin intervención del LLM.
- **FR-040 (M)** — *Given* el estado con `status`, *when* corre `respond`, *then* el LLM redacta una respuesta en español que incluye: los resultados numéricos (solo si hay), las tablas y páginas usadas, una explicación breve del cálculo, las conversiones aplicadas (BR-016) y, por cada parte `missing`, qué dato falta.
- **FR-042 (M)** — *Given* una candidata de Pinecone cuyo `<table_id>.parquet` no existe en `TABLES_DIR`, *when* termina `retrieve`, *then* esa candidata se descarta, se registra un aviso `"tabla <name> indexada pero sin datos locales; reingerir <file_name>"` que la UI muestra, y el flujo sigue con las restantes (si no queda ninguna aplica FR-032).
- **FR-041 (M)** — *Given* `status = cannot_answer`, *when* corre `respond`, *then* el estado no contiene resultados numéricos y la respuesta explica qué falta y, si hay una tabla relacionada, qué cubre (p. ej. "cubre 2019–2023; 2024 no está").

### 4.4 UI Streamlit

- **FR-050 (M)** — *Given* la pestaña "Subir PDF", *when* el usuario sube uno o más PDFs y confirma, *then* se ejecuta la ingesta de cada uno mostrando en vivo (stream de actualizaciones del grafo) el nodo en curso y sus contadores (tablas detectadas, intentos de validación).
- **FR-051 (M)** — *Given* la pestaña "Explorar tablas", *when* se abre, *then* se listan todas las tablas indexadas (desde `data/tables/*.json`) con nombre, descripción, fuente (archivo + páginas) y un expander con la tabla completa.
- **FR-052 (C)** — *Given* la pestaña "Explorar tablas", *when* el usuario pide borrar un documento, *then* se borran sus vectores de Pinecone y sus archivos en `data/tables/`.
- **FR-053 (M)** — *Given* la pestaña "Preguntar", *when* el usuario envía una pregunta, *then* se agrega al historial tipo chat de la sesión y la respuesta muestra: texto final, badge de estado, datos faltantes (si `status ≠ answered`), tablas seleccionadas en expanders con sus datos, código generado en bloque de código, salida de la ejecución y cantidad de reintentos (`code_attempts - 1`).
- **FR-056 (S)** — *Given* la pestaña "Preguntar", *when* el usuario envía una pregunta vacía (solo espacios) o de más de 1 000 caracteres, *then* no se ejecuta el Analista y se muestra un aviso de validación.
- **FR-054 (M)** — *Given* una excepción de LiteLLM, Pinecone, LangSmith o del grafo durante cualquier acción, *when* ocurre, *then* la UI muestra un `st.error` con el servicio y el mensaje resumido, la app sigue respondiendo y el historial de la sesión se conserva.
- **FR-055 (S)** — *Given* una respuesta del Analista, *when* se muestra, *then* incluye un link a la traza de LangSmith de esa ejecución.

### 4.5 Observabilidad

- **FR-060 (M)** — *Given* `LANGSMITH_TRACING=true`, *when* corre una ingesta, *then* aparece en `LANGSMITH_PROJECT` una traza raíz llamada `ingestion:<nombre_archivo>`, con un span hijo por cada nodo ejecutado (incluyendo cada repetición de `enrich`/`validate`) y metadata `doc_hash`, `n_tables`, `n_warnings`.
- **FR-061 (M)** — *Given* `LANGSMITH_TRACING=true`, *when* corre el Analista, *then* aparece una traza raíz llamada `analyst:<primeros 60 caracteres de la pregunta>` con un span por nodo y metadata `status`, `selected_table_ids`, `code_attempts`.

### 4.6 Evaluación y entregables

- **FR-070 (M)** — *Given* el repo, *when* se inspecciona `data/sample_pdfs/`, *then* hay ≥ 2 PDFs reales y públicos con capa de texto, al menos uno con una tabla que continúa en otra página, y en el README se cita la fuente (URL) de cada uno. PDFs elegidos (detalle, rangos de páginas y particularidades en `data/sample_pdfs/SOURCES.md`):
  - `indec_eph_mercado_trabajo_2t2026.pdf` — INDEC, *Mercado de trabajo. Tasas e indicadores socioeconómicos (EPH). Segundo trimestre de 2026* (26 págs., completo): https://www.indec.gob.ar/uploads/informesdeprensa/mercado_trabajo_eph_2trim26433FCBC5A8.pdf
  - `indec_eph_mercado_trabajo_2t2025.pdf` — INDEC, ídem, *Segundo trimestre de 2025* (23 págs., completo): https://www.indec.gob.ar/uploads/informesdeprensa/mercado_trabajo_eph_2trim25C42A813B2A.pdf
  - `cepal_anuario_estadistico_2025_poblacion_trabajo.pdf` — CEPAL, *Anuario Estadístico de América Latina y el Caribe 2025*, extracto de las págs. 15–24 de 175 (1.1 Población y 1.2 Trabajo); contiene la tabla multipágina (cuadro 1.1.1, págs. 3–4 del extracto): https://repositorio.cepal.org/server/api/core/bitstreams/b2476d6d-34cd-4e70-a7b7-d39e244a9070/content
- **FR-071 (M)** — *Given* el repo, *when* se inspecciona `eval/questions.yaml`, *then* hay ≥ 8 preguntas, cada una con `expected_status`, `expected_answer` (valor y unidad, o `null`), tolerancia, y tablas/páginas esperadas; con al menos 2 multitabla, al menos 1 sin ninguna tabla relacionada (`cannot_answer`) y al menos 1 con tabla relacionada pero dato faltante (`cannot_answer` o `partial`).
- **FR-072 (S)** — *Given* `eval/questions.yaml` y los PDFs ingeridos, *when* se corre `python -m eval.run_eval`, *then* se ejecuta el Analista sobre cada pregunta y se genera la tabla de resultados de `EVALUATION.md` (estado esperado vs obtenido, valor esperado vs obtenido, correcto/incorrecto, reintentos, link de traza).
- **FR-073 (M)** — *Given* el repo, *when* se lee `EVALUATION.md`, *then* contiene los resultados de todas las preguntas de prueba, un análisis honesto de errores y limitaciones, y 3 links (o capturas) de trazas representativas de LangSmith: una ingesta, una respuesta `answered` multitabla con ≥ 1 reintento si existe, y una `cannot_answer`/`partial`.
- **FR-074 (M)** — *Given* el repo, *when* se lee el README, *then* contiene: instalación y ejecución, diagrama Mermaid de ambos grafos (generado con `draw_mermaid()`), decisiones de diseño (PyMuPDF, Parquet + JSON como almacén, una tabla = un vector, enfoque de sandbox), limitaciones conocidas, créditos y el link al video demo (≤ 5 min).

## 5. Business Rules

- **BR-001 — Continuación de tablas.** Una tabla T₂ en la página *n+1* es continuación de la tabla lógica T₁ cuyo último fragmento está en la página *n* si se cumplen **todas**: (a) T₂ es la primera tabla de la página *n+1*; (b) el último fragmento de T₁ es la última tabla de la página *n*; (c) misma cantidad de columnas; (d) la fila de encabezado de T₂ es igual a la de T₁ tras normalizar (minúsculas, espacios colapsados) **o** T₂ no tiene fila de encabezado (su primera fila tiene ≥ 50 % de celdas numéricas). Si T₂ repite el encabezado, esa fila se descarta. La regla se aplica en cadena (una tabla puede abarcar ≥ 3 páginas). *Razón:* es la definición del enunciado (§5.1). *Excepción:* si T₂ tiene encima, en la página *n+1* (fuera de la franja de encabezado/pie de página), su propio epígrafe —un bloque que empieza con `Cuadro|Tabla|Table|Gráfico|Figura|Figure|Mapa` + número, o con una numeración tipo `1.1.4 `— no es continuación aunque cumpla las cuatro condiciones (en los informes de INDEC todas las tablas comparten encabezado, y sin esta excepción se unen cuadros distintos). Tablas que no cumplen las cuatro condiciones quedan separadas.
- **BR-002 — Aplanado de encabezados.** Encabezados de varias filas se combinan por columna uniendo los niveles no vacíos con `" - "` (p. ej. `"2023 - Ene"`); las celdas combinadas horizontalmente se propagan hacia la derecha (forward-fill) antes de unir. Nombres vacíos se reemplazan por `col_<i>`; duplicados reciben sufijo `_2`, `_3`. *Razón:* el código generado necesita nombres únicos y estables. *Excepción:* ninguna.
- **BR-003 — Normalización numérica.** Una columna se convierte a numérica si ≥ 80 % de sus celdas no vacías parsean como número. El parseo admite: separadores de miles `.`, `,`, espacio o espacio fino; decimal `,` o `.` (se infiere por columna: si algún valor tiene el patrón `\d{1,3}(\.\d{3})+,\d+` o `\d+,\d{1,2}$` sin otro separador, el decimal es `,`); símbolos `$`, `US$`, `€`, `%`; paréntesis como negativo `(1.234)` → `-1234`; signo `−` unicode. Las unidades removidas (`%`, moneda) se registran en el JSON de la tabla como `unit_hint` de la columna. *Razón:* los PDFs reales argentinos usan formato es-AR; los en inglés, en-US. *Excepción:* columnas de años (enteros 1900–2100 en el encabezado o en todas las celdas) se mantienen como enteros sin separadores.
- **BR-004 — Celdas vacías.** Celdas vacías y los marcadores `-`, `–`, `—`, `s/d`, `n/d`, `...`, `///`, `x` (solo) se convierten a `NaN`. *Razón:* son convenciones habituales de "sin dato" en publicaciones estadísticas; nunca se interpretan como 0. *Excepción:* ninguna.
- **BR-005 — Mínimos de tabla.** Se descarta una tabla detectada si tiene < 2 filas de datos, < 2 columnas, o > 60 % de celdas vacías tras BR-004. *Razón:* evita indexar falsos positivos de `find_tables` (recuadros, layouts). *Excepción:* ninguna.
- **BR-006 — Contexto.** Para cada tabla: *arriba* = hasta 3 bloques de texto cuyo borde inferior está ≤ 120 pt sobre el borde superior de la tabla (el más cercano primero); *abajo* = hasta 2 bloques cuyo borde superior está ≤ 80 pt bajo la tabla; *sección* = el último bloque anterior en el documento con tamaño de fuente ≥ 1,15 × la mediana del documento o en negrita, con ≤ 120 caracteres. Si una página no tiene capa de texto se aplica FR-015 y no FR-016. Para tablas multipágina se usa el contexto superior de la primera página y el inferior de la última. El contexto total por tabla se trunca a 2 000 caracteres. *Razón:* acota tokens y captura epígrafe, título, fuente y notas. *Excepción:* ninguna.
- **BR-007 — Validación de metadatos.** `TableEnrichment` es válido si: (1) `len(columns) == n_cols` del DataFrame; (2) los `columns[i].name` coinciden exactamente y en orden con las columnas del DataFrame; (3) cada `row_headers` existe (comparando con `strip()`) en la primera columna de dtype texto del DataFrame, y si el DataFrame no tiene columna de texto `row_headers` es `[]`; (4) `name` tiene 3–80 caracteres; (5) `description` tiene 2–4 oraciones (cantidad de coincidencias de `[.!?](?=\s|$)`) y ≤ 600 caracteres; (6) cada `columns[i].dtype` es compatible con el dtype real (numérico ↔ `number|integer|percent|currency|year`). *Razón:* el enunciado exige coherencia metadatos-tabla y el Analista depende de nombres exactos. *Excepción:* ninguna.
- **BR-008 — Reparación determinística.** Si una tabla agota reintentos: se conservan `name` y `description` del último intento (o `"Tabla p<página> #<i> de <archivo>"` y el contexto superior truncado si están vacíos), las `columns` se reconstruyen desde el DataFrame (nombre exacto, dtype real, descripción del LLM si el nombre coincidía, si no `""`), y `row_headers` se filtra a los que existen. *Razón:* no perder tablas por un fallo del LLM sin inventar estructura. *Excepción:* ninguna.
- **BR-009 — Texto para embeddings.** Una tabla = un vector. Texto: `name` + `\n` + `description` + `\nColumnas: ` + `nombre (unidad): descripción` por columna + `\nFilas: ` + primeros 50 `row_headers` + `\nFuente: <archivo>, págs. <páginas>` + `\nSección: <sección>`. *Razón:* las tablas son unidades de recuperación; se busca por qué mide la tabla, no por sus valores. *Excepción:* ninguna.
- **BR-010 — Identidad de tablas.** `doc_hash = sha256(bytes_del_pdf)`; `table_id = f"{doc_hash[:16]}-p{primera_pagina:03d}-t{indice_en_pagina}"` (página 1-based, índice 0-based). Al reingerir un `doc_hash`, se listan los ids con prefijo `doc_hash[:16]` en Pinecone y se borran los que no están en la nueva ingesta, y se reemplazan los archivos locales. *Razón:* idempotencia exigida (§8 del enunciado). *Excepción:* ninguna.
- **BR-011 — Límites de reintentos.** `MAX_ENRICH_RETRIES` (default 2) y `MAX_CODE_RETRIES` (default 3) son configurables por entorno y acotan el total de intentos a `1 + MAX_*`. *Razón:* presupuesto del proxy y prohibición de ciclos sin límite. *Excepción:* ninguna.
- **BR-012 — Sandbox.** El código generado: (a) pasa primero un chequeo AST que rechaza imports fuera de la allowlist {`pandas`, `numpy`, `math`, `statistics`, `json`, `datetime`, `re`, `collections`, `itertools`, `functools`, `decimal`, `fractions`} y el uso de `open`, `exec`, `eval`, `compile`, `__import__`, `input`, `globals`, `getattr` sobre módulos, atributos que empiezan con `__`, builtins de introspección, funciones de pandas/numpy que leen o escriben archivos (`read_*`, `to_*` de archivo, `np.save`/`np.load`…) y atributos "pivote" hacia módulos del sistema (`os`, `sys`, `builtins`, `io`, `subprocess`…), porque pandas/pyarrow acceden a archivos sin pasar por `open` y reexportan `os` (D-026); (b) se ejecuta con `python -I` en un subproceso con directorio de trabajo temporal propio, que contiene solo los archivos Parquet de las tablas seleccionadas; (c) recibe un entorno mínimo (`PATH`, `SYSTEMROOT`, `SYSTEMDRIVE`, `TEMP`, `TMP`) sin ninguna variable de `.env` (`SYSTEMDRIVE` evita que Windows cree carpetas de caché en el directorio de trabajo; D-027); (d) el preámbulo, después de cargar las tablas, reemplaza `socket.socket`, `socket.create_connection` y `builtins.open` (no `io.open`, que pandas necesita para fechas con zona horaria; el código generado no puede importar `io`) por funciones que lanzan `PermissionError`; (e) se mata si supera `SANDBOX_TIMEOUT_S` (default 30); (f) su stdout/stderr se truncan a 20 000 caracteres. *Razón:* el enunciado exige subproceso + timeout + sin red ni FS más allá de las tablas. *Excepción:* ninguna; la limitación de que no es una frontera de seguridad del SO se documenta en el README.
- **BR-013 — Preámbulo de carga.** El sistema (no el LLM) antepone al código un preámbulo fijo que define `tables: dict[str, pd.DataFrame]` con alias `t1…tn` cargados con `pd.read_parquet` (los dtypes vienen preservados en el propio archivo). El código generado no lee archivos. *Razón:* elimina rutas adivinadas y permite bloquear `open`. *Excepción:* ninguna.
- **BR-014 — Respondibilidad.** El Analista solo usa valores presentes en las tablas seleccionadas. Una parte se marca `missing` cuando: falta un período o entidad (no se interpola, extrapola ni se usa el año más cercano); la conversión requerida necesita datos externos (tipo de cambio, inflación, población); falta el valor de comparación pedido (no se sustituye por un valor calculado, p. ej. promedio de regiones, salvo que la pregunta lo defina así); o el significado/unidad de una columna es ambiguo y cambia el resultado (p. ej. nominal vs real no indicado). *Razón:* §6.4 del enunciado. *Excepción:* conversiones permitidas por BR-016.
- **BR-015 — Decisión de estado.** Sea `P` el conjunto de partes del `QueryPlan` y `A ⊆ P` las partes con resultado válido en la salida del código (y `covered` en la selección). `status = answered` si `A = P`; `partial` si `∅ ≠ A ⊊ P`; `cannot_answer` si `A = ∅`. Las partes que el código reporta en `missing` no cuentan en `A`. Se implementa como función pura `decide_status(plan, selection, execution) -> Status` y su resultado se guarda en el estado del grafo. *Razón:* el enunciado exige que el estado lo decida la lógica del agente y sea testeable. *Excepción:* ninguna.
- **BR-016 — Conversiones permitidas.** Sin datos externos se permite: cambio de escala (miles ↔ millones, etc.), porcentaje ↔ fracción, suma de valores mensuales/trimestrales para obtener un año **solo si están todos los períodos**, y operaciones aritméticas entre columnas de igual unidad. Cada conversión aplicada se declara en la salida del código (`conversions`) y en la respuesta. *Razón:* §6.4. *Excepción:* ninguna.

## 6. Non-Functional Requirements

| ID | Requisito | Umbral medible |
|---|---|---|
| NFR-001 | Versión de Python | Corre en CPython 3.11, 3.12 y 3.13. |
| NFR-002 | Setup reproducible | En un venv nuevo, `pip install -r requirements.txt` (todas las dependencias con `==`) termina sin errores y `streamlit run app.py` levanta la app con solo completar `.env`. |
| NFR-003 | Secretos | `git grep -nE "(sk-[A-Za-z0-9]{16,}|pcsk[_]|lsv2[_])"` sobre el commit entregado devuelve 0 resultados; `.env` está en `.gitignore`; `.env.example` lista todas las variables de §7.1 sin valores. |
| NFR-004 | Timeout del sandbox | Un programa con loop infinito termina (proceso hijo muerto) en ≤ `SANDBOX_TIMEOUT_S + 2` s. |
| NFR-005 | Tamaño de metadata Pinecone | Cada registro serializado ocupa ≤ 35 KB (margen bajo el límite de 40 KB); si no, se truncan `preview` y `row_headers` en ese orden. |
| NFR-006 | Presupuesto LLM — Analista | Una pregunta hace ≤ `4 + MAX_CODE_RETRIES` llamadas de chat (understand, select, generate × intentos, respond) y ≤ 3 llamadas de embeddings. |
| NFR-007 | Presupuesto LLM — Ingesta | Por tabla, ≤ `1 + MAX_ENRICH_RETRIES` llamadas de chat; embeddings en lotes (≤ 1 llamada cada 64 tablas). |
| NFR-008 | Latencia Ingesta | Un PDF de ≤ 30 páginas y ≤ 15 tablas se ingiere en ≤ 5 min con el modelo elegido (medido en la corrida de evaluación). |
| NFR-009 | Latencia Analista | Mediana ≤ 60 s y máximo ≤ 180 s por pregunta sobre el set de `eval/questions.yaml`. |
| NFR-010 | Calidad de código | `ruff check .` sin errores; todas las funciones públicas con type hints; los estados de los grafos son `TypedDict` y los payloads LLM modelos Pydantic. |
| NFR-011 | Tests | `pytest -m "not integration"` pasa sin red ni claves en ≤ 60 s. |
| NFR-012 | Exactitud del sistema | En el set de evaluación, ≥ 75 % de las preguntas con estado correcto **y** valor dentro de la tolerancia (objetivo, se informa el valor real obtenido aunque no se alcance). |

## 7. Data & Interfaces

### 7.1 Variables de entorno

| Variable | Obligatoria | Default | Uso |
|---|---|---|---|
| `LITELLM_BASE_URL` | Sí | — | Base URL del proxy (OpenAI-compatible). |
| `LITELLM_API_KEY` | Sí | — | Clave personal del proxy. |
| `LLM_MODEL` | Sí | — | Modelo de chat. Elegido: `gpt-5.4-mini` (con `temperature=0`). |
| `EMBEDDING_MODEL` | Sí | — | Modelo de embeddings. Elegido: `text-embedding-3-small`. |
| `EMBEDDING_DIM` | Sí | — | Dimensión del modelo de embeddings. Para `text-embedding-3-small`: `1536`. |
| `PINECONE_API_KEY` | Sí | — | |
| `PINECONE_INDEX_NAME` | Sí | `mas-2026s2-tables` | |
| `PINECONE_CLOUD` / `PINECONE_REGION` | No | `aws` / `us-east-1` | Para crear el índice serverless. |
| `LANGSMITH_TRACING` | Sí | `true` | |
| `LANGSMITH_API_KEY` | Sí | — | |
| `LANGSMITH_PROJECT` | Sí | `mas-2026s2-<legajo>` | |
| `TABLES_DIR` | No | `data/tables` | Almacén Parquet + JSON. |
| `MAX_ENRICH_RETRIES` | No | `2` | BR-011. |
| `MAX_CODE_RETRIES` | No | `3` | BR-011. |
| `RETRIEVAL_TOP_K` | No | `5` | FR-031. |
| `SANDBOX_TIMEOUT_S` | No | `30` | BR-012. |
| `LLM_CACHE` | No | `false` | FR-005. |
| `MAX_TABLES_PER_PDF` | No | `40` | FR-028. |

### 7.2 Almacén de tablas (`TABLES_DIR`)

- `<table_id>.parquet` — datos normalizados (motor `pyarrow`, sin índice, nombres de columna `str`). Fuente de verdad de los datos; preserva los dtypes (numéricos, enteros, texto, `NaN`), así que recargar no requiere reinferir tipos.
- `<table_id>.json` — `TableRecord` (§7.3), con `dtypes` por columna como información para prompts y UI (no se usan para recargar).
- El catálogo para la pestaña Explorar es el conjunto de `*.json`. El directorio está en `.gitignore`.

### 7.3 Modelos de Ingesta (Pydantic)

```text
ColumnMeta      { name: str; description: str; dtype: "number"|"integer"|"percent"|"currency"|"year"|"date"|"text"; unit: str | None }
TableEnrichment { name: str; description: str; columns: list[ColumnMeta]; row_headers: list[str] }      # lo que devuelve el LLM
TableRecord     { table_id: str; doc_hash: str; name; description; columns; row_headers;
                  source: { file_name: str; pages: list[int] }; section: str | None;
                  n_rows: int; n_cols: int; dtypes: dict[str, str];
                  metadata_quality: "ok"|"degraded"; validation_attempts: int; data_path: str }
IngestionWarning{ page: int | None; table_ref: str | None; reason: str }
```

`table_id` y `source` **no** los genera el LLM: los asigna el código.

Estado del grafo de Ingesta (`TypedDict`): `file_name`, `pdf_bytes`, `doc_hash`, `pages`, `tables` (lista de borradores con DataFrame, páginas, bbox, contexto, enrichment, errores, intentos), `warnings`, `records` (TableRecord indexados).

### 7.4 Registro en Pinecone

- **id:** `table_id`; **namespace:** default; **vector:** embedding de BR-009.
- **metadata:** `table_id`, `doc_hash`, `name`, `description`, `columns_json` (JSON string de `ColumnMeta[]`), `row_headers` (list[str], truncada), `file_name`, `pages` (list[str] — Pinecone solo admite listas de strings en metadata; se decodifica a `list[int]` al leer), `section` (`""` si no hay), `n_rows`, `n_cols`, `preview_csv` (primeras 5 filas serializadas como texto CSV, solo para el prompt de selección), `metadata_quality`, `embedding_text`.

### 7.5 Modelos del Analista (Pydantic)

```text
QueryPart     { id: "p1".."pn"; text: str; metrics: list[str]; entities: list[str]; periods: list[str]; operation: str; search_queries: list[str] (1..3) }
QueryPlan     { parts: list[QueryPart] (1..5) }
PartCoverage  { part_id: str; coverage: "covered"|"missing"; table_ids: list[str]; missing_reason: str | None; related_table_note: str | None }
TableSelection{ selected_table_ids: list[str]; coverage: list[PartCoverage]; rationale: str }
```

Estado del grafo Analista (`TypedDict`): `question`, `plan`, `candidates`, `selection`, `code`, `code_attempts`, `execution` (stdout, stderr, returncode, duration_s, parsed, error), `results`, `missing`, `status`, `answer`, `run_url`.

### 7.6 Contrato de salida del código generado

Última línea de stdout, JSON en una línea:

```json
{"results": [{"part_id": "p1", "label": "Crecimiento interanual promedio 2021-2024", "value": 12.34, "unit": "%"}],
 "missing": [{"part_id": "p2", "reason": "La tabla cubre 2019-2023; falta 2024"}],
 "conversions": ["miles de $ → millones de $"]}
```

Válido si: es JSON parseable; cada `part_id` de las partes `covered` aparece exactamente una vez entre `results` y `missing`; cada `value` es número finito, lista de números finitos o dict `str → número finito`.

### 7.7 Interfaces internas

- `ingest_pdf(file_name: str, pdf_bytes: bytes) -> IngestionResult` y `ingestion_graph.stream(...)`.
- `ask(question: str) -> AnalystResult` y `analyst_graph.stream(...)`.
- `run_in_sandbox(code: str, tables: dict[str, Path], timeout_s: int, static_check: bool = True) -> ExecutionResult` (`static_check=False` solo se usa en tests, para verificar las defensas de runtime de BR-012).
- `decide_status(plan, selection, execution) -> Literal["answered","partial","cannot_answer"]`.

## 8. Verification Criteria

Etiquetas: **[U]** test unitario sin red; **[I]** test de integración con servicios reales (`@pytest.mark.integration`); **[E]** corrida de evaluación; **[M]** verificación manual con evidencia (captura/traza).

### Configuración
- VC-001 [U] (FR-001): `grep` de imports en el código → solo `langchain_openai.ChatOpenAI`/`OpenAIEmbeddings` construidos en `services/llm.py`, con `base_url=LITELLM_BASE_URL`.
- VC-002 [U] (FR-002): cargar config sin `PINECONE_API_KEY` → `ConfigError` cuyo mensaje contiene `PINECONE_API_KEY`; la app renderiza el error sin traceback.
- VC-003 [I] (FR-003): índice inexistente + primera ingesta → índice creado con dimensión `EMBEDDING_DIM` y métrica `cosine`.
- VC-004 [U] (FR-004): índice simulado con dimensión 1536 y `EMBEDDING_DIM=3072` → error que muestra ambas dimensiones; 0 upserts.
- VC-005 [U] (FR-005): `LLM_CACHE=true`, misma llamada dos veces con un LLM fake contador → el contador vale 1.

### Ingesta
- VC-010 [U] (FR-010): PDF de fixture → `doc_hash` igual a `sha256` de los bytes; `pages[i].blocks` no vacío para páginas con texto.
- VC-011 [U] (FR-011): fixture con 2 tablas en una página → 2 DataFrames con `page` y `index_on_page` 0 y 1.
- VC-012 [U] (FR-012, BR-002): encabezado de 2 filas `["", "2023", ""] / ["Región", "Ene", "Feb"]` con celda combinada → columnas `["Región", "2023 - Ene", "2023 - Feb"]`; encabezado con dos vacíos → `col_0`, `col_1`; duplicados → `X`, `X_2`.
- VC-013 [U] (FR-013, BR-003): `"1.234,5"`→1234.5; `"1,234.5"`→1234.5; `"(1.234)"`→-1234; `"12,5%"`→12.5 con `unit_hint="%"`; `"US$ 3.000"`→3000; `"−7"`→-7; columna con 70 % numéricos → queda texto; columna de años `"2021","2022"` → int.
- VC-014 [U] (FR-013, BR-004): celdas `""`, `"-"`, `"s/d"`, `"..."` → `NaN`, nunca 0.
- VC-015 [U] (FR-014, BR-001): tabla de 3 páginas que repite encabezado → 1 tabla lógica, filas = suma de filas de datos, sin encabezado repetido, `pages=[4,5,6]`.
- VC-016 [U] (BR-001): continuación sin encabezado (primera fila numérica) → se une; misma página siguiente pero distinta cantidad de columnas → quedan 2 tablas; tabla que no es la primera de la página siguiente → no se une; primera tabla de la página siguiente con mismo encabezado y columnas pero con epígrafe propio ("Cuadro 2.3 …") encima → no se une.
- VC-017 [U] (FR-015): página sin texto (solo imagen) → aviso `"sin capa de texto"` con su número; 0 tablas de esa página.
- VC-018 [U] (FR-016): página con imagen del 30 % del área → aviso `"imagen grande no procesada…"`; imagen del 10 % → sin aviso.
- VC-019 [U] (FR-017, BR-005): tabla de 1 fila de datos / 1 columna / 70 % vacía → descartada con aviso; tabla de 2×2 completa → conservada.
- VC-020 [U] (FR-018, BR-006): fixture con "Cuadro 3. …" 40 pt arriba, párrafo 300 pt arriba y "Fuente: INDEC" 20 pt abajo → `above` contiene "Cuadro 3", no el párrafo lejano; `below` contiene "Fuente: INDEC"; contexto ≤ 2 000 caracteres.
- VC-021 [I] (FR-019): tabla real + contexto → `TableEnrichment` parseado sin excepción, con `len(columns) == n_cols`.
- VC-022 [U] (FR-020, BR-007): un caso válido → 0 errores; un caso por cada regla 1–6 violada → exactamente el error correspondiente.
- VC-023 [U] (FR-021): LLM fake que devuelve columnas incorrectas en el intento 1 y correctas en el 2 → el grafo pasa 2 veces por `enrich`, el segundo prompt contiene el texto del error, `metadata_quality="ok"`, `validation_attempts=2`.
- VC-024 [U] (FR-022, BR-008, BR-011): LLM fake siempre inválido, `MAX_ENRICH_RETRIES=2` → exactamente 3 llamadas a `enrich`, columnas reconstruidas idénticas a las del DataFrame, `metadata_quality="degraded"`, aviso presente.
- VC-025 [U] (FR-023, BR-009): `index` con Pinecone fake → por tabla existen `<id>.parquet` y `<id>.json`, y `pd.read_parquet` devuelve un DataFrame igual (`assert_frame_equal`, dtypes incluidos) al indexado; upsert con id `table_id`; `embedding_text` contiene name, description, cada columna y "Fuente:".
- VC-026 [U] (NFR-005): tabla de 5 000 filas y 300 row headers → metadata serializada ≤ 35 KB.
- VC-027 [I] (FR-024, BR-010): ingerir PDF A → N vectores; reingerir A renombrado → mismos `table_id`, total de vectores del índice sin cambios (se lee `describe_index_stats` reintentando hasta 30 s por la consistencia eventual de Pinecone serverless).
- VC-028 [U] (BR-010): ingesta previa con ids {a,b,c}, nueva con {a,b} → `c` borrado de Pinecone fake y de `TABLES_DIR`.
- VC-029 [M] (FR-025): ingerir un PDF de muestra → la UI muestra por tabla nombre, descripción, columnas, filas, vista previa de 5 filas, páginas y calidad (captura en README/video).
- VC-030 [U] (FR-026): PDF de fixture sin tablas → 0 upserts y resultado con mensaje "No se encontraron tablas legibles".
- VC-031 [U] (FR-027): bytes de un `.txt`, un PDF truncado y un PDF encriptado → cada uno termina en `load_pdf` con error "No se pudo abrir el PDF", 0 upserts, `TABLES_DIR` sin cambios.
- VC-032 [U] (FR-028): fixture con 5 tablas y `MAX_TABLES_PER_PDF=3` → 3 tablas enriquecidas, aviso "2 tablas omitidas".
- VC-033 [U] (FR-029): Pinecone fake que falla en el upsert de la 2.ª de 3 tablas → error informado con "1 de 3 tablas indexadas"; reingerir con Pinecone sano → 3 vectores en total.

### Analista
- VC-040 [I] (FR-030): "¿Qué región tuvo el mayor desempleo en 2023 y cuánto superó al promedio nacional?" → `QueryPlan` con 2 partes, cada una con ≥ 1 `search_query`.
- VC-056 [U] (FR-042): Pinecone fake devuelve 2 candidatas, una sin Parquet local → queda 1 candidata y el aviso nombra la tabla faltante; si ambas faltan → `cannot_answer`.
- VC-041 [U] (FR-031): Pinecone fake devuelve la misma tabla para 2 consultas → 1 candidata, con el score máximo.
- VC-042 [U] (FR-032): Pinecone fake vacío → no se llama a `select_tables` ni a `generate_code`; `status="cannot_answer"`.
- VC-043 [I] (FR-033): pregunta multitabla del set → `selected_table_ids` contiene ≥ 2 tablas y todas las partes `covered`.
- VC-044 [U] (FR-034): selección fake con todas las partes `missing` → 0 llamadas a `generate_code`; `status="cannot_answer"`; `results=[]`.
- VC-045 [U] (FR-035, BR-013): prompt de `generate_code` contiene los nombres exactos de columnas y dtypes de cada alias; el programa ejecutado accede a `tables["t1"]` sin abrir archivos.
- VC-046 [U] (FR-036): código `print('{"results":[...],"missing":[],"conversions":[]}')` válido → `execution.parsed` igual al dict, `returncode=0`.
- VC-047 [U] (FR-037): LLM fake devuelve código con `KeyError` y luego código correcto → 2 llamadas a `generate_code`, el segundo prompt contiene `KeyError`, `code_attempts=2`, `status="answered"`.
- VC-048 [U] (FR-037, §7.6): salida sin JSON final / JSON sin una parte `covered` / `value=NaN` → cada caso cuenta como inválido y dispara reintento.
- VC-049 [U] (FR-038, BR-011): LLM fake siempre roto, `MAX_CODE_RETRIES=3` → exactamente 4 llamadas a `generate_code`; partes pasan a `missing` con "falló la ejecución tras 4 intentos"; `status` según BR-015.
- VC-050 [U] (FR-039, BR-015): tabla de verdad de `decide_status`: todas las partes con resultado → `answered`; 1 de 2 → `partial`; 0 → `cannot_answer`; parte `covered` pero reportada en `missing` por el código → no cuenta como respondida.
- VC-051 [I] (FR-040, BR-016): pregunta que requiere miles → millones → respuesta menciona la conversión, la tabla y las páginas usadas.
- VC-052 [U] (FR-041): estado final `cannot_answer` → `results == []` y la UI no renderiza el bloque de valores.
- VC-053 [E] (BR-014): pregunta del set sobre un año fuera de rango → `cannot_answer` o `partial` con `missing_reason` que nombra el período, sin valor interpolado.
- VC-054 [E] (BR-014): pregunta que pide valor en USD sobre tabla en pesos sin tabla de tipo de cambio → `missing` por conversión externa.
- VC-055 [E] (BR-014): pregunta "vs el promedio nacional" sin promedio nacional en las tablas → `missing` por valor de comparación; no se devuelve el promedio de las regiones.

### Sandbox
- VC-060 [U] (BR-012a): código con `import os`, `import requests`, `open("x")`, `__import__("os")`, `().__class__.__bases__` → rechazado antes de ejecutar, con el motivo en `execution.error`.
- VC-061 [U] (BR-012c): con las variables de `.env` cargadas en el proceso padre, `run_in_sandbox` con el chequeo AST desactivado (modo test) ejecuta `import os, json; print(json.dumps(sorted(os.environ)))` → la lista no contiene `LITELLM_API_KEY`, `PINECONE_API_KEY` ni `LANGSMITH_API_KEY`.
- VC-062 [U] (BR-012d): con el AST check desactivado (modo test), código que abre un socket a `1.1.1.1:80` → `PermissionError` en stderr; código que hace `open("C:/Windows/win.ini")` → `PermissionError`.
- VC-063 [U] (BR-012e, NFR-004): `while True: pass` con timeout 2 s → resultado `timeout` en ≤ 4 s y proceso hijo inexistente.
- VC-065 [U] (BR-012f): código que imprime 100 000 caracteres → `stdout` guardado de 20 000 caracteres y marcado como truncado.
- VC-064 [U] (BR-012b): directorio de trabajo del hijo contiene solo los archivos Parquet de las tablas seleccionadas.

### UI
- VC-070 [M] (FR-050): subir un PDF → se ven en vivo los nodos `load_pdf → … → index` y contadores.
- VC-071 [M] (FR-051): pestaña Explorar lista todas las tablas de `TABLES_DIR` con nombre, descripción, fuente y expander con datos.
- VC-072 [M] (FR-052): borrar un documento → desaparece de Explorar y su cantidad de vectores baja a 0.
- VC-073 [M] (FR-053): hacer 2 preguntas seguidas → ambas en el historial; cada respuesta muestra texto, badge de estado, faltantes, tablas, código, salida y reintentos.
- VC-074 [U] (FR-054): wrapper de UI con un grafo fake que lanza `openai.APIConnectionError` / excepción de Pinecone → se llama a `st.error` con el nombre del servicio; no se propaga la excepción; el historial previo sigue en `session_state`.
- VC-076 [U] (FR-056): pregunta `"   "` y pregunta de 1 001 caracteres → el grafo no se invoca; pregunta de 1 000 caracteres → se invoca.
- VC-075 [M] (FR-055): respuesta del Analista muestra link que abre la traza correcta en LangSmith.

### Observabilidad
- VC-080 [I] (FR-060): tras una ingesta, `langsmith.Client().list_runs(project_name=..., is_root=True)` devuelve un run `ingestion:<archivo>` con hijos llamados como cada nodo y metadata `doc_hash`, `n_tables`, `n_warnings`.
- VC-081 [I] (FR-061): tras una pregunta, run raíz `analyst:<pregunta>` con hijos por nodo (incluye repeticiones de `generate_code`/`execute`) y metadata `status`, `selected_table_ids`, `code_attempts`.

### Entregables y NFR (verificación de punta a punta)
- VC-090 [M] (FR-070): `data/sample_pdfs/` tiene ≥ 2 PDFs; el README cita la URL pública de cada uno; abrir cada PDF permite seleccionar texto; uno tiene una tabla que cruza página (y VC-015 equivalente pasa sobre él como test [I]).
- VC-091 [U] (FR-071): test que carga `eval/questions.yaml` → ≥ 8 preguntas, campos obligatorios presentes, ≥ 2 con ≥ 2 tablas esperadas, ≥ 1 `cannot_answer` sin tablas esperadas, ≥ 1 `cannot_answer`/`partial` con tablas esperadas.
- VC-092 [E] (FR-072, NFR-009, NFR-012): `python -m eval.run_eval` → escribe la tabla en `EVALUATION.md` con una fila por pregunta, mediana/máximo de latencia y porcentaje de aciertos.
- VC-093 [M] (FR-073): `EVALUATION.md` contiene 3 links/capturas de trazas de los tipos pedidos y una sección de errores y limitaciones.
- VC-094 [M] (FR-074): README contiene las secciones listadas y los diagramas Mermaid coinciden con `draw_mermaid()` del código del commit.
- VC-095 [M] (NFR-001..003, NFR-010, NFR-011): en un clon limpio: `python -m venv .venv` + `pip install -r requirements.txt` OK; `ruff check .` OK; `pytest -m "not integration"` OK en ≤ 60 s; `git grep` de secretos vacío.
- VC-096 [E] (NFR-006, NFR-007, NFR-008): en la corrida de evaluación, el conteo de llamadas LLM por run (desde LangSmith) respeta los máximos y la ingesta de cada PDF de muestra cumple el tiempo.

## 9. Open Questions

Ninguna.

## 10. Assumptions

- A-1: La app es local y monousuario; no hay concurrencia sobre `TABLES_DIR` ni sobre el índice.
- A-2: Los PDFs de muestra están en español o inglés y pesan ≤ 20 MB.
- A-3: Un único índice Pinecone y namespace por defecto alcanzan (plan Starter).
- A-4: `find_tables()` de PyMuPDF detecta las tablas de los PDFs elegidos; si no, se evalúa `pdfplumber` como alternativa y se documenta. *Validada (v0.5):* `find_tables()` detecta todas las tablas de datos de los tres PDFs de FR-070; los encabezados que los PDFs de INDEC dibujan aparte de la grilla se reconstruyen a partir de las palabras y rayas de la página, sin otra librería.
- A-5: El modelo elegido soporta salida estructurada vía el proxy (validado con `notebooks/00_litellm_exploracion.ipynb`: `gpt-5.4-mini` pasa `json_schema`, `function_calling`, tool calling y codegen).
- A-6: El presupuesto de la clave LiteLLM alcanza para desarrollo con caché + 2–3 corridas completas de evaluación.

## 11. Changelog

- 0.8 (2026-10-07): BR-012 refleja el sandbox implementado: chequeo AST más estricto, `SYSTEMDRIVE`/`TMP` en el entorno, `io.open` no se bloquea (D-026, D-027).
- 0.7 (2026-10-07): §7.4 corrige el tipo de `pages` en la metadata de Pinecone (list[str], restricción de Pinecone; D-018).
- 0.6 (2026-10-07): BR-001 incorpora la excepción por epígrafe propio (D-015, ratificada por el usuario); VC-016 agrega ese caso.
- 0.5 (2026-10-07): se resuelve la pregunta abierta de los PDFs de muestra con los PDFs provistos por el usuario: dos informes de la EPH de INDEC (2.º trimestre de 2025 y de 2026, completos) y un extracto de 10 páginas del Anuario Estadístico 2025 de CEPAL (población y trabajo, con la tabla multipágina). Afecta FR-070 (archivos y URLs), §9 (se quita la pregunta) y A-4 (validada).
- 0.4 (2026-10-07): se resuelve la pregunta abierta de modelos con `notebooks/00_litellm_exploracion.ipynb`: `LLM_MODEL=gpt-5.4-mini` (`temperature=0`), `EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIM=1536`. Afecta §7.1, §9 (se quita la pregunta de modelos) y A-5. NFR-003: la regex de secretos se escribe con clases `[_]` (equivalente) para que el propio documento no coincida con ella.
- 0.3 (2026-10-07): almacén de tablas en Parquet (`pyarrow`) en lugar de CSV, por pedido del usuario: preserva dtypes sin reinferirlos. Afecta FR-023, FR-042, FR-074, BR-012, BR-013, §7.1–7.4, VC-025, VC-056, VC-064.
- 0.2 (2026-10-07): correcciones de `/review-spec`: FR-027/028/029 (PDF inválido, tope de tablas, falla parcial al indexar), FR-042 (datos locales faltantes), FR-056 (validación de pregunta), precisión de BR-006/BR-007, `static_check` en la interfaz del sandbox, VCs nuevos.
- 0.1 (2026-10-07): primer borrador a partir de `assignment.es.md`. Decisiones del usuario: sin bonus, solo PDFs reales, almacén CSV por tabla, `requirements.txt`.
