"""Normalización de tablas extraídas (spec BR-002..BR-005, FR-012, FR-013, FR-017).

Funciones puras, sin PDFs: reciben filas crudas (listas de celdas ``str | None``
como las devuelve ``find_tables().extract()`` de PyMuPDF) y devuelven un
``DataFrame`` listo para Parquet.

Invariante de ``normalize_table``: cada columna es numérica (``float64`` o
``Int64`` para años) o texto (dtype ``str`` de pandas 3); nunca ``object`` mixto.
Las celdas vacías y los marcadores de "sin dato" quedan como ``NaN``/``<NA>``,
nunca como 0.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import pandas as pd

Decimal = Literal[",", "."]
Cell = str | None

# BR-003: proporción mínima de celdas no vacías que deben parsear como número.
NUMERIC_THRESHOLD = 0.8
# BR-003 (excepción): rango de años que se mantienen como enteros.
YEAR_MIN, YEAR_MAX = 1900, 2100
# BR-005: mínimos de tabla.
MIN_DATA_ROWS = 2
MIN_COLS = 2
MAX_EMPTY_FRACTION = 0.6

HEADER_JOINER = " - "

# BR-004: marcadores de dato faltante (se comparan tras strip() y casefold()).
# "…" (U+2026) es la forma tipográfica de "..." que suelen traer los PDFs.
MISSING_MARKERS: frozenset[str] = frozenset({"-", "–", "—", "s/d", "n/d", "...", "…", "///", "x"})

# Espacios: normal, no separable, fino y fino no separable (separadores de miles).
_SPACES_RE = re.compile(r"[\s   ]+")
_UNICODE_MINUS = "−"
# Orden importante: "US$" antes que "$".
_UNIT_TOKENS: tuple[str, ...] = ("US$", "$", "€", "%")
# Llamadas a nota al pie al final de un número (D-011): "667 888.6b", "12,3(a)", "45,4 (1)", "45,0*".
# Letras minúsculas (1-2), números de nota entre paréntesis (1-2 dígitos) o asteriscos, y solo
# detrás de un dígito o de "%" (un "(12)" solo sigue siendo un negativo).
_FOOTNOTE_RE = re.compile(r"(?<=[\d%])\s*(?:\(\s*(?:[a-z]{1,2}|\d{1,2})\s*\)|[a-z]{1,2}|\*{1,3})$")

# Señales para inferir el separador decimal de una columna (sobre el núcleo numérico
# sin signo, unidades ni espacios). Las dos primeras de cada grupo son las de BR-003.
_DECIMAL_COMMA_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d{1,3}(?:\.\d{3})+,\d+"),  # 1.234,5 (spec)
    re.compile(r"\d+,\d{1,2}"),  # 12,5 sin otro separador (spec)
    re.compile(r"\d+,\d{4,}"),  # 0,1234: una coma con != 3 dígitos no es separador de miles
    re.compile(r"\d{1,3}(?:\.\d{3}){2,}"),  # 1.234.567: puntos como miles
)
_DECIMAL_POINT_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d{1,3}(?:,\d{3})+\.\d+"),  # 1,234.5
    re.compile(r"\d+\.\d{1,2}"),  # 12.5
    re.compile(r"\d+\.\d{4,}"),  # 0.1234
    re.compile(r"\d{1,3}(?:,\d{3}){2,}"),  # 1,234,567: comas como miles
)
_YEAR_RE = re.compile(r"\d{4}")


@dataclass
class NormalizedTable:
    """Resultado de ``normalize_table``.

    - ``df``: datos normalizados; columnas ``str`` únicas y no vacías (BR-002).
    - ``dtypes``: columna → ``str(df[col].dtype)`` (``"float64"``, ``"Int64"`` o ``"str"``).
    - ``unit_hints``: columna → unidad removida al parsear (``"%"``, ``"$"``, ``"US$"``,
      ``"€"``) o ``None`` (BR-003).
    """

    df: pd.DataFrame
    dtypes: dict[str, str]
    unit_hints: dict[str, str | None]


def clean_text(cell: object) -> str:
    """Texto de la celda con espacios (incl. saltos de línea) colapsados; ``None`` → ``""``."""
    if cell is None:
        return ""
    return _SPACES_RE.sub(" ", str(cell)).strip()


def is_missing_marker(cell: object) -> bool:
    """BR-004: ``True`` si la celda es vacía o un marcador de "sin dato"."""
    text = clean_text(cell)
    return text == "" or text.casefold() in MISSING_MARKERS


def flatten_header(rows: Sequence[Sequence[Cell]], n_cols: int | None = None) -> list[str]:
    """BR-002: aplana un encabezado de una o más filas a nombres únicos y no vacíos.

    - Celdas combinadas horizontalmente (vacías a la derecha de un rótulo) se
      propagan hacia la derecha en todas las filas salvo la última (nivel hoja),
      sin cruzar el inicio de un grupo de una fila superior.
    - Por columna se unen los niveles no vacíos con ``" - "``.
    - Nombres vacíos → ``col_<i>``; duplicados → sufijos ``_2``, ``_3``…
    """
    width = n_cols if n_cols is not None else max((len(r) for r in rows), default=0)
    grid = [[clean_text(r[j]) if j < len(r) else "" for j in range(width)] for r in rows]

    filled: list[list[str]] = []
    for r, row in enumerate(grid):
        is_leaf = r == len(grid) - 1
        out = list(row)
        if not is_leaf:
            for j in range(1, width):
                group_starts = any(grid[k][j] for k in range(r))
                if not out[j] and not group_starts:
                    out[j] = out[j - 1]
        filled.append(out)

    names = [HEADER_JOINER.join(row[j] for row in filled if row[j]) for j in range(width)]
    return _dedupe([name or f"col_{j}" for j, name in enumerate(names)])


def _dedupe(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        candidate, k = name, 2
        while candidate in seen:
            candidate = f"{name}_{k}"
            k += 1
        seen.add(candidate)
        result.append(candidate)
    return result


def _decompose(cell: object) -> tuple[str, bool, str | None]:
    """Separa una celda en (núcleo numérico sin espacios externos, negativo, unidad)."""
    text = _FOOTNOTE_RE.sub("", clean_text(cell).replace(_UNICODE_MINUS, "-"))
    unit: str | None = None
    for token in _UNIT_TOKENS:
        if token in text:
            if unit is None or token == "%":
                unit = token
            text = text.replace(token, "")
    text = text.strip()
    negative = False
    if len(text) >= 2 and text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    if text[:1] in ("-", "+"):
        negative = negative != (text[0] == "-")
        text = text[1:].strip()
    return text, negative, unit


def _number_re(decimal: Decimal) -> re.Pattern[str]:
    thousands = re.escape("," if decimal == "." else ".")
    dec = re.escape(decimal)
    return re.compile(
        rf"(?:(?P<int>\d{{1,3}}(?:{thousands}\d{{3}})+|\d{{1,3}}(?: \d{{3}})+|\d+)(?:{dec}(?P<frac>\d+))?"
        rf"|{dec}(?P<frac_only>\d+))"
    )


_NUMBER_RES: dict[str, re.Pattern[str]] = {",": _number_re(","), ".": _number_re(".")}


def _parse_with_unit(cell: object, decimal: Decimal) -> tuple[float, str | None] | None:
    if is_missing_marker(cell):
        return None
    core, negative, unit = _decompose(cell)
    match = _NUMBER_RES[decimal].fullmatch(core)
    if match is None:
        return None
    int_part = re.sub(r"\D", "", match.group("int") or "") or "0"
    frac = match.group("frac") or match.group("frac_only") or "0"
    value = float(f"{int_part}.{frac}")
    return (-value if negative else value), unit


def parse_number(cell: str | None, decimal: Decimal) -> float | None:
    """BR-003: parsea una celda formateada como texto; ``None`` si no es un número.

    Admite separadores de miles (el que no es ``decimal``, o espacio / espacio fino,
    con grupos de 3 dígitos), ``$``, ``US$``, ``€``, ``%``, paréntesis como negativo
    y el signo menos unicode. Ignora una llamada a nota al pie al final (``"6.5b"``,
    ``"12,3(a)"``, ``"45,4 (1)"``, ``"45,0*"``; D-011). Los marcadores de BR-004 devuelven ``None``.
    """
    parsed = _parse_with_unit(cell, decimal)
    return None if parsed is None else parsed[0]


def infer_decimal_separator(cells: Sequence[Cell], default: Decimal = ".") -> Decimal:
    """BR-003: infiere el separador decimal de una columna.

    ``","`` si algún valor tiene señal es-AR (``1.234,5``, ``12,5``, ``1.234.567``…);
    si no, ``"."`` si alguno tiene señal en-US (``1,234.5``, ``12.5``…); si ninguno
    tiene señal (p. ej. solo ``"1.234"`` o enteros), ``default``. ``default`` permite
    pasar la señal del documento (D-008).
    """
    cores = []
    for cell in cells:
        if is_missing_marker(cell):
            continue
        core, _, _ = _decompose(cell)
        cores.append(core.replace(" ", ""))
    if any(p.fullmatch(c) for c in cores for p in _DECIMAL_COMMA_SIGNALS):
        return ","
    if any(p.fullmatch(c) for c in cores for p in _DECIMAL_POINT_SIGNALS):
        return "."
    return default


def _is_year_column(values: list[str]) -> bool:
    return bool(values) and all(
        _YEAR_RE.fullmatch(v) is not None and YEAR_MIN <= int(v) <= YEAR_MAX for v in values
    )


def _normalize_column(cells: list[str | None], default_decimal: Decimal) -> tuple[pd.Series, str | None]:
    present = [c for c in cells if c is not None]
    if not present:
        return pd.Series(cells, dtype="str"), None
    if _is_year_column(present):
        years = [None if c is None else int(c) for c in cells]
        return pd.Series(years, dtype="Int64"), None

    decimal = infer_decimal_separator(present, default=default_decimal)
    parsed = [None if c is None else _parse_with_unit(c, decimal) for c in cells]
    n_ok = sum(p is not None for p in parsed)
    if n_ok / len(present) < NUMERIC_THRESHOLD:
        return pd.Series(cells, dtype="str"), None

    values = [float("nan") if p is None else p[0] for p in parsed]
    units = Counter(p[1] for p in parsed if p is not None and p[1] is not None)
    unit = units.most_common(1)[0][0] if units else None
    return pd.Series(values, dtype="float64"), unit


def normalize_table(
    raw_rows: Sequence[Sequence[Cell]],
    header_rows: int,
    default_decimal: Decimal = ".",
) -> NormalizedTable:
    """Normaliza una tabla cruda (FR-012, FR-013; BR-002, BR-003, BR-004).

    ``raw_rows`` son todas las filas (encabezado + datos); las primeras
    ``header_rows`` forman el encabezado (``0`` → columnas ``col_<i>``). Las filas
    se completan con celdas vacías hasta el ancho máximo. Por columna: celdas vacías
    o marcadores → ``NaN``; años (todas las celdas no vacías enteros 1900–2100) →
    ``Int64``; si ≥ 80 % de las no vacías parsean como número → ``float64`` (las que
    no parsean quedan ``NaN``) con ``unit_hint``; si no → texto (``str``).
    ``default_decimal`` se usa en columnas sin señal de separador decimal.
    """
    if not 0 <= header_rows <= len(raw_rows):
        raise ValueError(f"header_rows={header_rows} fuera de rango para {len(raw_rows)} filas")
    width = max((len(r) for r in raw_rows), default=0)
    columns = flatten_header(raw_rows[:header_rows], n_cols=width)

    data = [[(r[j] if j < len(r) else None) for j in range(width)] for r in raw_rows[header_rows:]]
    series: dict[str, pd.Series] = {}
    unit_hints: dict[str, str | None] = {}
    for j, name in enumerate(columns):
        cells = [None if is_missing_marker(row[j]) else clean_text(row[j]) for row in data]
        series[name], unit_hints[name] = _normalize_column(cells, default_decimal)

    df = pd.DataFrame(series, columns=columns)
    df.index = pd.RangeIndex(len(data))
    dtypes = {name: str(df[name].dtype) for name in columns}
    return NormalizedTable(df=df, dtypes=dtypes, unit_hints=unit_hints)


def check_minimums(df: pd.DataFrame) -> str | None:
    """BR-005: motivo de descarte, o ``None`` si la tabla cumple los mínimos.

    Se descarta si tiene < 2 filas de datos, < 2 columnas o > 60 % de celdas vacías
    (``df`` ya normalizado: los marcadores de BR-004 son ``NaN``).
    """
    n_rows, n_cols = df.shape
    if n_rows < MIN_DATA_ROWS:
        return f"tabla descartada: {n_rows} fila(s) de datos (mínimo {MIN_DATA_ROWS})"
    if n_cols < MIN_COLS:
        return f"tabla descartada: {n_cols} columna(s) (mínimo {MIN_COLS})"
    empty_fraction = float(df.isna().to_numpy().mean())
    if empty_fraction > MAX_EMPTY_FRACTION:
        return f"tabla descartada: {empty_fraction:.0%} de celdas vacías (máximo {MAX_EMPTY_FRACTION:.0%})"
    return None
