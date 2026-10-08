"""App Streamlit: subir PDFs, explorar las tablas indexadas y hacer preguntas.

Uso (desde la raíz del repo, con ``.env`` completo)::

    .venv\\Scripts\\streamlit run app.py

Pestañas: "Subir PDF" (Agente de Ingesta con progreso en vivo), "Explorar tablas" (catálogo de
``TABLES_DIR`` y borrado de documentos) y "Preguntar" (chat con el Agente Analista). Si falta
configuración (FR-002) o el índice tiene otra dimensión (FR-004) se muestra el error y se
deshabilitan las pestañas que dependen de eso; Explorar sigue disponible en solo lectura.
"""

from __future__ import annotations

import streamlit as st

from services.config import Settings
from services.llm import enable_cache_if_configured
from services.tracing import RunUrlBuilder
from services.vectorstore import open_index
from ui.ask import render_ask_tab
from ui.explore import render_explore_tab
from ui.setup import AppContext, IndexCheck, build_app_context, make_analyst_runner, make_ingestion_runner
from ui.upload import render_upload_tab

INDEX_CHECK_KEY = "index_check"
RUN_URL_KEY = "run_url_builder"


@st.cache_resource(show_spinner=False)
def _enable_llm_cache(_settings: Settings, enabled: bool) -> bool:
    """Instala la caché SQLite del LLM una sola vez por proceso (FR-005)."""
    return enable_cache_if_configured(_settings) is not None if enabled else False


def _render_header(ctx: AppContext) -> None:
    st.title("QA sobre tablas en PDFs")
    st.caption(
        "Subí informes en PDF, explorá las tablas que el Agente de Ingesta extrajo e indexó, y hacé "
        "preguntas que el Agente Analista responde ejecutando código sobre esas tablas."
    )
    if ctx.config_error is not None:
        lines = ["**Configuración incompleta.** Completá el archivo `.env` (ver `.env.example`)."]
        lines += [f"- Falta la variable `{name}`" for name in ctx.config_error.missing]
        lines += [
            f"- `{name}` tiene un valor inválido: {why}" for name, why in ctx.config_error.invalid.items()
        ]
        lines.append(
            "\nLas pestañas «Subir PDF» y «Preguntar» quedan deshabilitadas; "
            "«Explorar tablas» es de solo lectura."
        )
        st.error("\n".join(lines), icon=":material/settings_alert:")
        return
    check = ctx.index_check
    if check is not None and check.error:
        (st.error if check.blocking else st.warning)(check.error)
    if check is not None and check.note:
        st.info(check.note)


def main() -> None:
    st.set_page_config(
        page_title="QA sobre tablas en PDFs", page_icon=":material/table_chart:", layout="wide"
    )
    index_check: IndexCheck | None = st.session_state.get(INDEX_CHECK_KEY)
    ctx = build_app_context(index_check=index_check)
    if ctx.index_check is not None:
        st.session_state[INDEX_CHECK_KEY] = ctx.index_check
    _render_header(ctx)

    settings = ctx.settings
    disabled = ctx.disabled_reason
    ingest_runner = analyst_runner = None
    provider = None
    run_url = None
    if settings is not None:
        _enable_llm_cache(settings, settings.llm_cache)
        provider = lambda: open_index(settings)  # noqa: E731
        if disabled is None:
            ingest_runner = make_ingestion_runner(settings)
            analyst_runner = make_analyst_runner(settings)
            run_url = st.session_state.setdefault(RUN_URL_KEY, RunUrlBuilder(settings))

    upload_tab, explore_tab, ask_tab = st.tabs(["Subir PDF", "Explorar tablas", "Preguntar"])
    with upload_tab:
        render_upload_tab(ingest_runner, ctx.table_store, disabled)
    with explore_tab:
        render_explore_tab(ctx.table_store, provider)
    with ask_tab:
        render_ask_tab(analyst_runner, ctx.table_store, disabled, run_url)


main()
