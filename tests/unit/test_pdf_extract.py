"""VC-010, VC-011, VC-015..VC-019, VC-031, VC-032: extracción de tablas desde PDFs.

Los PDFs se generan en el test con ``tests/fixtures/make_pdfs.py`` (sin red ni archivos).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable

import pytest

from services import pdf_extract
from services.pdf_extract import (
    REASON_LARGE_IMAGE,
    REASON_NO_TEXT,
    IngestionWarning,
    PdfOpenError,
    extract_tables,
    load_pdf,
)
from tests.fixtures import make_pdfs


def extract(pdf: bytes, max_tables: int = 40):
    return extract_tables(load_pdf("fixture.pdf", pdf), max_tables=max_tables)


# --- VC-010 (FR-010) -----------------------------------------------------------------------------


def test_vc010_load_pdf_hash_name_and_blocks() -> None:
    """Given un PDF de fixture, when corre load_pdf, then doc_hash es el SHA-256 de los bytes,
    se guarda el nombre y las páginas con texto tienen bloques con bbox."""
    pdf = make_pdfs.two_tables_one_page()

    loaded = load_pdf("informe.pdf", pdf)

    assert loaded.doc_hash == hashlib.sha256(pdf).hexdigest()
    assert loaded.file_name == "informe.pdf"
    assert loaded.n_pages == 1
    page = loaded.pages[0]
    assert page.number == 1 and page.has_text
    assert page.blocks, "una página con texto debe tener bloques"
    texts = " ".join(b.text for b in page.blocks)
    assert "Informe de prueba" in texts
    for block in page.blocks:
        x0, y0, x1, y1 = block.bbox
        assert x0 < x1 and y0 < y1
        assert block.font_size > 0
    assert loaded.median_font_size > 0


def test_vc010_page_without_text_has_no_blocks() -> None:
    loaded = load_pdf("img.pdf", make_pdfs.image_only_page())
    assert loaded.pages[0].has_text
    assert not loaded.pages[1].has_text and loaded.pages[1].blocks == []


# --- VC-011 (FR-011) -----------------------------------------------------------------------------


def test_vc011_two_tables_on_one_page() -> None:
    """Given una página con 2 tablas, when corre extract_tables, then hay 2 DataFrames con
    page=1 e index_on_page 0 y 1, cada uno con su bbox."""
    tables, warnings = extract(make_pdfs.two_tables_one_page())

    assert warnings == []
    assert [t.pages for t in tables] == [[1], [1]]
    assert [t.index_on_page for t in tables] == [0, 1]
    assert tables[0].bbox_first[1] < tables[1].bbox_first[1]
    assert list(tables[0].df.columns) == ["Región", "Población", "Tasa"]
    assert tables[0].df.shape == (3, 3)
    assert tables[0].df["Población"].tolist() == [1001.5, 2002.5, 3003.5]  # es-AR → float
    assert tables[0].dtypes == {"Región": "str", "Población": "float64", "Tasa": "float64"}
    assert list(tables[1].df.columns) == ["Sector", "Ocupados"]
    assert [t.ref for t in tables] == ["p001-t0", "p001-t1"]


# --- VC-015 (FR-014, BR-001) ---------------------------------------------------------------------


def test_vc015_three_page_table_with_repeated_header_is_one_logical_table() -> None:
    """Given una tabla en las págs. 4–6 que repite el encabezado, when se extrae, then queda
    1 tabla lógica con 5+5+3 filas en orden, sin encabezados repetidos y pages=[4, 5, 6]."""
    tables, warnings = extract(make_pdfs.multipage_repeated_header())

    assert warnings == []
    assert len(tables) == 1
    table = tables[0]
    assert table.pages == [4, 5, 6]
    assert table.df.shape == (13, 3)
    assert table.df["Región"].tolist() == [f"Región {i}" for i in range(1, 14)]
    assert "Región" not in table.df["Región"].tolist()  # el encabezado repetido no es un dato
    assert table.df["Tasa"].dtype == "float64"
    assert table.bbox_first != table.bbox_last


# --- VC-016 (BR-001) -----------------------------------------------------------------------------


def test_vc016_continuation_without_header_is_joined() -> None:
    """Continuación sin encabezado (primera fila numérica) → se une a la tabla anterior."""
    tables, _ = extract(make_pdfs.continuation_without_header())

    assert len(tables) == 1
    assert tables[0].pages == [1, 2]
    assert tables[0].df.shape == (7, 3)
    assert tables[0].df["Región"].tolist()[-3:] == ["Región 5", "Región 6", "Región 7"]


def test_vc016_next_page_with_different_column_count_is_not_joined() -> None:
    """Misma posición en la página siguiente pero otra cantidad de columnas → 2 tablas."""
    tables, _ = extract(make_pdfs.continuation_different_columns())

    assert [t.pages for t in tables] == [[1], [2]]
    assert [t.df.shape[1] for t in tables] == [3, 4]
    assert tables[1].has_header is False and tables[1].header == ["col_0", "col_1", "col_2", "col_3"]


def test_vc016_table_that_is_not_first_on_next_page_is_not_joined() -> None:
    """La candidata no es la primera tabla de la página siguiente → no se une."""
    tables, _ = extract(make_pdfs.continuation_not_first_on_next_page())

    assert [(t.pages, t.index_on_page) for t in tables] == [([1], 0), ([2], 0), ([2], 1)]
    assert tables[0].df.shape == (4, 3)


def test_table_with_its_own_caption_is_not_a_continuation() -> None:
    """D-015: aunque repita el encabezado, una tabla con su propio epígrafe ("Cuadro 2")
    arriba en la página siguiente es una tabla nueva."""
    tables, _ = extract(make_pdfs.continuation_with_own_caption())

    assert [t.pages for t in tables] == [[1], [2]]


# --- Encabezado como texto suelto (D-012) --------------------------------------------------------


def test_header_written_as_loose_text_with_spanning_label() -> None:
    """Given un encabezado de dos niveles escrito como texto encima de la grilla, con "Total"
    subrayado sobre las columnas 1–2, when se extrae, then los nombres combinan los niveles."""
    tables, _ = extract(make_pdfs.header_as_loose_text())

    assert len(tables) == 1
    assert tables[0].has_header
    assert tables[0].header == ["Indicador", "Total - 2024", "Total - 2025"]
    assert tables[0].df["Total - 2025"].tolist() == [48.9, 45.0, 7.9]
    # El epígrafe "Cuadro 7." no forma parte del encabezado.
    assert all("Cuadro" not in name for name in tables[0].header)


def test_zebra_table_without_vertical_rules_keeps_white_rows_and_columns() -> None:
    """Given un cuadro sin rayas verticales con filas de fondo gris alternado (``find_tables``
    solo arma celdas en las grises), una columna de unidad a veces vacía y el encabezado
    "1º semestre de 2026*" escrito solo sobre la columna de valores, when se extrae, then
    queda una tabla con las 7 filas, cada valor en su columna y el encabezado (D-044)."""
    tables, warnings = extract(make_pdfs.zebra_table())

    assert warnings == []
    assert len(tables) == 1
    df = tables[0].df
    assert tables[0].header == ["col_0", "col_1", "1º semestre de 2026*"]
    assert df["col_0"].tolist() == [label for label, _, _ in make_pdfs.ZEBRA_ROWS]
    assert df["1º semestre de 2026*"].tolist() == [24.4, 3.89, 3.15, 1440395.0, 922755.0, -517640.0, 35.9]
    assert df["col_1"].fillna("").tolist() == ["%", "", "", "$", "$", "$", "%"]


def test_zebra_table_ending_on_white_row_keeps_last_row_but_not_the_source_note() -> None:
    """Given el mismo cuadro con una última fila sin fondo, when se extrae, then esa fila se
    conserva y la nota "Fuente: ..." de abajo no se agrega como fila (D-044)."""
    tables, _ = extract(make_pdfs.zebra_table(ends_on_white_row=True))

    assert len(tables) == 1
    df = tables[0].df
    assert len(df) == 8
    assert df.iloc[-1].tolist()[0] == "Hogares pobres (en miles)"
    assert df.iloc[-1].tolist()[2] == 2498.0
    assert not df["col_0"].str.contains("Fuente").any()


# --- VC-017 (FR-015) -----------------------------------------------------------------------------


def test_vc017_page_without_text_layer_is_skipped_with_warning() -> None:
    """Given una página solo imagen, when corre extract_tables, then hay un aviso "sin capa
    de texto" con su número, 0 tablas de esa página y no se avisa además la imagen (FR-016)."""
    tables, warnings = extract(make_pdfs.image_only_page())

    assert warnings == [IngestionWarning(page=2, reason=REASON_NO_TEXT)]
    assert all(2 not in t.pages for t in tables)
    assert len(tables) == 1


# --- VC-018 (FR-016) -----------------------------------------------------------------------------


def test_vc018_image_covering_30_percent_is_warned() -> None:
    tables, warnings = extract(make_pdfs.page_with_image(0.30))

    assert warnings == [IngestionWarning(page=1, reason=REASON_LARGE_IMAGE)]
    assert len(tables) == 1  # la página se procesa igual; no se inventa contenido


def test_vc018_image_covering_10_percent_is_not_warned() -> None:
    _, warnings = extract(make_pdfs.page_with_image(0.10))

    assert warnings == []


# --- VC-019 (FR-017, BR-005) ---------------------------------------------------------------------


def test_vc019_tables_below_minimums_are_discarded_with_warning() -> None:
    """Tabla de 1 fila de datos y tabla 69 % vacía → descartadas con aviso (página y motivo);
    tabla 2×2 completa → conservada."""
    tables, warnings = extract(make_pdfs.tables_below_minimums())

    assert [t.pages for t in tables] == [[3]]
    assert tables[0].df.shape == (2, 2)
    assert [(w.page, w.table_ref) for w in warnings] == [(1, "p001-t0"), (2, "p002-t0")]
    assert "1 fila(s) de datos" in warnings[0].reason
    assert "celdas vacías" in warnings[1].reason


def test_vc019_one_column_table_is_discarded_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """``find_tables`` no detecta grillas de 1 columna, así que el caso se inyecta: una tabla
    de 1 columna que llega al pipeline se descarta con aviso."""

    def one_column(page: object, page_number: int) -> list[pdf_extract._Fragment]:
        rows = [["1"], ["2"], ["3"]]
        return [pdf_extract._Fragment(page_number, 0, (72, 100, 152, 148), ["Valor"], rows, 1)]

    monkeypatch.setattr(pdf_extract, "_page_fragments", one_column)
    tables, warnings = extract(make_pdfs.no_tables())

    assert tables == []
    assert [(w.page, w.table_ref) for w in warnings] == [(1, "p001-t0")]
    assert "1 columna(s)" in warnings[0].reason


# --- VC-031 (FR-027) -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "make_bytes", "detail"),
    [
        ("txt", lambda: "Región;Valor\nSalta;12\n".encode(), "no es un PDF válido"),
        ("truncado", lambda: make_pdfs.two_tables_one_page()[:300], "no es un PDF válido"),
        ("encriptado", make_pdfs.encrypted, "contraseña"),
        ("vacío", lambda: b"", "vacío"),
    ],
)
def test_vc031_invalid_pdf_raises_pdf_open_error(
    label: str, make_bytes: Callable[[], bytes], detail: str
) -> None:
    """Given bytes de un .txt, un PDF truncado o un PDF encriptado, when corre load_pdf,
    then se lanza PdfOpenError con el mensaje "No se pudo abrir el PDF: <motivo>"."""
    with pytest.raises(PdfOpenError) as info:
        load_pdf(f"{label}.pdf", make_bytes())

    assert str(info.value).startswith("No se pudo abrir el PDF: ")
    assert detail in info.value.reason


# --- VC-032 (FR-028) -----------------------------------------------------------------------------


def test_vc032_only_first_max_tables_are_kept_with_warning() -> None:
    """Given 5 tablas y MAX_TABLES_PER_PDF=3, then se devuelven las 3 primeras en orden de
    página y un aviso "2 tablas omitidas"."""
    tables, warnings = extract(make_pdfs.five_tables(), max_tables=3)

    assert [t.header[0] for t in tables] == ["Concepto 0", "Concepto 1", "Concepto 2"]
    assert len(warnings) == 1
    assert "2 tablas omitidas" in warnings[0].reason
    assert warnings[0].page is None


def test_no_tables_gives_empty_result_without_warnings() -> None:
    tables, warnings = extract(make_pdfs.no_tables())
    assert tables == [] and warnings == []


# --- Separador decimal del documento (D-008) -----------------------------------------------------


def test_document_decimal_signal_decides_ambiguous_columns() -> None:
    """Una columna con solo "1.234" se lee con la señal es-AR del resto del documento."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    make_pdfs.draw_table(page, 72, 100, [["Región", "Tasa"], ["A", "12,5"], ["B", "7,25"]], [120, 80])
    make_pdfs.draw_table(page, 72, 300, [["Rubro", "Monto"], ["X", "1.234"], ["Y", "2.500"]], [120, 80])
    tables, _ = extract(doc.tobytes())

    assert tables[1].df["Monto"].tolist() == [1234.0, 2500.0]
    assert not any(math.isnan(v) for v in tables[0].df["Tasa"])
