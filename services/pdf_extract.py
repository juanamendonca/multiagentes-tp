"""Extracción de tablas desde PDFs con PyMuPDF.

Spec FR-010, FR-011, FR-014..FR-017, FR-027, FR-028; BR-001, BR-005.

- ``load_pdf`` abre el PDF (o lanza ``PdfOpenError``), calcula ``doc_hash`` y lee por
  página los bloques de texto con bbox, tamaño de fuente y negrita.
- ``extract_tables`` detecta tablas con ``page.find_tables()``, reconstruye el
  encabezado a partir de las palabras de la franja de encabezado (D-012), une las
  continuaciones multipágina (BR-001), normaliza con ``services.table_parsing`` y
  aplica los mínimos (BR-005) y el tope de tablas (FR-028), juntando avisos.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import pymupdf
from pydantic import BaseModel, ConfigDict

from services.table_parsing import (
    YEAR_MAX,
    YEAR_MIN,
    Decimal,
    check_minimums,
    clean_text,
    flatten_header,
    infer_decimal_separator,
    is_missing_marker,
    normalize_table,
    parse_number,
)

BBox = tuple[float, float, float, float]
Row = list[str | None]

# FR-015 / FR-016: motivos de aviso (la UI los muestra tal cual).
REASON_NO_TEXT = "sin capa de texto"
REASON_LARGE_IMAGE = "imagen grande no procesada (posible tabla en imagen)"
# FR-016: fracción del área de la página a partir de la cual una imagen se avisa.
LARGE_IMAGE_FRACTION = 0.20
# BR-001 (d): fracción de celdas numéricas para considerar que una fila es de datos.
CONTINUATION_NUMERIC_FRACTION = 0.5

# Reconstrucción del encabezado (D-012), en puntos PDF.
MAX_HEADER_ROWS = 8  # filas iniciales que pueden ser encabezado
HEADER_FRAGMENT_MAX_ROWS = 6  # "tabla" de solo encabezado dibujada aparte, sobre la tabla
HEADER_FRAGMENT_MAX_GAP = 15.0
HEADER_FRAGMENT_MIN_OVERLAP = 0.8
HEADER_EXTEND_UP = 3.0  # texto pegado sobre el borde superior también es encabezado
PHRASE_GAP = 6.0  # separación horizontal que corta una línea en frases
CELL_LINE_GAP = 3.0  # líneas de una misma celda (texto en varias líneas)
RULE_MAX_GAP = 9.0  # una raya horizontal hasta 9 pt bajo una frase la "subraya"
RULE_FULL_WIDTH = 0.95  # rayas casi del ancho de la tabla son bordes, no agrupadores
RULE_CENTRE_TOLERANCE = 0.15  # un rótulo agrupador está centrado sobre su raya
EXTERNAL_HEADER_MAX_GAP = 22.0  # encabezado escrito como texto suelto sobre la tabla
EXTERNAL_HEADER_LINE_GAP = 9.0  # separación máxima entre líneas de ese encabezado
# D-044: tablas sin rayas verticales con filas de fondo alternado ("cebra"). ``find_tables``
# solo arma celdas en las filas sombreadas: las blancas llegan como una celda de todo el
# ancho o como huecos entre tablas de una fila. Se reconstruyen con las palabras.
STACK_MAX_GAP = 20.0  # hueco máximo (≈ una fila blanca) entre fragmentos apilados
STACK_MIN_OVERLAP = 0.9  # solapamiento horizontal mínimo entre fragmentos apilados
LINE_Y_TOLERANCE = 3.0  # palabras de una misma línea de texto (centro vertical)
# D-014: franjas superior e inferior de la página (encabezados/pies de página repetidos).
PAGE_MARGIN_FRACTION = 0.05
# D-015: epígrafe que abre una tabla nueva ("Cuadro 2.3", "Gráfico 4", "1.1.4 Población...").
CAPTION_RE = re.compile(
    r"^(?:(?:cuadro|tabla|table|gr[aá]fico|figura|figure|mapa)\s*[\divx]|\d+(?:\.\d+){1,3}\s)",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\d{4}")


class IngestionWarning(BaseModel):
    """Aviso de la ingesta (spec §7.3): página (1-based) y/o tabla, y motivo en español."""

    model_config = ConfigDict(frozen=True)

    page: int | None = None
    table_ref: str | None = None
    reason: str


class PdfOpenError(Exception):
    """El archivo no es un PDF válido, está dañado o está protegido con contraseña (FR-027)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"No se pudo abrir el PDF: {reason}")


@dataclass(frozen=True)
class TextBlock:
    """Bloque de texto de ``get_text("dict")``: bbox, texto (líneas unidas con ``\\n``),
    tamaño de fuente máximo de sus spans y si todo su texto está en negrita."""

    bbox: BBox
    text: str
    font_size: float
    bold: bool


@dataclass(frozen=True)
class PageText:
    """Una página: número 1-based, tamaño, bloques de texto y la mayor fracción del área
    de la página que cubre una sola imagen."""

    number: int
    width: float
    height: float
    blocks: list[TextBlock]
    has_text: bool
    large_image_fraction: float

    def trace_summary(self) -> dict[str, object]:
        """Resumen para trazas (sin el texto de los bloques)."""
        return {
            "number": self.number,
            "n_blocks": len(self.blocks),
            "has_text": self.has_text,
            "large_image_fraction": round(self.large_image_fraction, 3),
        }


@dataclass(frozen=True)
class LoadedPdf:
    """Resultado de ``load_pdf`` (FR-010)."""

    file_name: str
    doc_hash: str
    pdf_bytes: bytes = field(repr=False)
    pages: list[PageText] = field(repr=False)
    median_font_size: float

    @property
    def n_pages(self) -> int:
        return len(self.pages)


@dataclass(eq=False)
class RawTable:
    """Tabla lógica extraída y normalizada (FR-011, FR-014).

    - ``pages``: páginas 1-based que abarca (más de una si hubo continuación, BR-001).
    - ``bbox_first`` / ``bbox_last``: bbox (incluida la franja de encabezado) en la
      primera y la última página.
    - ``index_on_page``: índice 0-based de la tabla en su primera página (BR-010).
    - ``header``: nombres de columna (== ``list(df.columns)``); ``has_header`` es
      ``False`` si el PDF no traía encabezado y los nombres son ``col_<i>``.
    """

    df: pd.DataFrame
    dtypes: dict[str, str]
    unit_hints: dict[str, str | None]
    pages: list[int]
    bbox_first: BBox
    bbox_last: BBox
    index_on_page: int
    header: list[str]
    has_header: bool

    @property
    def ref(self) -> str:
        """Referencia corta y estable para avisos: ``p<primera página>-t<índice>``."""
        return f"p{self.pages[0]:03d}-t{self.index_on_page}"

    def trace_summary(self) -> dict[str, object]:
        """Resumen para trazas (sin los datos)."""
        return {"ref": self.ref, "pages": self.pages, "shape": list(self.df.shape), "columns": self.header}


@dataclass
class _Fragment:
    """Tabla detectada en una página, antes de unir continuaciones y normalizar."""

    page: int
    index_on_page: int
    bbox: BBox
    header: list[str] | None
    body: list[Row]
    n_cols: int


@dataclass
class _ViewRow:
    bbox: BBox
    cells: list[BBox | None]
    texts: Row


@dataclass
class _TableView:
    """Copia editable de una tabla de ``find_tables`` (bbox, filas con celdas y textos), para
    poder unir fragmentos apilados y rearmar filas mal partidas (D-044)."""

    bbox: BBox
    col_count: int
    rows: list[_ViewRow]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def extract(self) -> list[Row]:
        return [list(r.texts) for r in self.rows]


@dataclass(frozen=True)
class _Phrase:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


# --- load_pdf ---------------------------------------------------------------------------------


def _open(pdf_bytes: bytes) -> pymupdf.Document:
    if not pdf_bytes:
        raise PdfOpenError("el archivo está vacío")
    pymupdf.TOOLS.mupdf_display_errors(False)
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # pymupdf lanza FileDataError, EmptyFileError, RuntimeError...
        raise PdfOpenError("el archivo no es un PDF válido o está dañado") from exc
    if doc.needs_pass:
        doc.close()
        raise PdfOpenError("el PDF está protegido con contraseña")
    if doc.page_count == 0:
        doc.close()
        raise PdfOpenError("el PDF no tiene páginas")
    return doc


def _read_page(page: pymupdf.Page, sizes: list[tuple[float, int]]) -> PageText:
    blocks: list[TextBlock] = []
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines: list[str] = []
        block_sizes: list[float] = []
        chars = bold_chars = 0
        for line in block.get("lines", []):
            lines.append("".join(span["text"] for span in line["spans"]))
            for span in line["spans"]:
                n = len(span["text"].strip())
                if n == 0:
                    continue
                chars += n
                block_sizes.append(span["size"])
                sizes.append((span["size"], n))
                if span["flags"] & pymupdf.TEXT_FONT_BOLD or "bold" in span["font"].lower():
                    bold_chars += n
        text = "\n".join(lines).strip()
        if not text or chars == 0:
            continue
        x0, y0, x1, y1 = block["bbox"]
        blocks.append(
            TextBlock(bbox=(x0, y0, x1, y1), text=text, font_size=max(block_sizes), bold=bold_chars == chars)
        )

    page_area = page.rect.width * page.rect.height
    image_fraction = 0.0
    for info in page.get_image_info():
        visible = pymupdf.Rect(info["bbox"]) & page.rect
        if not visible.is_empty and page_area > 0:
            image_fraction = max(image_fraction, visible.width * visible.height / page_area)
    return PageText(
        number=page.number + 1,
        width=page.rect.width,
        height=page.rect.height,
        blocks=blocks,
        has_text=bool(blocks),
        large_image_fraction=image_fraction,
    )


def _weighted_median(sizes: list[tuple[float, int]]) -> float:
    if not sizes:
        return 0.0
    ordered = sorted(sizes)
    half = sum(n for _, n in ordered) / 2
    acc = 0
    for size, n in ordered:
        acc += n
        if acc >= half:
            return size
    return ordered[-1][0]


def load_pdf(file_name: str, pdf_bytes: bytes) -> LoadedPdf:
    """FR-010 / FR-027: abre el PDF y devuelve hash, nombre y bloques de texto por página.

    Lanza ``PdfOpenError`` ("No se pudo abrir el PDF: <motivo>") si los bytes están
    vacíos, no son un PDF, están dañados o el PDF pide contraseña.
    """
    doc = _open(pdf_bytes)
    sizes: list[tuple[float, int]] = []
    try:
        pages = [_read_page(page, sizes) for page in doc]
    except Exception as exc:
        raise PdfOpenError("el PDF está dañado (no se pudo leer una página)") from exc
    finally:
        doc.close()
    return LoadedPdf(
        file_name=file_name,
        doc_hash=hashlib.sha256(pdf_bytes).hexdigest(),
        pdf_bytes=pdf_bytes,
        pages=pages,
        median_font_size=_weighted_median(sizes),
    )


# --- clasificación de celdas y filas ---------------------------------------------------------


def _is_number(cell: object) -> bool:
    """La celda es un número en formato es-AR o en-US (no importa cuál)."""
    if is_missing_marker(cell):
        return False
    text = clean_text(cell)
    return parse_number(text, ",") is not None or parse_number(text, ".") is not None


def _is_data_number(cell: object) -> bool:
    """Número que no es un año suelto (los años aparecen en encabezados)."""
    text = clean_text(cell)
    if _YEAR_RE.fullmatch(text) and YEAR_MIN <= int(text) <= YEAR_MAX:
        return False
    return _is_number(text)


def _is_numeric_row(row: Sequence[object]) -> bool:
    """BR-001 (d): ≥ 50 % de las celdas con contenido son números (D-013)."""
    filled = [c for c in row if not is_missing_marker(c)]
    return bool(filled) and sum(_is_number(c) for c in filled) / len(filled) >= CONTINUATION_NUMERIC_FRACTION


def _is_empty_row(row: Sequence[object]) -> bool:
    return all(clean_text(c) == "" for c in row)


def _count_header_rows(rows: Sequence[Sequence[object]]) -> int:
    """Filas iniciales de encabezado (D-012): 0 si la primera fila es de datos (BR-001 d, o
    un rótulo seguido de algún número que no es un año, p. ej. "Incidencia | % | 24,4",
    D-044); si no, la primera fila más las siguientes que tienen vacía la primera celda
    (rótulo de fila) y ningún número que no sea un año; 1 si la tabla no tiene números."""
    if not rows or _is_numeric_row(rows[0]) or any(_is_data_number(c) for c in rows[0][1:]):
        return 0
    n = 1
    for row in rows[1:MAX_HEADER_ROWS]:
        if any(_is_data_number(c) for c in row) or clean_text(row[0] if row else None) != "":
            break
        n += 1
    return n


# --- geometría de la página ------------------------------------------------------------------


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _horizontal_rules(page: pymupdf.Page) -> list[tuple[float, float, float]]:
    """Rayas horizontales dibujadas (``(x0, x1, y)``), con tramos contiguos fusionados."""
    segments: list[tuple[float, float, float]] = []
    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 0.5 and abs(p1.x - p2.x) > 3:
                    segments.append((min(p1.x, p2.x), max(p1.x, p2.x), (p1.y + p2.y) / 2))
            elif item[0] == "re":
                rect = item[1]
                if rect.height < 1.5 and rect.width > 3:
                    segments.append((rect.x0, rect.x1, (rect.y0 + rect.y1) / 2))
    segments.sort(key=lambda s: (round(s[2], 1), s[0]))
    merged: list[tuple[float, float, float]] = []
    for x0, x1, y in segments:
        if merged and abs(merged[-1][2] - y) < 0.6 and x0 <= merged[-1][1] + 2:
            mx0, mx1, my = merged[-1]
            merged[-1] = (mx0, max(mx1, x1), my)
        else:
            merged.append((x0, x1, y))
    return merged


def _column_bounds(table: Any, first_body_row: int) -> list[tuple[float, float]]:
    """Límites horizontales de cada columna, tomados de las celdas del cuerpo (de las filas
    con todas sus celdas, si hay: las filas con una celda de todo el ancho no cuentan, D-044)."""
    rows = table.rows[first_body_row:] or table.rows
    rows = [r for r in rows if all(c is not None for c in r.cells)] or rows
    bounds: list[tuple[float, float] | None] = []
    for j in range(table.col_count):
        cells = [r.cells[j] for r in rows if j < len(r.cells) and r.cells[j] is not None]
        if cells:
            bounds.append((statistics.median(c[0] for c in cells), statistics.median(c[2] for c in cells)))
        else:
            bounds.append(None)
    x0, _, x1, _ = table.bbox
    result: list[tuple[float, float]] = []
    for j, b in enumerate(bounds):
        if b is not None:
            result.append(b)
            continue
        left = result[-1][1] if result else x0
        right = next((nb[0] for nb in bounds[j + 1 :] if nb is not None), x1)
        result.append((left, right))
    return result


def _phrases(words: Sequence[tuple], band: BBox) -> list[_Phrase]:
    """Palabras de la franja agrupadas por línea y cortadas en frases por huecos grandes."""
    bx0, by0, bx1, by1 = band
    by_line: dict[tuple[int, int], list[tuple]] = {}
    for w in words:
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if by0 <= cy <= by1 and bx0 - 2 <= cx <= bx1 + 2:
            by_line.setdefault((w[5], w[6]), []).append(w)
    phrases: list[_Phrase] = []
    for line_words in by_line.values():
        line_words.sort(key=lambda w: w[0])
        current = [line_words[0]]
        for w in line_words[1:]:
            if w[0] - current[-1][2] > PHRASE_GAP:
                phrases.append(_make_phrase(current))
                current = [w]
            else:
                current.append(w)
        phrases.append(_make_phrase(current))
    return phrases


def _make_phrase(words: list[tuple]) -> _Phrase:
    return _Phrase(
        x0=min(w[0] for w in words),
        y0=min(w[1] for w in words),
        x1=max(w[2] for w in words),
        y1=max(w[3] for w in words),
        text=" ".join(w[4] for w in words),
    )


def _phrase_columns(
    phrase: _Phrase,
    columns: list[tuple[float, float]],
    rules: list[tuple[float, float, float]],
    phrases: list[_Phrase],
) -> list[int]:
    """Columnas que abarca una frase del encabezado.

    Si hay una raya horizontal hasta ``RULE_MAX_GAP`` pt debajo, que cubre el centro de
    la frase y tiene texto de encabezado justo debajo (subencabezados), la frase abarca
    las columnas cuyo centro cae bajo la raya (celda combinada). Si no, la columna que
    contiene el centro de la frase (o la más cercana).
    """
    cx = (phrase.x0 + phrase.x1) / 2
    for rx0, rx1, ry in sorted(rules, key=lambda r: r[2]):
        if not (0 <= ry - phrase.y1 <= RULE_MAX_GAP and rx0 - 1 <= cx <= rx1 + 1):
            continue
        has_subheaders = any(
            0 <= p.y0 - ry <= RULE_MAX_GAP + 2 and _overlap(p.x0, p.x1, rx0, rx1) > 0 for p in phrases
        )
        underlined = [
            p for p in phrases if 0 <= ry - p.y1 <= RULE_MAX_GAP and _overlap(p.x0, p.x1, rx0, rx1) > 0
        ]
        centred = abs(cx - (rx0 + rx1) / 2) <= max(RULE_CENTRE_TOLERANCE * (rx1 - rx0), 10.0)
        span = [j for j, (a, b) in enumerate(columns) if rx0 - 1 <= (a + b) / 2 <= rx1 + 1]
        if has_subheaders and centred and len(underlined) == 1 and len(span) > 1:
            return span
    for j, (a, b) in enumerate(columns):
        if a <= cx <= b:
            return [j]
    return [min(range(len(columns)), key=lambda j: abs((columns[j][0] + columns[j][1]) / 2 - cx))]


def _header_from_band(
    band: BBox,
    columns: list[tuple[float, float]],
    words: Sequence[tuple],
    rules: list[tuple[float, float, float]],
) -> list[str]:
    """D-012: nombre de cada columna a partir de las palabras de la franja de encabezado.

    Por columna se apilan de arriba hacia abajo los textos que la cubren (las frases
    "subrayadas" por una raya se repiten en todas las columnas que abarcan), se unen las
    líneas consecutivas de una misma celda con espacio y los niveles con ``" - "``.
    """
    phrases = _phrases(words, band)
    width = band[2] - band[0]
    band_rules = [
        r for r in rules if band[1] - 1 <= r[2] <= band[3] + 1 and (r[1] - r[0]) < RULE_FULL_WIDTH * width
    ]
    stacks: list[list[tuple[float, float, str, bool]]] = [[] for _ in columns]
    for phrase in phrases:
        span = _phrase_columns(phrase, columns, band_rules, phrases)
        for j in span:
            stacks[j].append((phrase.y0, phrase.y1, phrase.text, len(span) > 1))

    names: list[str] = []
    for stack in stacks:
        stack.sort(key=lambda e: (e[0], e[1]))
        levels: list[tuple[float, float, str, bool]] = []
        for y0, y1, text, spanning in stack:
            prev = levels[-1] if levels else None
            if prev and not prev[3] and not spanning and y0 - prev[1] <= CELL_LINE_GAP:
                levels[-1] = (prev[0], y1, f"{prev[2]} {text}", False)
            else:
                levels.append((y0, y1, text, spanning))
        names.append(" - ".join(clean_text(level[2]) for level in levels))
    return names


def _external_header_top(words: Sequence[tuple], top: float, x0: float, x1: float) -> float | None:
    """Borde superior de un encabezado escrito como texto suelto justo sobre la tabla (D-012).

    Empieza por las líneas que terminan hasta ``EXTERNAL_HEADER_MAX_GAP`` pt sobre la tabla
    y sube mientras la línea siguiente esté a ≤ ``EXTERNAL_HEADER_LINE_GAP`` pt. ``None``
    si no hay texto cerca.
    """
    lines: dict[tuple[int, int], list[float]] = {}
    for w in words:
        if w[3] <= top + 1 and x0 - 2 <= (w[0] + w[2]) / 2 <= x1 + 2:
            box = lines.setdefault((w[5], w[6]), [w[1], w[3]])
            box[0], box[1] = min(box[0], w[1]), max(box[1], w[3])
    start = [y0 for y0, y1 in lines.values() if top - EXTERNAL_HEADER_MAX_GAP <= y1]
    if not start:
        return None
    band_top = min(start)
    while True:
        more = [
            y0 for y0, y1 in lines.values() if y0 < band_top and y1 >= band_top - EXTERNAL_HEADER_LINE_GAP
        ]
        if not more:
            return band_top
        band_top = min(more)


def _extend_up(words: Sequence[tuple], top: float, x0: float, x1: float) -> float:
    """Sube el borde superior de la franja mientras haya texto pegado (≤ 3 pt) encima."""
    while True:
        touching = [
            w
            for w in words
            if top - HEADER_EXTEND_UP <= w[3] <= top + 1 and w[1] < top and _overlap(w[0], w[2], x0, x1) > 0
        ]
        if not touching:
            return top
        top = min(w[1] for w in touching)


def _is_header_fragment(upper: Any, lower: Any) -> bool:
    """Una "tabla" sin datos dibujada justo encima de otra es su encabezado (D-012)."""
    ux0, uy0, ux1, uy1 = upper.bbox
    lx0, ly0, lx1, _ = lower.bbox
    if upper.row_count > HEADER_FRAGMENT_MAX_ROWS or not -2 <= ly0 - uy1 <= HEADER_FRAGMENT_MAX_GAP:
        return False
    if _overlap(ux0, ux1, lx0, lx1) < HEADER_FRAGMENT_MIN_OVERLAP * (lx1 - lx0):
        return False
    return not any(_is_data_number(c) for row in upper.extract() for c in row)


def _view(table: Any) -> _TableView:
    texts = table.extract()
    rows = [_ViewRow(tuple(r.bbox), list(r.cells), list(t)) for r, t in zip(table.rows, texts, strict=True)]
    return _TableView(tuple(table.bbox), table.col_count, rows)


def _has_data_numbers(view: _TableView) -> bool:
    return any(_is_data_number(c) for row in view.extract() for c in row)


def _row_from_words(words: Sequence[tuple], band: BBox, columns: list[tuple[float, float]]) -> Row | None:
    """D-044: arma una fila con las frases de la franja, cada una en la columna que contiene
    su centro (o la más cercana). ``None`` si ninguna línea tiene más de una frase (subtítulo,
    nota al pie de varias líneas, rótulo sin valores): no hay columnas que separar."""
    phrases = sorted(_phrases(words, band), key=lambda p: (round(p.y0), p.x0))
    lines: list[list[_Phrase]] = []
    for p in phrases:
        if lines and abs((p.y0 + p.y1) / 2 - (lines[-1][0].y0 + lines[-1][0].y1) / 2) <= LINE_Y_TOLERANCE:
            lines[-1].append(p)
        else:
            lines.append([p])
    if all(len(line) <= 1 for line in lines):
        return None
    texts: Row = [None] * len(columns)
    for p in phrases:
        cx = (p.x0 + p.x1) / 2
        j = next(
            (j for j, (a, b) in enumerate(columns) if a <= cx <= b),
            min(range(len(columns)), key=lambda k: abs((columns[k][0] + columns[k][1]) / 2 - cx)),
        )
        texts[j] = p.text if texts[j] is None else f"{texts[j]} {p.text}"
    return texts


def _text_lines(words: Sequence[tuple], x0: float, y0: float, x1: float, y1: float) -> list[BBox]:
    """Franjas (bbox) de las líneas de texto con centro dentro del rectángulo."""
    centres = sorted(
        (w[1] + w[3]) / 2
        for w in words
        if y0 < (w[1] + w[3]) / 2 < y1 and x0 - 2 <= (w[0] + w[2]) / 2 <= x1 + 2
    )
    lines: list[list[float]] = []
    for cy in centres:
        if lines and cy - lines[-1][-1] <= LINE_Y_TOLERANCE:
            lines[-1].append(cy)
        else:
            lines.append([cy])
    return [(x0, line[0] - LINE_Y_TOLERANCE, x1, line[-1] + LINE_Y_TOLERANCE) for line in lines]


def _stacks(upper: _TableView, lower: _TableView, words: Sequence[tuple], rules: list) -> bool:
    """D-044: ``lower`` es la continuación en la misma página de ``upper``, separadas solo por
    filas blancas (texto en el hueco, sin rayas ni epígrafes): misma cantidad de columnas,
    mismo ancho y ambas con datos."""
    ux0, _, ux1, uy1 = upper.bbox
    lx0, ly0, lx1, _ = lower.bbox
    if upper.col_count != lower.col_count or not 0 <= ly0 - uy1 <= STACK_MAX_GAP:
        return False
    if _overlap(ux0, ux1, lx0, lx1) < STACK_MIN_OVERLAP * max(ux1 - ux0, lx1 - lx0):
        return False
    if not (_has_data_numbers(upper) and _has_data_numbers(lower)):
        return False
    width = ux1 - ux0
    if any(uy1 + 1 < y < ly0 - 1 and _overlap(rx0, rx1, ux0, ux1) > 0.5 * width for rx0, rx1, y in rules):
        return False
    gap = _text_lines(words, ux0, uy1, ux1, ly0)
    if not gap:
        return False
    return not any(CAPTION_RE.match(" ".join(p.text for p in _phrases(words, band))) for band in gap)


def _extend_trailing_rows(
    view: _TableView, words: Sequence[tuple], rules: list, columns: list[tuple[float, float]], limit: float
) -> None:
    """D-044: agrega las filas blancas que siguen a la última fila sombreada (la tabla termina
    en una fila sin fondo): líneas pegadas debajo, sin raya en el medio, con frases en más de
    una columna, un rótulo de texto en la primera y algún número en las demás. Corta en la
    primera línea que no cumple (nota, "Fuente: …", marcas de eje de un gráfico)."""
    if view.row_count < 2:
        return
    x0, _, x1, _ = view.bbox
    width = x1 - x0
    while True:
        bottom = view.bbox[3]
        lines = _text_lines(words, x0, bottom, x1, min(bottom + STACK_MAX_GAP, limit))
        if not lines:
            return
        band = lines[0]
        if band[1] + LINE_Y_TOLERANCE - bottom > STACK_MAX_GAP:
            return
        if any(
            bottom - 1 < y < band[1] + 1 and _overlap(rx0, rx1, x0, x1) > 0.5 * width for rx0, rx1, y in rules
        ):
            return
        texts = _row_from_words(words, band, columns)
        if texts is None or not any(_is_data_number(c) for c in texts[1:]):
            return
        label = clean_text(texts[0])
        if not label or _is_number(label):
            return
        view.rows.append(_ViewRow(band, [None] * view.col_count, texts))
        view.bbox = (x0, view.bbox[1], x1, band[3])


def _repair_views(views: list[_TableView], words: Sequence[tuple], rules: list) -> list[_TableView]:
    """D-044: une fragmentos apilados (rearmando las filas blancas del hueco) y rearma las
    filas que ``find_tables`` devolvió como una sola celda de todo el ancho."""
    merged: list[_TableView] = []
    for view in views:
        if merged and _stacks(merged[-1], view, words, rules):
            upper = merged[-1]
            columns = _column_bounds(upper, 0)
            x0, uy1, x1 = upper.bbox[0], upper.bbox[3], upper.bbox[2]
            gap_rows = []
            for band in _text_lines(words, x0, uy1, x1, view.bbox[1]):
                texts = _row_from_words(words, band, columns)
                if texts is None:  # una sola frase: rótulo sin valores, en la primera columna
                    phrases = sorted(_phrases(words, band), key=lambda p: p.x0)
                    texts = [" ".join(p.text for p in phrases)] + [None] * (upper.col_count - 1)
                gap_rows.append(_ViewRow(band, [None] * upper.col_count, texts))
            bbox = (min(x0, view.bbox[0]), upper.bbox[1], max(x1, view.bbox[2]), view.bbox[3])
            merged[-1] = _TableView(bbox, upper.col_count, upper.rows + gap_rows + view.rows)
        else:
            merged.append(view)

    for k, view in enumerate(merged):
        if view.col_count < 2 or not any(all(c is not None for c in r.cells) for r in view.rows):
            continue
        columns = _column_bounds(view, 0)
        limit = merged[k + 1].bbox[1] if k + 1 < len(merged) else float("inf")
        _extend_trailing_rows(view, words, rules, columns, limit)
        for row in view.rows:
            first = row.cells[0]
            spans = (
                first is not None and all(c is None for c in row.cells[1:]) and first[2] > columns[0][1] + 2
            )
            if spans:
                band = (view.bbox[0], row.bbox[1], view.bbox[2], row.bbox[3])
                row.texts = _row_from_words(words, band, columns) or row.texts
    return merged


def _page_fragments(page: pymupdf.Page, page_number: int) -> list[_Fragment]:
    """Tablas de una página (FR-011), con su encabezado reconstruido y su cuerpo crudo."""
    found = sorted(page.find_tables().tables, key=lambda t: (round(t.bbox[1]), t.bbox[0]))
    if not found:
        return []
    words = page.get_text("words")
    rules = _horizontal_rules(page)
    tables = _repair_views([_view(t) for t in found], words, rules)

    header_top: dict[int, float] = {}
    absorbed: set[int] = set()
    for i, lower in enumerate(tables):
        for k, upper in enumerate(tables[:i]):
            if k not in absorbed and k not in header_top and _is_header_fragment(upper, lower):
                absorbed.add(k)
                header_top[i] = upper.bbox[1]
                break

    fragments: list[_Fragment] = []
    for i, table in enumerate(tables):
        if i in absorbed:
            continue
        rows = table.extract()
        n_header = _count_header_rows(rows)
        x0, y0, x1, y1 = table.bbox
        data_top = table.rows[n_header].bbox[1] if n_header < len(table.rows) else y1
        top = min(header_top.get(i, y0), y0)
        columns = _column_bounds(table, n_header)
        header: list[str] | None = None
        if data_top - top > 1:
            top = _extend_up(words, top, x0, x1)
            names = _header_from_band((x0, top, x1, data_top), columns, words, rules)
            if any(names):
                header = names
        else:
            # Sin encabezado propio: puede estar escrito como texto suelto encima (D-012).
            # Se acepta si nombra >= 2 columnas, o una sola que no sea la primera (p. ej.
            # "1º semestre de 2026*" sobre la columna de valores, D-044): un título de una
            # línea arranca a la izquierda y cae en la primera columna.
            ext_top = _external_header_top(words, y0, x0, x1)
            if ext_top is not None:
                names = _header_from_band((x0, ext_top, x1, y0), columns, words, rules)
                named = [j for j, n in enumerate(names) if n]
                if len(named) >= 2 or (len(named) == 1 and named[0] > 0):
                    header, top = names, ext_top
        body = [list(r) for r in rows[n_header:] if not _is_empty_row(r)]
        fragments.append(
            _Fragment(
                page=page_number,
                index_on_page=len(fragments),
                bbox=(x0, top, x1, y1),
                header=header,
                body=body,
                n_cols=table.col_count,
            )
        )
    return fragments


# --- unión multipágina (BR-001) --------------------------------------------------------------


def _normalized_header(header: Sequence[str]) -> list[str]:
    return [clean_text(h).casefold() for h in header]


def is_page_furniture(block: TextBlock, page: PageText) -> bool:
    """D-014: el bloque está en la franja superior o inferior de la página (encabezado/pie)."""
    margin = PAGE_MARGIN_FRACTION * page.height
    return block.bbox[3] <= margin or block.bbox[1] >= page.height - margin


def _has_caption_above(fragment: _Fragment, page: PageText) -> bool:
    """D-015: hay un epígrafe ("Cuadro N", "1.1.4 ...") entre el borde de la página y la tabla."""
    return any(
        b.bbox[3] <= fragment.bbox[1] + 1
        and not is_page_furniture(b, page)
        and CAPTION_RE.match(clean_text(b.text)) is not None
        for b in page.blocks
    )


def _continues(
    group: list[_Fragment], candidate: _Fragment, last_index: dict[int, int], captioned: set[int]
) -> bool:
    """BR-001: ``candidate`` (primera tabla de su página) continúa la tabla lógica ``group``.

    Además de (a)-(d), una tabla con su propio epígrafe encima es una tabla nueva (D-015).
    """
    last = group[-1]
    if candidate.page != last.page + 1 or candidate.index_on_page != 0:  # (a)
        return False
    if candidate.page in captioned:  # D-015
        return False
    if last.index_on_page != last_index[last.page]:  # (b)
        return False
    if candidate.n_cols != last.n_cols:  # (c)
        return False
    first_header = group[0].header
    if candidate.header is not None:  # (d) mismo encabezado (se descarta al unir)
        return first_header is not None and _normalized_header(candidate.header) == _normalized_header(
            first_header
        )
    return bool(candidate.body) and _is_numeric_row(candidate.body[0])  # (d) sin encabezado


def _merge(fragments: list[_Fragment], captioned: set[int]) -> list[list[_Fragment]]:
    last_index: dict[int, int] = {}
    for f in fragments:
        last_index[f.page] = max(last_index.get(f.page, -1), f.index_on_page)
    groups: list[list[_Fragment]] = []
    for f in fragments:
        if groups and _continues(groups[-1], f, last_index, captioned):
            groups[-1].append(f)
        else:
            groups.append([f])
    return groups


def _build_table(group: list[_Fragment], decimal: Decimal) -> RawTable:
    first, last = group[0], group[-1]
    n_cols = first.n_cols
    names = flatten_header([first.header if first.header is not None else [""] * n_cols], n_cols=n_cols)
    body = [row for f in group for row in f.body]
    normalized = normalize_table([names, *body], header_rows=1, default_decimal=decimal)
    return RawTable(
        df=normalized.df,
        dtypes=normalized.dtypes,
        unit_hints=normalized.unit_hints,
        pages=[f.page for f in group],
        bbox_first=first.bbox,
        bbox_last=last.bbox,
        index_on_page=first.index_on_page,
        header=list(normalized.df.columns),
        has_header=first.header is not None,
    )


# --- extract_tables --------------------------------------------------------------------------


def extract_tables(loaded: LoadedPdf, max_tables: int = 40) -> tuple[list[RawTable], list[IngestionWarning]]:
    """FR-011, FR-014..FR-017, FR-028: tablas lógicas normalizadas y avisos.

    Páginas sin texto se saltean con aviso (FR-015); páginas con una imagen ≥ 20 % del
    área se avisan (FR-016) y se procesan igual. Las continuaciones se unen en cadena
    (BR-001; una tabla con epígrafe propio no continúa a la anterior, D-015), la
    normalización usa el separador decimal inferido sobre todo el documento
    como ``default_decimal`` (D-008), y BR-005 se aplica después de unir. Se devuelven
    como máximo ``max_tables`` tablas en orden de página (FR-028).
    """
    warnings: list[IngestionWarning] = []
    fragments: list[_Fragment] = []
    doc = _open(loaded.pdf_bytes)
    try:
        for page_text in loaded.pages:
            if not page_text.has_text:
                warnings.append(IngestionWarning(page=page_text.number, reason=REASON_NO_TEXT))
                continue
            if page_text.large_image_fraction >= LARGE_IMAGE_FRACTION:
                warnings.append(IngestionWarning(page=page_text.number, reason=REASON_LARGE_IMAGE))
            fragments.extend(_page_fragments(doc[page_text.number - 1], page_text.number))
    finally:
        doc.close()

    captioned = {
        f.page for f in fragments if f.index_on_page == 0 and _has_caption_above(f, loaded.pages[f.page - 1])
    }
    decimal = infer_decimal_separator([c for f in fragments for row in f.body for c in row], default=".")
    tables: list[RawTable] = []
    for group in _merge(fragments, captioned):
        table = _build_table(group, decimal)
        reason = check_minimums(table.df)
        if reason is not None:
            warnings.append(IngestionWarning(page=table.pages[0], table_ref=table.ref, reason=reason))
            continue
        tables.append(table)

    if len(tables) > max_tables:
        omitted = len(tables) - max_tables
        warnings.append(
            IngestionWarning(
                reason=(
                    f"{omitted} tablas omitidas: el PDF tiene {len(tables)} tablas lógicas y se "
                    f"procesan las primeras {max_tables} (MAX_TABLES_PER_PDF)"
                )
            )
        )
        tables = tables[:max_tables]
    return tables, warnings
