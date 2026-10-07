"""VC-020: contexto de cada tabla (FR-018, BR-006)."""

from __future__ import annotations

from services.pdf_context import MAX_CONTEXT_CHARS, collect_context
from services.pdf_extract import extract_tables, load_pdf
from tests.fixtures import make_pdfs


def context_of(pdf: bytes, index: int = 0):
    loaded = load_pdf("fixture.pdf", pdf)
    tables, _ = extract_tables(loaded)
    return collect_context(loaded, tables[index], tables)


def test_vc020_caption_above_and_source_below_but_not_far_paragraph() -> None:
    """Given "Cuadro 3. …" 40 pt arriba, un párrafo 300 pt arriba y "Fuente: INDEC" 20 pt
    abajo, when corre collect_context, then above contiene "Cuadro 3" y no el párrafo
    lejano, below contiene "Fuente: INDEC" y el contexto total es ≤ 2 000 caracteres."""
    ctx = context_of(make_pdfs.table_with_context())

    assert any("Cuadro 3" in text for text in ctx.above)
    assert not any("Párrafo lejano" in text for text in ctx.above)
    assert any("Fuente: INDEC" in text for text in ctx.below)
    assert len(ctx.text) <= MAX_CONTEXT_CHARS


def test_vc020_section_is_nearest_large_title_before_table() -> None:
    """La sección es el último bloque anterior con fuente ≥ 1,15× la mediana o negrita."""
    ctx = context_of(make_pdfs.table_with_context())

    assert ctx.section == "2. Mercado de trabajo regional"


def test_vc020_above_is_nearest_first_and_header_text_is_not_context() -> None:
    """El bloque más cercano va primero; el encabezado de la tabla no es contexto."""
    ctx = context_of(make_pdfs.table_with_context())

    assert ctx.above[0].startswith("Cuadro 3")
    assert all("Región" not in text for text in ctx.above + ctx.below)


def test_vc020_context_is_truncated_to_2000_characters() -> None:
    """Con bloques largos cerca de la tabla, el contexto total se trunca a 2 000 caracteres,
    priorizando la sección y los bloques más cercanos."""
    ctx = context_of(make_pdfs.table_with_context(long_paragraph=True))

    total = (len(ctx.section) if ctx.section else 0) + sum(len(t) for t in ctx.above + ctx.below)
    assert len(ctx.text) <= MAX_CONTEXT_CHARS
    assert total <= MAX_CONTEXT_CHARS
    assert ctx.section == "2. Mercado de trabajo regional"
    assert ctx.above and ctx.above[0].startswith("Cuadro 3")


def test_multipage_table_uses_top_of_first_page_and_bottom_of_last() -> None:
    """BR-006: para una tabla multipágina, el contexto de arriba sale de la primera página
    y el de abajo de la última."""
    ctx = context_of(make_pdfs.continuation_without_header())

    assert any("Cuadro 1" in text for text in ctx.above)
    assert ctx.below == ["Fuente: INDEC."]


def test_text_of_another_table_is_not_context() -> None:
    """Con dos tablas apiladas, las celdas de la primera no son contexto de la segunda."""
    ctx = context_of(make_pdfs.two_tables_one_page(), index=1)

    assert all("Región" not in text for text in ctx.above)
