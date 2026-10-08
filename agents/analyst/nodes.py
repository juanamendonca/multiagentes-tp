"""Nodos del grafo Analista (spec §4.3) y sus dependencias inyectables.

Cada nodo recibe el estado (y ``AnalystDeps``) y devuelve **solo** las claves que actualiza;
esas actualizaciones son los eventos de ``graph.stream(stream_mode="updates")``.

Orden: ``understand_query`` → ``retrieve`` → ``select_tables`` → ``generate_code`` →
``execute`` (⇄ ``generate_code`` mientras falle y queden intentos, FR-037) → ``decide_status`` →
``respond``. ``generate_code`` pide al LLM un programa pandas sobre los alias ``t1..tn``
(FR-035) y ``execute`` lo corre en el sandbox (FR-036, BR-012/BR-013); al agotar los
``1 + MAX_CODE_RETRIES`` intentos las partes ``covered`` pasan a ``missing`` (FR-038). Contrato
del estado en ``agents/analyst/state.py``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openai
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from agents.analyst.prompts import (
    build_codegen_messages,
    build_respond_messages,
    build_select_messages,
    build_understand_messages,
    describe_feedback,
    fallback_answer,
)
from agents.analyst.schemas import (
    Candidate,
    MissingPart,
    PartCoverage,
    QueryPart,
    QueryPlan,
    TableSelection,
)
from agents.analyst.state import AnalystState
from agents.analyst.status import answered_part_ids, decide_status
from services.config import Settings
from services.sandbox import ExecutionResult, run_in_sandbox
from services.table_store import TableStore
from services.vectorstore import Match, VectorStore, decode_metadata, open_index

logger = logging.getLogger(__name__)

NO_CANDIDATES_REASON = "no hay tablas indexadas relacionadas"
EXHAUSTED_REASON = "falló la ejecución tras {attempts} intentos: {error}"
NO_CODE_ERROR = "la respuesta del modelo no contenía código Python"
DEFAULT_MISSING_REASON = "ninguna tabla disponible contiene este dato"
NOT_EVALUATED_REASON = "el selector de tablas no evaluó esta parte"
COVERED_WITHOUT_TABLE_REASON = "el selector la marcó como cubierta sin indicar una tabla candidata válida"
SELECTION_ERROR_REASON = "no se pudo interpretar la selección de tablas del modelo"
NO_CODE_REASON = "no se ejecutó código para calcular esta parte"
NO_RESULT_REASON = "el código no devolvió un resultado para esta parte"
MAX_ERROR_SUMMARY = 300
# Salidas del LLM que no respetan el formato pedido: se degradan sin cortar la pregunta. El resto
# (conexión, autenticación, presupuesto, 4xx/5xx del proxy) se propaga y lo muestra la UI (FR-054).
RECOVERABLE_LLM_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,  # incluye ValidationError de Pydantic y OutputParserException
    openai.LengthFinishReasonError,
    openai.ContentFilterFinishReasonError,
)

_UNSET: Any = object()

SandboxRunner = Callable[..., ExecutionResult]
"""Firma de ``run_in_sandbox(code, tables, timeout_s, *, covered_part_ids=...)``."""


@dataclass
class AnalystDeps:
    """Dependencias del grafo, inyectables para tests.

    - ``chat_model``: ``with_structured_output(QueryPlan | TableSelection)`` e ``invoke`` (respond).
    - ``embeddings``: ``embed_documents`` con todas las consultas de búsqueda en **una** llamada.
    - ``vector_store_factory``: devuelve el ``VectorStore`` o ``None`` si el índice no existe; se
      llama una sola vez y recién en ``retrieve``. Por defecto ``open_index`` (solo lectura: el
      Analista nunca crea ni escribe el índice).
    - ``table_store``: almacén Parquet + JSON (FR-042: candidatas sin Parquet se descartan; el
      codegen lee ``df.head(5)``/dtypes y el sandbox recibe ``parquet_path``).
    - ``sandbox``: ``run_in_sandbox`` (BR-012); los tests lo reemplazan por un fake.
    """

    settings: Settings
    chat_model: BaseChatModel
    embeddings: Embeddings
    vector_store_factory: Callable[[], VectorStore | None]
    table_store: TableStore
    sandbox: SandboxRunner = run_in_sandbox
    _vector_store: Any = field(default=_UNSET, init=False, repr=False)

    @property
    def max_code_attempts(self) -> int:
        """Intentos de generación de código: ``1 + MAX_CODE_RETRIES`` (BR-011)."""
        return 1 + self.settings.max_code_retries

    def vector_store(self) -> VectorStore | None:
        if self._vector_store is _UNSET:
            self._vector_store = self.vector_store_factory()
        return self._vector_store


def build_default_deps(settings: Settings) -> AnalystDeps:
    """Dependencias reales: chat y embeddings del proxy LiteLLM, Pinecone en solo lectura
    (``open_index``, perezoso) y ``TableStore(settings.tables_dir)``. No hace red al construirse."""
    from services.llm import get_chat_model, get_embeddings

    return AnalystDeps(
        settings=settings,
        chat_model=get_chat_model(settings),
        embeddings=get_embeddings(settings),
        vector_store_factory=lambda: open_index(settings),
        table_store=TableStore(settings.tables_dir),
    )


def _short(value: object) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= MAX_ERROR_SUMMARY else text[: MAX_ERROR_SUMMARY - 1] + "…"


def _unique(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --- understand_query ----------------------------------------------------------------------------


def fallback_plan(question: str) -> QueryPlan:
    """Plan de una sola parte (la pregunta entera) si el LLM no devolvió un plan válido."""
    text = " ".join(question.split())
    return QueryPlan(
        parts=[
            QueryPart(
                id="p1",
                text=text,
                metrics=[],
                entities=[],
                periods=[],
                operation="valor",
                search_queries=[text[:500]],
            )
        ]
    )


def understand_query_node(state: AnalystState, deps: AnalystDeps) -> dict[str, Any]:
    """FR-030: ``QueryPlan`` con 1–5 partes y 1–3 consultas de búsqueda por parte."""
    question = state["question"]
    structured = deps.chat_model.with_structured_output(QueryPlan)
    warnings: list[str] = []
    try:
        plan = structured.invoke(build_understand_messages(question))
        if not isinstance(plan, QueryPlan):  # pragma: no cover - with_structured_output da el schema
            plan = QueryPlan.model_validate(plan)
    except RECOVERABLE_LLM_ERRORS as exc:
        plan = fallback_plan(question)
        warnings.append(
            "el plan de la pregunta no respetó el formato pedido; se busca con la pregunta completa "
            f"({_short(exc)})"
        )
    return {"plan": plan, "llm_calls": 1, "warnings": warnings}


# --- retrieve ------------------------------------------------------------------------------------


def _candidate(match: Match) -> Candidate:
    meta = decode_metadata(match.metadata)
    return Candidate(
        table_id=match.id,
        score=float(match.score),
        name=str(meta.get("name") or match.id),
        description=str(meta.get("description") or ""),
        columns=meta.get("columns") or [],
        row_headers=[str(h) for h in meta.get("row_headers") or []],
        file_name=str(meta.get("file_name") or ""),
        pages=meta.get("pages") or [],
        section=meta.get("section"),
        n_rows=meta.get("n_rows") or 0,
        n_cols=meta.get("n_cols") or 0,
        preview_csv=str(meta.get("preview_csv") or ""),
        metadata_quality=str(meta.get("metadata_quality") or "ok"),
    )


def merge_matches(results: Sequence[tuple[str, str, Sequence[Match]]]) -> tuple[list[Candidate], list[str]]:
    """FR-031: deduplica por ``table_id`` conservando el score **máximo** y registrando qué
    partes/consultas trajeron cada tabla. ``results`` = ``(part_id, consulta, matches)``.
    Orden: score descendente, después ``table_id``. Devuelve también avisos de metadata ilegible."""
    by_id: dict[str, Candidate] = {}
    warnings: list[str] = []
    for part_id, query, matches in results:
        for match in matches:
            current = by_id.get(match.id)
            if current is None:
                try:
                    current = _candidate(match)
                except Exception as exc:  # metadata corrupta: se saltea esa tabla
                    message = f"tabla {match.id} con metadata ilegible en Pinecone; se ignora ({_short(exc)})"
                    if message not in warnings:
                        warnings.append(message)
                    continue
                by_id[match.id] = current
            current.score = max(current.score, float(match.score))
            if part_id not in current.matched_parts:
                current.matched_parts.append(part_id)
            if query not in current.matched_queries:
                current.matched_queries.append(query)
    candidates = sorted(by_id.values(), key=lambda c: (-c.score, c.table_id))
    return candidates, warnings


def missing_data_warning(candidate: Candidate) -> str:
    """Aviso de FR-042 para una candidata sin Parquet local."""
    file_name = candidate.file_name or "(archivo desconocido)"
    return f"tabla {candidate.name} indexada pero sin datos locales; reingerir {file_name}"


def retrieve_node(state: AnalystState, deps: AnalystDeps) -> dict[str, Any]:
    """FR-031 / FR-032 / FR-042: consulta Pinecone con cada consulta de búsqueda (top-k),
    deduplica por ``table_id`` con el score máximo y descarta las candidatas sin Parquet local.

    Embeddings: una sola llamada a ``embed_documents`` con las consultas distintas del plan
    (NFR-006). Si el índice no existe, no hay candidatas (y no se calcula ningún embedding).
    """
    plan: QueryPlan = state["plan"]
    pairs = [(part.id, query) for part in plan.parts for query in part.search_queries]
    queries = _unique([query for _, query in pairs])
    store = deps.vector_store()
    if store is None:
        name = deps.settings.pinecone_index_name
        return {
            "candidates": [],
            "warnings": [f"el índice de Pinecone {name} no existe todavía: ingerí al menos un PDF"],
        }
    vectors = deps.embeddings.embed_documents(queries) if queries else []
    by_query = {
        query: store.query(vector, deps.settings.retrieval_top_k)
        for query, vector in zip(queries, vectors, strict=True)
    }
    candidates, warnings = merge_matches([(part_id, query, by_query[query]) for part_id, query in pairs])
    kept: list[Candidate] = []
    for candidate in candidates:
        if deps.table_store.table_exists(candidate.table_id):
            kept.append(candidate)
        else:
            warnings.append(missing_data_warning(candidate))
    return {"candidates": kept, "warnings": warnings, "embedding_calls": 1 if queries else 0}


def route_after_retrieve(state: AnalystState) -> str:
    """FR-032: sin candidatas se salta la selección y el código."""
    return "select_tables" if state.get("candidates") else "decide_status"


# --- select_tables -------------------------------------------------------------------------------


def normalize_selection(
    raw: TableSelection, plan: QueryPlan, candidates: Sequence[Candidate]
) -> tuple[TableSelection, list[str]]:
    """Ajusta la salida del LLM a lo que el resto del grafo asume (D-030):

    - una entrada de cobertura por parte del plan, en orden (las que faltan → ``missing`` con
      ``NOT_EVALUATED_REASON``; ids desconocidos o repetidos se ignoran);
    - ``table_ids`` filtrados a las candidatas; una parte ``covered`` sin tablas válidas pasa a
      ``missing``; una ``missing`` sin motivo recibe ``DEFAULT_MISSING_REASON``;
    - ``selected_table_ids`` = las tablas de las partes ``covered`` (primero en el orden que dio
      el LLM), sin repetir; sin partes cubiertas queda vacío.

    Devuelve la selección y avisos sobre ids inventados por el LLM.
    """
    valid = {c.table_id for c in candidates}
    part_ids = set(plan.part_ids)
    warnings: list[str] = []
    unknown: list[str] = []
    by_part: dict[str, PartCoverage] = {}
    for item in raw.coverage:
        if item.part_id not in part_ids or item.part_id in by_part:
            continue
        ids = _unique([t for t in item.table_ids if t in valid])
        unknown += [t for t in item.table_ids if t not in valid]
        if item.coverage == "covered" and ids:
            by_part[item.part_id] = PartCoverage(
                part_id=item.part_id,
                coverage="covered",
                table_ids=ids,
                missing_reason=None,
                related_table_note=None,
            )
        elif item.coverage == "covered":
            by_part[item.part_id] = PartCoverage(
                part_id=item.part_id,
                coverage="missing",
                table_ids=[],
                missing_reason=COVERED_WITHOUT_TABLE_REASON,
                related_table_note=None,
            )
        else:
            by_part[item.part_id] = PartCoverage(
                part_id=item.part_id,
                coverage="missing",
                table_ids=ids,
                missing_reason=(item.missing_reason or "").strip() or DEFAULT_MISSING_REASON,
                related_table_note=(item.related_table_note or "").strip() or None,
            )
    coverage = [
        by_part.get(pid)
        or PartCoverage(
            part_id=pid,
            coverage="missing",
            table_ids=[],
            missing_reason=NOT_EVALUATED_REASON,
            related_table_note=None,
        )
        for pid in plan.part_ids
    ]
    covered_tables = _unique([t for c in coverage if c.coverage == "covered" for t in c.table_ids])
    selected = _unique([t for t in raw.selected_table_ids if t in covered_tables] + covered_tables)
    unknown += [t for t in raw.selected_table_ids if t not in valid]
    if unknown:
        warnings.append(
            f"el selector nombró tablas que no están entre las candidatas: {', '.join(_unique(unknown))}"
        )
    return TableSelection(selected_table_ids=selected, coverage=coverage, rationale=raw.rationale), warnings


def select_tables_node(state: AnalystState, deps: AnalystDeps) -> dict[str, Any]:
    """FR-033: el LLM recibe metadatos completos + vista previa de cada candidata y devuelve un
    ``TableSelection``; después se normaliza con ``normalize_selection``."""
    plan: QueryPlan = state["plan"]
    candidates = state["candidates"]
    structured = deps.chat_model.with_structured_output(TableSelection)
    try:
        raw = structured.invoke(build_select_messages(state["question"], plan, candidates))
        if not isinstance(raw, TableSelection):  # pragma: no cover
            raw = TableSelection.model_validate(raw)
    except RECOVERABLE_LLM_ERRORS as exc:
        selection = TableSelection(
            selected_table_ids=[],
            coverage=[
                PartCoverage(
                    part_id=pid,
                    coverage="missing",
                    table_ids=[],
                    missing_reason=SELECTION_ERROR_REASON,
                    related_table_note=None,
                )
                for pid in plan.part_ids
            ],
            rationale="",
        )
        return {
            "selection": selection,
            "llm_calls": 1,
            "warnings": [f"{SELECTION_ERROR_REASON} ({_short(exc)})"],
        }
    selection, warnings = normalize_selection(raw, plan, candidates)
    return {"selection": selection, "llm_calls": 1, "warnings": warnings}


def route_after_select(state: AnalystState) -> str:
    """FR-034: sin partes ``covered`` no se genera código."""
    selection: TableSelection | None = state.get("selection")
    return "generate_code" if selection is not None and selection.covered_part_ids else "decide_status"


# --- generate_code / execute (FR-035..FR-038) ---------------------------------------------------

_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """Programa de la respuesta del LLM: el primer bloque ```` ```python ```` (o ```` ```py ````);
    si no hay, el primer bloque sin lenguaje; si no hay bloques, el texto entero (si no es
    código, el chequeo AST lo rechaza con "error de sintaxis" y se reintenta)."""
    blocks = [(lang.lower(), body) for lang, body in _FENCE_RE.findall(text or "")]
    for lang, body in blocks:
        if lang in ("python", "py", "python3"):
            return body.strip()
    for lang, body in blocks:
        if not lang:
            return body.strip()
    stripped = (text or "").strip()
    if stripped.startswith("```"):  # bloque sin cerrar
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return stripped.strip()


def table_aliases(selection: TableSelection) -> dict[str, str]:
    """Alias del sandbox: ``t{i}`` = ``selected_table_ids[i-1]`` (mismo orden en prompt y sandbox)."""
    return {f"t{i}": table_id for i, table_id in enumerate(selection.selected_table_ids, start=1)}


def generate_code_node(state: AnalystState, deps: AnalystDeps) -> dict[str, Any]:
    """FR-035 / FR-037: el LLM recibe, por alias ``t1..tn``, nombres exactos de columnas, dtypes,
    unidades, la primera columna fila por fila y ``df.head(5)``, más las partes ``covered``; en un
    reintento, además el código anterior y su error. Devuelve el programa (sin el preámbulo)."""
    selection: TableSelection = state["selection"]
    plan: QueryPlan = state["plan"]
    attempt = int(state.get("code_attempts") or 0) + 1
    candidates = {c.table_id: c for c in state.get("candidates") or []}
    tables = [
        (alias, candidates[table_id], deps.table_store.load_table(table_id))
        for alias, table_id in table_aliases(selection).items()
    ]
    feedback = None
    previous: ExecutionResult | None = state.get("execution")
    if attempt > 1 and previous is not None and not previous.ok:
        feedback = describe_feedback(attempt - 1, deps.max_code_attempts, state.get("code") or "", previous)
    messages = build_codegen_messages(
        state["question"],
        plan,
        selection.covered_part_ids,
        tables,
        timeout_s=deps.settings.sandbox_timeout_s,
        feedback=feedback,
    )
    reply = deps.chat_model.invoke(messages)
    text = reply.text if hasattr(reply, "text") else str(reply)
    return {"code": extract_code(text), "code_attempts": attempt, "llm_calls": 1}


def execute_node(state: AnalystState, deps: AnalystDeps) -> dict[str, Any]:
    """FR-036 / FR-038: corre el programa en el sandbox con las tablas ``t1..tn`` y valida la
    salida (§7.6) contra las partes ``covered``. Si falla y no quedan intentos, cada parte
    ``covered`` queda en ``missing`` con "falló la ejecución tras N intentos: <error>"."""
    selection: TableSelection = state["selection"]
    code = state.get("code") or ""
    covered = selection.covered_part_ids
    if not code.strip():
        execution = ExecutionResult(status="rejected", error=NO_CODE_ERROR, rejected_reasons=[NO_CODE_ERROR])
    else:
        tables: Mapping[str, Path] = {
            alias: deps.table_store.parquet_path(table_id)
            for alias, table_id in table_aliases(selection).items()
        }
        execution = deps.sandbox(code, tables, deps.settings.sandbox_timeout_s, covered_part_ids=covered)
    attempts = int(state.get("code_attempts") or 0)
    missing: list[MissingPart] = []
    if not execution.ok and attempts >= deps.max_code_attempts:
        reason = EXHAUSTED_REASON.format(attempts=attempts, error=_short(execution.error or execution.status))
        missing = [
            MissingPart(part_id=c.part_id, reason=reason, table_ids=list(c.table_ids))
            for c in selection.coverage
            if c.coverage == "covered"
        ]
    return {"execution": execution, "missing": missing}


def route_after_execute(state: AnalystState, deps: AnalystDeps) -> str:
    """FR-037: vuelve a ``generate_code`` mientras la ejecución falle y queden intentos."""
    execution: ExecutionResult | None = state.get("execution")
    if execution is not None and not execution.ok:
        if int(state.get("code_attempts") or 0) < deps.max_code_attempts:
            return "generate_code"
    return "decide_status"


# --- decide_status -------------------------------------------------------------------------------


def _covered_not_answered_reason(
    part_id: str, execution: ExecutionResult | None, code_missing: dict[str, MissingPart]
) -> MissingPart:
    if part_id in code_missing:
        return code_missing[part_id]
    if execution is not None and execution.parsed is not None:
        reported = next((m for m in execution.parsed.missing if m.part_id == part_id), None)
        if reported is not None:
            return MissingPart(part_id=part_id, reason=reported.reason)
    if execution is None:
        return MissingPart(part_id=part_id, reason=NO_CODE_REASON)
    if not execution.ok:
        return MissingPart(
            part_id=part_id, reason=f"falló la ejecución: {execution.error or execution.status}"
        )
    return MissingPart(part_id=part_id, reason=NO_RESULT_REASON)


def build_missing(
    plan: QueryPlan | None,
    selection: TableSelection | None,
    execution: ExecutionResult | None,
    code_missing: Sequence[MissingPart],
    answered: set[str],
) -> list[MissingPart]:
    """Lista final de faltantes: una entrada por parte del plan no respondida, en orden.

    Motivo: sin selección → ``NO_CANDIDATES_REASON`` (FR-032); ``missing`` en la selección → su
    motivo y tabla relacionada; ``covered`` sin resultado → el faltante del código (``missing``
    del estado o ``execution.parsed.missing``) o el error de ejecución.
    """
    if plan is None:
        return []
    by_code: dict[str, MissingPart] = {}
    for item in code_missing:
        by_code.setdefault(item.part_id, item)
    out: list[MissingPart] = []
    for part in plan.parts:
        if part.id in answered:
            continue
        coverage = selection.coverage_of(part.id) if selection is not None else None
        if selection is None:
            out.append(MissingPart(part_id=part.id, reason=NO_CANDIDATES_REASON))
        elif coverage is None:
            out.append(MissingPart(part_id=part.id, reason=NOT_EVALUATED_REASON))
        elif coverage.coverage == "missing":
            out.append(
                MissingPart(
                    part_id=part.id,
                    reason=coverage.missing_reason or DEFAULT_MISSING_REASON,
                    related_table_note=coverage.related_table_note,
                    table_ids=list(coverage.table_ids),
                )
            )
        else:
            item = _covered_not_answered_reason(part.id, execution, by_code)
            out.append(item.model_copy(update={"table_ids": item.table_ids or list(coverage.table_ids)}))
    return out


def decide_status_node(state: AnalystState) -> dict[str, Any]:
    """FR-039 / BR-015: estado determinístico; ``results`` y ``conversions`` solo de las partes
    respondidas (vacíos con ``cannot_answer``, FR-041) y ``missing`` final por parte."""
    plan = state.get("plan")
    selection = state.get("selection")
    execution = state.get("execution")
    status = decide_status(plan, selection, execution)
    answered = answered_part_ids(plan, selection, execution)
    parsed = execution.parsed if execution is not None else None
    results = [r for r in parsed.results if r.part_id in answered] if parsed and answered else []
    conversions = list(parsed.conversions) if parsed and answered else []
    missing = build_missing(plan, selection, execution, state.get("missing") or [], answered)
    return {"status": status, "results": results, "conversions": conversions, "missing": missing}


# --- respond -------------------------------------------------------------------------------------


def tables_by_id(candidates: Sequence[Candidate], ids: Sequence[str]) -> list[Candidate]:
    index = {c.table_id: c for c in candidates}
    return [index[i] for i in _unique(list(ids)) if i in index]


def respond_node(state: AnalystState, deps: AnalystDeps) -> dict[str, Any]:
    """FR-040 / FR-041: el LLM redacta la respuesta en español con lo que decidió el código.

    Con ``cannot_answer`` solo recibe los faltantes y las tablas relacionadas (ni resultados ni
    conversiones), así que no tiene números que reportar.
    """
    status = state["status"]
    candidates = state.get("candidates") or []
    selection = state.get("selection")
    missing = state.get("missing") or []
    used = (
        tables_by_id(candidates, selection.selected_table_ids)
        if selection and status != "cannot_answer"
        else []
    )
    related = tables_by_id(
        candidates, [t for m in missing for t in m.table_ids if t not in {c.table_id for c in used}]
    )
    results = (state.get("results") or []) if status != "cannot_answer" else []
    messages = build_respond_messages(
        state["question"],
        status,
        state.get("plan"),
        results,
        missing,
        used,
        related_tables=related,
        conversions=(state.get("conversions") or []) if status != "cannot_answer" else [],
        code=state.get("code") if status != "cannot_answer" else None,
    )
    reply = deps.chat_model.invoke(messages)
    answer = (reply.text if hasattr(reply, "text") else str(reply)).strip()
    if not answer:
        answer = fallback_answer(status, results, missing)
    return {"answer": answer, "llm_calls": 1}
