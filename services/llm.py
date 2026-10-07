"""Clientes de LLM y embeddings vía el proxy LiteLLM (FR-001) y caché SQLite opcional (FR-005).

Este módulo es el **único** lugar del código que construye ``ChatOpenAI`` y
``OpenAIEmbeddings`` (verificado por ``tests/unit/test_single_llm_client.py``).
"""

from __future__ import annotations

import sqlite3
import threading
import warnings
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain_core._api import LangChainBetaWarning
from langchain_core.caches import RETURN_VAL_TYPE, BaseCache
from langchain_core.globals import set_llm_cache
from langchain_core.load import dumps, loads
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from services.config import Settings

DEFAULT_CACHE_PATH = Path(".langchain.db")
CHAT_TIMEOUT_S = 120
CHAT_MAX_RETRIES = 2


def get_chat_model(settings: Settings, **overrides: Any) -> ChatOpenAI:
    """Chat model del proxy LiteLLM con ``temperature=0`` (D-001). ``overrides`` pisa parámetros."""
    params: dict[str, Any] = {
        "base_url": settings.litellm_base_url,
        "api_key": SecretStr(settings.litellm_api_key),
        "model": settings.llm_model,
        "temperature": 0,
        "timeout": CHAT_TIMEOUT_S,
        "max_retries": CHAT_MAX_RETRIES,
    }
    params.update(overrides)
    return ChatOpenAI(**params)


def get_embeddings(settings: Settings) -> OpenAIEmbeddings:
    """Modelo de embeddings del proxy LiteLLM (dimensión esperada: ``settings.embedding_dim``)."""
    return OpenAIEmbeddings(
        base_url=settings.litellm_base_url,
        api_key=SecretStr(settings.litellm_api_key),
        model=settings.embedding_model,
        max_retries=CHAT_MAX_RETRIES,
    )


class SQLiteCache(BaseCache):
    """Caché de respuestas de LLM en un archivo SQLite local, sin dependencias extra (D-004).

    La clave es ``(prompt, llm_string)`` tal como la arma LangChain; el valor es la lista de
    ``Generation`` serializada con ``langchain_core.load.dumps``.
    """

    def __init__(self, database_path: str | Path = DEFAULT_CACHE_PATH) -> None:
        self.database_path = Path(database_path)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS llm_cache ("
                " prompt TEXT NOT NULL, llm_string TEXT NOT NULL, response TEXT NOT NULL,"
                " PRIMARY KEY (prompt, llm_string))"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Conexión corta por operación: commit al salir y cierre siempre."""
        conn = sqlite3.connect(self.database_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def lookup(self, prompt: str, llm_string: str) -> RETURN_VAL_TYPE | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT response FROM llm_cache WHERE prompt = ? AND llm_string = ?",
                (prompt, llm_string),
            ).fetchone()
        if row is None:
            return None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", LangChainBetaWarning)
                return loads(row[0], allowed_objects="core")
        except Exception:  # entrada corrupta o de otra versión: se trata como miss
            return None

    def update(self, prompt: str, llm_string: str, return_val: Sequence[Any]) -> None:
        payload = dumps(list(return_val))
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (prompt, llm_string, response) VALUES (?, ?, ?)",
                (prompt, llm_string, payload),
            )

    def clear(self, **kwargs: Any) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM llm_cache")


def enable_cache_if_configured(
    settings: Settings, database_path: str | Path = DEFAULT_CACHE_PATH
) -> SQLiteCache | None:
    """Si ``LLM_CACHE=true`` instala un ``SQLiteCache`` global de LangChain y lo devuelve.

    Con ``LLM_CACHE=false`` no toca la caché global y devuelve ``None``.
    """
    if not settings.llm_cache:
        return None
    cache = SQLiteCache(database_path)
    set_llm_cache(cache)
    return cache
