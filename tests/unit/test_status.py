"""``decide_status`` (BR-015, FR-039): VC-050 como tabla de verdad, más los casos sin
selección, sin ejecución y con ejecución fallida."""

from __future__ import annotations

import pytest

from agents.analyst.schemas import PartCoverage, QueryPart, QueryPlan, TableSelection
from agents.analyst.status import answered_part_ids, decide_status
from services.sandbox import ExecutionResult, MissingItem, ParsedOutput, ResultItem


def plan_of(n: int) -> QueryPlan:
    return QueryPlan(
        parts=[
            QueryPart(
                id=f"p{i}",
                text=f"parte {i}",
                metrics=[],
                entities=[],
                periods=[],
                operation="valor",
                search_queries=[f"consulta {i}"],
            )
            for i in range(1, n + 1)
        ]
    )


def selection_of(covered: list[str], missing: list[str] = ()) -> TableSelection:
    coverage = [PartCoverage(part_id=p, coverage="covered", table_ids=["t-a"]) for p in covered]
    coverage += [PartCoverage(part_id=p, coverage="missing", missing_reason="falta") for p in missing]
    return TableSelection(selected_table_ids=["t-a"] if covered else [], coverage=coverage, rationale="")


def execution_of(results: list[str], missing: list[str] = (), status: str = "ok") -> ExecutionResult:
    parsed = ParsedOutput(
        results=[ResultItem(part_id=p, label=p, value=1.0, unit="%") for p in results],
        missing=[MissingItem(part_id=p, reason="no está") for p in missing],
    )
    return ExecutionResult(status=status, returncode=0 if status == "ok" else 1, parsed=parsed)


@pytest.mark.parametrize(
    ("n_parts", "covered", "sel_missing", "results", "code_missing", "expected"),
    [
        # todas las partes con resultado → answered
        (2, ["p1", "p2"], [], ["p1", "p2"], [], "answered"),
        (1, ["p1"], [], ["p1"], [], "answered"),
        # 1 de 2 → partial (la otra missing en la selección)
        (2, ["p1"], ["p2"], ["p1"], [], "partial"),
        # 1 de 2 → partial (las dos covered, el código reporta p2 en missing)
        (2, ["p1", "p2"], [], ["p1"], ["p2"], "partial"),
        # 0 → cannot_answer
        (2, ["p1", "p2"], [], [], ["p1", "p2"], "cannot_answer"),
        # parte covered pero reportada en missing por el código → no cuenta
        (1, ["p1"], [], [], ["p1"], "cannot_answer"),
        # resultado para una parte que la selección marcó missing → no cuenta
        (2, ["p1"], ["p2"], ["p1", "p2"], [], "partial"),
    ],
)
def test_vc050_truth_table(
    n_parts: int,
    covered: list[str],
    sel_missing: list[str],
    results: list[str],
    code_missing: list[str],
    expected: str,
) -> None:
    """Given plan, selección y ejecución, then el estado es el de BR-015."""
    status = decide_status(
        plan_of(n_parts), selection_of(covered, sel_missing), execution_of(results, code_missing)
    )

    assert status == expected


def test_vc050_part_in_both_results_and_missing_does_not_count() -> None:
    """Given una parte covered que el código pone en results y en missing (salida que §7.6
    rechazaría), then no cuenta como respondida."""
    execution = execution_of(["p1"], ["p1"])

    assert answered_part_ids(plan_of(1), selection_of(["p1"]), execution) == set()
    assert decide_status(plan_of(1), selection_of(["p1"]), execution) == "cannot_answer"


@pytest.mark.parametrize("status", ["error", "timeout", "rejected", "invalid_output"])
def test_failed_execution_answers_nothing_even_with_parsed_results(status: str) -> None:
    """Given una ejecución que no terminó ok, then ninguna parte cuenta aunque haya resultados."""
    execution = execution_of(["p1"], status=status)

    assert decide_status(plan_of(1), selection_of(["p1"]), execution) == "cannot_answer"


def test_without_execution_selection_or_plan_is_cannot_answer() -> None:
    """Given que no hubo código (execution None), selección o plan, then cannot_answer."""
    assert decide_status(plan_of(2), selection_of(["p1", "p2"]), None) == "cannot_answer"
    assert decide_status(plan_of(2), None, None) == "cannot_answer"
    assert decide_status(None, None, None) == "cannot_answer"
    assert decide_status(plan_of(1), selection_of(["p1"]), ExecutionResult(status="ok")) == "cannot_answer"


def test_decide_status_is_pure() -> None:
    """Given las mismas entradas, then el mismo estado y sin modificar las entradas."""
    plan, selection, execution = plan_of(2), selection_of(["p1", "p2"]), execution_of(["p1"])
    before = (plan.model_dump(), selection.model_dump(), execution.model_dump())

    assert decide_status(plan, selection, execution) == decide_status(plan, selection, execution) == "partial"
    assert (plan.model_dump(), selection.model_dump(), execution.model_dump()) == before
