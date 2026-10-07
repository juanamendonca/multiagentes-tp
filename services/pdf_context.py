"""Contexto textual de cada tabla (spec FR-018, BR-006).

- ``above``: hasta 3 bloques cuyo borde inferior está ≤ 120 pt sobre la tabla (el más
  cercano primero), en la primera página de la tabla.
- ``below``: hasta 2 bloques cuyo borde superior está ≤ 80 pt bajo la tabla, en la
  última página.
- ``section``: el último bloque anterior a la tabla en el documento con fuente
  ≥ 1,15 × la mediana del documento o en negrita, de ≤ 120 caracteres.

El texto total (``section`` + ``above`` + ``below``) se trunca a 2 000 caracteres. Se
ignoran los bloques que caen dentro de una tabla y los encabezados/pies de página, y
como sección no se toman epígrafes "Cuadro N"/"Gráfico N" ni rótulos sueltos (D-014).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from services.pdf_extract import (
    CAPTION_RE,
    BBox,
    LoadedPdf,
    PageText,
    RawTable,
    TextBlock,
    is_page_furniture,
)
from services.table_parsing import clean_text

ABOVE_MAX_GAP = 120.0
BELOW_MAX_GAP = 80.0
MAX_ABOVE = 3
MAX_BELOW = 2
SECTION_FONT_RATIO = 1.15
SECTION_MAX_CHARS = 120
MAX_CONTEXT_CHARS = 2000


@dataclass(frozen=True)
class TableContext:
    """Contexto de una tabla (FR-018). ``text`` junta todo en un string ≤ 2 000 caracteres."""

    above: list[str]
    below: list[str]
    section: str | None

    @property
    def text(self) -> str:
        parts = ([self.section] if self.section else []) + self.above + self.below
        return "\n".join(parts)


def _inside(block: TextBlock, bbox: BBox) -> bool:
    x0, y0, x1, y1 = block.bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return bbox[0] - 1 <= cx <= bbox[2] + 1 and bbox[1] - 1 <= cy <= bbox[3] + 1


def _candidates(page: PageText, table_boxes: Sequence[BBox]) -> list[TextBlock]:
    return [
        b
        for b in page.blocks
        if not is_page_furniture(b, page) and not any(_inside(b, box) for box in table_boxes)
    ]


def _boxes_on_page(page_number: int, tables: Sequence[RawTable]) -> list[BBox]:
    boxes: list[BBox] = []
    for t in tables:
        if t.pages[0] == page_number:
            boxes.append(t.bbox_first)
        if t.pages[-1] == page_number:
            boxes.append(t.bbox_last)
    return boxes


def _is_section_block(block: TextBlock, median_size: float) -> bool:
    """BR-006 (sección), más D-014: no es un epígrafe de cuadro/gráfico y empieza como un
    título (mayúscula o número) y, si solo es negrita, no es más chico que el texto común;
    así se descartan rótulos sueltos de gráficos."""
    text = clean_text(block.text)
    big = median_size > 0 and block.font_size >= SECTION_FONT_RATIO * median_size
    # Negrita más chica que el texto común son rótulos de gráficos, no títulos (D-014).
    bold = block.bold and block.font_size >= median_size
    return (
        (big or bold)
        and 0 < len(text) <= SECTION_MAX_CHARS
        and any(ch.isalpha() for ch in text)
        and (text[0].isupper() or text[0].isdigit())
        and not _is_figure_caption(text)
    )


def _is_figure_caption(text: str) -> bool:
    """Epígrafes "Cuadro N" / "Gráfico N" (la numeración de secciones tipo "1.2" sí vale)."""
    return CAPTION_RE.match(text) is not None and not text[0].isdigit()


def _truncate(parts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Recorta partes (en orden de prioridad) para que el texto unido no pase el máximo."""
    budget = MAX_CONTEXT_CHARS
    kept: list[tuple[str, str]] = []
    for kind, text in parts:
        cost = len(text) + (1 if kept else 0)
        if budget <= (1 if kept else 0):
            break
        if cost > budget:
            text = text[: budget - (1 if kept else 0)]
            cost = budget
        kept.append((kind, text))
        budget -= cost
    return kept


def collect_context(loaded: LoadedPdf, table: RawTable, tables: Sequence[RawTable] = ()) -> TableContext:
    """FR-018 / BR-006: texto arriba, abajo y sección más cercana de ``table``.

    ``tables`` (opcional) son todas las tablas del documento: sus bloques de texto no se
    toman como contexto de otras tablas.
    """
    all_tables = list(tables) if any(t is table for t in tables) else [*tables, table]
    first_page = loaded.pages[table.pages[0] - 1]
    last_page = loaded.pages[table.pages[-1] - 1]
    top = table.bbox_first[1]
    bottom = table.bbox_last[3]

    above_blocks = [
        b
        for b in _candidates(first_page, _boxes_on_page(first_page.number, all_tables))
        if 0 <= top - b.bbox[3] + 1 and top - b.bbox[3] <= ABOVE_MAX_GAP
    ]
    above_blocks.sort(key=lambda b: top - b.bbox[3])
    below_blocks = [
        b
        for b in _candidates(last_page, _boxes_on_page(last_page.number, all_tables))
        if 0 <= b.bbox[1] - bottom + 1 and b.bbox[1] - bottom <= BELOW_MAX_GAP
    ]
    below_blocks.sort(key=lambda b: b.bbox[1] - bottom)

    section: str | None = None
    for page in loaded.pages[: first_page.number]:
        blocks = sorted(
            _candidates(page, _boxes_on_page(page.number, all_tables)), key=lambda b: (b.bbox[1], b.bbox[0])
        )
        for b in blocks:
            if page.number == first_page.number and b.bbox[3] > top + 1:
                break
            if _is_section_block(b, loaded.median_font_size):
                section = clean_text(b.text)

    above = [clean_text(b.text) for b in above_blocks[:MAX_ABOVE]]
    below = [clean_text(b.text) for b in below_blocks[:MAX_BELOW]]
    # Prioridad al truncar: sección, el bloque más cercano de arriba y de abajo, el resto.
    ordered: list[tuple[str, str]] = []
    if section:
        ordered.append(("section", section))
    for i in range(max(len(above), len(below))):
        if i < len(above):
            ordered.append(("above", above[i]))
        if i < len(below):
            ordered.append(("below", below[i]))
    kept = _truncate(ordered)
    return TableContext(
        above=[t for k, t in kept if k == "above"],
        below=[t for k, t in kept if k == "below"],
        section=next((t for k, t in kept if k == "section"), None),
    )
