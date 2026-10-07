"""FR-001 / VC-005 (FR-005): clientes LiteLLM y caché SQLite, sin red."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.globals import get_llm_cache, set_llm_cache
from pydantic import BaseModel

from services.config import Settings, load_settings
from services.llm import SQLiteCache, enable_cache_if_configured, get_chat_model, get_embeddings
from tests.fakes import ScriptedChatModel
from tests.unit.test_config import FULL_ENV


@pytest.fixture
def settings() -> Settings:
    return load_settings(FULL_ENV)


@pytest.fixture(autouse=True)
def reset_global_cache() -> Iterator[None]:
    previous = get_llm_cache()
    set_llm_cache(None)
    yield
    set_llm_cache(previous)


def test_get_chat_model_points_to_litellm_proxy(settings: Settings) -> None:
    """FR-001: el chat model usa base_url del proxy, el modelo configurado y temperature=0."""
    llm = get_chat_model(settings)

    assert llm.openai_api_base == settings.litellm_base_url
    assert llm.model_name == settings.llm_model
    assert llm.temperature == 0
    assert llm.openai_api_key is not None
    assert llm.openai_api_key.get_secret_value() == settings.litellm_api_key


def test_get_chat_model_accepts_overrides(settings: Settings) -> None:
    llm = get_chat_model(settings, temperature=0.3, max_retries=0)

    assert llm.temperature == 0.3
    assert llm.max_retries == 0


def test_get_embeddings_points_to_litellm_proxy(settings: Settings) -> None:
    """FR-001: los embeddings usan base_url del proxy y el modelo configurado."""
    embeddings = get_embeddings(settings)

    assert embeddings.openai_api_base == settings.litellm_base_url
    assert embeddings.model == settings.embedding_model


def test_cache_enabled_serves_repeated_call_without_hitting_model(settings: Settings, tmp_path: Path) -> None:
    """VC-005: LLM_CACHE=true, misma llamada dos veces con un LLM fake contador → contador = 1."""
    cache = enable_cache_if_configured(replace(settings, llm_cache=True), tmp_path / ".langchain.db")
    fake = ScriptedChatModel(responses=["respuesta 1", "respuesta 2"])

    first = fake.invoke("¿Cuánto es 2 + 2?")
    second = fake.invoke("¿Cuánto es 2 + 2?")

    assert isinstance(cache, SQLiteCache)
    assert get_llm_cache() is cache
    assert len(fake.calls) == 1
    assert first.text == second.text == "respuesta 1"
    assert (tmp_path / ".langchain.db").exists()


def test_cache_distinguishes_different_prompts(settings: Settings, tmp_path: Path) -> None:
    enable_cache_if_configured(replace(settings, llm_cache=True), tmp_path / ".langchain.db")
    fake = ScriptedChatModel(responses=["a", "b"])

    assert fake.invoke("pregunta 1").text == "a"
    assert fake.invoke("pregunta 2").text == "b"
    assert len(fake.calls) == 2


def test_cache_persists_on_disk_across_instances(settings: Settings, tmp_path: Path) -> None:
    db = tmp_path / ".langchain.db"
    enable_cache_if_configured(replace(settings, llm_cache=True), db)
    ScriptedChatModel(responses=["guardada"]).invoke("hola")

    set_llm_cache(SQLiteCache(db))  # nueva instancia sobre el mismo archivo
    fresh = ScriptedChatModel(responses=[])

    assert fresh.invoke("hola").text == "guardada"
    assert fresh.calls == []


def test_cache_disabled_does_not_install_cache(settings: Settings, tmp_path: Path) -> None:
    """LLM_CACHE=false: no se instala caché y cada llamada llega al modelo."""
    result = enable_cache_if_configured(settings, tmp_path / ".langchain.db")
    fake = ScriptedChatModel(responses=["x", "y"])

    fake.invoke("igual")
    fake.invoke("igual")

    assert result is None
    assert get_llm_cache() is None
    assert len(fake.calls) == 2
    assert not (tmp_path / ".langchain.db").exists()


class _Answer(BaseModel):
    value: int


def test_scripted_fake_structured_output_and_call_log() -> None:
    """Convención de tests/fakes.py: salidas estructuradas guionadas y registro de prompts."""
    fake = ScriptedChatModel(responses=[{"value": 1}, _Answer(value=2)])
    structured = fake.with_structured_output(_Answer)

    assert structured.invoke("primero") == _Answer(value=1)
    assert structured.invoke("segundo con feedback") == _Answer(value=2)
    assert fake.calls == ["primero", "segundo con feedback"]
    with pytest.raises(AssertionError):
        structured.invoke("tercero")
