"""Analista contra servicios reales: VC-040 (plan con el LLM real), una pregunta sin relación
con los PDFs ingeridos → ``cannot_answer``, y (Iteración 8) preguntas que generan y ejecutan
código: una tabla (valor verificado a mano), VC-043 (multitabla), VC-051 (conversión de escala) y
VC-081 (traza en LangSmith).

Los tests de preguntas usan el índice de **producción** ``PINECONE_INDEX_NAME`` y ``TABLES_DIR`` (las 33
tablas de los PDFs de muestra) en **solo lectura**: el Analista abre el índice con
``open_index`` (no crea ni escribe) y el test verifica que la cantidad de vectores no cambia.
Con ``LLM_CACHE=true`` las corridas repetidas no gastan presupuesto.
"""

from __future__ import annotations

import re
import time
import warnings
from typing import Any

import pytest

from agents.analyst.graph import NODE_NAMES, run_analyst, trace_run_name
from agents.analyst.nodes import AnalystDeps, build_default_deps, understand_query_node
from agents.analyst.schemas import AnalystResult, QueryPlan
from services.config import ConfigError, Settings, load_settings
from services.llm import enable_cache_if_configured
from services.vectorstore import open_index

pytestmark = pytest.mark.integration

VC040_QUESTION = "¿Qué región tuvo el mayor desempleo en 2023 y cuánto superó al promedio nacional?"
UNRELATED_QUESTION = "¿Cuántos goles hizo Lionel Messi en el Mundial de Qatar 2022?"


@pytest.fixture(scope="module")
def settings() -> Settings:
    try:
        loaded = load_settings()
    except ConfigError as exc:  # pragma: no cover - depende del .env local
        pytest.skip(f"configuración incompleta: {exc}")
    enable_cache_if_configured(loaded)
    return loaded


@pytest.fixture(scope="module")
def deps(settings: Settings) -> AnalystDeps:
    return build_default_deps(settings)


def test_vc040_plan_splits_question_in_two_parts_with_search_queries(deps: AnalystDeps) -> None:
    """Given la pregunta de VC-040, when corre understand_query con el LLM real, then el
    QueryPlan tiene 2 partes, cada una con ≥ 1 consulta de búsqueda."""
    update = understand_query_node({"question": VC040_QUESTION}, deps)

    plan = update["plan"]
    assert isinstance(plan, QueryPlan)
    assert update["warnings"] == []  # el LLM respetó el formato (no se usó el plan de respaldo)
    assert plan.part_ids == ["p1", "p2"]
    for part in plan.parts:
        assert 1 <= len(part.search_queries) <= 3
        assert all(q.strip() for q in part.search_queries)
    assert any("2023" in period for p in plan.parts for period in p.periods)


def test_unrelated_question_is_cannot_answer_reading_production_index(
    settings: Settings, deps: AnalystDeps
) -> None:
    """Given las tablas de los PDFs de muestra (EPH, CEPAL) y una pregunta de fútbol, when se
    pregunta, then status=cannot_answer, sin resultados numéricos ni código, con un faltante por
    parte y una respuesta en texto; el índice de producción no cambia."""
    store = open_index(settings)
    if store is None or not any(settings.tables_dir.glob("*.parquet")):
        pytest.skip("no hay índice de producción o tablas locales (ingerir los PDFs de muestra)")
    before = store.count()

    result = run_analyst(UNRELATED_QUESTION, deps)

    assert result.status == "cannot_answer"
    assert result.results == [] and result.conversions == []
    assert result.code is None and result.execution is None
    assert result.plan is not None and len(result.missing) == len(result.plan.parts)
    assert result.answer.strip()
    assert result.llm_calls <= 4 + settings.max_code_retries and result.embedding_calls <= 3
    assert store.count() == before


# --- Iteración 8: código real en el sandbox (VC-043, VC-051, VC-081) ----------------------------

SINGLE_TABLE_QUESTION = (
    "¿Cuál fue la tasa de desocupación del total de 31 aglomerados en el 2° trimestre de 2026 y "
    "cuánto cambió respecto del 2° trimestre de 2025?"
)
MULTITABLE_QUESTION = (
    "¿Cuál fue la tasa de desocupación de Gran Buenos Aires en el 2° trimestre de 2025 y en el "
    "2° trimestre de 2026, y cuántos puntos porcentuales cambió entre ambos?"
)
SCALE_QUESTION = (
    "¿Cuántas personas desocupadas había en el total de 31 aglomerados urbanos en el 2° trimestre "
    "de 2026, expresado en millones de personas?"
)
DOC_2T2025 = "2c14158b26aed2d1"  # indec_eph_mercado_trabajo_2t2025.pdf
DOC_2T2026 = "b0a700bac4db8192"  # indec_eph_mercado_trabajo_2t2026.pdf
CUADRO_1_3_2T2026 = f"{DOC_2T2026}-p008-t0"
# Tablas de la EPH 2T2026 con la tasa de desocupación del total de 31 aglomerados en 2T2025 y 2T2026.
EQUIVALENT_TABLES_2T2026 = {f"{DOC_2T2026}-p005-t0", CUADRO_1_3_2T2026}
TRACE_TIMEOUT_S = 120


def _values(result: AnalystResult) -> list[float]:
    """Todos los números de ``results`` (valor, lista o dict), para comparar sin depender de cómo
    el LLM agrupó los valores."""
    out: list[float] = []
    for item in result.results:
        value = item.value
        if isinstance(value, dict):
            out += list(value.values())
        elif isinstance(value, list):
            out += value
        else:
            out.append(value)
    return out


def _has(values: list[float], expected: float, tol: float = 1e-6) -> bool:
    return any(abs(v - expected) <= tol for v in values)


@pytest.fixture(scope="module")
def production(settings: Settings) -> Settings:
    store = open_index(settings)
    if store is None or not (settings.tables_dir / f"{CUADRO_1_3_2T2026}.parquet").exists():
        pytest.skip("no hay índice de producción o tablas locales (ingerir los PDFs de muestra)")
    return settings


@pytest.fixture(scope="module")
def multitable(production: Settings, deps: AnalystDeps) -> AnalystResult:
    return run_analyst(MULTITABLE_QUESTION, deps)


def test_single_table_question_is_answered_with_the_pdf_value(
    production: Settings, deps: AnalystDeps
) -> None:
    """Given la EPH 2T2026, que trae la tasa de desocupación del total de 31 aglomerados (7,6 en
    2T2025 y 7,9 en 2T2026) en dos tablas equivalentes (resumen de tasas, pág. 5, y Cuadro 1.3,
    pág. 8), when se pregunta el valor y su cambio, then answered con 7,9 % y 0,3 p.p. usando
    alguna de esas tablas."""
    result = run_analyst(SINGLE_TABLE_QUESTION, deps)

    assert result.status == "answered", result.missing
    selected = set(result.selection.selected_table_ids)  # type: ignore[union-attr]
    assert selected & EQUIVALENT_TABLES_2T2026, selected
    values = _values(result)
    assert _has(values, 7.9) and _has([abs(v) for v in values], 0.3, tol=1e-3)
    assert result.execution is not None and result.execution.ok
    assert 1 <= result.code_attempts <= 1 + production.max_code_retries
    assert result.llm_calls <= 4 + production.max_code_retries and result.embedding_calls <= 3


def test_vc043_multitable_question_selects_two_tables_and_covers_every_part(
    production: Settings, multitable: AnalystResult
) -> None:
    """Given la tasa de desocupación de GBA en 2T2025 (EPH 2T2025, Cuadro 3.3, pág. 18: 8,7) y en
    2T2026 (EPH 2T2026, Cuadro 3.4, pág. 20: 8,2), when se pide ambas y su variación, then se
    eligen ≥ 2 tablas (una de cada publicación), todas las partes quedan covered y la respuesta
    es answered con 8,7, 8,2 y 0,5 p.p."""
    result = multitable
    assert result.selection is not None
    selected = result.selection.selected_table_ids
    assert len(selected) >= 2
    assert {t[:16] for t in selected} >= {DOC_2T2025, DOC_2T2026}
    assert all(c.coverage == "covered" for c in result.selection.coverage)
    assert result.status == "answered", result.missing
    values = _values(result)
    assert _has(values, 8.7) and _has(values, 8.2)
    assert _has([abs(v) for v in values], 0.5, tol=1e-3)


def test_vc051_scale_conversion_is_declared_and_mentioned_with_table_and_pages(
    production: Settings, deps: AnalystDeps
) -> None:
    """Given la población desocupada en miles (Cuadro 1.3 EPH 2T2026, pág. 8: 1.164 miles), when se
    pide en millones, then el código declara la conversión miles → millones, el valor es 1,164 y
    la respuesta menciona la conversión, la tabla y la página usadas."""
    result = run_analyst(SCALE_QUESTION, deps)

    assert result.status == "answered", result.missing
    assert _has(_values(result), 1.164, tol=1e-3)
    assert result.conversions and any("millones" in c for c in result.conversions)
    answer = result.answer.casefold()
    assert "millones" in answer and ("miles" in answer or "conversi" in answer)
    table = next(t for t in result.selected_tables if t.table_id == CUADRO_1_3_2T2026)
    assert table.file_name.casefold() in answer or table.name.casefold() in answer
    assert re.search(r"p[áa]g(?:s?\.|ina)?\s*8\b", answer), result.answer


def _runs(client: Any, **kwargs: Any) -> list[Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return list(client.list_runs(**kwargs))


def test_vc081_analyst_trace_in_langsmith(production: Settings, multitable: AnalystResult) -> None:
    """Given una pregunta con LANGSMITH_TRACING=true, then list_runs(is_root=True) devuelve el run
    analyst:<pregunta> con un hijo por nodo ejecutado (generate_code/execute repetidos tantas
    veces como intentos) y metadata status, selected_table_ids y code_attempts."""
    if not production.langsmith_tracing:
        pytest.skip("LANGSMITH_TRACING=false")
    from langsmith import Client

    result = multitable
    assert result.run_id is not None
    client = Client(api_key=production.langsmith_api_key)
    project = production.langsmith_project
    name = trace_run_name(MULTITABLE_QUESTION)
    deadline = time.monotonic() + TRACE_TIMEOUT_S
    root = None
    children: list[Any] = []
    while time.monotonic() < deadline:
        roots = _runs(client, project_name=project, is_root=True, filter=f'eq(name, "{name}")', limit=20)
        root = next((r for r in roots if str(r.id) == result.run_id), None)
        if root is not None and root.end_time is not None and "code_attempts" in (root.metadata or {}):
            children = [
                r
                for r in _runs(client, project_name=project, trace_id=result.run_id)
                if r.parent_run_id == root.id
            ]
            if "respond" in {c.name for c in children}:
                break
        time.sleep(5)

    assert root is not None, "no apareció el run raíz en LangSmith"
    assert root.name == name == "analyst:" + MULTITABLE_QUESTION[:60]
    metadata = root.metadata or {}
    assert metadata["status"] == result.status
    assert metadata["selected_table_ids"] == result.selection.selected_table_ids  # type: ignore[union-attr]
    assert metadata["code_attempts"] == result.code_attempts
    names = [c.name for c in children]
    assert set(names) <= set(NODE_NAMES), names
    for node in NODE_NAMES:
        assert node in names, names
    assert names.count("generate_code") == names.count("execute") == result.code_attempts
