"""Estado del grafo Analista (spec §7.5).

``TypedDict`` con las claves de la spec (``question``, ``plan``, ``candidates``, ``selection``,
``code``, ``code_attempts``, ``execution``, ``results``, ``missing``, ``status``, ``answer``,
``run_url``) más contadores y avisos. ``warnings``, ``llm_calls`` y ``embedding_calls`` se
acumulan (reducer ``operator.add``).

Contrato con los nodos de código (``generate_code``/``execute``, Iteración 8):

- ``code``: último programa generado (sin el preámbulo); ``None`` si no se generó.
- ``code_attempts``: llamadas a ``generate_code`` hechas (0 si no hubo código).
- ``execution``: ``ExecutionResult`` de la última ejecución en el sandbox; ``None`` si no hubo.
- ``missing`` **antes** de ``decide_status``: faltantes que agrega el lado del código (p. ej.
  FR-038, "falló la ejecución tras N intentos: …"). ``decide_status`` lo **reemplaza** por la
  lista final (selección + código + partes cubiertas sin resultado), una entrada por parte.
- ``results`` / ``conversions``: los fija ``decide_status`` a partir de ``execution.parsed``,
  solo para las partes respondidas (BR-015); con ``cannot_answer`` quedan vacíos (FR-041).
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from agents.analyst.schemas import Candidate, MissingPart, QueryPlan, TableSelection
from agents.analyst.status import Status
from services.sandbox import ExecutionResult, ResultItem


class AnalystState(TypedDict, total=False):
    """Estado del grafo Analista."""

    # entrada
    question: str
    # understand_query (FR-030)
    plan: QueryPlan | None
    # retrieve (FR-031, FR-032, FR-042)
    candidates: list[Candidate]
    # select_tables (FR-033, FR-034)
    selection: TableSelection | None
    # generate_code / execute (FR-035..FR-038; stubs en la Iteración 7)
    code: str | None
    code_attempts: int
    execution: ExecutionResult | None
    # decide_status (FR-039, BR-015)
    results: list[ResultItem]
    missing: list[MissingPart]
    conversions: list[str]
    status: Status | None
    # respond (FR-040, FR-041)
    answer: str
    run_url: str | None
    # contadores y avisos
    warnings: Annotated[list[str], operator.add]
    llm_calls: Annotated[int, operator.add]  # llamadas de chat (NFR-006)
    embedding_calls: Annotated[int, operator.add]  # llamadas de embeddings (NFR-006)
