"""Analista contra servicios reales: VC-040 (plan con el LLM real) y una pregunta sin relación
con los PDFs ingeridos → ``cannot_answer``.

El segundo test usa el índice de **producción** ``PINECONE_INDEX_NAME`` y ``TABLES_DIR`` (las 33
tablas de los PDFs de muestra) en **solo lectura**: el Analista abre el índice con
``open_index`` (no crea ni escribe) y el test verifica que la cantidad de vectores no cambia.
Con ``LLM_CACHE=true`` las corridas repetidas no gastan presupuesto.
"""

from __future__ import annotations

import pytest

from agents.analyst.graph import run_analyst
from agents.analyst.nodes import AnalystDeps, build_default_deps, understand_query_node
from agents.analyst.schemas import QueryPlan
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
