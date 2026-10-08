"""Modelos Pydantic del Analista (spec §7.5) y del resultado de ``ask``.

- ``QueryPlan`` / ``QueryPart``: salida estructurada de ``understand_query`` (FR-030).
- ``TableSelection`` / ``PartCoverage``: salida estructurada de ``select_tables`` (FR-033).
- ``Candidate``: una tabla recuperada de Pinecone (metadatos decodificados + score, FR-031).
- ``MissingPart``: una parte sin respuesta y su motivo (lo que muestran ``respond`` y la UI).

Los modelos que devuelve el LLM normalizan en un validador ``before`` lo que es seguro corregir
sin cambiar el sentido (ids ``p1..pn`` en orden, consultas vacías o repetidas, más de 3
consultas); el resto de las restricciones de §7.5 se validan y, si fallan, la salida del LLM
cuenta como inválida.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from agents.ingestion.schemas import ColumnMeta
from services.sandbox import ExecutionResult, ResultItem

MAX_PARTS = 5
MAX_SEARCH_QUERIES = 3

Coverage = Literal["covered", "missing"]
Status = Literal["answered", "partial", "cannot_answer"]


def _clean_queries(raw: object, fallback: str) -> list[str]:
    queries: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        text = " ".join(str(item).split())
        if text and text.casefold() not in {q.casefold() for q in queries}:
            queries.append(text)
    if not queries and fallback.strip():
        queries = [" ".join(fallback.split())]
    return queries[:MAX_SEARCH_QUERIES]


class QueryPart(BaseModel):
    """Una parte de la pregunta que se responde con un valor (o conjunto de valores) propio."""

    id: str = Field(pattern=r"^p[1-9]\d*$", description='Identificador "p1", "p2", … en orden.')
    text: str = Field(description="La parte de la pregunta, reformulada como pregunta autónoma.")
    metrics: list[str] = Field(
        description="Indicadores o variables que pide (p. ej. 'tasa de desocupación')."
    )
    entities: list[str] = Field(description="Regiones, países, grupos o categorías nombradas.")
    periods: list[str] = Field(
        description="Períodos pedidos tal como aparecen (año, trimestre); [] si no hay."
    )
    operation: str = Field(
        description="Operación: 'valor', 'máximo', 'mínimo', 'diferencia', 'variación', 'promedio', 'suma', …"
    )
    search_queries: list[str] = Field(
        min_length=1,
        max_length=MAX_SEARCH_QUERIES,
        description="1 a 3 consultas que describen la TABLA con el dato (qué mide, población, período).",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            data = dict(data)
            data["search_queries"] = _clean_queries(data.get("search_queries"), str(data.get("text", "")))
        return data


class QueryPlan(BaseModel):
    """Pregunta dividida en 1–5 partes (FR-030)."""

    parts: list[QueryPart] = Field(min_length=1, max_length=MAX_PARTS)

    @model_validator(mode="before")
    @classmethod
    def _renumber(cls, data: Any) -> Any:
        """Los ids se reasignan ``p1..pn`` en el orden dado (el LLM a veces numera desde 0 o
        repite ids); el resto de cada parte no cambia."""
        if isinstance(data, Mapping) and isinstance(data.get("parts"), list):
            parts = []
            for i, part in enumerate(data["parts"], start=1):
                if isinstance(part, QueryPart):
                    part = part.model_dump()
                if isinstance(part, Mapping):
                    part = {**part, "id": f"p{i}"}
                parts.append(part)
            data = {**data, "parts": parts}
        return data

    @property
    def part_ids(self) -> list[str]:
        return [p.id for p in self.parts]

    def part(self, part_id: str) -> QueryPart | None:
        return next((p for p in self.parts if p.id == part_id), None)


class PartCoverage(BaseModel):
    """Cobertura de una parte por las tablas candidatas (FR-033)."""

    part_id: str = Field(description='Id de la parte ("p1", …).')
    coverage: Coverage = Field(description="covered si las tablas tienen TODOS los datos; si no, missing.")
    table_ids: list[str] = Field(
        default_factory=list,
        description="covered: tablas necesarias para calcularla. missing: la tabla relacionada, si existe.",
    )
    missing_reason: str | None = Field(
        default=None, description="Si missing: qué dato falta y por qué (BR-014). Si no, null."
    )
    related_table_note: str | None = Field(
        default=None,
        description="Si missing y hay tabla relacionada: qué cubre (p. ej. 'cubre 2019–2023; 2024 no está').",
    )


class TableSelection(BaseModel):
    """Tablas elegidas y cobertura por parte (FR-033)."""

    selected_table_ids: list[str] = Field(
        description="Ids de las tablas elegidas (de la lista de candidatas)."
    )
    coverage: list[PartCoverage] = Field(description="Una entrada por cada parte de la pregunta.")
    rationale: str = Field(default="", description="Explicación breve de la elección, en español.")

    def coverage_of(self, part_id: str) -> PartCoverage | None:
        return next((c for c in self.coverage if c.part_id == part_id), None)

    @property
    def covered_part_ids(self) -> list[str]:
        return [c.part_id for c in self.coverage if c.coverage == "covered"]


class Candidate(BaseModel):
    """Tabla candidata recuperada de Pinecone (FR-031): metadatos de §7.4 decodificados, score
    máximo entre las consultas y qué consultas/partes la trajeron."""

    table_id: str
    score: float
    name: str
    description: str = ""
    columns: list[ColumnMeta] = Field(default_factory=list)
    row_headers: list[str] = Field(default_factory=list)
    file_name: str = ""
    pages: list[int] = Field(default_factory=list)
    section: str | None = None
    n_rows: int = 0
    n_cols: int = 0
    preview_csv: str = ""
    metadata_quality: str = "ok"
    matched_parts: list[str] = Field(default_factory=list)
    matched_queries: list[str] = Field(default_factory=list)


class MissingPart(BaseModel):
    """Parte sin resultado: motivo y, si existe, la tabla relacionada y qué cubre (FR-040/041)."""

    part_id: str
    reason: str
    related_table_note: str | None = None
    table_ids: list[str] = Field(default_factory=list)


class AnalystResult(BaseModel):
    """Resultado de ``ask`` (lo que muestran la UI, el CLI y la evaluación).

    ``selected_tables`` son las candidatas elegidas (con archivo y páginas), en el orden de los
    alias ``t1..tn``. ``run_id`` es el id del run raíz de LangSmith (``None`` sin trazas);
    ``run_url`` queda para la UI (Iteración 9).
    """

    question: str
    status: Status
    answer: str = ""
    plan: QueryPlan | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    selection: TableSelection | None = None
    results: list[ResultItem] = Field(default_factory=list)
    missing: list[MissingPart] = Field(default_factory=list)
    conversions: list[str] = Field(default_factory=list)
    code: str | None = None
    code_attempts: int = 0
    execution: ExecutionResult | None = None
    warnings: list[str] = Field(default_factory=list)
    llm_calls: int = 0
    embedding_calls: int = 0
    run_id: str | None = None
    run_url: str | None = None
    elapsed_s: float = 0.0

    @property
    def selected_tables(self) -> list[Candidate]:
        if self.selection is None:
            return []
        index = {c.table_id: c for c in self.candidates}
        return [index[t] for t in self.selection.selected_table_ids if t in index]
