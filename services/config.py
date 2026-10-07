"""Configuración de la aplicación a partir de variables de entorno (spec §7.1, FR-002).

Una variable vacía o con solo espacios cuenta como no definida. Las variables
obligatorias sin default concreto que falten se informan todas juntas en un
único ``ConfigError``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

DEFAULT_PINECONE_INDEX_NAME = "mas-2026s2-tables"

# Variables obligatorias sin default concreto (spec §7.1). PINECONE_INDEX_NAME y
# LANGSMITH_TRACING son obligatorias pero tienen default, así que nunca faltan.
# LANGSMITH_PROJECT tiene un default de plantilla (`mas-2026s2-<legajo>`), que no
# es un valor usable: se exige explícitamente.
REQUIRED_VARS: tuple[str, ...] = (
    "LITELLM_BASE_URL",
    "LITELLM_API_KEY",
    "LLM_MODEL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "PINECONE_API_KEY",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
)

_TRUE = {"1", "true", "yes", "y", "si", "sí", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


class ConfigError(Exception):
    """Configuración incompleta o inválida. El mensaje nombra cada variable afectada."""

    def __init__(self, missing: list[str], invalid: dict[str, str] | None = None) -> None:
        self.missing = list(missing)
        self.invalid = dict(invalid or {})
        parts: list[str] = []
        if self.missing:
            parts.append("Faltan variables de entorno obligatorias: " + ", ".join(self.missing))
        if self.invalid:
            parts.append(
                "Variables con valor inválido: "
                + "; ".join(f"{name} ({reason})" for name, reason in self.invalid.items())
            )
        super().__init__(". ".join(parts) + ". Revisá el archivo .env (ver .env.example).")


@dataclass(frozen=True)
class Settings:
    """Valores de configuración ya validados y tipados (spec §7.1)."""

    litellm_base_url: str
    litellm_api_key: str
    llm_model: str
    embedding_model: str
    embedding_dim: int
    pinecone_api_key: str
    langsmith_api_key: str
    langsmith_project: str
    pinecone_index_name: str = DEFAULT_PINECONE_INDEX_NAME
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    langsmith_tracing: bool = True
    tables_dir: Path = Path("data/tables")
    max_enrich_retries: int = 2
    max_code_retries: int = 3
    retrieval_top_k: int = 5
    sandbox_timeout_s: int = 30
    llm_cache: bool = False
    max_tables_per_pdf: int = 40

    def __repr__(self) -> str:  # no exponer claves en logs ni tracebacks
        return (
            f"Settings(llm_model={self.llm_model!r}, embedding_model={self.embedding_model!r}, "
            f"embedding_dim={self.embedding_dim}, pinecone_index_name={self.pinecone_index_name!r}, "
            f"langsmith_project={self.langsmith_project!r}, llm_cache={self.llm_cache})"
        )


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError("se esperaba true/false")


def _parse_positive_int(raw: str) -> int:
    value = int(raw.strip())
    if value <= 0:
        raise ValueError("debe ser un entero positivo")
    return value


def _parse_non_negative_int(raw: str) -> int:
    value = int(raw.strip())
    if value < 0:
        raise ValueError("debe ser un entero >= 0")
    return value


# variable -> (campo de Settings, parser). Las opcionales usan el default del dataclass.
_OPTIONAL_VARS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "PINECONE_INDEX_NAME": ("pinecone_index_name", str.strip),
    "PINECONE_CLOUD": ("pinecone_cloud", str.strip),
    "PINECONE_REGION": ("pinecone_region", str.strip),
    "LANGSMITH_TRACING": ("langsmith_tracing", _parse_bool),
    "TABLES_DIR": ("tables_dir", lambda raw: Path(raw.strip())),
    "MAX_ENRICH_RETRIES": ("max_enrich_retries", _parse_non_negative_int),
    "MAX_CODE_RETRIES": ("max_code_retries", _parse_non_negative_int),
    "RETRIEVAL_TOP_K": ("retrieval_top_k", _parse_positive_int),
    "SANDBOX_TIMEOUT_S": ("sandbox_timeout_s", _parse_positive_int),
    "LLM_CACHE": ("llm_cache", _parse_bool),
    "MAX_TABLES_PER_PDF": ("max_tables_per_pdf", _parse_positive_int),
}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Construye ``Settings`` desde ``env`` o, si es ``None``, desde ``.env`` + ``os.environ``.

    Con ``env=None`` se carga el ``.env`` del directorio de trabajo (sin pisar variables ya
    definidas en el proceso). Lanza ``ConfigError`` con **todas** las variables obligatorias
    faltantes y las que tienen valores inválidos.
    """
    if env is None:
        load_dotenv(find_dotenv(usecwd=True), override=False)
        env = os.environ

    def get(name: str) -> str | None:
        value = env.get(name)
        if value is None or not value.strip():
            return None
        return value

    missing = [name for name in REQUIRED_VARS if get(name) is None]
    invalid: dict[str, str] = {}

    embedding_dim = 0
    raw_dim = get("EMBEDDING_DIM")
    if raw_dim is not None:
        try:
            embedding_dim = _parse_positive_int(raw_dim)
        except ValueError as exc:
            invalid["EMBEDDING_DIM"] = _reason(exc)

    optional: dict[str, Any] = {}
    for name, (field_name, parser) in _OPTIONAL_VARS.items():
        raw = get(name)
        if raw is None:
            continue
        try:
            optional[field_name] = parser(raw)
        except ValueError as exc:
            invalid[name] = _reason(exc)

    if missing or invalid:
        raise ConfigError(missing, invalid)

    return Settings(
        litellm_base_url=str(get("LITELLM_BASE_URL")).strip(),
        litellm_api_key=str(get("LITELLM_API_KEY")).strip(),
        llm_model=str(get("LLM_MODEL")).strip(),
        embedding_model=str(get("EMBEDDING_MODEL")).strip(),
        embedding_dim=embedding_dim,
        pinecone_api_key=str(get("PINECONE_API_KEY")).strip(),
        langsmith_api_key=str(get("LANGSMITH_API_KEY")).strip(),
        langsmith_project=str(get("LANGSMITH_PROJECT")).strip(),
        **optional,
    )


def _reason(exc: ValueError) -> str:
    message = str(exc)
    if message.startswith("invalid literal for int()"):
        return "se esperaba un entero"
    return message
