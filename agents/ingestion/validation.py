"""Validación (BR-007) y reparación determinística (BR-008) de los metadatos de una tabla.

Funciones puras sobre ``TableEnrichment`` + ``DataFrame``; sin LLM ni E/S.

``validate_enrichment`` devuelve **a lo sumo un error por regla** de BR-007, cada uno con el
prefijo ``[regla N]`` y el detalle necesario para que el LLM lo corrija en el reintento
(FR-021). Si la cantidad de columnas no coincide (regla 1) no se evalúan las reglas que
comparan columna a columna (2 y 6): el error de la regla 1 ya lista los nombres esperados.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

import pandas as pd

from agents.ingestion.schemas import ColumnDtype, ColumnMeta, TableEnrichment
from services.table_parsing import YEAR_MAX, YEAR_MIN

NAME_MIN_CHARS = 3
NAME_MAX_CHARS = 80
DESCRIPTION_MIN_SENTENCES = 2
DESCRIPTION_MAX_SENTENCES = 4
DESCRIPTION_MAX_CHARS = 600
# BR-007 (5): una oración termina en . ! o ? seguido de espacio o fin de texto.
SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
# BR-007 (6): dtypes semánticos compatibles con una columna numérica; el resto, con texto.
NUMERIC_DTYPES: frozenset[str] = frozenset({"number", "integer", "percent", "currency", "year"})
TEXT_DTYPES: frozenset[str] = frozenset({"text", "date"})
CURRENCY_HINTS = frozenset({"$", "US$", "U$S", "€"})


def rule_tag(rule: int) -> str:
    """Prefijo de los errores de la regla ``rule`` de BR-007 (``"[regla 3]"``)."""
    return f"[regla {rule}]"


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def is_numeric_column(series: pd.Series) -> bool:
    """``True`` si el dtype real de la columna es numérico (``float64``, ``Int64``…)."""
    return pd.api.types.is_numeric_dtype(series.dtype) and not pd.api.types.is_bool_dtype(series.dtype)


def first_text_column(df: pd.DataFrame) -> str | None:
    """Nombre de la primera columna de texto del DataFrame (BR-007 regla 3), o ``None``."""
    for name in df.columns:
        if not is_numeric_column(df[name]):
            return str(name)
    return None


def row_header_candidates(df: pd.DataFrame) -> list[str]:
    """Valores distintos y no vacíos (con ``strip()``) de la primera columna de texto, en orden."""
    column = first_text_column(df)
    if column is None:
        return []
    seen: dict[str, None] = {}
    for value in df[column].dropna().tolist():
        text = str(value).strip()
        if text:
            seen.setdefault(text, None)
    return list(seen)


def count_sentences(text: str) -> int:
    """Cantidad de oraciones de BR-007 (5): coincidencias de ``[.!?](?=\\s|$)``."""
    return len(SENTENCE_END_RE.findall(text))


def validate_enrichment(enrichment: TableEnrichment, df: pd.DataFrame) -> list[str]:
    """BR-007: lista de errores (vacía si los metadatos son válidos), a lo sumo uno por regla."""
    errors: list[str] = []
    expected = [str(c) for c in df.columns]
    got = [c.name for c in enrichment.columns]

    # (1) una entrada por columna
    same_length = len(got) == len(expected)
    if not same_length:
        errors.append(
            f"{rule_tag(1)} columns tiene {len(got)} entradas pero la tabla tiene {len(expected)} "
            f"columnas; deben ser exactamente estas, en este orden: "
            f"{json.dumps(expected, ensure_ascii=False)}"
        )

    # (2) nombres exactos y en orden
    if same_length:
        mismatches = [
            f"posición {i}: se esperaba {_q(want)} y llegó {_q(have)}"
            for i, (want, have) in enumerate(zip(expected, got, strict=True))
            if want != have
        ]
        if mismatches:
            errors.append(
                f"{rule_tag(2)} los nombres de columns deben copiar exactamente, y en orden, los de "
                f"la tabla: " + "; ".join(mismatches)
            )

    # (3) row_headers existentes en la primera columna de texto
    text_column = first_text_column(df)
    if text_column is None:
        if enrichment.row_headers:
            errors.append(
                f"{rule_tag(3)} la tabla no tiene columna de texto: row_headers debe ser [] "
                f"(llegaron {len(enrichment.row_headers)} rótulos)"
            )
    else:
        valid = set(row_header_candidates(df))
        unknown = [h for h in enrichment.row_headers if h.strip() not in valid]
        if unknown:
            shown = json.dumps(unknown[:10], ensure_ascii=False)
            more = f" (y {len(unknown) - 10} más)" if len(unknown) > 10 else ""
            errors.append(
                f"{rule_tag(3)} estos row_headers no existen en la columna {_q(text_column)}: "
                f"{shown}{more}; copiá los valores exactos de esa columna"
            )

    # (4) largo del nombre
    name_length = len(enrichment.name.strip())
    if not NAME_MIN_CHARS <= name_length <= NAME_MAX_CHARS:
        errors.append(
            f"{rule_tag(4)} name tiene {name_length} caracteres; debe tener entre "
            f"{NAME_MIN_CHARS} y {NAME_MAX_CHARS}"
        )

    # (5) descripción: 2–4 oraciones y ≤ 600 caracteres
    sentences = count_sentences(enrichment.description)
    chars = len(enrichment.description)
    if (
        not (DESCRIPTION_MIN_SENTENCES <= sentences <= DESCRIPTION_MAX_SENTENCES)
        or chars > DESCRIPTION_MAX_CHARS
    ):
        errors.append(
            f"{rule_tag(5)} description tiene {sentences} oraciones y {chars} caracteres; debe tener "
            f"de {DESCRIPTION_MIN_SENTENCES} a {DESCRIPTION_MAX_SENTENCES} oraciones (cada una "
            f"terminada en punto seguido de espacio, sin abreviaturas con punto) y como máximo "
            f"{DESCRIPTION_MAX_CHARS} caracteres"
        )

    # (6) dtype semántico compatible con el dtype real (por posición)
    incompatible: list[str] = []
    if same_length:
        for name, column in zip(expected, enrichment.columns, strict=True):
            numeric = is_numeric_column(df[name])
            allowed = NUMERIC_DTYPES if numeric else TEXT_DTYPES
            if column.dtype not in allowed:
                kind = "numérica" if numeric else "de texto"
                incompatible.append(
                    f"{_q(name)} es {kind} (usar {' | '.join(sorted(allowed))}) y llegó {_q(column.dtype)}"
                )
    if incompatible:
        errors.append(f"{rule_tag(6)} dtype incompatible con el tipo real: " + "; ".join(incompatible))
    return errors


# --- BR-008 ----------------------------------------------------------------------------------------


def semantic_dtype(series: pd.Series, unit_hint: str | None = None) -> ColumnDtype:
    """dtype semántico derivado del dtype real (BR-008): enteros 1900–2100 → ``year``; otros
    enteros → ``integer``; reales con ``unit_hint`` ``%`` → ``percent``, moneda → ``currency``,
    si no ``number``; no numéricas → ``text``."""
    if not is_numeric_column(series):
        return "text"
    if pd.api.types.is_integer_dtype(series.dtype):
        values = series.dropna()
        if len(values) and bool(((values >= YEAR_MIN) & (values <= YEAR_MAX)).all()):
            return "year"
        return "integer"
    if unit_hint == "%":
        return "percent"
    if unit_hint in CURRENCY_HINTS:
        return "currency"
    return "number"


def default_name(file_name: str, first_page: int, index_on_page: int) -> str:
    """Nombre por defecto de BR-008: ``"Tabla p<página> #<i> de <archivo>"``."""
    return f"Tabla p{first_page} #{index_on_page} de {file_name}"


def repair_enrichment(
    last: TableEnrichment | None,
    df: pd.DataFrame,
    *,
    file_name: str,
    first_page: int,
    index_on_page: int,
    context_above: Sequence[str] = (),
    unit_hints: Mapping[str, str | None] | None = None,
) -> TableEnrichment:
    """BR-008: metadatos válidos en estructura a partir del último intento del LLM.

    - ``name``/``description``: los del último intento; si están vacíos, el nombre por defecto
      y el contexto superior truncado a 600 caracteres.
    - ``columns``: reconstruidas desde el DataFrame (nombre exacto, dtype semántico derivado del
      real); la descripción (y la unidad) del LLM solo si el nombre coincidía exactamente; si no,
      descripción ``""`` y la unidad de ``unit_hints``.
    - ``row_headers``: los del último intento que existen en la primera columna de texto.
    """
    hints = dict(unit_hints or {})
    previous: dict[str, ColumnMeta] = {}
    if last is not None:
        for column in last.columns:
            previous.setdefault(column.name, column)

    name = last.name.strip() if last is not None else ""
    if not name:
        name = default_name(file_name, first_page, index_on_page)
    description = last.description.strip() if last is not None else ""
    if not description:
        description = " ".join(t.strip() for t in context_above if t.strip())[:DESCRIPTION_MAX_CHARS]
    if not description:
        description = f"Tabla extraída de {file_name}."

    columns: list[ColumnMeta] = []
    for raw_name in df.columns:
        column_name = str(raw_name)
        hint = hints.get(column_name)
        match = previous.get(column_name)
        columns.append(
            ColumnMeta(
                name=column_name,
                description=match.description if match is not None else "",
                dtype=semantic_dtype(df[raw_name], hint),
                unit=(match.unit if match is not None and match.unit else hint),
            )
        )

    valid = set(row_header_candidates(df))
    row_headers = [h.strip() for h in (last.row_headers if last is not None else []) if h.strip() in valid]
    return TableEnrichment(name=name, description=description, columns=columns, row_headers=row_headers)


def apply_unit_hints(enrichment: TableEnrichment, unit_hints: Mapping[str, str | None]) -> TableEnrichment:
    """Completa ``unit`` con el ``unit_hint`` de BR-003 en las columnas donde el LLM no puso
    unidad (así las unidades removidas al normalizar quedan en el JSON de la tabla)."""
    columns = [
        column.model_copy(update={"unit": unit_hints.get(column.name)})
        if not column.unit and unit_hints.get(column.name)
        else column
        for column in enrichment.columns
    ]
    return enrichment.model_copy(update={"columns": columns})
