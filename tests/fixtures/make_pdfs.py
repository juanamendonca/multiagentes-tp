"""PDFs de fixture para los tests de extracción, generados con PyMuPDF.

Son fixtures de test (no datos de demo): tablas dibujadas con líneas para que
``page.find_tables()`` las detecte, texto con ``insert_text`` y, donde hace falta,
imágenes. Cada función devuelve los bytes de un PDF.
"""

from __future__ import annotations

from collections.abc import Sequence

import pymupdf

FONT = "helv"
ROW_HEIGHT = 16.0
PAGE_W, PAGE_H = 595.0, 842.0

Rows = Sequence[Sequence[str]]


def draw_table(
    page: pymupdf.Page,
    x: float,
    y: float,
    rows: Rows,
    col_widths: Sequence[float],
    fontsize: float = 8,
) -> tuple[float, float, float, float]:
    """Dibuja una grilla con líneas y escribe ``rows`` en sus celdas; devuelve el bbox."""
    xs = [x]
    for w in col_widths:
        xs.append(xs[-1] + w)
    bottom = y + len(rows) * ROW_HEIGHT
    for r in range(len(rows) + 1):
        page.draw_line((x, y + r * ROW_HEIGHT), (xs[-1], y + r * ROW_HEIGHT), width=0.5)
    for cx in xs:
        page.draw_line((cx, y), (cx, bottom), width=0.5)
    for r, row in enumerate(rows):
        for j, cell in enumerate(row):
            if cell:
                page.insert_text(
                    (xs[j] + 3, y + r * ROW_HEIGHT + ROW_HEIGHT - 4), cell, fontsize=fontsize, fontname=FONT
                )
    return (x, y, xs[-1], bottom)


def write(page: pymupdf.Page, x: float, baseline: float, text: str, fontsize: float = 10) -> None:
    page.insert_text((x, baseline), text, fontsize=fontsize, fontname=FONT)


def _new() -> pymupdf.Document:
    return pymupdf.open()


def _page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=PAGE_W, height=PAGE_H)


def _bytes(doc: pymupdf.Document) -> bytes:
    data = doc.tobytes()
    doc.close()
    return data


def _gray_pixmap() -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pix.set_rect(pix.irect, (180, 180, 180))
    return pix


REGION_HEADER = ["Región", "Población", "Tasa"]


def region_rows(start: int, n: int) -> list[list[str]]:
    """Filas de datos numéricas es-AR: ``["Región <i>", "<i>.<iii>,5", "<i>,5"]``
    (p. ej. ``["Región 3", "3.003,5", "3,5"]``)."""
    return [[f"Región {i}", f"{i}.{i:03d},5", f"{i},5"] for i in range(start, start + n)]


# --- FR-010 / FR-011 ---------------------------------------------------------------------------


def two_tables_one_page() -> bytes:
    """Una página de texto y dos tablas, una debajo de la otra (VC-010, VC-011)."""
    doc = _new()
    page = _page(doc)
    write(page, 72, 60, "Informe de prueba con dos cuadros.")
    draw_table(page, 72, 100, [REGION_HEADER, *region_rows(1, 3)], [120, 80, 60])
    sectors = [["Sector", "Ocupados"], ["Industria", "12,5"], ["Comercio", "20,1"]]
    draw_table(page, 72, 320, sectors, [120, 80])
    return _bytes(doc)


# --- FR-014 / BR-001 ---------------------------------------------------------------------------


def multipage_repeated_header() -> bytes:
    """Págs. 1–3 de texto; una tabla que ocupa las págs. 4, 5 y 6 repitiendo el encabezado
    (5 + 5 + 3 filas de datos) (VC-015)."""
    doc = _new()
    for i in range(3):
        write(_page(doc), 72, 80, f"Página de texto {i + 1}.")
    for start, n in ((1, 5), (6, 5), (11, 3)):
        page = _page(doc)
        draw_table(page, 72, 80, [REGION_HEADER, *region_rows(start, n)], [120, 80, 60])
    return _bytes(doc)


def continuation_without_header() -> bytes:
    """Pág. 1: "Cuadro 1." y una tabla con encabezado al final; pág. 2: la tabla sigue sin
    encabezado y abajo dice "Fuente: INDEC." (VC-016; contexto multipágina de BR-006)."""
    doc = _new()
    page = _page(doc)
    write(page, 72, 570, "Cuadro 1. Población por región.")
    draw_table(page, 72, 600, [REGION_HEADER, *region_rows(1, 4)], [120, 80, 60])
    page = _page(doc)
    _, _, _, bottom = draw_table(page, 72, 80, region_rows(5, 3), [120, 80, 60])
    write(page, 72, bottom + 25, "Fuente: INDEC.")
    return _bytes(doc)


def continuation_different_columns() -> bytes:
    """La primera tabla de la pág. 2 tiene otra cantidad de columnas: no se une (VC-016)."""
    doc = _new()
    draw_table(_page(doc), 72, 600, [REGION_HEADER, *region_rows(1, 4)], [120, 80, 60])
    rows = [[*r, "1,0"] for r in region_rows(5, 3)]
    draw_table(_page(doc), 72, 80, rows, [120, 80, 60, 50])
    return _bytes(doc)


def continuation_not_first_on_next_page() -> bytes:
    """En la pág. 2 la candidata a continuación no es la primera tabla: no se une (VC-016)."""
    doc = _new()
    draw_table(_page(doc), 72, 600, [REGION_HEADER, *region_rows(1, 4)], [120, 80, 60])
    page = _page(doc)
    draw_table(page, 72, 80, [["Concepto", "Valor"], ["Altas", "10"], ["Bajas", "4"]], [120, 80])
    draw_table(page, 72, 300, region_rows(5, 3), [120, 80, 60])
    return _bytes(doc)


def continuation_with_own_caption() -> bytes:
    """Mismo encabezado en págs. 1 y 2, pero la de la pág. 2 tiene su propio epígrafe
    ("Cuadro 2"): es otra tabla (D-015)."""
    doc = _new()
    page = _page(doc)
    write(page, 72, 560, "Cuadro 1. Población por región.")
    draw_table(page, 72, 600, [REGION_HEADER, *region_rows(1, 4)], [120, 80, 60])
    page = _page(doc)
    write(page, 72, 70, "Cuadro 2. Población por región, segundo trimestre.")
    draw_table(page, 72, 100, [REGION_HEADER, *region_rows(5, 3)], [120, 80, 60])
    return _bytes(doc)


def header_as_loose_text() -> bytes:
    """Encabezado de dos niveles escrito como texto suelto sobre la tabla (como en INDEC):
    "Total" subrayado por una raya que abarca las columnas 1–2 y "2024"/"2025" debajo
    (D-012). Columnas esperadas: ["Indicador", "Total - 2024", "Total - 2025"]."""
    doc = _new()
    page = _page(doc)
    write(page, 72, 40 + 30, "Cuadro 7. Tasas principales.", fontsize=10)
    # Columnas de la grilla: [72, 232) [232, 312) [312, 392)
    write(page, 303, 110, "Total", fontsize=8)  # centrado sobre 232–392 (centro 312)
    page.draw_line((232, 113), (392, 113), width=0.5)
    write(page, 80, 122, "Indicador", fontsize=8)
    write(page, 255, 122, "2024", fontsize=8)
    write(page, 335, 122, "2025", fontsize=8)
    rows = [["Actividad", "48,1", "48,9"], ["Empleo", "44,5", "45,0"], ["Desocupación", "7,6", "7,9"]]
    draw_table(page, 72, 135, rows, [160, 80, 80])
    return _bytes(doc)


# --- FR-015 / FR-016 ---------------------------------------------------------------------------


def image_only_page() -> bytes:
    """Pág. 1 con una tabla; pág. 2 es solo una imagen, sin capa de texto (VC-017)."""
    doc = _new()
    draw_table(_page(doc), 72, 100, [REGION_HEADER, *region_rows(1, 3)], [120, 80, 60])
    page = _page(doc)
    page.insert_image(page.rect, pixmap=_gray_pixmap())
    return _bytes(doc)


def page_with_image(fraction: float) -> bytes:
    """Una página con texto, una tabla y una imagen que cubre ``fraction`` del área (VC-018)."""
    doc = _new()
    page = _page(doc)
    write(page, 72, 60, "Página con una imagen.")
    draw_table(page, 72, 100, [REGION_HEADER, *region_rows(1, 3)], [120, 80, 60])
    side = (fraction * PAGE_W * PAGE_H) ** 0.5
    page.insert_image(pymupdf.Rect(40, 300, 40 + side, 300 + side), pixmap=_gray_pixmap())
    return _bytes(doc)


# --- FR-017 / BR-005 ---------------------------------------------------------------------------


def tables_below_minimums() -> bytes:
    """Pág. 1: 1 fila de datos; pág. 2: 69 % de celdas vacías; pág. 3: tabla 2×2 completa
    (VC-019). Cada tabla tiene otra forma o encabezado para que no se unan."""
    doc = _new()
    draw_table(_page(doc), 72, 100, [["Región", "Valor"], ["Salta", "12"]], [120, 80])
    empty = [["A", "B", "C", "D"], *([f"r{i}", "", "", ""] for i in (1, 2, 3)), ["r4", "5", "", ""]]
    draw_table(_page(doc), 72, 100, empty, [80, 60, 60, 60])
    complete = [["Provincia", "Tasa"], ["Jujuy", "12,5"], ["Tucumán", "13,1"]]
    draw_table(_page(doc), 72, 100, complete, [120, 80])
    return _bytes(doc)


# --- FR-018 / BR-006 ---------------------------------------------------------------------------


def table_with_context(long_paragraph: bool = False) -> bytes:
    """Título de sección grande arriba de todo, un párrafo ~300 pt sobre la tabla,
    "Cuadro 3. …" ~40 pt arriba y "Fuente: INDEC" ~20 pt abajo (VC-020).

    Con ``long_paragraph=True`` cambia el párrafo lejano por bloques largos cerca de la
    tabla (arriba y abajo) para probar el truncado a 2 000 caracteres."""
    doc = _new()
    page = _page(doc)
    write(page, 72, 50, "2. Mercado de trabajo regional", fontsize=16)
    if not long_paragraph:
        write(page, 72, 95, "Párrafo lejano sobre la metodología de la encuesta.")
    else:
        long_text = "Texto muy largo de contexto. " * 120
        page.insert_textbox(pymupdf.Rect(72, 290, 520, 350), long_text, fontsize=3, fontname=FONT)
        page.insert_textbox(pymupdf.Rect(72, 460, 520, 520), long_text, fontsize=3, fontname=FONT)
    write(page, 72, 355, "Cuadro 3. Ocupación por región. Segundo trimestre de 2026")
    _, _, _, bottom = draw_table(page, 72, 400, [REGION_HEADER, *region_rows(1, 3)], [120, 80, 60])
    write(page, 72, bottom + 28, "Fuente: INDEC, Encuesta Permanente de Hogares.")
    return _bytes(doc)


# --- FR-026 / FR-027 / FR-028 ------------------------------------------------------------------


def no_tables() -> bytes:
    """Un PDF con texto y sin tablas."""
    doc = _new()
    write(_page(doc), 72, 80, "Este documento no tiene tablas.")
    return _bytes(doc)


def encrypted() -> bytes:
    """PDF válido protegido con contraseña de usuario (VC-031)."""
    doc = _new()
    write(_page(doc), 72, 80, "Contenido protegido.")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="dueno", user_pw="usuario")
    doc.close()
    return data


def five_tables() -> bytes:
    """Cinco tablas separadas en una página (VC-032)."""
    doc = _new()
    page = _page(doc)
    for k in range(5):
        rows = [[f"Concepto {k}", f"Valor {k}"], [f"a{k}", "1,5"], [f"b{k}", "2,5"]]
        draw_table(page, 72, 60 + k * 140, rows, [120, 80])
    return _bytes(doc)


# --- D-044: tablas "cebra" sin rayas verticales ------------------------------------------------

ZEBRA_ROWS = [
    ("Incidencia de la pobreza en hogares", "%", "24,4"),
    ("Tamaño promedio del hogar pobre en personas", "", "3,89"),
    ("Tamaño promedio del hogar pobre en adulto equivalente", "", "3,15"),
    ("Canasta básica total promedio del hogar pobre (a)", "$", "1.440.395"),
    ("Ingreso total familiar promedio del hogar pobre (b)", "$", "922.755"),
    ("Brecha monetaria promedio de los hogares pobres (b-a)", "$", "-517.640"),
    ("Brecha porcentual promedio de los hogares pobres (b-a)/a", "%", "35,9"),
]


def zebra_table(ends_on_white_row: bool = False) -> bytes:
    """Cuadro estilo INDEC (Cuadro 2.2 del informe de pobreza): epígrafe, encabezado de una
    sola columna escrito entre dos rayas ("1º semestre de 2026*" sobre los valores), sin rayas
    verticales, filas de fondo gris alternado dibujado celda por celda y columna de unidad a
    veces vacía. ``ends_on_white_row`` agrega una 8.ª fila sin fondo al final."""
    rows = list(ZEBRA_ROWS)
    if ends_on_white_row:
        rows.append(("Hogares pobres (en miles)", "", "2.498"))
    doc = _new()
    page = _page(doc)
    xs = [129.0, 405.0, 456.0, 534.0]
    row_h, top = 14.3, 302.0
    write(page, 60, 266, "Cuadro 2.2   Brecha de la pobreza. Total 31 aglomerados urbanos", fontsize=11)
    for y in (280.0, 294.0):
        page.draw_line((xs[0], y), (xs[-1], y), width=1)
    write(page, 466, 291, "1º semestre de 2026*", fontsize=7)
    shape = page.new_shape()
    for i in range(0, len(rows), 2):
        for a, b in zip(xs, xs[1:], strict=False):
            shape.draw_rect(pymupdf.Rect(a, top + i * row_h, b, top + (i + 1) * row_h))
    shape.finish(fill=(0.96, 0.96, 0.96), color=None, width=0)
    shape.commit()
    for i, (label, unit, value) in enumerate(rows):
        baseline = top + i * row_h + row_h - 4
        write(page, xs[0] + 4, baseline, label, fontsize=8)
        if unit:
            write(page, 428, baseline, unit, fontsize=8)
        write(
            page, 516 - pymupdf.get_text_length(value, fontname=FONT, fontsize=8), baseline, value, fontsize=8
        )
    bottom = top + len(rows) * row_h
    page.draw_line((xs[0], bottom), (xs[-1], bottom), width=1)
    write(page, 129, bottom + 16, "Fuente: INDEC, Dirección de Encuesta Permanente de Hogares.", fontsize=7)
    return _bytes(doc)
