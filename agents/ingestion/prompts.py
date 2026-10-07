"""Prompt de enriquecimiento de tablas (FR-019, FR-021), en español.

El prompt de sistema fija las reglas de BR-007; el mensaje de usuario trae los datos de la
tabla: archivo, páginas, sección, contexto arriba/abajo, columnas **exactas** con su dtype real
y los dtypes semánticos permitidos, primeras filas, rótulos de fila candidatos y, en un
reintento, el bloque de feedback con los errores de validación del intento anterior.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agents.ingestion.state import TableDraft
from agents.ingestion.validation import (
    DESCRIPTION_MAX_CHARS,
    NAME_MAX_CHARS,
    NAME_MIN_CHARS,
    NUMERIC_DTYPES,
    TEXT_DTYPES,
    first_text_column,
    is_numeric_column,
    row_header_candidates,
)
from services.vectorstore import format_pages

PREVIEW_ROWS = 8
MAX_CANDIDATE_ROW_HEADERS = 150

SYSTEM_PROMPT = f"""Sos un analista de datos que documenta tablas extraídas de PDFs de estadísticas públicas.
Para la tabla que te paso devolvés sus metadatos en el formato estructurado pedido. Reglas obligatorias:

1. `columns`: exactamente una entrada por columna de la tabla, en el mismo orden.
2. `columns[i].name`: copiá el nombre de la columna carácter por carácter, tal como aparece en la
   lista "Columnas" (incluidos asteriscos, signos °, guiones, espacios y mayúsculas). No lo traduzcas,
   abrevies ni corrijas.
3. `row_headers`: copiá exactamente, y en el mismo orden, los valores de la lista "Rótulos de fila
   candidatos". Si esa lista está vacía, devolvé [].
4. `name`: nombre corto y descriptivo en español, de {NAME_MIN_CHARS} a {NAME_MAX_CHARS} caracteres
   (qué mide la tabla, para qué población o región y qué período, si se sabe).
5. `description`: de 2 a 4 oraciones en español y como máximo {DESCRIPTION_MAX_CHARS} caracteres. Cada
   oración termina en punto. No uses abreviaturas con punto ("pág.", "aprox.", "p. ej.", "etc.") ni
   puntos suspensivos. Explicá qué muestra la tabla, su unidad y su fuente, sin inventar datos.
6. `columns[i].dtype`: si la columna es numérica usá uno de {", ".join(sorted(NUMERIC_DTYPES))};
   si es de texto usá {" o ".join(sorted(TEXT_DTYPES))}.
7. `columns[i].description`: qué mide la columna (en español, una frase). `columns[i].unit`: la unidad
   ("%", "miles de personas", "años", …) si se deduce del encabezado o del contexto; si no, null.

Usá solo la información de la tabla y su contexto; no agregues datos externos."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def describe_columns(draft: TableDraft) -> str:
    """Una línea por columna: índice, nombre exacto (JSON), dtype real, dtypes permitidos y unidad
    detectada al normalizar (``unit_hint``)."""
    df = draft.df
    lines = []
    for i, name in enumerate(df.columns):
        numeric = is_numeric_column(df[name])
        allowed = NUMERIC_DTYPES if numeric else TEXT_DTYPES
        hint = draft.raw.unit_hints.get(str(name))
        line = (
            f"{i}. {_json(str(name))} — dtype real {df[name].dtype} "
            f"({'numérica' if numeric else 'texto'}; dtype permitido: {' | '.join(sorted(allowed))})"
        )
        if hint:
            line += f"; unidad detectada: {hint}"
        lines.append(line)
    return "\n".join(lines)


def build_enrich_messages(
    draft: TableDraft,
    file_name: str,
    feedback: Sequence[str] = (),
) -> list[BaseMessage]:
    """Mensajes para ``with_structured_output(TableEnrichment)``. ``feedback`` son los errores de
    validación del intento anterior (FR-021); vacío en el primer intento."""
    df = draft.df
    context = draft.context
    candidates = row_header_candidates(df)
    text_column = first_text_column(df)
    shown = candidates[:MAX_CANDIDATE_ROW_HEADERS]
    parts = [
        f"Archivo: {file_name}",
        f"Ubicación: {format_pages(draft.raw.pages)} "
        f"(tabla {draft.raw.index_on_page + 1} de su primera página)",
        f"Sección: {context.section if context and context.section else '(sin sección)'}",
        "Contexto arriba de la tabla (el más cercano primero):",
        "\n".join(f"- {t}" for t in context.above) if context and context.above else "(ninguno)",
        "Contexto abajo de la tabla (notas, fuente):",
        "\n".join(f"- {t}" for t in context.below) if context and context.below else "(ninguno)",
        f"Tamaño: {len(df)} filas × {len(df.columns)} columnas.",
        f"Columnas ({len(df.columns)}, en orden):",
        describe_columns(draft),
        f"Primeras {min(PREVIEW_ROWS, len(df))} filas (CSV):",
        df.head(PREVIEW_ROWS).to_csv(index=False, lineterminator="\n").strip(),
        (
            f"Rótulos de fila candidatos (valores de la columna {_json(text_column)}):"
            if text_column is not None
            else "Rótulos de fila candidatos: la tabla no tiene columna de texto."
        ),
        _json(shown),
    ]
    if len(shown) < len(candidates):
        parts.append(f"(se muestran {len(shown)} de {len(candidates)}; devolvé solo los mostrados)")
    if feedback:
        parts += [
            "",
            "## Tu respuesta anterior fue rechazada",
            "Errores de validación que tenés que corregir:",
            "\n".join(f"- {error}" for error in feedback),
            "Devolvé los metadatos completos de nuevo, corrigiendo todos los errores.",
        ]
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="\n".join(parts))]
