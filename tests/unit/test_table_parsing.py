"""VC-012, VC-013, VC-014, VC-019 (mínimos): normalización de tablas (BR-002..BR-005).

Cada ejemplo de las VCs tiene su propio test, con el id de la VC en el nombre.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from services.table_parsing import (
    check_minimums,
    flatten_header,
    infer_decimal_separator,
    is_missing_marker,
    normalize_table,
    parse_number,
)


def column(rows: list[list[str | None]], header: str = "valor") -> list[list[str | None]]:
    """Tabla de 2 columnas (rótulo + valor) con un encabezado de una fila."""
    return [["Región", header]] + [[f"r{i}", cell] for i, row in enumerate(rows) for cell in row]


def values_of(cells: list[str | None], default_decimal: str = ".") -> pd.Series:
    table = normalize_table(column([[c] for c in cells]), header_rows=1, default_decimal=default_decimal)
    return table.df["valor"]


# --- VC-012 (FR-012, BR-002): aplanado de encabezados -------------------------------------------


def test_vc012_two_row_header_with_merged_cell() -> None:
    """Given encabezado ["", "2023", ""] / ["Región", "Ene", "Feb"] con "2023" combinada,
    when se aplana, then las columnas son ["Región", "2023 - Ene", "2023 - Feb"]."""
    rows = [["", "2023", ""], ["Región", "Ene", "Feb"]]

    assert flatten_header(rows) == ["Región", "2023 - Ene", "2023 - Feb"]


def test_vc012_two_empty_header_names_become_col_i() -> None:
    """Given un encabezado con dos nombres vacíos, then se llaman col_0 y col_1."""
    assert flatten_header([["", None, "Total"]]) == ["col_0", "col_1", "Total"]


def test_vc012_duplicate_names_get_suffix() -> None:
    """Given dos columnas "X", then quedan "X" y "X_2" (y "X_3" si hay una tercera)."""
    assert flatten_header([["X", "X"]]) == ["X", "X_2"]
    assert flatten_header([["X", "X", "X"]]) == ["X", "X_2", "X_3"]


def test_vc012_normalized_table_has_single_row_of_unique_names() -> None:
    """FR-012: el DataFrame resultante tiene una sola fila de nombres únicos y no vacíos."""
    raw = [["", "2023", ""], ["Región", "Ene", "Feb"], ["GBA", "7,1", "7,4"], ["NEA", "5,0", "4,8"]]

    df = normalize_table(raw, header_rows=2).df

    assert list(df.columns) == ["Región", "2023 - Ene", "2023 - Feb"]
    assert len(df) == 2
    assert df["2023 - Feb"].tolist() == [7.4, 4.8]


def test_header_merge_does_not_cross_upper_group_boundary() -> None:
    """Un rótulo de nivel 2 no se propaga a una columna donde empieza otro grupo de nivel 1."""
    rows = [["", "2022", "", "2023", ""], ["", "I", "", "I", "II"], ["Región", "a", "b", "a", "b"]]

    assert flatten_header(rows) == [
        "Región",
        "2022 - I - a",
        "2022 - I - b",
        "2023 - I - a",
        "2023 - II - b",
    ]


def test_header_cells_with_newlines_are_collapsed() -> None:
    """Los saltos de línea dentro de una celda del encabezado se colapsan a un espacio."""
    assert flatten_header([["Tasa de\nactividad", "  Tasa  de empleo "]]) == [
        "Tasa de actividad",
        "Tasa de empleo",
    ]


# --- VC-013 (FR-013, BR-003): normalización numérica --------------------------------------------


def test_vc013_es_ar_thousands_and_decimal_comma() -> None:
    """ "1.234,5" → 1234.5."""
    assert infer_decimal_separator(["1.234,5"]) == ","
    assert parse_number("1.234,5", ",") == 1234.5
    assert values_of(["1.234,5", "2,0"]).tolist() == [1234.5, 2.0]


def test_vc013_en_us_thousands_and_decimal_point() -> None:
    """ "1,234.5" → 1234.5."""
    assert infer_decimal_separator(["1,234.5"]) == "."
    assert parse_number("1,234.5", ".") == 1234.5
    assert values_of(["1,234.5", "2.0"]).tolist() == [1234.5, 2.0]


def test_vc013_parentheses_are_negative() -> None:
    """ "(1.234)" → -1234 (en columna es-AR)."""
    assert parse_number("(1.234)", ",") == -1234.0
    assert values_of(["(1.234)", "5,5"]).tolist() == [-1234.0, 5.5]


def test_vc013_percent_sets_unit_hint() -> None:
    """ "12,5%" → 12.5 con unit_hint="%"."""
    table = normalize_table(column([["12,5%"], ["7,3%"]]), header_rows=1)

    assert table.df["valor"].tolist() == [12.5, 7.3]
    assert table.unit_hints["valor"] == "%"
    assert table.unit_hints["Región"] is None


def test_vc013_us_dollar_amount() -> None:
    """ "US$ 3.000" → 3000 (con unit_hint="US$")."""
    assert parse_number("US$ 3.000", ",") == 3000.0
    table = normalize_table(column([["US$ 3.000"], ["US$ 1.250,50"]]), header_rows=1)
    assert table.df["valor"].tolist() == [3000.0, 1250.5]
    assert table.unit_hints["valor"] == "US$"


def test_vc013_unicode_minus() -> None:
    """ "−7" (signo menos unicode) → -7."""
    assert parse_number("−7", ".") == -7.0
    assert values_of(["−7", "3"]).tolist() == [-7.0, 3.0]


def test_vc013_column_with_70_percent_numeric_stays_text() -> None:
    """Columna con 70 % de celdas numéricas (< 80 %) → queda texto, sin perder valores."""
    cells = ["1", "2", "3", "4", "5", "6", "7", "a", "b", "c"]

    table = normalize_table(column([[c] for c in cells]), header_rows=1)

    assert table.dtypes["valor"] == "str"
    assert table.df["valor"].tolist() == cells


def test_vc013_year_column_is_integer() -> None:
    """Columna de años "2021", "2022" → entero (Int64), sin separadores ni decimales."""
    table = normalize_table([["Año", "Valor"], ["2021", "10,5"], ["2022", "11,0"]], header_rows=1)

    assert pd.api.types.is_integer_dtype(table.df["Año"])
    assert table.dtypes["Año"] == "Int64"
    assert table.df["Año"].tolist() == [2021, 2022]


def test_year_header_names_stay_plain_text() -> None:
    """Años en el encabezado quedan como "2021", no "2021.0" ni "2.021"."""
    table = normalize_table([["Región", "2021", "2022"], ["GBA", "1", "2"], ["NEA", "3", "4"]], 1)

    assert list(table.df.columns) == ["Región", "2021", "2022"]


def test_column_meeting_80_percent_becomes_numeric() -> None:
    """Con 80 % de celdas numéricas la columna pasa a numérica; la que no parsea → NaN."""
    series = values_of(["1", "2", "3", "4", "nota"])

    assert series.dtype == "float64"
    assert series.iloc[:4].tolist() == [1.0, 2.0, 3.0, 4.0]
    assert math.isnan(series.iloc[4])


# --- VC-014 (FR-013, BR-004): celdas vacías -----------------------------------------------------


def assert_missing_becomes_nan(marker: str) -> None:
    assert is_missing_marker(marker)
    assert parse_number(marker, ",") is None
    series = values_of(["1,5", marker, "2,5"])
    assert series.dtype == "float64"
    assert series.isna().tolist() == [False, True, False]
    assert 0.0 not in series.tolist()


def test_vc014_empty_string_is_nan() -> None:
    """ "" → NaN, nunca 0."""
    assert_missing_becomes_nan("")


def test_vc014_dash_is_nan() -> None:
    """ "-" → NaN, nunca 0."""
    assert_missing_becomes_nan("-")


def test_vc014_s_d_is_nan() -> None:
    """ "s/d" → NaN, nunca 0."""
    assert_missing_becomes_nan("s/d")


def test_vc014_ellipsis_is_nan() -> None:
    """ "..." → NaN, nunca 0."""
    assert_missing_becomes_nan("...")


@pytest.mark.parametrize("marker", ["–", "—", "n/d", "N/D", "S/D", "///", "x", "X", "…", "  -  "])
def test_other_missing_markers(marker: str) -> None:
    """El resto de los marcadores de BR-004 (sin distinguir mayúsculas) también son faltantes."""
    assert_missing_becomes_nan(marker)


def test_missing_markers_in_text_column_are_nan() -> None:
    """En columnas de texto los marcadores también quedan NaN (no la cadena "-")."""
    table = normalize_table([["Región", "Obs"], ["-", "a"], ["NEA", "s/d"]], header_rows=1)

    assert table.df["Región"].isna().tolist() == [True, False]
    assert table.df["Obs"].isna().tolist() == [False, True]


# --- VC-019 (FR-017, BR-005): mínimos de tabla --------------------------------------------------


def test_vc019_one_data_row_is_discarded() -> None:
    """Tabla con 1 fila de datos → descartada con motivo."""
    df = normalize_table([["A", "B"], ["1", "2"]], header_rows=1).df

    reason = check_minimums(df)

    assert reason is not None and "fila" in reason


def test_vc019_one_column_is_discarded() -> None:
    """Tabla con 1 columna → descartada con motivo."""
    df = normalize_table([["A"], ["1"], ["2"], ["3"]], header_rows=1).df

    reason = check_minimums(df)

    assert reason is not None and "columna" in reason


def test_vc019_seventy_percent_empty_is_discarded() -> None:
    """Tabla con 70 % de celdas vacías tras BR-004 (incluye marcadores) → descartada."""
    raw = [["A", "B"], ["1", "-"], ["", "s/d"], ["...", ""], ["x", ""], ["2", "3"]]
    df = normalize_table(raw, header_rows=1).df

    reason = check_minimums(df)

    assert reason is not None and "70%" in reason and "vacías" in reason


def test_vc019_complete_2x2_table_is_kept() -> None:
    """Tabla de 2×2 completa → se conserva (sin motivo de descarte)."""
    df = normalize_table([["A", "B"], ["x1", "1"], ["x2", "2"]], header_rows=1).df

    assert check_minimums(df) is None


def test_exactly_60_percent_empty_is_kept() -> None:
    """El umbral es estricto: 60 % de vacías se conserva."""
    df = pd.DataFrame({"a": [1.0, None, None, None, 5.0], "b": [None, 2.0, None, 4.0, None]})

    assert check_minimums(df) is None


# --- Casos borde del plan -----------------------------------------------------------------------


def test_none_cell_is_missing() -> None:
    """Una celda None (como la devuelve PyMuPDF en celdas combinadas) → NaN."""
    assert is_missing_marker(None)
    assert parse_number(None, ".") is None
    assert values_of(["1", None, "3"]).isna().tolist() == [False, True, False]


def test_all_empty_column_is_text_of_nan() -> None:
    """Columna toda vacía → texto con todos NaN (sin señal numérica), sin romper la tabla."""
    table = normalize_table([["A", "B"], ["x", ""], ["y", None], ["z", "-"]], header_rows=1)

    assert table.dtypes["B"] == "str"
    assert table.df["B"].isna().all()


def test_zero_comma_five() -> None:
    """ "0,5" → 0.5 (señal es-AR)."""
    assert infer_decimal_separator(["0,5"]) == ","
    assert values_of(["0,5", "1"]).tolist() == [0.5, 1.0]


def test_lone_1234_in_es_ar_column_is_thousands() -> None:
    """ "1.234" en una columna con señal es-AR ("5,5") → 1234."""
    assert values_of(["1.234", "5,5"]).tolist() == [1234.0, 5.5]


def test_lone_1234_in_en_us_column_is_decimal() -> None:
    """ "1.234" en una columna con señal en-US ("5.5") → 1.234."""
    assert values_of(["1.234", "5.5"]).tolist() == [1.234, 5.5]


def test_lone_1234_without_signal_uses_default_decimal() -> None:
    """Sin señal en la columna se usa default_decimal (D-008): "." → 1.234; "," → 1234."""
    assert infer_decimal_separator(["1.234", "2.345"]) == "."
    assert values_of(["1.234", "2.345"]).tolist() == [1.234, 2.345]
    assert infer_decimal_separator(["1.234", "2.345"], default=",") == ","
    assert values_of(["1.234", "2.345"], default_decimal=",").tolist() == [1234.0, 2345.0]


def test_column_signal_overrides_default_decimal() -> None:
    """Una señal en la columna tiene prioridad sobre default_decimal."""
    assert values_of(["1,234.5", "2.0"], default_decimal=",").tolist() == [1234.5, 2.0]


def test_negative_with_ascii_and_unicode_minus() -> None:
    """Negativos con "-" y con "−" (U+2212), con y sin separadores de miles."""
    assert parse_number("-7", ".") == -7.0
    assert parse_number("−7", ".") == -7.0
    assert parse_number("-1.234,5", ",") == -1234.5
    assert parse_number("−1.234,5", ",") == -1234.5
    assert values_of(["-3,5", "−2,0", "1,0"]).tolist() == [-3.5, -2.0, 1.0]


def test_space_and_thin_space_thousands() -> None:
    """Separadores de miles espacio, espacio no separable y espacio fino."""
    assert parse_number("1 234 567", ".") == 1234567.0
    assert parse_number("1 234,5", ",") == 1234.5
    assert parse_number("1 234,5", ",") == 1234.5


def test_euro_and_peso_symbols() -> None:
    """ "$" y "€" se remueven y se registran como unit_hint."""
    assert parse_number("$ 1.500", ",") == 1500.0
    assert parse_number("€12,30", ",") == 12.3
    table = normalize_table(column([["$ 1.500"], ["$ 200"]]), header_rows=1)
    assert table.unit_hints["valor"] == "$"


def test_malformed_thousands_grouping_is_not_a_number() -> None:
    """Grupos de miles mal formados ("12.34.5") no parsean."""
    assert parse_number("12.34.5", ",") is None
    assert parse_number("1.23", ",") is None
    assert parse_number("abc", ".") is None


def test_header_rows_zero_uses_col_names() -> None:
    """Sin filas de encabezado las columnas se llaman col_<i> y todas las filas son datos."""
    table = normalize_table([["a", "1"], ["b", "2"]], header_rows=0)

    assert list(table.df.columns) == ["col_0", "col_1"]
    assert len(table.df) == 2


def test_ragged_rows_are_padded_with_nan() -> None:
    """Filas más cortas se completan con celdas vacías (NaN)."""
    table = normalize_table([["A", "B", "C"], ["x", "1"], ["y", "2", "3"]], header_rows=1)

    assert table.df.shape == (2, 3)
    assert table.df["C"].isna().tolist() == [True, False]


def test_header_rows_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        normalize_table([["A", "B"]], header_rows=2)


# --- Invariante para Parquet (Iteración 4) ------------------------------------------------------


def test_columns_are_numeric_or_text_and_roundtrip_parquet(tmp_path: Path) -> None:
    """Cada columna es float64, Int64 o str (nunca object) y el DataFrame se escribe y
    relee en Parquet sin cambios (dtypes incluidos)."""
    raw = [
        ["", "2023", ""],
        ["Región", "Ene", "Feb"],
        ["GBA", "1.234,5", "s/d"],
        ["NEA", "(12,0)", "7,4%"],
        ["Total", "−3", "8,0%"],
    ]
    year_raw = [["Año", "Región", "Obs"], ["2021", "GBA", "-"], ["2022", None, "nota"]]

    for table in (normalize_table(raw, 2), normalize_table(year_raw, 1)):
        assert set(table.dtypes.values()) <= {"float64", "Int64", "str"}
        assert table.dtypes == {c: str(t) for c, t in table.df.dtypes.items()}
        path = tmp_path / "t.parquet"
        table.df.to_parquet(path, engine="pyarrow", index=False)
        pd.testing.assert_frame_equal(pd.read_parquet(path), table.df)


# --- Llamadas a nota al pie pegadas a números (D-011, Iteración 3) ------------------------------


@pytest.mark.parametrize(
    ("cell", "decimal", "expected"),
    [
        ("6.5b", ".", 6.5),
        ("667 888.6b", ".", 667888.6),
        ("-0.6c", ".", -0.6),
        ("12,3(a)", ",", 12.3),
        ("45,0*", ",", 45.0),
        ("45,4 (1)", ",", 45.4),
        ("25,2(2)", ",", 25.2),
        ("12,5%a", ",", 12.5),
    ],
)
def test_footnote_mark_after_number_is_ignored(cell: str, decimal: str, expected: float) -> None:
    """Given un número con una llamada a nota al pie al final (como en el Anuario de CEPAL),
    when se parsea, then se ignora la llamada y se obtiene el número."""
    assert parse_number(cell, decimal) == pytest.approx(expected)


def test_footnote_marks_do_not_turn_text_into_numbers() -> None:
    """Letras sin un dígito inmediatamente antes siguen siendo texto."""
    assert parse_number("América Latina d", ".") is None
    assert parse_number("abc", ".") is None
    assert parse_number("x", ".") is None  # marcador de faltante (BR-004)
    assert parse_number("(12)", ",") == -12  # paréntesis sin dígito antes: sigue siendo negativo


def test_footnoted_totals_keep_column_numeric() -> None:
    """Given una columna con totales regionales marcados con notas ("667 888.6b"),
    when se normaliza, then la columna es numérica y los totales no se pierden."""
    table = normalize_table(
        [["País", "Población"], ["Argentina", "45 851.4"], ["Brasil", "212 812.4"], ["Total", "667 888.6b"]],
        header_rows=1,
    )
    assert table.dtypes["Población"] == "float64"
    assert table.df["Población"].tolist() == [45851.4, 212812.4, 667888.6]
