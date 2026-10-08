"""Prompts del Analista, en español: entender la pregunta (FR-030), seleccionar tablas
(FR-033, con las reglas de BR-014/BR-016), generar el código de cálculo (FR-035/FR-037, con
BR-013/BR-014/BR-016 y el contrato de §7.6) y redactar la respuesta (FR-040, FR-041).

El prompt de selección muestra, por candidata, todo lo que permite distinguir tablas casi
iguales de publicaciones distintas (p. ej. las EPH 2T2025 y 2T2026 de INDEC): archivo, páginas,
sección, nombre, descripción, columnas exactas con tipo y unidad, rótulos de fila y la vista
previa ``preview_csv`` guardada en Pinecone (D-029).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pandas as pd
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agents.analyst.schemas import Candidate, MissingPart, QueryPlan
from agents.analyst.status import Status
from agents.ingestion.validation import first_text_column
from services.sandbox import ALLOWED_IMPORTS, ExecutionResult, ResultItem
from services.vectorstore import format_pages

MAX_PROMPT_ROW_HEADERS = 60
MAX_PREVIEW_CHARS = 1500
MAX_CODEGEN_ROWS = 100  # filas de la primera columna de texto listadas una por una (codegen)
MAX_FEEDBACK_STDERR = 1500  # cola de stderr reenviada en un reintento (D-032)
MAX_FEEDBACK_STDOUT = 500  # cola de stdout reenviada si la salida no cumple §7.6 (D-032)
MAX_RESPOND_CODE_CHARS = 3000  # código que ve respond para explicar el cálculo

# --- understand_query ----------------------------------------------------------------------------

UNDERSTAND_SYSTEM_PROMPT = """Sos el planificador de un agente que responde preguntas usando SOLO tablas \
estadísticas extraídas de PDFs (informes de INDEC, anuarios de CEPAL y similares). No respondés la \
pregunta: la descomponés para buscar las tablas que hacen falta.

Devolvé un plan con 1 a 5 partes. Reglas:
1. Cada parte es un valor (o conjunto de valores) que se calcula por separado. Si la pregunta pide
   un dato y además una comparación, diferencia o relación con otro dato, son partes distintas.
   Si pide lo mismo para varias entidades o períodos en una sola cuenta, es una sola parte.
2. `text`: la parte reformulada como pregunta autónoma (que se entienda sin las otras partes).
3. `metrics`: indicadores o variables (p. ej. "tasa de desocupación", "población ocupada").
   `entities`: regiones, países, aglomerados, sexos, grupos de edad u otras categorías nombradas.
   `periods`: los períodos tal como los pide la pregunta ("2023", "2° trimestre de 2026"); [] si no hay.
   `operation`: "valor", "máximo", "mínimo", "diferencia", "variación", "promedio", "suma", "cociente", etc.
4. `search_queries`: de 1 a 3 frases cortas en español que describan la TABLA que tendría el dato
   (qué mide, para qué población o región, con qué desagregación y período). No incluyas valores
   numéricos ni la respuesta. Variá el enfoque entre consultas (indicador, desagregación, fuente).
5. Usá ids "p1", "p2", … en el orden en que hay que calcularlas.
6. No agregues partes que la pregunta no pide ni inventes períodos o entidades.

Ejemplo: "¿Cuál fue la tasa de actividad de las mujeres en el 1° trimestre de 2025 y cuántos puntos
menos que la de los varones?" → p1: tasa de actividad de mujeres en 1T2025 (operation "valor");
p2: diferencia entre la tasa de actividad de varones y la de mujeres en 1T2025 (operation "diferencia")."""


def build_understand_messages(question: str) -> list[BaseMessage]:
    """Mensajes para ``with_structured_output(QueryPlan)``."""
    return [
        SystemMessage(content=UNDERSTAND_SYSTEM_PROMPT),
        HumanMessage(content=f"Pregunta del usuario:\n{question.strip()}"),
    ]


# --- select_tables -------------------------------------------------------------------------------

SELECT_SYSTEM_PROMPT = """Sos el selector de tablas de un agente que responde preguntas usando SOLO \
los valores de tablas extraídas de PDFs. Recibís la pregunta dividida en partes y una lista de tablas \
candidatas (con metadatos y las primeras filas). Decidís qué tablas usar y, para cada parte, si las \
candidatas tienen TODOS los datos necesarios para calcularla. No calculás nada.

Cómo decidir:
1. Una parte es `covered` si las tablas que elegís para ella, EN CONJUNTO, tienen explícitamente: la
   métrica pedida, cada entidad pedida (como fila o columna) y cada período pedido (en los nombres de
   columna, los rótulos de fila, la descripción o la vista previa). Revisá los nombres de columna y
   los rótulos de fila: ahí están los períodos y las entidades.
   No hace falta que una sola tabla tenga todo: el código combina tablas. Ej.: piden la tasa de una
   región en 2T2025 y en 2T2026 (y su variación); la edición 2T2025 de un informe la tiene para
   2T2025 y la edición 2T2026 para 2T2026 → la parte es covered con las DOS tablas en `table_ids`.
   Que los datos estén repartidos en varias tablas nunca es motivo de `missing`; pero cada período
   y cada entidad pedidos tienen que figurar en alguna de las tablas elegidas: si un período pedido
   no está en ninguna candidata (p. ej. piden 2023 y las tablas son de 2025 y 2026), es `missing`.
2. Si no, la parte es `missing`, con `missing_reason` (qué dato falta y por qué) y, si hay una tabla
   relacionada, su id en `table_ids` y en `related_table_note` qué cubre esa tabla. Causas de
   `missing` (BR-014), con ejemplos:
   a. Falta un período o una entidad. Ej.: piden 2024 y la tabla cubre 2019–2023 → missing,
      "la tabla cubre 2019–2023; 2024 no está". Nunca interpoles, extrapoles ni uses el año o
      trimestre más cercano. Si piden un año y la tabla solo tiene trimestres, el año está cubierto
      solo si la pregunta lo permite sumando todos los trimestres (ver conversiones).
   b. La conversión necesita datos externos. Ej.: piden el valor en dólares, la tabla está en pesos y
      no hay tabla de tipo de cambio → missing. Lo mismo para inflación, población u otra tabla que
      no esté en la lista.
   c. Falta el valor de comparación pedido. Ej.: piden "cuánto superó al promedio nacional" y ninguna
      tabla tiene el promedio o total nacional → missing. No lo sustituyas por un valor calculado
      (p. ej. el promedio de las regiones), salvo que la pregunta lo defina así.
   d. El significado o la unidad es ambiguo y cambia el resultado. Ej.: piden el salario real y la
      tabla no dice si es nominal o real → missing.
3. Conversiones permitidas sin datos externos (BR-016), que NO hacen a una parte missing: cambio de
   escala (miles ↔ millones), porcentaje ↔ fracción, sumar meses o trimestres para obtener un año
   solo si están todos los períodos, y cuentas entre columnas de la misma unidad.
4. Tablas casi iguales: los informes de distintos períodos repiten nombres de tabla (p. ej. dos
   ediciones de la EPH con "Principales tasas del mercado de trabajo"). Distinguilas por archivo,
   sección, páginas y nombres de columna (que traen el año y el trimestre). Para cada período pedido
   elegí una tabla cuyas columnas, filas o descripción lo contienen; si varias lo contienen, preferí
   la publicación más reciente (y una sola tabla si ya tiene todos los períodos).
5. `table_ids` de una parte covered: las tablas necesarias para calcularla (usualmente una; más si
   la parte combina datos de tablas distintas). `selected_table_ids`: todas las tablas usadas por
   las partes covered, sin repetir. Usá solo ids de la lista de candidatas, copiados exactos.
6. Devolvé exactamente una entrada de `coverage` por parte, con su `part_id`. Para las partes
   covered, `missing_reason` y `related_table_note` son null.
7. `rationale`: 1 a 3 oraciones explicando la elección."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def describe_plan(plan: QueryPlan) -> str:
    lines = []
    for part in plan.parts:
        lines.append(f"- {part.id}: {part.text}")
        details = [
            f"métricas {_json(part.metrics)}" if part.metrics else "",
            f"entidades {_json(part.entities)}" if part.entities else "",
            f"períodos {_json(part.periods)}" if part.periods else "",
            f"operación {_json(part.operation)}" if part.operation else "",
        ]
        details = [d for d in details if d]
        if details:
            lines.append(f"  ({'; '.join(details)})")
    return "\n".join(lines)


def describe_candidate(candidate: Candidate) -> str:
    """Bloque de una candidata para el prompt de selección (metadatos completos + vista previa)."""
    columns = []
    for i, column in enumerate(candidate.columns):
        extra = [column.dtype]
        if column.unit:
            extra.append(f"unidad: {column.unit}")
        line = f"  {i}. {_json(column.name)} ({', '.join(extra)})"
        if column.description:
            line += f": {column.description}"
        columns.append(line)
    headers = candidate.row_headers[:MAX_PROMPT_ROW_HEADERS]
    headers_text = _json(headers)
    if len(headers) < len(candidate.row_headers):
        headers_text += f" (+{len(candidate.row_headers) - len(headers)} más)"
    preview = candidate.preview_csv.strip()
    if len(preview) > MAX_PREVIEW_CHARS:
        preview = preview[:MAX_PREVIEW_CHARS] + "…"
    lines = [
        f"### Tabla {candidate.table_id}",
        f"Nombre: {candidate.name}",
        f"Archivo: {candidate.file_name} ({format_pages(candidate.pages) if candidate.pages else 'págs. ?'})",
        f"Sección: {candidate.section or '(sin sección)'}",
        f"Descripción: {candidate.description}",
        f"Tamaño: {candidate.n_rows} filas × {candidate.n_cols} columnas",
        "Columnas:",
        *columns,
        f"Rótulos de fila: {headers_text}",
        "Primeras filas (CSV):",
        preview or "(sin vista previa)",
    ]
    if candidate.metadata_quality != "ok":
        lines.append(f"Calidad de metadatos: {candidate.metadata_quality}")
    return "\n".join(lines)


def build_select_messages(
    question: str, plan: QueryPlan, candidates: Sequence[Candidate]
) -> list[BaseMessage]:
    """Mensajes para ``with_structured_output(TableSelection)``."""
    blocks = "\n\n".join(describe_candidate(c) for c in candidates)
    content = "\n".join(
        [
            f"Pregunta del usuario:\n{question.strip()}",
            "",
            "Partes de la pregunta:",
            describe_plan(plan),
            "",
            f"Tablas candidatas ({len(candidates)}, ids válidos: {_json([c.table_id for c in candidates])}):",
            "",
            blocks,
        ]
    )
    return [SystemMessage(content=SELECT_SYSTEM_PROMPT), HumanMessage(content=content)]


# --- generate_code -------------------------------------------------------------------------------

CODEGEN_SYSTEM_PROMPT = """Sos el programador de un agente que responde preguntas usando SOLO los valores \
de tablas extraídas de PDFs. Escribís un programa Python que calcula, con pandas, las partes de la \
pregunta que te indico, a partir de las tablas que ya están cargadas. Respondé solo con el programa \
completo, en un único bloque ```python ... ```.

## Cómo acceder a los datos
- El programa ya tiene definidas estas variables; no las vuelvas a crear ni leas archivos:
  - `tables`: dict[str, pandas.DataFrame] con las tablas seleccionadas, por alias: `tables["t1"]`, \
`tables["t2"]`, …
  - `pd` (pandas) y `np` (numpy).
- Usá los nombres de columna EXACTOS listados abajo, siempre con `df["..."]` (nunca `df.nombre`): \
incluyen espacios, acentos, `°`, `*`, `/` y `%`. Copialos tal cual.
- Los tipos ya vienen correctos: numéricas `float64`/`Int64`, texto `str`. Un dato faltante es \
`NaN`/`<NA>`, nunca 0. No conviertas texto a número.
- Las filas se identifican por el texto de la primera columna de texto. Un mismo rótulo puede \
repetirse en distintos bloques de la tabla (p. ej. "Mujeres" bajo "Actividad" y bajo \
"Desocupación"): ubicá primero la fila del bloque y después la fila dentro del bloque (con los \
índices listados o filtrando a partir de la fila del bloque); nunca tomes la primera coincidencia de \
un rótulo repetido. Compará textos con `.str.strip()`.
- Solo podés importar: {allowed}.
- Prohibido: leer o escribir archivos (`open`, `pd.read_*`, `to_csv`, `np.load`, …), red, \
`eval`/`exec`, `getattr` sobre módulos y atributos que empiecen con `__`. El código que los use se \
rechaza sin ejecutarse.
- Límite de tiempo: {timeout} s. La salida se recorta a los últimos 20 000 caracteres: no imprimas \
tablas enteras.

## Qué calcular y qué reportar como faltante (BR-014, BR-016)
- Usá solo valores presentes en las tablas. Si falta un período o una entidad, la conversión necesita \
datos externos (tipo de cambio, inflación, población que no esté en las tablas), falta el valor de \
comparación pedido, o la unidad es ambigua y cambia el resultado: NO lo calcules ni lo aproximes; poné \
esa parte en `missing` con el motivo concreto (qué dato falta). Nunca interpoles, extrapoles ni uses \
el período más cercano.
- Una parte es todo o nada: si pide varios valores (p. ej. dos períodos) y falta alguno, la parte \
entera va a `missing` (no la pongas en `results` con solo los valores que encontraste).
- Conversiones permitidas sin datos externos: cambio de escala (miles ↔ millones, etc.), porcentaje ↔ \
fracción, sumar meses o trimestres para obtener un año solo si están todos, y cuentas entre columnas \
de la misma unidad. Declará cada conversión aplicada en `conversions` (p. ej. "miles de personas → \
millones de personas"). Si la pregunta pide otra escala o unidad que la de la tabla y la conversión \
está permitida, convertí y declarala.
- Las diferencias entre tasas en % se expresan en puntos porcentuales ("p.p.").

## Formato de salida
Imprimí como ÚLTIMA línea de stdout, en una sola línea, este JSON (usá \
`print(json.dumps(salida, ensure_ascii=False))`):
{{"results": [{{"part_id": "p1", "label": "<qué es el valor>", "value": <número>, "unit": "<unidad>"}}],
 "missing": [{{"part_id": "p2", "reason": "<qué dato falta y por qué>"}}],
 "conversions": ["<conversión aplicada, p. ej. miles → millones>"]}}
Reglas:
- Cada parte a calcular ({part_ids}) aparece exactamente una vez: en `results` si la calculaste o en \
`missing` si falta el dato. No agregues otras partes.
- `value` es un número finito, una lista de números o un objeto {{"texto": número}}. Convertí con \
`float(...)` (los enteros de numpy/pandas no se serializan a JSON) y redondeá los valores calculados \
con `round(x, 4)`. Nunca NaN ni infinito: si el resultado daría NaN, la parte va a `missing`.
- `label` describe el valor con la entidad y el período (p. ej. "Tasa de desocupación, total 31 \
aglomerados, 2T2026"); `unit` es la unidad del valor final.
- No imprimas nada después de esa línea."""


def _cell(value: object) -> str:
    try:
        if pd.isna(value):  # type: ignore[call-overload]
            return "<NaN>"
    except (TypeError, ValueError):
        pass
    return " ".join(str(value).split())


def describe_codegen_table(alias: str, candidate: Candidate, df: pd.DataFrame) -> str:
    """Bloque de una tabla para el prompt de codegen (FR-035): alias, nombre, archivo y páginas,
    columnas EXACTAS con dtype real, tipo, unidad y descripción, la primera columna de texto fila
    por fila (con su índice de ``df.iloc``) y ``df.head(5)``."""
    meta = {c.name: c for c in candidate.columns}
    columns = []
    for name in df.columns:
        column = meta.get(str(name))
        details = [f"dtype {df[name].dtype}"]
        if column is not None:
            details.append(f"tipo {column.dtype}")
            if column.unit:
                details.append(f"unidad: {column.unit}")
        line = f"  - {_json(str(name))} ({'; '.join(details)})"
        if column is not None and column.description:
            line += f": {column.description}"
        columns.append(line)
    pages = format_pages(candidate.pages) if candidate.pages else "págs. ?"
    lines = [
        f'### {alias} = tables["{alias}"]: {candidate.name}',
        f"Archivo: {candidate.file_name} ({pages}); id {candidate.table_id}",
        f"Descripción: {candidate.description}",
        f"Tamaño: {len(df)} filas × {len(df.columns)} columnas",
        "Columnas (nombre exacto entre comillas):",
        *columns,
    ]
    label_column = first_text_column(df)
    if label_column is not None:
        values = df[label_column].tolist()
        lines.append(f"Primera columna de texto {_json(label_column)}, fila por fila (índice: valor):")
        lines += [f"  {i}: {_cell(v)}" for i, v in enumerate(values[:MAX_CODEGEN_ROWS])]
        if len(values) > MAX_CODEGEN_ROWS:
            lines.append(f"  … (+{len(values) - MAX_CODEGEN_ROWS} filas más)")
    lines += ["df.head(5) (CSV):", df.head(5).to_csv(index=False).strip()]
    return "\n".join(lines)


def describe_feedback(attempt: int, max_attempts: int, code: str, execution: ExecutionResult) -> str:
    """Feedback de un intento fallido (FR-037, D-032): el código anterior, ``execution.error``
    (motivo del AST, ``returncode N: <última línea de stderr>``, timeout o violación de §7.6) y
    la cola de stderr (error/timeout) o de stdout (salida inválida)."""
    lines = [
        f"## El intento {attempt} de {max_attempts} falló",
        "Código anterior:",
        "```python",
        code.strip(),
        "```",
        f"Error: {execution.error or execution.status}",
    ]
    if execution.status in ("error", "timeout") and execution.stderr.strip():
        lines += [
            f"Final de stderr (hasta {MAX_FEEDBACK_STDERR} caracteres):",
            execution.stderr.strip()[-MAX_FEEDBACK_STDERR:],
        ]
    if execution.status == "invalid_output":
        tail = execution.stdout.strip()[-MAX_FEEDBACK_STDOUT:]
        lines += [f"Final de stdout (hasta {MAX_FEEDBACK_STDOUT} caracteres):", tail or "(vacío)"]
    lines.append(
        "Corregí el error y devolvé el programa COMPLETO en un único bloque ```python```, "
        "respetando todas las reglas."
    )
    return "\n".join(lines)


def build_codegen_messages(
    question: str,
    plan: QueryPlan,
    covered_part_ids: Sequence[str],
    tables: Sequence[tuple[str, Candidate, pd.DataFrame]],
    *,
    timeout_s: int,
    feedback: str | None = None,
) -> list[BaseMessage]:
    """Mensajes para ``generate_code`` (FR-035). ``tables`` = ``(alias, candidata, df)`` en el
    orden de los alias ``t1..tn``; ``feedback`` = ``describe_feedback`` del intento anterior."""
    system = CODEGEN_SYSTEM_PROMPT.format(
        allowed=", ".join(sorted(ALLOWED_IMPORTS)),
        timeout=timeout_s,
        part_ids=", ".join(covered_part_ids),
    )
    parts = []
    for pid in covered_part_ids:
        part = plan.part(pid)
        if part is None:
            continue
        line = f"- {part.id}: {part.text}"
        details = [
            f"operación {_json(part.operation)}" if part.operation else "",
            f"entidades {_json(part.entities)}" if part.entities else "",
            f"períodos {_json(part.periods)}" if part.periods else "",
        ]
        details = [d for d in details if d]
        if details:
            line += f" ({'; '.join(details)})"
        parts.append(line)
    content = [
        f"Pregunta del usuario:\n{question.strip()}",
        "",
        "Partes a calcular (part_id: texto):",
        *parts,
        "",
        f"Tablas disponibles ({len(tables)}):",
        "",
        "\n\n".join(describe_codegen_table(alias, c, df) for alias, c, df in tables),
    ]
    if feedback:
        content += ["", feedback]
    return [SystemMessage(content=system), HumanMessage(content="\n".join(content))]


# --- respond -------------------------------------------------------------------------------------

RESPOND_SYSTEM_PROMPT = """Redactás la respuesta final, en español, de un agente que responde preguntas \
usando SOLO tablas extraídas de PDFs. Te paso la pregunta, el estado ya decidido por el sistema, los \
resultados calculados (si hay), las tablas usadas y lo que falta. Reglas:
1. No cambies el estado ni lo contradigas. No inventes valores ni uses conocimiento propio, fuentes
   externas o estimaciones: los únicos números que podés dar como resultado son los de "Resultados".
2. Si hay resultados: dalos con su unidad, citá las tablas usadas con su archivo y sus páginas,
   explicá en una o dos oraciones cómo se calculó (según el código, sin mostrarlo) y mencioná
   cada conversión aplicada tal como figura en "Conversiones aplicadas".
3. Por cada parte que falta, explicá el motivo TAL COMO figura en "Partes que faltan" (no deduzcas
   ni inventes otros motivos: si el motivo es que no se pudo calcular, no digas que falta un dato).
   Si la tabla relacionada trae una nota de qué cubre, decila (p. ej. "la tabla cubre 2019–2023;
   2024 no está"); no describas el contenido de las tablas más allá de esas notas.
4. Si el estado es cannot_answer, no des ningún resultado numérico: explicá qué falta.
5. Sé breve (1 a 2 párrafos o una lista corta). Sin encabezados ni bloques de código.
6. Escribí los números con coma decimal y punto de miles (p. ej. 7,9 %; 1,164 millones; 13.569
   miles), nunca con punto decimal, para que no se confundan con separadores de miles."""

STATUS_LABELS: dict[str, str] = {
    "answered": "answered (respondida completa)",
    "partial": "partial (respondida en parte)",
    "cannot_answer": "cannot_answer (no se puede responder con las tablas disponibles)",
}


def _format_value(result: ResultItem) -> str:
    value = result.value
    if isinstance(value, dict):
        text = ", ".join(f"{k}: {v:g}" for k, v in value.items())
    elif isinstance(value, list):
        text = ", ".join(f"{v:g}" for v in value)
    else:
        text = f"{value:g}"
    return f"{text} {result.unit}".strip() if result.unit else text


def _label(result: ResultItem) -> str:
    return f"{result.label} = " if result.label else ""


def describe_tables_used(tables: Sequence[Candidate]) -> str:
    return "\n".join(
        f"- {c.name} ({c.file_name}, {format_pages(c.pages) if c.pages else 'págs. ?'}; id {c.table_id})"
        for c in tables
    )


def build_respond_messages(
    question: str,
    status: Status,
    plan: QueryPlan | None,
    results: Sequence[ResultItem],
    missing: Sequence[MissingPart],
    tables_used: Sequence[Candidate],
    related_tables: Sequence[Candidate] = (),
    conversions: Sequence[str] = (),
    code: str | None = None,
) -> list[BaseMessage]:
    """Mensajes para redactar la respuesta (FR-040). Con ``cannot_answer`` (FR-041) solo se
    pasan los faltantes y las tablas relacionadas: ni resultados, ni conversiones, ni código.
    ``code`` (el programa ejecutado, recortado) sirve para explicar brevemente el cálculo."""
    texts = {p.id: p.text for p in plan.parts} if plan else {}
    names_by_id = {c.table_id: f"{c.name} ({c.file_name})" for c in [*tables_used, *related_tables]}
    lines = [f"Pregunta del usuario:\n{question.strip()}", "", f"Estado: {STATUS_LABELS[status]}"]
    if status != "cannot_answer":
        lines += ["", "Resultados:"]
        lines += [
            f"- {r.part_id} ({texts.get(r.part_id, '')}): {_label(r)}{_format_value(r)}" for r in results
        ]
        lines += ["", "Tablas usadas:", describe_tables_used(tables_used) or "(ninguna)"]
        lines += ["", "Conversiones aplicadas:"]
        lines += [f"- {c}" for c in conversions] or ["(ninguna)"]
        if code and code.strip():
            snippet = code.strip()
            if len(snippet) > MAX_RESPOND_CODE_CHARS:
                snippet = snippet[:MAX_RESPOND_CODE_CHARS] + "\n# … (recortado)"
            lines += ["", "Código que calculó los resultados (solo para explicar el cálculo):", snippet]
    lines += ["", "Partes que faltan:"]
    if missing:
        for m in missing:
            line = f"- {m.part_id} ({texts.get(m.part_id, '')}): motivo: {m.reason}"
            names = [names_by_id[t] for t in m.table_ids if t in names_by_id]
            if names:
                line += f". Tabla relacionada: {'; '.join(names)}"
            if m.related_table_note:
                line += f". Qué cubre: {m.related_table_note}"
            lines.append(line)
    else:
        lines.append("(ninguna)")
    if related_tables:
        lines += ["", "Tablas relacionadas:", describe_tables_used(related_tables)]
    return [SystemMessage(content=RESPOND_SYSTEM_PROMPT), HumanMessage(content="\n".join(lines))]


def fallback_answer(status: Status, results: Sequence[ResultItem], missing: Sequence[MissingPart]) -> str:
    """Texto determinístico si el LLM devuelve una respuesta vacía."""
    lines = [f"Estado: {status}."]
    lines += [f"{r.part_id}: {_label(r)}{_format_value(r)}" for r in results]
    for m in missing:
        line = f"Falta {m.part_id}: {m.reason}"
        if m.related_table_note:
            line += f" ({m.related_table_note})"
        lines.append(line)
    return "\n".join(lines)
