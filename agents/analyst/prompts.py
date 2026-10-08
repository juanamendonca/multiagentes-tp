"""Prompts del Analista, en español: entender la pregunta (FR-030), seleccionar tablas
(FR-033, con las reglas de BR-014/BR-016) y redactar la respuesta (FR-040, FR-041).

El prompt de selección muestra, por candidata, todo lo que permite distinguir tablas casi
iguales de publicaciones distintas (p. ej. las EPH 2T2025 y 2T2026 de INDEC): archivo, páginas,
sección, nombre, descripción, columnas exactas con tipo y unidad, rótulos de fila y la vista
previa ``preview_csv`` guardada en Pinecone (D-029).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agents.analyst.schemas import Candidate, MissingPart, QueryPlan
from agents.analyst.status import Status
from services.sandbox import ResultItem
from services.vectorstore import format_pages

MAX_PROMPT_ROW_HEADERS = 60
MAX_PREVIEW_CHARS = 1500

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
1. Una parte es `covered` solo si una tabla (o una combinación de tablas) de la lista tiene
   explícitamente: la métrica pedida, cada entidad pedida (como fila o columna) y cada período pedido
   (en los nombres de columna, los rótulos de fila, la descripción o la vista previa). Revisá los
   nombres de columna y los rótulos de fila: ahí están los períodos y las entidades.
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
   sección, páginas y nombres de columna (que traen el año y el trimestre). Elegí la tabla cuyas
   columnas o filas contienen exactamente el período pedido; si varias lo contienen, preferí la
   publicación más reciente.
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


# --- respond -------------------------------------------------------------------------------------

RESPOND_SYSTEM_PROMPT = """Redactás la respuesta final, en español, de un agente que responde preguntas \
usando SOLO tablas extraídas de PDFs. Te paso la pregunta, el estado ya decidido por el sistema, los \
resultados calculados (si hay), las tablas usadas y lo que falta. Reglas:
1. No cambies el estado ni lo contradigas. No inventes valores ni uses conocimiento propio, fuentes
   externas o estimaciones: los únicos números que podés dar como resultado son los de "Resultados".
2. Si hay resultados: dalos con su unidad, citá las tablas y páginas usadas, explicá en una o dos
   oraciones cómo se calculó y mencioná las conversiones aplicadas.
3. Por cada parte que falta, explicá el motivo TAL COMO figura en "Partes que faltan" (no deduzcas
   ni inventes otros motivos: si el motivo es que no se pudo calcular, no digas que falta un dato).
   Si la tabla relacionada trae una nota de qué cubre, decila (p. ej. "la tabla cubre 2019–2023;
   2024 no está"); no describas el contenido de las tablas más allá de esas notas.
4. Si el estado es cannot_answer, no des ningún resultado numérico: explicá qué falta.
5. Sé breve (1 a 2 párrafos o una lista corta). Sin encabezados ni bloques de código."""

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
) -> list[BaseMessage]:
    """Mensajes para redactar la respuesta (FR-040). Con ``cannot_answer`` (FR-041) solo se
    pasan los faltantes y las tablas relacionadas: ni resultados ni conversiones."""
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
