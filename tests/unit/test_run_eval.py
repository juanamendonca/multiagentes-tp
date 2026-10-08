"""Runner de evaluación (FR-072, VC-092) con un ``ask`` fake: comparación de estado y valores,
resumen (aciertos, latencias, NFR-006) y escritura de la tabla en ``EVALUATION.md``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agents.analyst.schemas import AnalystResult, Candidate, PartCoverage, TableSelection
from eval.run_eval import (
    END_MARKER,
    START_MARKER,
    Question,
    evaluate,
    load_questions,
    render_section,
    rerender,
    run_questions,
    summarize,
    update_evaluation_md,
    write_results,
)
from services.sandbox import ResultItem


def make_question(**overrides) -> Question:
    fields = {
        "id": "q01",
        "question": "¿Tasa?",
        "expected_status": "answered",
        "expected_answer": [{"label": "tasa", "value": 7.9, "unit": "%"}],
        "tolerance": 0.05,
        "expected_tables": [{"file": "a.pdf", "pages": [8]}],
        "notes": "pág. 8",
    }
    fields.update(overrides)
    return Question.model_validate(fields)


def make_result(status="answered", values=(), *, code_attempts=1, llm_calls=4, tables=(("a.pdf", [8]),)):
    candidates = [
        Candidate(table_id=f"id{i}", score=0.9, name=f"T{i}", file_name=f, pages=p)
        for i, (f, p) in enumerate(tables)
    ]
    selection = TableSelection(
        selected_table_ids=[c.table_id for c in candidates],
        coverage=[PartCoverage(part_id="p1", coverage="covered", table_ids=[c.table_id for c in candidates])],
    )
    return AnalystResult(
        question="¿Tasa?",
        status=status,
        candidates=candidates,
        selection=selection,
        results=[ResultItem(part_id=f"p{i + 1}", value=v, unit="%") for i, v in enumerate(values)],
        code_attempts=code_attempts,
        llm_calls=llm_calls,
        embedding_calls=1,
        run_id="11111111-1111-1111-1111-111111111111",
    )


def test_correct_when_status_and_all_values_match_within_tolerance():
    """Given 7,9 esperado ± 0,05, when el Analista responde 7,93 (más un valor extra), then es
    correcto y las tablas esperadas figuran entre las elegidas."""
    outcome = evaluate(make_question(), make_result(values=[7.93, 99.0]), latency_s=3.0)

    assert outcome.status_ok and outcome.value_ok and outcome.correct
    assert outcome.tables_ok
    assert outcome.retries == 0


@pytest.mark.parametrize(
    ("status", "values", "status_ok", "value_ok"),
    [
        ("answered", [8.0], True, False),  # fuera de tolerancia
        ("partial", [7.9], False, True),  # estado incorrecto
        ("answered", [], True, False),  # sin valores
    ],
)
def test_incorrect_when_status_or_value_differs(status, values, status_ok, value_ok):
    outcome = evaluate(make_question(), make_result(status=status, values=values), latency_s=1.0)

    assert (outcome.status_ok, outcome.value_ok, outcome.correct) == (status_ok, value_ok, False)


def test_abs_values_compare_magnitude_and_values_inside_lists_and_dicts_count():
    """Given una variación −0,5 con abs: true, when el sistema informa 0,5 dentro de una lista y
    los otros valores en un dict, then es correcto."""
    question = make_question(
        expected_answer=[
            {"value": 8.7},
            {"value": 8.2},
            {"value": -0.5, "abs": True},
        ]
    )
    result = make_result(values=[{"2T2025": 8.7, "2T2026": 8.2}, [0.5]])

    assert evaluate(question, result, latency_s=1.0).correct


def test_cannot_answer_is_correct_only_without_numeric_results():
    question = make_question(expected_status="cannot_answer", expected_answer=None, expected_tables=[])

    ok = evaluate(question, make_result(status="cannot_answer", tables=()), latency_s=1.0)
    assert ok.correct and ok.tables_ok is None  # sin cálculo: la columna de tablas no aplica

    wrong = evaluate(question, make_result(status="answered", values=[3.0]), latency_s=1.0)
    assert not wrong.correct and not wrong.tables_ok


def test_question_model_rejects_inconsistent_expectations():
    with pytest.raises(ValueError):
        make_question(expected_status="cannot_answer")  # con valores esperados
    with pytest.raises(ValueError):
        make_question(expected_status="partial", expected_answer=None)


def test_load_questions_rejects_duplicated_ids(tmp_path):
    item = make_question().model_dump()
    path = tmp_path / "q.yaml"
    path.write_text(yaml.safe_dump({"questions": [item, item]}, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="repetidos"):
        load_questions(path)


def test_run_questions_records_exceptions_and_summary_computes_nfrs():
    """Given un ask fake que responde dos preguntas y falla en una tercera, when se corre la
    evaluación, then la excepción queda como error (no corta) y el resumen calcula aciertos,
    latencias y el máximo de llamadas contra 4 + MAX_CODE_RETRIES."""
    questions = [make_question(id="q01"), make_question(id="q02"), make_question(id="q03")]
    answers = {
        "q01": make_result(values=[7.9], code_attempts=2, llm_calls=5),
        "q02": make_result(values=[1.0]),
    }
    order = iter(["q01", "q02", "q03"])

    def fake_ask(_question: str) -> AnalystResult:
        qid = next(order)
        if qid not in answers:
            raise RuntimeError("proxy caído")
        return answers[qid]

    seen = []
    outcomes = run_questions(
        questions, fake_ask, lambda run_id: f"https://trazas/{run_id}", on_outcome=seen.append
    )
    summary = summarize(outcomes, max_code_retries=3)

    assert [o.correct for o in outcomes] == [True, False, False]
    assert outcomes[0].retries == 1 and outcomes[0].trace_url.startswith("https://trazas/")
    assert outcomes[2].obtained_status == "error" and "proxy caído" in outcomes[2].error
    assert len(seen) == 3
    assert summary.n_correct == 1 and summary.accuracy == pytest.approx(1 / 3)
    assert summary.llm_calls_max == 5 and summary.max_llm_calls_allowed == 7 and summary.nfr006_ok
    assert not summary.nfr012_ok
    assert summary.latency_max_s >= summary.latency_median_s >= 0


def test_render_and_update_evaluation_md_replaces_only_the_marked_section(tmp_path):
    """Given un EVALUATION.md con texto propio y una sección vieja, when se escribe la tabla,
    then hay una fila por pregunta, mediana/máximo de latencia y % de aciertos, y el resto del
    archivo no cambia; sin archivo, se crea."""
    questions = [make_question(id="q01"), make_question(id="q02", question="¿Otra | con barra?")]
    outcomes = [
        evaluate(questions[0], make_result(values=[7.9]), latency_s=10.0, trace_url="https://t/1"),
        evaluate(questions[1], make_result(status="partial", values=[7.9]), latency_s=20.0),
    ]
    summary = summarize(outcomes, max_code_retries=3)
    section = render_section(outcomes, summary, {"date": "2026-10-07", "model": "m", "llm_cache": "false"})

    path = tmp_path / "EVALUATION.md"
    old = f"# Eval\n\nIntro propia.\n\n{START_MARKER}\nvieja\n{END_MARKER}\n\nCierre.\n"
    path.write_text(old, encoding="utf-8")
    update_evaluation_md(section, path)
    text = path.read_text(encoding="utf-8")

    assert "Intro propia." in text and "Cierre." in text and "vieja" not in text
    assert text.count(START_MARKER) == 1 and text.count(END_MARKER) == 1
    rows = [line for line in text.splitlines() if line.startswith("| q0")]
    assert len(rows) == 2
    assert "[traza](https://t/1)" in rows[0] and "**sí**" in rows[0]
    assert "\\|" in rows[1]  # la barra de la pregunta no rompe la tabla
    assert "50.0 %" in text and "mediana 15.0 s" in text and "máximo 20.0 s" in text

    new_path = tmp_path / "nuevo.md"
    update_evaluation_md(section, new_path)
    assert new_path.read_text(encoding="utf-8").startswith("# Evaluación")


def test_write_results_serializes_outcomes(tmp_path):
    outcome = evaluate(make_question(), make_result(values=[7.9]), latency_s=1.5)
    summary = summarize([outcome], max_code_retries=3)

    path = tmp_path / "results" / "latest.json"
    write_results(path, [outcome], summary, {"date": "x"})
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    assert data["summary"]["n_correct"] == 1
    assert data["outcomes"][0]["obtained"][0]["value"] == 7.9


def test_rerender_rewrites_section_from_saved_results(tmp_path):
    """Given resultados guardados, when se re-renderiza, then EVALUATION.md se reescribe sin
    llamar al Analista y la columna de tablas se recalcula con el yaml."""
    question = make_question()
    questions_path = tmp_path / "q.yaml"
    questions_path.write_text(
        yaml.safe_dump({"questions": [question.model_dump()]}, allow_unicode=True), encoding="utf-8"
    )
    outcome = evaluate(question, make_result(values=[7.9]), latency_s=1.0)
    outcome = outcome.model_copy(update={"tables_ok": False})
    results_path = tmp_path / "run.json"
    summary = summarize([outcome], max_code_retries=3)
    write_results(results_path, [outcome], summary, {"max_code_retries": 3})
    target = tmp_path / "EVALUATION.md"

    outcomes, summary = rerender(results_path, questions_path, target)

    assert outcomes[0].tables_ok is True and summary.n_correct == 1
    assert "| q01 |" in target.read_text(encoding="utf-8")
