"""Estado del grafo de Ingesta (spec §7.3).

El estado es un ``TypedDict``. Las claves de la spec (``file_name``, ``pdf_bytes``, ``doc_hash``,
``pages``, ``tables``, ``warnings``, ``records``) más las de control del grafo. ``warnings`` y
``llm_calls`` se acumulan (reducer ``operator.add``): cada nodo devuelve solo lo que agrega.

``tables`` es una lista de ``TableDraft`` (borradores por tabla). Los nodos no los mutan:
devuelven una lista nueva con copias (``dataclasses.replace``), así cada actualización del
``stream`` refleja el estado de ese paso.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Literal, TypedDict

import pandas as pd

from agents.ingestion.schemas import IngestionWarning, TableEnrichment, TableRecord
from services.pdf_context import TableContext
from services.pdf_extract import PageText, RawTable

# pending: sin enriquecer · valid: pasó BR-007 · invalid: tiene errores de BR-007
# repaired: agotó los reintentos y se aplicó BR-008 (metadata_quality="degraded")
DraftStatus = Literal["pending", "valid", "invalid", "repaired"]
# ok: tablas indexadas · no_tables: FR-026 · pdf_error: FR-027 · index_error: FR-029/FR-004
IngestionStatus = Literal["running", "ok", "no_tables", "pdf_error", "index_error"]


@dataclass(frozen=True, eq=False)
class TableDraft:
    """Borrador de una tabla durante la ingesta.

    - ``table_id``: id de BR-010 (lo asigna el código).
    - ``raw``: la tabla extraída (``DataFrame``, páginas, bbox, ``unit_hints``…).
    - ``context``: contexto de BR-006 (``None`` hasta ``collect_context``).
    - ``enrichment``: última salida del LLM (o la reparada por BR-008).
    - ``errors``: errores de validación del último intento (BR-007) o de la llamada al LLM.
    - ``attempts``: llamadas a ``enrich`` hechas para esta tabla (≤ ``1 + MAX_ENRICH_RETRIES``).
    """

    table_id: str
    raw: RawTable
    context: TableContext | None = None
    enrichment: TableEnrichment | None = None
    errors: list[str] = field(default_factory=list)
    attempts: int = 0
    status: DraftStatus = "pending"

    @property
    def ref(self) -> str:
        return self.raw.ref

    @property
    def df(self) -> pd.DataFrame:
        return self.raw.df

    def trace_summary(self) -> dict[str, object]:
        """Resumen para trazas de LangSmith (sin el DataFrame)."""
        return {
            "table_id": self.table_id,
            "pages": self.raw.pages,
            "shape": list(self.df.shape),
            "status": self.status,
            "attempts": self.attempts,
            "errors": self.errors,
            "name": self.enrichment.name if self.enrichment else None,
            "description": self.enrichment.description if self.enrichment else None,
        }


class IngestionState(TypedDict, total=False):
    """Estado del grafo de Ingesta."""

    # entrada
    file_name: str
    pdf_bytes: bytes
    # load_pdf (FR-010)
    doc_hash: str
    pages: list[PageText]
    median_font_size: float
    # extract_tables / collect_context / enrich / validate / repair
    tables: list[TableDraft]
    n_detected: int  # tablas lógicas extraídas (después del tope de FR-028)
    enrich_round: int  # vueltas por enrich (1 = primer intento)
    llm_calls: Annotated[int, operator.add]  # llamadas de chat hechas por enrich (NFR-007)
    warnings: Annotated[list[IngestionWarning], operator.add]
    # index (FR-023, FR-029)
    records: list[TableRecord]  # TableRecord guardados (y, si no hubo error, indexados)
    n_indexed: int
    deleted_ids: list[str]  # ids obsoletos borrados (BR-010)
    # resultado
    status: IngestionStatus
    message: str
    error: str | None
