"""Decisión determinística del estado de la respuesta (BR-015, FR-039).

Sea ``P`` el conjunto de partes del ``QueryPlan`` y ``A ⊆ P`` las partes respondidas: las que
están ``covered`` en la selección **y** tienen un resultado en ``execution.parsed.results`` de
una ejecución exitosa (``execution.ok``). Las partes que el código reporta en ``missing`` no
cuentan. ``answered`` si ``A = P``; ``partial`` si ``∅ ≠ A ⊊ P``; ``cannot_answer`` si ``A = ∅``.

Funciones puras, sin LLM ni E/S. ``execution`` es ``None`` cuando no se generó código.
"""

from __future__ import annotations

from agents.analyst.schemas import QueryPlan, Status, TableSelection
from services.sandbox import ExecutionResult

__all__ = ["Status", "answered_part_ids", "decide_status"]


def answered_part_ids(
    plan: QueryPlan | None,
    selection: TableSelection | None,
    execution: ExecutionResult | None,
) -> set[str]:
    """``A`` de BR-015: partes del plan ``covered`` con resultado válido en la ejecución."""
    if plan is None or selection is None or execution is None:
        return set()
    if not execution.ok or execution.parsed is None:
        return set()
    parts = set(plan.part_ids)
    covered = set(selection.covered_part_ids)
    reported_missing = {m.part_id for m in execution.parsed.missing}
    with_result = {r.part_id for r in execution.parsed.results}
    return (with_result & covered & parts) - reported_missing


def decide_status(
    plan: QueryPlan | None,
    selection: TableSelection | None,
    execution: ExecutionResult | None,
) -> Status:
    """Estado de BR-015 (spec §7.7)."""
    if plan is None or not plan.parts:
        return "cannot_answer"
    answered = answered_part_ids(plan, selection, execution)
    if not answered:
        return "cannot_answer"
    return "answered" if answered == set(plan.part_ids) else "partial"
