"""VC-002 (FR-002): validación de configuración desde variables de entorno."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.config import REQUIRED_VARS, ConfigError, Settings, load_settings

FULL_ENV: dict[str, str] = {
    "LITELLM_BASE_URL": "https://proxy.example.test",
    "LITELLM_API_KEY": "test-litellm-key",
    "LLM_MODEL": "modelo-chat",
    "EMBEDDING_MODEL": "modelo-embeddings",
    "EMBEDDING_DIM": "1536",
    "PINECONE_API_KEY": "test-pinecone-key",
    "LANGSMITH_API_KEY": "test-langsmith-key",
    "LANGSMITH_PROJECT": "mas-2026s2-test",
}


def env_without(*names: str) -> dict[str, str]:
    return {k: v for k, v in FULL_ENV.items() if k not in names}


def test_full_env_builds_settings_with_defaults() -> None:
    """Con todas las obligatorias, las opcionales toman los defaults de la spec §7.1."""
    settings = load_settings(FULL_ENV)

    assert isinstance(settings, Settings)
    assert settings.litellm_base_url == "https://proxy.example.test"
    assert settings.embedding_dim == 1536
    assert settings.pinecone_index_name == "mas-2026s2-tables"
    assert (settings.pinecone_cloud, settings.pinecone_region) == ("aws", "us-east-1")
    assert settings.langsmith_tracing is True
    assert settings.tables_dir == Path("data/tables")
    assert settings.max_enrich_retries == 2
    assert settings.max_code_retries == 3
    assert settings.retrieval_top_k == 5
    assert settings.sandbox_timeout_s == 30
    assert settings.llm_cache is False
    assert settings.max_tables_per_pdf == 40


def test_missing_pinecone_api_key_raises_config_error_naming_it() -> None:
    """VC-002: sin PINECONE_API_KEY → ConfigError cuyo mensaje contiene PINECONE_API_KEY."""
    with pytest.raises(ConfigError) as excinfo:
        load_settings(env_without("PINECONE_API_KEY"))

    assert "PINECONE_API_KEY" in str(excinfo.value)
    assert excinfo.value.missing == ["PINECONE_API_KEY"]


def test_two_missing_variables_are_both_listed() -> None:
    """VC-002: con dos faltantes, el error lista ambas."""
    with pytest.raises(ConfigError) as excinfo:
        load_settings(env_without("PINECONE_API_KEY", "LANGSMITH_API_KEY"))

    message = str(excinfo.value)
    assert "PINECONE_API_KEY" in message
    assert "LANGSMITH_API_KEY" in message
    assert set(excinfo.value.missing) == {"PINECONE_API_KEY", "LANGSMITH_API_KEY"}


def test_empty_environment_lists_every_required_variable() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings({})

    assert excinfo.value.missing == list(REQUIRED_VARS)


def test_blank_value_counts_as_missing() -> None:
    env = {**FULL_ENV, "LLM_MODEL": "   "}

    with pytest.raises(ConfigError) as excinfo:
        load_settings(env)

    assert excinfo.value.missing == ["LLM_MODEL"]


def test_blank_optional_value_uses_default() -> None:
    settings = load_settings({**FULL_ENV, "PINECONE_INDEX_NAME": "", "MAX_CODE_RETRIES": " "})

    assert settings.pinecone_index_name == "mas-2026s2-tables"
    assert settings.max_code_retries == 3


def test_optional_values_are_parsed() -> None:
    env = {
        **FULL_ENV,
        "PINECONE_INDEX_NAME": "otro-indice",
        "LANGSMITH_TRACING": "false",
        "TABLES_DIR": "tmp/tablas",
        "MAX_ENRICH_RETRIES": "0",
        "MAX_CODE_RETRIES": "5",
        "RETRIEVAL_TOP_K": "8",
        "SANDBOX_TIMEOUT_S": "10",
        "LLM_CACHE": "TRUE",
        "MAX_TABLES_PER_PDF": "3",
    }

    settings = load_settings(env)

    assert settings.pinecone_index_name == "otro-indice"
    assert settings.langsmith_tracing is False
    assert settings.tables_dir == Path("tmp/tablas")
    assert settings.max_enrich_retries == 0
    assert settings.max_code_retries == 5
    assert settings.retrieval_top_k == 8
    assert settings.sandbox_timeout_s == 10
    assert settings.llm_cache is True
    assert settings.max_tables_per_pdf == 3


def test_invalid_values_are_reported_together_with_missing() -> None:
    env = {**env_without("PINECONE_API_KEY"), "EMBEDDING_DIM": "abc", "LLM_CACHE": "quizas"}

    with pytest.raises(ConfigError) as excinfo:
        load_settings(env)

    message = str(excinfo.value)
    assert "PINECONE_API_KEY" in message
    assert "EMBEDDING_DIM" in message
    assert "LLM_CACHE" in message
    assert set(excinfo.value.invalid) == {"EMBEDDING_DIM", "LLM_CACHE"}


def test_non_positive_embedding_dim_is_invalid() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings({**FULL_ENV, "EMBEDDING_DIM": "0"})

    assert "EMBEDDING_DIM" in excinfo.value.invalid


def test_repr_does_not_expose_keys() -> None:
    text = repr(load_settings(FULL_ENV))

    for secret in ("test-litellm-key", "test-pinecone-key", "test-langsmith-key"):
        assert secret not in text


def test_load_settings_without_env_reads_dotenv_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con env=None se lee el `.env` del directorio de trabajo sin pisar el entorno del proceso."""
    for name in (*REQUIRED_VARS, "LLM_CACHE", "PINECONE_INDEX_NAME"):
        monkeypatch.delenv(name, raising=False)
    lines = [f"{k}={v}" for k, v in FULL_ENV.items() if k != "LLM_MODEL"]
    (tmp_path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_MODEL", "desde-proceso")

    settings = load_settings()

    assert settings.llm_model == "desde-proceso"
    assert settings.pinecone_api_key == "test-pinecone-key"
    # monkeypatch registró estas variables como ausentes: al terminar borra lo que cargó load_dotenv.
