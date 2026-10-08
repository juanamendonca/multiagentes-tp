"""UI Streamlit sin red: errores de configuración (VC-002, lado UI), manejo de errores de servicios
(VC-074), validación de preguntas (VC-076) y la lógica testeable de las pestañas (progreso de la
ingesta, borrado de documentos, chequeo del índice, link de la traza).

Dos tests usan ``streamlit.testing.v1.AppTest`` (la app real sin configuración y la pestaña
Preguntar con un Analista fake); el resto llama funciones de ``ui/`` con ``st.error``/``st.warning``
reemplazados por grabadoras.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest
import streamlit as st
from pinecone.exceptions import PineconeApiException
from streamlit.testing.v1 import AppTest

import services.config as config_module
from agents.analyst.schemas import AnalystResult
from agents.ingestion.state import TableDraft
from services.config import REQUIRED_VARS
from services.sandbox import ExecutionResult, ResultItem
from services.table_store import TableStore, doc_prefix
from services.tracing import RunUrlBuilder
from services.vectorstore import IndexDimensionMismatch, VectorRecord
from tests.fakes import FakeVectorStore
from tests.tables import DOC_HASH, OTHER_DOC_HASH, fake_settings, sample_df, sample_record
from ui.ask import ChatTurn, describe_update, submit_question, validate_question
from ui.errors import describe_error, redact, run_safely
from ui.explore import delete_document, group_by_document
from ui.setup import check_index
from ui.upload import IngestionProgress, result_message

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Reemplaza ``st.error``/``st.warning`` por grabadoras (sin runtime de Streamlit)."""
    calls: dict[str, list[str]] = {"error": [], "warning": []}
    monkeypatch.setattr(st, "error", lambda body, *a, **k: calls["error"].append(str(body)))
    monkeypatch.setattr(st, "warning", lambda body, *a, **k: calls["warning"].append(str(body)))
    return calls


def answered_result(question: str = "¿Tasa?") -> AnalystResult:
    return AnalystResult(
        question=question,
        status="answered",
        answer="La tasa de desocupación fue 7,9 %.",
        results=[ResultItem(part_id="p1", label="Tasa de desocupación", value=7.9, unit="%")],
        code="print('{\"results\": []}')",
        code_attempts=2,
        execution=ExecutionResult(status="ok", stdout="{}", returncode=0, duration_s=1.2),
        run_id=str(uuid.uuid4()),
    )


def connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "http://litellm.invalid/chat"))


class FakeRunner:
    """Analista fake: cuenta llamadas, emite un evento y devuelve (o lanza) lo guionado."""

    def __init__(self, *outcomes: AnalystResult | Exception) -> None:
        self.outcomes = list(outcomes)
        self.questions: list[str] = []

    def __call__(self, question: str, on_update: Callable[[str, Mapping[str, Any]], None]) -> AnalystResult:
        self.questions.append(question)
        on_update("decide_status", {"status": "answered"})
        outcome = self.outcomes.pop(0) if self.outcomes else answered_result(question)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- VC-002 (lado UI): la app sin PINECONE_API_KEY muestra el error, sin traceback --------------


def test_vc002_app_without_pinecone_key_renders_the_error_without_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given la app sin PINECONE_API_KEY (y sin leer el .env real)
    When se renderiza
    Then hay un st.error que nombra PINECONE_API_KEY, no hay excepción y Preguntar queda deshabilitada."""
    monkeypatch.setattr(config_module, "load_dotenv", lambda *a, **k: False)
    for name in REQUIRED_VARS:
        monkeypatch.setenv(name, "1536" if name == "EMBEDDING_DIM" else "valor-de-test")
    monkeypatch.delenv("PINECONE_API_KEY")
    monkeypatch.setenv("TABLES_DIR", str(tmp_path))
    monkeypatch.chdir(REPO)

    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=30).run()

    assert not at.exception
    errors = [e.value for e in at.error]
    assert any("PINECONE_API_KEY" in text for text in errors)
    assert not any("Traceback" in text for text in errors)
    assert [t.label for t in at.tabs] == ["Subir PDF", "Explorar tablas", "Preguntar"]
    assert at.chat_input[0].disabled
    assert any("PINECONE_API_KEY" in info.value for info in at.info)  # motivo en las pestañas


# --- VC-074: errores de servicios → st.error con el servicio, sin propagar, historial intacto ----


@pytest.mark.parametrize(
    ("error", "service"),
    [
        (connection_error(), "LiteLLM"),
        (PineconeApiException("index unavailable", 503), "Pinecone"),
        (IndexDimensionMismatch("idx", 3072, 1536), "Pinecone"),
        (RuntimeError("se rompió el grafo"), "Analista"),
    ],
    ids=["openai", "pinecone", "dimension", "generica"],
)
def test_vc074_service_error_shows_st_error_keeps_history_and_does_not_raise(
    shown: dict[str, list[str]], error: Exception, service: str
) -> None:
    """Given un historial con una respuesta previa y un Analista que lanza
    When se envía otra pregunta
    Then st.error nombra el servicio, no se propaga la excepción y el historial previo sigue."""
    history = [ChatTurn("user", "primera"), ChatTurn("assistant", "ok", result=answered_result("primera"))]
    runner = FakeRunner(error)

    turn = submit_question("¿Segunda pregunta?", history, runner)

    assert len(shown["error"]) == 1 and shown["error"][0].startswith(service)
    assert turn is not None and turn.error is not None and turn.error.startswith(service)
    assert [t.content for t in history[:2]] == ["primera", "ok"]
    assert len(history) == 4 and history[2].content == "¿Segunda pregunta?"


def test_run_safely_returns_value_or_none_and_reports_chained_service(shown: dict[str, list[str]]) -> None:
    """Given una excepción genérica causada por una de Pinecone
    When se ejecuta con run_safely
    Then se informa Pinecone; sin error devuelve el valor."""

    def fails() -> None:
        try:
            raise PineconeApiException("timeout", 504)
        except PineconeApiException as exc:
            raise RuntimeError("falló la ingesta") from exc

    errors: list[tuple[str, str]] = []
    assert run_safely("Ingesta", fails, on_error=lambda s, m: errors.append((s, m))) is None
    assert errors[0][0] == "Pinecone" and "falló la ingesta" in errors[0][1]
    assert run_safely("Ingesta", lambda x: x + 1, 1) == 2
    assert len(shown["error"]) == 1


def test_error_messages_are_summarized_and_secrets_redacted() -> None:
    """Given un mensaje largo con algo con forma de clave
    When se describe
    Then se recorta a ≤ 300 caracteres y la clave no aparece."""
    key = "sk-" + "a1B2" * 8
    service, summary = describe_error(
        ValueError(f"Incorrect API key provided: {key} " + "x" * 500), "Analista"
    )
    assert service == "Analista"
    assert key not in summary and "sk-***" in summary and len(summary) <= 300
    assert redact("token lsv2" + "_pt_abc123def") == "token lsv2" + "_***"


# --- VC-076: validación de la pregunta antes de llamar al Analista ------------------------------


@pytest.mark.parametrize(
    ("question", "invoked"),
    [("   ", False), ("", False), ("x" * 1001, False), ("x" * 1000, True), ("  " + "x" * 1000 + "  ", True)],
    ids=["espacios", "vacia", "1001", "1000", "1000-con-espacios"],
)
def test_vc076_question_validation_gates_the_analyst(
    shown: dict[str, list[str]], question: str, invoked: bool
) -> None:
    """Given una pregunta vacía / de 1 001 / de 1 000 caracteres
    When se envía
    Then el Analista se invoca solo si es válida; si no, hay un aviso y el historial no cambia."""
    history: list[ChatTurn] = []
    runner = FakeRunner()

    turn = submit_question(question, history, runner)

    assert (len(runner.questions) == 1) is invoked
    assert (turn is not None) is invoked
    assert (len(shown["warning"]) == 0) is invoked
    assert len(history) == (2 if invoked else 0)
    assert (validate_question(question) is None) is invoked


def test_vc073_ask_tab_keeps_two_questions_and_renders_each_answer(tmp_path: Path) -> None:
    """Given la pestaña Preguntar con un Analista fake (AppTest)
    When se hacen 2 preguntas seguidas y después una vacía
    Then ambas quedan en el historial con badge, texto, reintentos y código; la vacía solo avisa."""

    def script(runner, tables_dir):  # noqa: ANN001 (se ejecuta como script aparte)
        from services.table_store import TableStore
        from ui.ask import render_ask_tab

        render_ask_tab(runner, TableStore(tables_dir), None, lambda run_id: f"https://smith.test/r/{run_id}")

    runner = FakeRunner()
    at = AppTest.from_function(script, args=(runner, str(tmp_path)), default_timeout=30).run()
    at.chat_input[0].set_value("¿Primera?").run()
    at.chat_input[0].set_value("¿Segunda?").run()
    at.chat_input[0].set_value("   ").run()

    assert not at.exception
    assert runner.questions == ["¿Primera?", "¿Segunda?"]
    history = at.session_state["chat_history"]
    assert [t.role for t in history] == ["user", "assistant", "user", "assistant"]
    markdown = " ".join(m.value for m in at.markdown)
    assert "¿Primera?" in markdown and "¿Segunda?" in markdown and "7,9 %" in markdown
    assert any("Reintentos de código: 1" in c.value for c in at.caption)
    assert any("Ver la traza en LangSmith" in m.value for m in at.markdown)
    assert any("vacía" in w.value for w in at.warning)


# --- Lógica de las pestañas ---------------------------------------------------------------------


def test_ingestion_progress_counts_tables_rounds_and_invalid() -> None:
    """Given eventos de stream de una ingesta con un reintento
    When se acumulan
    Then las líneas muestran nodos y contadores (tablas detectadas, intento, inválidas)."""
    record = sample_record()
    drafts = [
        TableDraft(
            table_id=record.table_id,
            raw=None,
            context=None,
            enrichment=None,
            errors=[],
            attempts=1,  # type: ignore[arg-type]
            status=status,
        )
        for status in ("valid", "invalid", "valid")
    ]
    progress = IngestionProgress(file_name="a.pdf")
    progress.update("load_pdf", {"pages": [1, 2, 3], "status": "running"})
    progress.update("extract_tables", {"tables": drafts, "warnings": [object(), object()]})
    progress.update("enrich", {"tables": drafts, "enrich_round": 1, "llm_calls": 3})
    line = progress.update("validate", {"tables": drafts})
    progress.update("enrich", {"tables": drafts, "enrich_round": 2, "llm_calls": 1})
    assert progress.lines[0] == "load_pdf: 3 páginas leídas"
    assert "3 tablas detectadas" in progress.lines[1] and "2 avisos" in progress.lines[1]
    assert line == "validate: intento de validación 1 · 2 válidas · 1 inválidas"
    assert progress.enrich_round == 2 and progress.llm_calls == 4
    assert progress.label == "a.pdf: Enriqueciendo con el LLM · 3 tablas · intento 2…"


def test_result_messages_cover_no_tables_pdf_error_and_partial_index() -> None:
    from agents.ingestion.graph import IngestionResult

    assert result_message(IngestionResult("a.pdf", "", "no_tables", "x")) == (
        "warning",
        "No se encontraron tablas legibles.",
    )
    assert result_message(IngestionResult("a.pdf", "", "pdf_error", "No se pudo abrir el PDF: roto"))[1] == (
        "No se pudo abrir el PDF: roto"
    )
    level, text = result_message(
        IngestionResult("a.pdf", "", "index_error", "1 de 3 tablas indexadas; falló")
    )
    assert level == "error" and "1 de 3 tablas indexadas" in text and "Reingerir" in text


def test_describe_update_of_the_analyst_stream() -> None:
    assert describe_update("generate_code", {"code_attempts": 2}) == "generate_code: intento 2"
    execution = ExecutionResult(status="error", duration_s=1.5, error="returncode 1: KeyError: 'x'")
    assert describe_update("execute", {"execution": execution}).startswith(
        "execute: error en 1.5 s — returncode 1"
    )
    assert describe_update("decide_status", {"status": "partial"}) == "decide_status: partial"


def test_delete_document_removes_its_vectors_and_files_only(tmp_path: Path) -> None:
    """Given dos documentos indexados (Pinecone fake + TABLES_DIR)
    When se borra uno
    Then sus vectores quedan en 0 y sus archivos desaparecen; el otro queda intacto (FR-052)."""
    store = TableStore(tmp_path)
    vectors = FakeVectorStore()
    for doc_hash in (DOC_HASH, OTHER_DOC_HASH):
        for page in (3, 5):
            record = store.save_table(sample_record(doc_hash, first_page=page), sample_df())
            vectors.upsert([VectorRecord(id=record.table_id, values=[1.0] * 8, metadata={})])

    outcome = delete_document(DOC_HASH, table_store=store, vector_store=vectors, sleep=lambda _: None)

    assert len(outcome.vectors_deleted) == 2 and len(outcome.files_deleted) == 2
    assert outcome.remaining_vectors == 0
    assert vectors.list_ids(doc_prefix(DOC_HASH)) == []
    assert len(vectors.list_ids(doc_prefix(OTHER_DOC_HASH))) == 2
    docs = group_by_document(store.list_records())
    assert [d.doc_hash for d in docs] == [OTHER_DOC_HASH]


def test_check_index_reports_mismatch_missing_index_and_outage(tmp_path: Path) -> None:
    """FR-004 al arrancar: dimensión distinta → error bloqueante con ambas dimensiones; índice
    inexistente → nota; Pinecone caído → error no bloqueante."""
    settings = fake_settings(tmp_path)

    def mismatch(_: Any) -> None:
        raise IndexDimensionMismatch("idx", 1536, 3072)

    def outage(_: Any) -> None:
        raise PineconeApiException("unavailable", 503)

    check = check_index(settings, mismatch)
    assert check.blocking and "1536" in (check.error or "") and "3072" in (check.error or "")
    assert check_index(settings, lambda _: None).note is not None
    down = check_index(settings, outage)
    assert not down.blocking and (down.error or "").startswith("Pinecone")
    assert check_index(settings, lambda _: FakeVectorStore()).error is None


class FakeLangSmith:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.project_reads = 0

    def read_project(self, *, project_name: str) -> Any:
        self.project_reads += 1
        if self.fail:
            raise ConnectionError("LangSmith caído")
        return type("Project", (), {"id": uuid.UUID(int=7)})()

    def get_run_url(self, *, run: Any, project_id: Any) -> str:
        return f"https://smith.test/projects/p/{project_id}/r/{run.id}?poll=true"


def test_run_url_builder_builds_link_from_run_id_and_degrades_gracefully(tmp_path: Path) -> None:
    """FR-055: con run_id arma la URL (pidiendo el proyecto una sola vez); sin trazas, sin run_id o
    con LangSmith caído devuelve None sin lanzar."""
    client = FakeLangSmith()
    builder = RunUrlBuilder(fake_settings(tmp_path, langsmith_tracing=True), client=client)
    run_id = str(uuid.uuid4())
    assert builder(run_id) == f"https://smith.test/projects/p/{uuid.UUID(int=7)}/r/{run_id}?poll=true"
    builder(str(uuid.uuid4()))
    assert client.project_reads == 1
    assert builder(None) is None
    assert RunUrlBuilder(fake_settings(tmp_path), client=FakeLangSmith())(run_id) is None  # trazas apagadas
    assert (
        RunUrlBuilder(fake_settings(tmp_path, langsmith_tracing=True), client=FakeLangSmith(True))(run_id)
        is None
    )
