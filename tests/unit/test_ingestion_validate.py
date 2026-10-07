"""BR-007 (validación de metadatos, VC-022) y BR-008 (reparación determinística), sin red."""

from __future__ import annotations

import pandas as pd
import pytest

from agents.ingestion.schemas import ColumnMeta, TableEnrichment
from agents.ingestion.validation import (
    apply_unit_hints,
    count_sentences,
    first_text_column,
    repair_enrichment,
    row_header_candidates,
    rule_tag,
    semantic_dtype,
    validate_enrichment,
)
from tests.tables import sample_df, valid_enrichment


def only_rule(errors: list[str], rule: int) -> None:
    assert len(errors) == 1, errors
    assert errors[0].startswith(rule_tag(rule)), errors


def numeric_df() -> pd.DataFrame:
    return pd.DataFrame({"A": [1.5, 2.5], "B": [3.0, 4.0]})


# --- VC-022 -----------------------------------------------------------------------------------------


def test_vc022_valid_enrichment_has_no_errors() -> None:
    """Given metadatos coherentes con la tabla, then 0 errores."""
    df = sample_df()

    assert validate_enrichment(valid_enrichment(df), df) == []


def test_vc022_rule1_column_count_mismatch() -> None:
    """Given una columna de menos, then solo el error de la regla 1, que lista las columnas."""
    df = sample_df()
    enrichment = valid_enrichment(df)
    enrichment = enrichment.model_copy(update={"columns": enrichment.columns[:-1]})

    errors = validate_enrichment(enrichment, df)

    only_rule(errors, 1)
    assert "Tasa de desocupación - 2T2026" in errors[0]


def test_vc022_rule2_column_names_must_match_exactly_and_in_order() -> None:
    """Given un nombre de columna reescrito (mismo dtype), then solo el error de la regla 2."""
    df = sample_df()
    enrichment = valid_enrichment(df)
    columns = list(enrichment.columns)
    columns[1] = columns[1].model_copy(update={"name": "Tasa de desocupación 2T2026"})

    errors = validate_enrichment(enrichment.model_copy(update={"columns": columns}), df)

    only_rule(errors, 2)
    assert "posición 1" in errors[0]


def test_vc022_rule3_row_headers_must_exist_in_first_text_column() -> None:
    """Given un rótulo de fila inventado, then solo el error de la regla 3; con espacios de más
    alrededor de un rótulo real no hay error (se compara con strip)."""
    df = sample_df()

    errors = validate_enrichment(valid_enrichment(df, row_headers=["GBA", "Cuyo"]), df)

    only_rule(errors, 3)
    assert "Cuyo" in errors[0]
    assert validate_enrichment(valid_enrichment(df, row_headers=["  GBA ", "Patagonia"]), df) == []


def test_vc022_rule3_without_text_column_row_headers_must_be_empty() -> None:
    """Given una tabla sin columnas de texto, then row_headers debe ser []."""
    df = numeric_df()

    only_rule(validate_enrichment(valid_enrichment(df, row_headers=["A"]), df), 3)
    assert validate_enrichment(valid_enrichment(df, row_headers=[]), df) == []


@pytest.mark.parametrize("name", ["ab", "x" * 81, "   "])
def test_vc022_rule4_name_length(name: str) -> None:
    """Given un nombre de < 3 o > 80 caracteres, then solo el error de la regla 4."""
    df = sample_df()

    only_rule(validate_enrichment(valid_enrichment(df, name=name), df), 4)


@pytest.mark.parametrize(
    "description",
    [
        "Una sola oración.",
        "Uno. Dos. Tres. Cuatro. Cinco.",
        "Primera oración larga " + "x" * 600 + ". Segunda.",
    ],
    ids=["1-oracion", "5-oraciones", "mas-de-600"],
)
def test_vc022_rule5_description_sentences_and_length(description: str) -> None:
    """Given una descripción de 1 o 5 oraciones, o de más de 600 caracteres, then solo el
    error de la regla 5."""
    df = sample_df()

    only_rule(validate_enrichment(valid_enrichment(df, description=description), df), 5)


def test_vc022_rule5_decimal_points_are_not_sentence_ends() -> None:
    """Un número con punto decimal no cuenta como fin de oración."""
    assert count_sentences("La tasa fue 7.6 por ciento. Subió 0.5 puntos.") == 2


def test_vc022_rule6_dtype_must_be_compatible_with_real_dtype() -> None:
    """Given una columna numérica tipada como text y una de texto como number, then un único
    error de la regla 6 que nombra ambas."""
    df = sample_df()
    enrichment = valid_enrichment(df)
    columns = list(enrichment.columns)
    columns[0] = columns[0].model_copy(update={"dtype": "number"})
    columns[1] = columns[1].model_copy(update={"dtype": "text"})

    errors = validate_enrichment(enrichment.model_copy(update={"columns": columns}), df)

    only_rule(errors, 6)
    assert "Región" in errors[0] and "Tasa de desocupación - 2T2026" in errors[0]


def test_vc022_numeric_columns_accept_every_numeric_semantic_dtype() -> None:
    df = numeric_df()
    for dtype in ("number", "integer", "percent", "currency", "year"):
        enrichment = valid_enrichment(
            df, columns=[ColumnMeta(name=c, description="", dtype=dtype) for c in df.columns]
        )
        assert validate_enrichment(enrichment, df) == [], dtype


def test_several_rules_violated_give_one_error_each() -> None:
    df = sample_df()
    enrichment = valid_enrichment(df, name="ab", description="Sin oraciones", row_headers=["Cuyo"])

    errors = validate_enrichment(enrichment, df)

    assert [e.split("]")[0] + "]" for e in errors] == [rule_tag(3), rule_tag(4), rule_tag(5)]


# --- helpers ------------------------------------------------------------------------------------------


def test_row_header_candidates_are_distinct_non_empty_values_of_first_text_column() -> None:
    df = pd.DataFrame({"v": [1.0, 2.0, 3.0, 4.0], "r": [" A ", "B", None, "A"], "s": ["x", "y", "z", "w"]})

    assert first_text_column(df) == "r"
    assert row_header_candidates(df) == ["A", "B"]


def test_semantic_dtype_from_real_dtype() -> None:
    df = sample_df()

    assert semantic_dtype(df["Región"]) == "text"
    assert semantic_dtype(df["Año"]) == "year"
    assert semantic_dtype(df["Tasa de desocupación - 2T2026"]) == "number"
    assert semantic_dtype(df["Tasa de desocupación - 2T2026"], "%") == "percent"
    assert semantic_dtype(df["Tasa de desocupación - 2T2026"], "US$") == "currency"
    assert semantic_dtype(pd.Series([3, 5000], dtype="Int64")) == "integer"


# --- BR-008 -------------------------------------------------------------------------------------------


def test_br008_repair_rebuilds_columns_from_dataframe_and_keeps_matching_descriptions() -> None:
    """Given un último intento con una columna mal nombrada, dtypes incompatibles y un rótulo
    inventado, then las columnas salen del DataFrame (nombre exacto y dtype real), con la
    descripción del LLM solo donde el nombre coincidía, y los rótulos se filtran."""
    df = sample_df()
    last = TableEnrichment(
        name="Desocupación por región",
        description="Una. Dos. Tres. Cuatro. Cinco.",
        columns=[
            ColumnMeta(name="Región", description="Región geográfica", dtype="number"),
            ColumnMeta(name="Tasa 2T2026", description="Tasa", dtype="percent", unit="%"),
        ],
        row_headers=["GBA", "Cuyo", "Patagonia "],
    )

    repaired = repair_enrichment(
        last,
        df,
        file_name="eph.pdf",
        first_page=3,
        index_on_page=1,
        unit_hints={"Tasa de desocupación - 2T2026": "%"},
    )

    assert [c.name for c in repaired.columns] == list(df.columns)
    assert [c.dtype for c in repaired.columns] == ["text", "percent", "year"]
    assert [c.description for c in repaired.columns] == ["Región geográfica", "", ""]
    assert repaired.columns[1].unit == "%"
    assert repaired.row_headers == ["GBA", "Patagonia"]
    assert repaired.name == "Desocupación por región"
    assert repaired.description == last.description
    errors = validate_enrichment(repaired, df)
    assert all(e.startswith(rule_tag(5)) for e in errors)  # la descripción se conserva tal cual


def test_br008_repair_with_empty_name_and_description_uses_defaults() -> None:
    """Given nombre y descripción vacíos (o sin intento válido), then "Tabla p<pág> #<i> de
    <archivo>" y el contexto superior truncado."""
    df = sample_df()
    above = ["Cuadro 3. Ocupación por región.", "x" * 700]

    from_empty = repair_enrichment(
        valid_enrichment(df, name="  ", description=""),
        df,
        file_name="eph.pdf",
        first_page=3,
        index_on_page=1,
        context_above=above,
    )
    from_none = repair_enrichment(None, df, file_name="eph.pdf", first_page=3, index_on_page=1)

    assert from_empty.name == "Tabla p3 #1 de eph.pdf"
    assert from_empty.description.startswith("Cuadro 3. Ocupación por región.")
    assert len(from_empty.description) == 600
    assert from_none.name == "Tabla p3 #1 de eph.pdf"
    assert from_none.description == "Tabla extraída de eph.pdf."
    assert from_none.row_headers == []
    assert [c.name for c in from_none.columns] == list(df.columns)


def test_apply_unit_hints_fills_only_missing_units() -> None:
    df = sample_df()
    enrichment = valid_enrichment(df)
    columns = list(enrichment.columns)
    columns[2] = columns[2].model_copy(update={"unit": "año"})
    enrichment = enrichment.model_copy(update={"columns": columns})

    out = apply_unit_hints(enrichment, {"Tasa de desocupación - 2T2026": "%", "Año": "%"})

    assert [c.unit for c in out.columns] == [None, "%", "año"]
