"""Runner de evaluación: ``python -m eval.run_eval [--ids q01 q02 ...]`` (spec FR-072, VC-092).

Corre el Analista (``ask``) sobre cada pregunta de ``eval/questions.yaml``, compara estado y
valores con lo esperado, mide latencia, cuenta llamadas LLM (``AnalystResult.llm_calls``, que
incluye las servidas por la caché si ``LLM_CACHE=true``) y arma el link a la traza de LangSmith.

Con el set completo escribe ``eval/results/latest.json`` (y ``eval/results/<tag>.json`` si se
pasa ``--tag``) y reemplaza en ``EVALUATION.md`` la sección entre ``<!-- EVAL:START -->`` y
``<!-- EVAL:END -->`` (crea el archivo si no existe). Con ``--ids`` (re-corrida de algunas
preguntas) solo escribe ``eval/results/subset-<fecha>.json`` y no toca ``EVALUATION.md``.
``--render <json>`` reescribe la sección de ``EVALUATION.md`` desde resultados ya guardados (sin
llamar al Analista).

Criterio de corrección (D-039): una pregunta es correcta si el estado obtenido es el esperado
**y** los valores coinciden: con ``expected_answer: null`` no debe haber resultados numéricos;
con una lista, cada valor esperado debe aparecer entre los valores obtenidos (números, listas o
dicts aplanados) a distancia ≤ ``tolerance`` (``abs: true`` compara valores absolutos). Los
valores extra no penalizan.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from agents.analyst.schemas import AnalystResult
from services.sandbox import ResultItem

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = ROOT / "eval" / "questions.yaml"
RESULTS_DIR = ROOT / "eval" / "results"
EVALUATION_PATH = ROOT / "EVALUATION.md"
START_MARKER = "<!-- EVAL:START -->"
END_MARKER = "<!-- EVAL:END -->"

Status = Literal["answered", "partial", "cannot_answer"]
AskFn = Callable[[str], AnalystResult]
UrlFn = Callable[[str | None], str | None]

# NFR-009 / NFR-012
MAX_MEDIAN_LATENCY_S = 60.0
MAX_LATENCY_S = 180.0
TARGET_ACCURACY = 0.75
MAX_EMBEDDING_CALLS = 3


class ExpectedValue(BaseModel):
    label: str = ""
    value: float
    unit: str | None = None
    abs: bool = False


class ExpectedTable(BaseModel):
    file: str
    pages: list[int] = Field(min_length=1)


class Question(BaseModel):
    id: str
    question: str
    kind: list[str] = Field(default_factory=list)
    expected_status: Status
    expected_answer: list[ExpectedValue] | None
    tolerance: float = Field(ge=0)
    expected_tables: list[ExpectedTable]
    vc: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("pregunta vacía")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Question:
        if self.expected_status == "cannot_answer" and self.expected_answer:
            raise ValueError(f"{self.id}: cannot_answer no lleva valores esperados")
        if self.expected_status == "partial" and not self.expected_answer:
            raise ValueError(f"{self.id}: partial necesita al menos un valor esperado")
        if self.expected_status == "answered" and not self.expected_answer:
            raise ValueError(f"{self.id}: answered necesita valores esperados")
        return self


def load_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    """Lee y valida ``questions.yaml`` (ids únicos)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    questions = [Question.model_validate(item) for item in data.get("questions") or []]
    ids = [q.id for q in questions]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        raise ValueError(f"ids repetidos en {path}: {duplicated}")
    return questions


# --------------------------------------------------------------------------- comparación


def flatten_values(results: Iterable[ResultItem]) -> list[float]:
    """Todos los números de los resultados (``value`` número, lista o dict)."""
    values: list[float] = []
    for item in results:
        raw = item.value
        if isinstance(raw, Mapping):
            values.extend(float(v) for v in raw.values())
        elif isinstance(raw, list | tuple):
            values.extend(float(v) for v in raw)
        else:
            values.append(float(raw))
    return [v for v in values if math.isfinite(v)]


def value_matches(expected: ExpectedValue, obtained: float, tolerance: float) -> bool:
    if expected.abs:
        return abs(abs(obtained) - abs(expected.value)) <= tolerance + 1e-9
    return abs(obtained - expected.value) <= tolerance + 1e-9


def check_values(question: Question, results: Sequence[ResultItem]) -> bool:
    """¿Los valores obtenidos coinciden con lo esperado? (ver docstring del módulo)."""
    obtained = flatten_values(results)
    if not question.expected_answer:
        return not obtained
    tol = question.tolerance
    return all(any(value_matches(e, v, tol) for v in obtained) for e in question.expected_answer)


def tables_match(
    question: Question, selected: Sequence[Mapping[str, Any]], obtained_status: str
) -> bool | None:
    """Informativo: cada tabla esperada está entre las que usó el Analista (mismo archivo y alguna
    página en común). ``None`` (no aplica) si no hubo cálculo (``cannot_answer`` o error)."""
    if obtained_status not in ("answered", "partial"):
        return None
    if not question.expected_tables:
        return not selected
    return all(
        any(t["file"] == e.file and set(t["pages"]) & set(e.pages) for t in selected)
        for e in question.expected_tables
    )


class QuestionOutcome(BaseModel):
    id: str
    question: str
    kind: list[str]
    vc: list[str] = Field(default_factory=list)
    expected_status: Status
    obtained_status: str
    status_ok: bool
    expected_answer: list[ExpectedValue] | None
    tolerance: float
    obtained: list[dict[str, Any]] = Field(default_factory=list)
    value_ok: bool
    correct: bool
    tables_ok: bool | None
    selected_tables: list[dict[str, Any]] = Field(default_factory=list)
    missing: list[dict[str, Any]] = Field(default_factory=list)
    conversions: list[str] = Field(default_factory=list)
    code_attempts: int = 0
    retries: int = 0
    llm_calls: int = 0
    embedding_calls: int = 0
    latency_s: float = 0.0
    run_id: str | None = None
    trace_url: str | None = None
    answer: str = ""
    error: str | None = None


def evaluate(
    question: Question,
    result: AnalystResult | None,
    *,
    latency_s: float,
    trace_url: str | None = None,
    error: str | None = None,
) -> QuestionOutcome:
    """Compara un resultado del Analista con lo esperado (función pura)."""
    if result is None:
        return QuestionOutcome(
            id=question.id,
            question=question.question,
            kind=question.kind,
            vc=question.vc,
            expected_status=question.expected_status,
            obtained_status="error",
            status_ok=False,
            expected_answer=question.expected_answer,
            tolerance=question.tolerance,
            value_ok=False,
            correct=False,
            tables_ok=None,
            latency_s=latency_s,
            error=error or "sin resultado",
        )
    selected = [
        {"table_id": c.table_id, "file": c.file_name, "pages": list(c.pages)} for c in result.selected_tables
    ]
    status_ok = result.status == question.expected_status
    value_ok = check_values(question, result.results)
    return QuestionOutcome(
        id=question.id,
        question=question.question,
        kind=question.kind,
        vc=question.vc,
        expected_status=question.expected_status,
        obtained_status=result.status,
        status_ok=status_ok,
        expected_answer=question.expected_answer,
        tolerance=question.tolerance,
        obtained=[r.model_dump() for r in result.results],
        value_ok=value_ok,
        correct=status_ok and value_ok,
        tables_ok=tables_match(question, selected, result.status),
        selected_tables=selected,
        missing=[{"part_id": m.part_id, "reason": m.reason} for m in result.missing],
        conversions=list(result.conversions),
        code_attempts=result.code_attempts,
        retries=max(result.code_attempts - 1, 0),
        llm_calls=result.llm_calls,
        embedding_calls=result.embedding_calls,
        latency_s=latency_s,
        run_id=result.run_id,
        trace_url=trace_url,
        answer=result.answer,
        error=error,
    )


class Summary(BaseModel):
    n: int
    n_correct: int
    accuracy: float
    n_status_ok: int
    latency_median_s: float
    latency_max_s: float
    llm_calls_max: int
    llm_calls_total: int
    embedding_calls_max: int
    retries_total: int
    max_llm_calls_allowed: int
    nfr006_ok: bool
    nfr009_ok: bool
    nfr012_ok: bool


def summarize(outcomes: Sequence[QuestionOutcome], *, max_code_retries: int) -> Summary:
    n = len(outcomes)
    latencies = [o.latency_s for o in outcomes] or [0.0]
    n_correct = sum(o.correct for o in outcomes)
    accuracy = n_correct / n if n else 0.0
    allowed = 4 + max_code_retries
    llm_max = max((o.llm_calls for o in outcomes), default=0)
    emb_max = max((o.embedding_calls for o in outcomes), default=0)
    median = statistics.median(latencies)
    maximum = max(latencies)
    return Summary(
        n=n,
        n_correct=n_correct,
        accuracy=accuracy,
        n_status_ok=sum(o.status_ok for o in outcomes),
        latency_median_s=median,
        latency_max_s=maximum,
        llm_calls_max=llm_max,
        llm_calls_total=sum(o.llm_calls for o in outcomes),
        embedding_calls_max=emb_max,
        retries_total=sum(o.retries for o in outcomes),
        max_llm_calls_allowed=allowed,
        nfr006_ok=llm_max <= allowed and emb_max <= MAX_EMBEDDING_CALLS,
        nfr009_ok=median <= MAX_MEDIAN_LATENCY_S and maximum <= MAX_LATENCY_S,
        nfr012_ok=accuracy >= TARGET_ACCURACY,
    )


def run_questions(
    questions: Sequence[Question],
    ask_fn: AskFn,
    url_fn: UrlFn | None = None,
    *,
    on_outcome: Callable[[QuestionOutcome], None] | None = None,
) -> list[QuestionOutcome]:
    """Corre ``ask_fn`` por pregunta; una excepción se registra como error y se sigue."""
    outcomes: list[QuestionOutcome] = []
    for question in questions:
        started = time.perf_counter()
        try:
            result = ask_fn(question.question)
        except Exception as exc:  # una pregunta rota no corta la evaluación
            outcome = evaluate(
                question,
                None,
                latency_s=time.perf_counter() - started,
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
        else:
            latency = time.perf_counter() - started
            url = url_fn(result.run_id) if url_fn is not None else None
            outcome = evaluate(question, result, latency_s=latency, trace_url=url)
        outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)
    return outcomes


# --------------------------------------------------------------------------- informe


def format_number(value: float) -> str:
    """Número en formato es-AR corto (coma decimal, hasta 4 decimales)."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if text not in ("-0", "") else "0"


def format_expected(question_answer: list[ExpectedValue] | None) -> str:
    if not question_answer:
        return "—"
    parts = []
    for e in question_answer:
        unit = f" {e.unit}" if e.unit else ""
        value = f"±{format_number(abs(e.value))}" if e.abs else format_number(e.value)
        parts.append(f"{value}{unit}")
    return "; ".join(parts)


def format_obtained(obtained: Sequence[Mapping[str, Any]]) -> str:
    if not obtained:
        return "—"
    parts = []
    for item in obtained:
        raw = item.get("value")
        if isinstance(raw, Mapping):
            text = ", ".join(f"{k}: {format_number(float(v))}" for k, v in raw.items())
        elif isinstance(raw, list | tuple):
            text = ", ".join(format_number(float(v)) for v in raw)
        else:
            text = format_number(float(raw))
        unit = f" {item['unit']}" if item.get("unit") else ""
        parts.append(f"{text}{unit}")
    return "; ".join(parts)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _mark(ok: bool) -> str:
    return "✔" if ok else "✘"


def render_section(
    outcomes: Sequence[QuestionOutcome], summary: Summary, meta: Mapping[str, Any]
) -> str:
    """Sección de resultados (entre marcadores) para ``EVALUATION.md``."""
    lines = [
        START_MARKER,
        "## Resultados de la corrida",
        "",
        "_Sección generada por `python -m eval.run_eval`; no editar a mano._",
        "",
        f"- Fecha: {meta.get('date', '')} · modelo: `{meta.get('model', '')}` · "
        f"`LLM_CACHE={meta.get('llm_cache', '')}` · `MAX_CODE_RETRIES={meta.get('max_code_retries', '')}` · "
        f"preguntas: {summary.n}",
        f"- **Aciertos (estado y valor correctos): {summary.n_correct}/{summary.n} = "
        f"{summary.accuracy * 100:.1f} %** (objetivo NFR-012: ≥ {TARGET_ACCURACY * 100:.0f} %, "
        f"{'alcanzado' if summary.nfr012_ok else 'no alcanzado'}); estado correcto en "
        f"{summary.n_status_ok}/{summary.n}.",
        f"- Latencia por pregunta: mediana {summary.latency_median_s:.1f} s, máximo "
        f"{summary.latency_max_s:.1f} s (NFR-009: mediana ≤ {MAX_MEDIAN_LATENCY_S:.0f} s y máximo ≤ "
        f"{MAX_LATENCY_S:.0f} s, {'cumple' if summary.nfr009_ok else 'no cumple'}).",
        f"- Llamadas de chat por pregunta: máximo {summary.llm_calls_max} (límite "
        f"{summary.max_llm_calls_allowed}), total {summary.llm_calls_total}; embeddings: máximo "
        f"{summary.embedding_calls_max} (límite {MAX_EMBEDDING_CALLS}); NFR-006 "
        f"{'cumple' if summary.nfr006_ok else 'no cumple'}. Reintentos de código en total: "
        f"{summary.retries_total}.",
        "",
        "| Id | Pregunta | Estado esperado | Estado obtenido | Valor esperado | Valor obtenido | Correcto "
        "| Tablas esperadas | Reintentos | Latencia (s) | Llamadas LLM | Traza |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        trace = f"[traza]({o.trace_url})" if o.trace_url else (f"`{o.run_id}`" if o.run_id else "—")
        obtained = format_obtained(o.obtained)
        if o.error:
            obtained = f"error: {o.error}"
        lines.append(
            "| "
            + " | ".join(
                [
                    o.id,
                    _cell(o.question),
                    o.expected_status,
                    f"{o.obtained_status} {_mark(o.status_ok)}",
                    _cell(format_expected(o.expected_answer)),
                    f"{_cell(obtained)} {_mark(o.value_ok)}",
                    "**sí**" if o.correct else "no",
                    "—" if o.tables_ok is None else _mark(o.tables_ok),
                    str(o.retries),
                    f"{o.latency_s:.1f}",
                    str(o.llm_calls),
                    trace,
                ]
            )
            + " |"
        )
    lines += [
        "",
        "Valor esperado con `±`: se compara el valor absoluto (variaciones). \"Tablas esperadas\": "
        "las tablas de `expected_tables` están entre las que usó el Analista (informativo, no "
        "afecta el acierto; \"—\" si no hubo cálculo). Detalle por pregunta (faltantes, conversiones, "
        "tablas, respuesta) en `eval/results/latest.json`.",
        END_MARKER,
    ]
    return "\n".join(lines) + "\n"


def update_evaluation_md(section: str, path: Path = EVALUATION_PATH) -> None:
    """Reemplaza la sección entre marcadores (o la agrega / crea el archivo)."""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        start, end = text.find(START_MARKER), text.find(END_MARKER)
        if start != -1 and end != -1 and end > start:
            text = text[:start] + section.rstrip("\n") + text[end + len(END_MARKER) :]
        else:
            text = text.rstrip("\n") + "\n\n" + section
    else:
        text = "# Evaluación\n\n" + section
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def write_results(
    path: Path, outcomes: Sequence[QuestionOutcome], summary: Summary, meta: Mapping[str, Any]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": dict(meta),
        "summary": summary.model_dump(),
        "outcomes": [o.model_dump(mode="json") for o in outcomes],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- CLI


def _print_outcome(outcome: QuestionOutcome) -> None:
    print(
        f"{outcome.id}: {outcome.obtained_status} (esperado {outcome.expected_status}) "
        f"valores={format_obtained(outcome.obtained)} correcto={outcome.correct} "
        f"reintentos={outcome.retries} llm={outcome.llm_calls} {outcome.latency_s:.1f}s"
        + (f" ERROR {outcome.error}" if outcome.error else ""),
        flush=True,
    )


def rerender(
    path: Path, questions_path: Path = QUESTIONS_PATH, evaluation_path: Path | None = None
) -> tuple[list[QuestionOutcome], Summary]:
    """Vuelve a escribir la sección de ``EVALUATION.md`` desde un JSON de resultados guardado, sin
    llamar al Analista; recalcula la columna informativa de tablas con ``questions.yaml``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    meta = data["meta"]
    by_id = {q.id: q for q in load_questions(questions_path)}
    outcomes = []
    for raw in data["outcomes"]:
        outcome = QuestionOutcome.model_validate(raw)
        if outcome.id in by_id:
            question = by_id[outcome.id]
            outcome.tables_ok = tables_match(question, outcome.selected_tables, outcome.obtained_status)
        outcomes.append(outcome)
    summary = summarize(outcomes, max_code_retries=int(meta.get("max_code_retries", 3)))
    write_results(Path(path), outcomes, summary, meta)
    update_evaluation_md(render_section(outcomes, summary, meta), evaluation_path or EVALUATION_PATH)
    return outcomes, summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.run_eval", description=__doc__.splitlines()[0])
    parser.add_argument("--ids", nargs="+", help="solo estas preguntas (no actualiza EVALUATION.md)")
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument("--tag", help="además guarda eval/results/<tag>.json (corrida completa)")
    parser.add_argument(
        "--render", type=Path, metavar="JSON", help="solo reescribe EVALUATION.md desde un JSON guardado"
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.render is not None:
        _, summary = rerender(args.render, args.questions)
        print(f"aciertos {summary.n_correct}/{summary.n}; escrito {EVALUATION_PATH}")
        return 0

    from agents.analyst.graph import ask
    from agents.analyst.nodes import build_default_deps
    from services.config import ConfigError, load_settings
    from services.llm import enable_cache_if_configured
    from services.tracing import RunUrlBuilder

    questions = load_questions(args.questions)
    if args.ids:
        unknown = sorted(set(args.ids) - {q.id for q in questions})
        if unknown:
            parser.error(f"ids desconocidos: {unknown}")
        questions = [q for q in questions if q.id in set(args.ids)]
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    enable_cache_if_configured(settings)
    deps = build_default_deps(settings)
    url_builder = RunUrlBuilder(settings)

    started = datetime.now().astimezone()
    outcomes = run_questions(questions, lambda q: ask(q, deps), url_builder, on_outcome=_print_outcome)
    summary = summarize(outcomes, max_code_retries=settings.max_code_retries)
    meta = {
        "date": started.strftime("%Y-%m-%d %H:%M"),
        "model": settings.llm_model,
        "llm_cache": str(settings.llm_cache).lower(),
        "max_code_retries": settings.max_code_retries,
        "retrieval_top_k": settings.retrieval_top_k,
        "questions": [q.id for q in questions],
        "tag": args.tag,
    }
    if args.ids:
        out = RESULTS_DIR / f"subset-{started.strftime('%Y%m%d-%H%M%S')}.json"
        write_results(out, outcomes, summary, meta)
    else:
        write_results(RESULTS_DIR / "latest.json", outcomes, summary, meta)
        if args.tag:
            write_results(RESULTS_DIR / f"{args.tag}.json", outcomes, summary, meta)
        update_evaluation_md(render_section(outcomes, summary, meta))
        out = EVALUATION_PATH
    print(
        f"\naciertos {summary.n_correct}/{summary.n} ({summary.accuracy * 100:.1f} %), estado ok "
        f"{summary.n_status_ok}/{summary.n}, latencia mediana {summary.latency_median_s:.1f} s / máx "
        f"{summary.latency_max_s:.1f} s, llamadas LLM máx {summary.llm_calls_max}; escrito {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
