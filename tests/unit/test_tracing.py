"""``services.tracing`` sin red: metadata del run raíz al terminar (mecanismo de FR-060, D-022),
trazas desactivadas y saneado de entradas/salidas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from langchain_core.runnables import RunnableLambda
from langsmith.utils import tracing_is_enabled

import services.tracing as tracing
from agents.ingestion.graph import run_ingestion, trace_metadata
from agents.ingestion.nodes import IngestionDeps
from services.pdf_extract import extract_tables, load_pdf
from services.table_store import TableStore
from services.tracing import RootMetadataTracer, make_trace_setup, sanitize_for_trace
from tests.fakes import FakeEmbeddings, FakeVectorStore, ScriptedChatModel
from tests.fixtures import make_pdfs
from tests.tables import fake_settings, valid_enrichment


class FakeLangSmithClient:
    """Lo que ``LangChainTracer`` usa de ``langsmith.Client``: registra creaciones y updates."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    def create_run(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    def update_run(self, **kwargs: Any) -> None:
        self.updated.append(kwargs)

    def flush(self) -> None:
        pass


def metadata_of(calls: list[dict[str, Any]], name: str) -> dict[str, Any]:
    (call,) = [c for c in calls if c.get("name") == name]
    return dict((call.get("extra") or {}).get("metadata") or {})


def test_root_metadata_is_added_to_the_root_run_when_it_ends() -> None:
    """Given un tracer con root_metadata, when termina el run raíz, then su PATCH final lleva la
    metadata calculada desde la salida; los hijos no la reciben."""
    client = FakeLangSmithClient()
    tracer = RootMetadataTracer(
        root_metadata=lambda out: {"n_keys": len(out)}, client=client, project_name="p"
    )
    child = RunnableLambda(lambda x: x, name="child")
    root = RunnableLambda(lambda x: child.invoke(x), name="root")

    root.invoke(
        {"a": 1, "b": 2},
        {"callbacks": [tracer], "run_name": "ingestion:x.pdf", "metadata": {"doc_hash": "h"}},
    )
    tracer.wait_for_futures()

    assert metadata_of(client.updated, "ingestion:x.pdf") == {"doc_hash": "h", "n_keys": 2}
    assert "n_keys" not in metadata_of(client.updated, "child")
    assert tracer.root_run_id is not None


def test_ingestion_trace_has_name_run_id_and_final_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given trazas activadas (cliente fake), when se ingiere un PDF, then el run raíz se llama
    ingestion:<archivo>, tiene el run_id del resultado, un hijo por nodo y metadata doc_hash,
    n_tables y n_warnings."""
    pdf = make_pdfs.two_tables_one_page()
    settings = fake_settings(tmp_path / "tables", langsmith_tracing=True)
    tables, _ = extract_tables(load_pdf("x.pdf", pdf))
    store = FakeVectorStore()
    deps = IngestionDeps(
        settings=settings,
        chat_model=ScriptedChatModel(responses=[valid_enrichment(t.df) for t in tables]),
        embeddings=FakeEmbeddings(dimension=8),
        vector_store_factory=lambda: store,
        table_store=TableStore(settings.tables_dir),
        enrich_concurrency=1,
    )
    client = FakeLangSmithClient()
    monkeypatch.setattr(tracing, "make_langsmith_client", lambda _settings: client)

    result = run_ingestion("informe.pdf", pdf, deps)

    root = [c for c in client.updated if c.get("name") == "ingestion:informe.pdf"]
    assert len(root) == 1 and str(root[0]["run_id"]) == result.run_id
    metadata = metadata_of(client.updated, "ingestion:informe.pdf")
    assert metadata["doc_hash"] == result.doc_hash
    assert metadata["n_tables"] == 2 and metadata["n_warnings"] == len(result.warnings)
    children = {c["name"] for c in client.created if str(c.get("parent_run_id")) == result.run_id}
    assert {"load_pdf", "extract_tables", "collect_context", "enrich", "validate", "index"} <= children
    root_inputs = next(c for c in client.created if c.get("name") == "ingestion:informe.pdf")["inputs"]
    assert root_inputs["file_name"] == "informe.pdf"


def test_disabled_tracing_has_no_tracer_and_disables_env_tracing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con LANGSMITH_TRACING=false en Settings no se agrega tracer y, aunque el entorno del
    proceso diga LANGSMITH_TRACING=true, la ejecución no traza."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    setup = make_trace_setup(fake_settings(tmp_path), run_name="ingestion:x.pdf", metadata={"doc_hash": "h"})

    assert "callbacks" not in setup.config and setup.tracer is None and not setup.enabled
    assert setup.config["run_name"] == "ingestion:x.pdf" and setup.config["run_id"] == setup.run_id
    with setup.activate():
        assert tracing_is_enabled() is False


def test_trace_metadata_counts_tables_and_warnings() -> None:
    state = {"doc_hash": "h", "tables": [1, 2, 3], "warnings": ["w"], "records": [], "status": "ok"}

    metadata = trace_metadata(state)

    assert metadata["doc_hash"] == "h" and metadata["n_tables"] == 3 and metadata["n_warnings"] == 1


def test_sanitize_for_trace_summarizes_bytes_frames_and_long_values() -> None:
    df = pd.DataFrame({"a": range(3), "b": ["x", "y", "z"]})
    payload = {"pdf_bytes": b"%PDF" * 10, "df": df, "items": list(range(80)), "text": "x" * 5000, "n": 1}

    out = sanitize_for_trace(payload)

    assert out["pdf_bytes"] == "<40 bytes>"
    assert out["df"] == {"type": "DataFrame", "shape": [3, 2], "columns": ["a", "b"]}
    assert len(out["items"]) == 51 and out["items"][-1] == "… (+30 elementos)"
    assert len(out["text"]) < 4100 and out["n"] == 1
