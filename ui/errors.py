"""Manejo de errores de la UI (FR-054).

``run_safely(service_name, fn, ...)`` ejecuta una acción; si lanza, muestra
``st.error("<servicio>: <mensaje resumido>")`` y devuelve ``None`` en vez de propagar: la app sigue
respondiendo y ``st.session_state`` (p. ej. el historial del chat) queda intacto.

El servicio se deduce del tipo de la excepción (o de su causa encadenada): ``openai`` → LiteLLM,
``pinecone`` → Pinecone, ``langsmith`` → LangSmith, ``langgraph`` → grafo; si no se reconoce, se
usa ``service_name`` (la acción: "Ingesta", "Analista", …). El mensaje se recorta y se le borran
cosas con forma de clave.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, TypeVar

import streamlit as st

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_ERROR_CHARS = 300

# módulo raíz del tipo de la excepción → nombre del servicio que se muestra
SERVICE_BY_MODULE: dict[str, str] = {
    "openai": "LiteLLM (proxy de LLM)",
    "langchain_openai": "LiteLLM (proxy de LLM)",
    "pinecone": "Pinecone",
    "langsmith": "LangSmith",
    "langgraph": "Grafo (LangGraph)",
}
# excepciones propias que pertenecen a un servicio
SERVICE_BY_TYPE: dict[str, str] = {
    "IndexDimensionMismatch": "Pinecone",
    "IndexingError": "Pinecone",
    "ConfigError": "Configuración",
    "PdfOpenError": "PDF",
}

# Formas de claves (OpenAI/LiteLLM, Pinecone, LangSmith) que nunca se muestran en pantalla.
_SECRET_RE = re.compile(r"(sk-|pcsk[_]|lsv2[_])[A-Za-z0-9_\-]{4,}")

ErrorCallback = Callable[[str, str], None]


def redact(text: str) -> str:
    """Reemplaza por ``***`` todo lo que tenga forma de clave de API."""
    return _SECRET_RE.sub(lambda m: m.group(1) + "***", text)


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen and len(seen) < 10:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def service_of(exc: BaseException, default: str) -> str:
    """Servicio responsable de ``exc`` (mirando también sus causas encadenadas)."""
    for item in _chain(exc):
        for cls in type(item).__mro__:
            if cls.__name__ in SERVICE_BY_TYPE:
                return SERVICE_BY_TYPE[cls.__name__]
            root = cls.__module__.split(".", 1)[0]
            if root in SERVICE_BY_MODULE:
                return SERVICE_BY_MODULE[root]
    return default


def summarize_error(exc: BaseException, max_chars: int = MAX_ERROR_CHARS) -> str:
    """``"<Tipo>: <primera línea del mensaje>"``, recortado y sin claves."""
    message = " ".join(str(exc).split()) or "(sin mensaje)"
    text = redact(f"{type(exc).__name__}: {message}")
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def describe_error(exc: BaseException, default_service: str) -> tuple[str, str]:
    """``(servicio, mensaje resumido)`` para mostrar en ``st.error``."""
    return service_of(exc, default_service), summarize_error(exc)


def run_safely(
    service_name: str,
    fn: Callable[..., T],
    *args: Any,
    on_error: ErrorCallback | None = None,
    **kwargs: Any,
) -> T | None:
    """Ejecuta ``fn(*args, **kwargs)``. Ante cualquier ``Exception`` muestra
    ``st.error("<servicio>: <mensaje>")``, llama ``on_error(servicio, mensaje)`` si se pasó y
    devuelve ``None`` (FR-054). Las excepciones de control de Streamlit (``st.rerun``,
    ``st.stop``) no son ``Exception`` y pasan."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        service, summary = describe_error(exc, service_name)
        logger.warning("error en %s (%s): %s", service_name, service, summary)
        st.error(f"{service}: {summary}")
        if on_error is not None:
            on_error(service, summary)
        return None
