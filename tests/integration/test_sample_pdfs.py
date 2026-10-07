"""VC-090 (parte técnica) y VC-015 sobre PDFs reales: extracción de los PDFs de muestra.

Sin red: lee ``data/sample_pdfs/``. Se marca ``integration`` porque depende de los PDFs
reales versionados (y tarda ~30 s por ``find_tables``).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest

from services.pdf_context import collect_context
from services.pdf_extract import LoadedPdf, RawTable, extract_tables, load_pdf

pytestmark = pytest.mark.integration

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "sample_pdfs"
CEPAL = "cepal_anuario_estadistico_2025_poblacion_trabajo.pdf"
EPH_2026 = "indec_eph_mercado_trabajo_2t2026.pdf"
EPH_2025 = "indec_eph_mercado_trabajo_2t2025.pdf"


@cache
def extracted(file_name: str) -> tuple[LoadedPdf, list[RawTable]]:
    loaded = load_pdf(file_name, (SAMPLE_DIR / file_name).read_bytes())
    tables, _ = extract_tables(loaded)
    return loaded, tables


def table(file_name: str, ref: str) -> RawTable:
    return next(t for t in extracted(file_name)[1] if t.ref == ref)


def value(t: RawTable, row_label: str, column: int) -> float:
    labels = t.df[t.df.columns[0]].str.strip()
    return float(t.df.loc[labels == row_label, t.df.columns[column]].iloc[0])


def test_vc090_sample_dir_has_at_least_two_real_pdfs_with_text_and_sources() -> None:
    pdfs = sorted(p.name for p in SAMPLE_DIR.glob("*.pdf"))
    assert pdfs == [CEPAL, EPH_2025, EPH_2026]
    assert (SAMPLE_DIR / "SOURCES.md").is_file()
    sources = (SAMPLE_DIR / "SOURCES.md").read_text(encoding="utf-8")
    for name in pdfs:
        loaded, tables = extracted(name)
        assert all(p.has_text for p in loaded.pages), f"{name}: hay páginas sin capa de texto"
        assert tables, f"{name}: sin tablas"
        assert name in sources and "https://" in sources


def test_vc015_cepal_population_table_spans_two_pages() -> None:
    """Cuadro 1.1.1 de CEPAL (págs. 3–4 del extracto, 17–18 del original) repite el
    encabezado en la pág. 4: queda 1 tabla lógica de 39 + 10 filas, sin encabezado repetido."""
    t = table(CEPAL, "p003-t0")

    assert t.pages == [3, 4]
    assert t.df.shape == (49, 7)
    countries = t.df["País"].str.strip().tolist()
    assert countries[0] == "Anguila" and countries[-1] == "El Caribe"
    assert "País" not in countries
    assert value(t, "Argentina", 1) == pytest.approx(45851.4)  # pág. 3
    assert value(t, "Uruguay", 1) == pytest.approx(3384.7)  # pág. 4 (continuación)
    assert value(t, "América Latina y el Caribe", 1) == pytest.approx(667888.6)  # "667 888.6b"


def test_eph_2026_headers_and_values_match_the_pdf() -> None:
    """Cuadro 1.3 (pág. 8): encabezado reconstruido de 3 niveles y números es-AR."""
    t = table(EPH_2026, "p008-t0")

    assert t.header[5] == "Total 31 aglomerados urbanos - Año 2026 - 2° trimestre*"
    assert value(t, "Tasa de actividad", 5) == pytest.approx(48.9)
    assert value(t, "Población total 31 aglomerados urbanos", 5) == pytest.approx(30158.0)
    # Cuadro 1.1: "45,4 (1)" lleva una llamada a nota y sigue siendo número.
    assert value(table(EPH_2026, "p005-t0"), "Empleo", 2) == pytest.approx(45.4)


def test_eph_reports_are_consistent_across_years() -> None:
    """La población de los 31 aglomerados del 2.º trimestre de 2025 es la misma en ambos
    informes (Cuadro 3.2 del de 2025 y columna 2T2025 del Cuadro 1.3 del de 2026)."""
    t2025 = table(EPH_2025, "p017-t0")
    t2026 = table(EPH_2026, "p008-t0")

    assert t2025.header[1] == "Población (en miles) - Total"
    assert value(t2025, "Total 31 aglomerados urbanos", 1) == value(
        t2026, "Población total 31 aglomerados urbanos", 1
    )


def test_eph_2026_consecutive_cuadros_with_same_header_are_not_merged() -> None:
    """Cuadro 2.2 (último de la pág. 15) y Cuadro 2.3 (primero de la pág. 16) tienen el
    mismo encabezado pero cada uno su epígrafe: quedan separados (D-015)."""
    refs = {t.ref: t.pages for t in extracted(EPH_2026)[1]}

    assert refs["p015-t1"] == [15]
    assert refs["p016-t0"] == [16]


def test_context_of_real_table_has_caption_and_section() -> None:
    loaded, tables = extracted(EPH_2026)
    ctx = collect_context(loaded, table(EPH_2026, "p008-t0"), tables)

    assert ctx.above[0].startswith("Cuadro 1.3")
    assert ctx.section is not None and ctx.section.startswith("1. Resultados del total")
    assert any("Fuente: INDEC" in text for text in ctx.below)
