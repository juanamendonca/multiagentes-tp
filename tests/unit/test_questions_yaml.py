"""VC-091 (FR-071): el set de evaluación ``eval/questions.yaml`` tiene la composición mínima."""

from __future__ import annotations

from pathlib import Path

from eval.run_eval import QUESTIONS_PATH, load_questions

SAMPLE_PDFS = Path(__file__).resolve().parents[2] / "data" / "sample_pdfs"


def test_vc091_question_set_meets_fr071_composition():
    """Given eval/questions.yaml, when se carga, then hay ≥ 8 preguntas con los campos
    obligatorios, ≥ 2 multitabla, ≥ 1 cannot_answer sin tablas y ≥ 1 cannot_answer/partial con
    tabla relacionada."""
    questions = load_questions(QUESTIONS_PATH)

    assert len(questions) >= 8
    for q in questions:
        assert q.id and q.question and q.expected_status in {"answered", "partial", "cannot_answer"}
        assert q.tolerance >= 0
        assert q.notes.strip(), f"{q.id}: falta la nota con la fuente del valor esperado"
    assert sum(len(q.expected_tables) >= 2 for q in questions) >= 2
    assert any(q.expected_status == "cannot_answer" and not q.expected_tables for q in questions)
    assert any(q.expected_status in {"cannot_answer", "partial"} and q.expected_tables for q in questions)


def test_question_set_covers_br014_causes_and_partial():
    """Given el set, then cubre período faltante, conversión externa, comparación faltante y
    al menos un partial; y las tablas esperadas nombran PDFs que existen."""
    questions = load_questions(QUESTIONS_PATH)
    kinds = {k for q in questions for k in q.kind}

    assert {"missing_period", "external_conversion", "missing_comparison", "no_related_table"} <= kinds
    assert any(q.expected_status == "partial" for q in questions)
    files = {p.name for p in SAMPLE_PDFS.glob("*.pdf")}
    for q in questions:
        for table in q.expected_tables:
            assert table.file in files, f"{q.id}: {table.file} no está en data/sample_pdfs"
