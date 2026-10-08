"""Arranque de la app: configuración (FR-002), chequeo del índice (FR-004) y armado de los runners.

``build_app_context()`` nunca lanza: una configuración incompleta o un índice con otra dimensión
quedan como mensajes, y las pestañas que dependen de eso se deshabilitan.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.config import ConfigError, Settings, load_settings
from services.table_store import TableStore
from services.vectorstore import IndexDimensionMismatch, VectorStore, open_index
from ui.errors import describe_error

DEFAULT_TABLES_DIR = "data/tables"

IndexOpener = Callable[[Settings], VectorStore | None]


@dataclass
class IndexCheck:
    """Resultado del chequeo del índice al arrancar. ``blocking``: no se puede usar el índice
    (dimensión distinta, FR-004) y se deshabilitan Subir PDF y Preguntar."""

    error: str | None = None
    note: str | None = None
    blocking: bool = False


@dataclass
class AppContext:
    settings: Settings | None
    config_error: ConfigError | None
    table_store: TableStore
    index_check: IndexCheck | None = None

    @property
    def disabled_reason(self) -> str | None:
        """Motivo por el que Subir PDF y Preguntar están deshabilitadas, o ``None``."""
        if self.config_error is not None:
            names = self.config_error.missing + list(self.config_error.invalid)
            return "Deshabilitado hasta completar la configuración: " + ", ".join(names) + "."
        if self.index_check is not None and self.index_check.blocking:
            return "Deshabilitado: el índice de Pinecone no es compatible con EMBEDDING_DIM."
        return None


def tables_dir_from_env(env: Mapping[str, str] | None = None) -> Path:
    """``TABLES_DIR`` aunque la configuración esté incompleta (para Explorar en solo lectura)."""
    env = os.environ if env is None else env
    return Path((env.get("TABLES_DIR") or "").strip() or DEFAULT_TABLES_DIR)


def load_app_settings() -> tuple[Settings | None, ConfigError | None]:
    """``load_settings()`` (lee ``.env``) sin lanzar: ``(settings, None)`` o ``(None, error)``."""
    try:
        return load_settings(), None
    except ConfigError as exc:
        return None, exc


def check_index(settings: Settings, opener: IndexOpener = open_index) -> IndexCheck:
    """FR-004 al arrancar, sin crear nada (``open_index`` es de solo lectura): dimensión distinta
    → error bloqueante con ambas dimensiones; índice inexistente → nota (se crea en la primera
    ingesta, FR-003); Pinecone caído → error no bloqueante (cada acción lo vuelve a informar)."""
    try:
        store = opener(settings)
    except IndexDimensionMismatch as exc:
        return IndexCheck(error=str(exc), blocking=True)
    except Exception as exc:
        service, summary = describe_error(exc, "Pinecone")
        return IndexCheck(error=f"{service}: {summary}")
    if store is None:
        return IndexCheck(
            note=f"El índice de Pinecone '{settings.pinecone_index_name}' no existe todavía: "
            "se crea en la primera ingesta."
        )
    return IndexCheck()


def build_app_context(
    *, index_check: IndexCheck | None = None, opener: IndexOpener = open_index, check: bool = True
) -> AppContext:
    settings, error = load_app_settings()
    tables_dir = settings.tables_dir if settings is not None else tables_dir_from_env()
    if settings is not None and index_check is None and check:
        index_check = check_index(settings, opener)
    return AppContext(settings, error, TableStore(tables_dir), index_check)


def make_ingestion_runner(settings: Settings) -> Callable[[str, bytes, Any], Any]:
    """``runner(file_name, pdf_bytes, on_update)`` con las dependencias reales (una por llamada)."""
    from agents.ingestion.graph import run_ingestion
    from agents.ingestion.nodes import build_default_deps

    def run(file_name: str, pdf_bytes: bytes, on_update: Any) -> Any:
        return run_ingestion(file_name, pdf_bytes, build_default_deps(settings), on_update=on_update)

    return run


def make_analyst_runner(settings: Settings) -> Callable[[str, Any], Any]:
    """``runner(question, on_update)`` con las dependencias reales. Se arman en cada pregunta para
    que el Analista vea un índice creado después de abrir la app."""
    from agents.analyst.graph import run_analyst
    from agents.analyst.nodes import build_default_deps

    def run(question: str, on_update: Any) -> Any:
        return run_analyst(question, build_default_deps(settings), on_update=on_update)

    return run
