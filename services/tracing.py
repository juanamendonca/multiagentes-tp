"""Trazas de LangSmith para los grafos (FR-060, FR-061; D-022).

- ``make_trace_setup`` arma el ``RunnableConfig`` de una ejecución: ``run_name`` (p. ej.
  ``"ingestion:<archivo>"``), ``run_id`` conocido de antemano (id del run raíz), ``tags``,
  ``metadata`` conocida al empezar, y como callback un ``RootMetadataTracer``.
- ``RootMetadataTracer`` es un ``LangChainTracer`` que, cuando termina el run **raíz**, le agrega
  metadata calculada a partir de la salida final del grafo (``n_tables``, ``n_warnings``…, que
  solo se conocen al final). La metadata viaja en el mismo PATCH que cierra el run.
- El cliente de LangSmith se crea con ``hide_inputs``/``hide_outputs`` = ``sanitize_for_trace``:
  los bytes del PDF, los ``DataFrame`` y las páginas de texto se reemplazan por resúmenes, así
  cada span no sube megabytes de estado.
- Con ``LANGSMITH_TRACING=false`` no hay tracer y la ejecución corre dentro de
  ``tracing_context(enabled=False)`` (no traza aunque el entorno del proceso diga lo contrario).
- ``RunUrlBuilder`` arma el link a la traza de un run raíz a partir de su ``run_id`` (FR-055).
  El link lleva ``?poll=true``: si la traza todavía no llegó a LangSmith, la página la espera.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
import warnings
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pandas as pd
from langchain_core.runnables import RunnableConfig
from langchain_core.tracers.langchain import LangChainTracer
from langchain_core.tracers.schemas import Run
from langsmith import Client
from langsmith.run_helpers import tracing_context
from pydantic import BaseModel

from services.config import Settings

logger = logging.getLogger(__name__)

MAX_TRACE_DEPTH = 6
MAX_TRACE_LIST = 50
MAX_TRACE_STR = 4000

RootMetadataFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _summarize_frame(df: pd.DataFrame) -> dict[str, Any]:
    return {"type": "DataFrame", "shape": list(df.shape), "columns": [str(c) for c in df.columns]}


def sanitize_for_trace(value: Any, _depth: int = 0) -> Any:
    """Copia de ``value`` apta para subir a LangSmith: ``bytes`` → ``"<N bytes>"``,
    ``DataFrame`` → forma y columnas, listas largas y textos largos recortados, dataclasses y
    modelos Pydantic → ``dict`` (recursivo, con profundidad acotada)."""
    if _depth > MAX_TRACE_DEPTH:
        return f"<{type(value).__name__}>"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return (
            value
            if len(value) <= MAX_TRACE_STR
            else value[:MAX_TRACE_STR] + f"… (+{len(value) - MAX_TRACE_STR})"
        )
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(value)} bytes>"
    if isinstance(value, pd.DataFrame):
        return _summarize_frame(value)
    if isinstance(value, pd.Series):
        return {"type": "Series", "name": str(value.name), "len": len(value)}
    if isinstance(value, BaseModel):
        return sanitize_for_trace(value.model_dump(mode="json"), _depth + 1)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        summary = getattr(value, "trace_summary", None)
        if callable(summary):
            return sanitize_for_trace(summary(), _depth + 1)
        data = {f.name: getattr(value, f.name) for f in dataclasses.fields(value) if f.repr}
        return {"type": type(value).__name__, **sanitize_for_trace(data, _depth + 1)}
    if isinstance(value, Mapping):
        return {str(k): sanitize_for_trace(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)
        out = [sanitize_for_trace(v, _depth + 1) for v in items[:MAX_TRACE_LIST]]
        if len(items) > MAX_TRACE_LIST:
            out.append(f"… (+{len(items) - MAX_TRACE_LIST} elementos)")
        return out
    return value


def _sanitize_payload(payload: dict) -> dict:
    return sanitize_for_trace(payload) if isinstance(payload, dict) else payload


def make_langsmith_client(settings: Settings) -> Client:
    """Cliente de LangSmith con la clave de ``Settings`` y el saneado de entradas/salidas."""
    return Client(
        api_key=settings.langsmith_api_key,
        hide_inputs=_sanitize_payload,
        hide_outputs=_sanitize_payload,
    )


class RootMetadataTracer(LangChainTracer):
    """``LangChainTracer`` que agrega metadata al run raíz cuando termina (o falla).

    ``root_metadata(outputs)`` recibe la salida final del grafo (el estado completo, sin sanear)
    y devuelve las claves a agregar. Errores al calcularla no rompen la ejecución.
    """

    def __init__(self, *, root_metadata: RootMetadataFn | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._root_metadata = root_metadata
        self.root_run_id: uuid.UUID | None = None

    def _add_root_metadata(self, run: Run) -> None:
        if run.parent_run_id is not None:
            return
        self.root_run_id = run.id
        if self._root_metadata is None:
            return
        try:
            extra = dict(self._root_metadata(run.outputs or {}))
        except Exception as exc:  # la traza nunca rompe la ingesta
            extra = {"root_metadata_error": str(exc)}
        run.extra.setdefault("metadata", {}).update(extra)

    def _on_chain_end(self, run: Run) -> None:
        self._add_root_metadata(run)
        super()._on_chain_end(run)

    def _on_chain_error(self, run: Run) -> None:
        self._add_root_metadata(run)
        super()._on_chain_error(run)


@dataclass
class TraceSetup:
    """Config de una ejecución trazada. ``run_id`` es el id del run raíz en LangSmith."""

    config: RunnableConfig
    run_id: uuid.UUID
    enabled: bool
    tracer: RootMetadataTracer | None = None
    project_name: str | None = None

    @contextmanager
    def activate(self) -> Iterator[None]:
        """Contexto de ejecución: deshabilita las trazas implícitas por entorno si
        ``enabled`` es ``False``; con trazas, no cambia nada (el tracer va en ``config``)."""
        if self.enabled:
            yield
            return
        with tracing_context(enabled=False):
            yield

    def flush(self) -> None:
        """Espera a que se envíen las trazas pendientes (llamar al terminar, p. ej. en el CLI)."""
        if self.tracer is not None:
            self.tracer.wait_for_futures()


def make_trace_setup(
    settings: Settings,
    *,
    run_name: str,
    metadata: Mapping[str, Any] | None = None,
    tags: list[str] | None = None,
    root_metadata: RootMetadataFn | None = None,
    client: Client | None = None,
) -> TraceSetup:
    """``RunnableConfig`` con ``run_name``, ``run_id``, ``tags``, ``metadata`` y el tracer de
    LangSmith (si ``settings.langsmith_tracing``) que agrega ``root_metadata`` al final."""
    run_id = uuid.uuid4()
    config: RunnableConfig = {
        "run_name": run_name,
        "run_id": run_id,
        "tags": list(tags or []),
        "metadata": dict(metadata or {}),
    }
    if not settings.langsmith_tracing:
        return TraceSetup(config=config, run_id=run_id, enabled=False)
    tracer = RootMetadataTracer(
        root_metadata=root_metadata,
        project_name=settings.langsmith_project,
        client=client if client is not None else make_langsmith_client(settings),
        tags=list(tags or []),
    )
    config["callbacks"] = [tracer]
    return TraceSetup(
        config=config, run_id=run_id, enabled=True, tracer=tracer, project_name=settings.langsmith_project
    )


@dataclass
class RunUrlBuilder:
    """Construye la URL de LangSmith de un run raíz a partir de su ``run_id`` (FR-055).

    La URL necesita el id del proyecto (``LANGSMITH_PROJECT``) y del tenant, que se piden una sola
    vez a LangSmith y quedan en el cliente. Sin trazas, sin ``run_id`` o si LangSmith no responde,
    ``__call__`` devuelve ``None`` (la UI muestra el ``run_id`` sin link) y nunca lanza.
    """

    settings: Settings
    client: Any | None = None
    _project_id: Any = field(default=None, init=False, repr=False)

    def __call__(self, run_id: str | uuid.UUID | None) -> str | None:
        if not run_id or not self.settings.langsmith_tracing:
            return None
        try:
            run_uuid = uuid.UUID(str(run_id))
            if self.client is None:
                self.client = make_langsmith_client(self.settings)
            if self._project_id is None:
                self._project_id = self.client.read_project(project_name=self.settings.langsmith_project).id
            run = SimpleNamespace(id=run_uuid, session_id=self._project_id, address=None)
            with warnings.catch_warnings():  # get_run_url está deprecado (sigue hasta 2027)
                warnings.simplefilter("ignore")
                return str(self.client.get_run_url(run=run, project_id=self._project_id))
        except Exception as exc:  # el link es opcional: nunca rompe la respuesta
            logger.warning("no se pudo armar la URL de la traza %s: %s", run_id, type(exc).__name__)
            return None
