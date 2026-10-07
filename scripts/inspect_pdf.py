"""Inspecciona las tablas que la ingesta extraería de un PDF (sin LLM ni red).

Uso (desde la raíz del repo):
    .venv\\Scripts\\python scripts/inspect_pdf.py data/sample_pdfs/<archivo>.pdf [--rows 5] [--context]

Imprime por tabla lógica: referencia, páginas, shape, columnas con dtype y unidad,
primeras filas y (con ``--context``) el contexto de BR-006; al final, los avisos.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.pdf_context import collect_context  # noqa: E402
from services.pdf_extract import PdfOpenError, extract_tables, load_pdf  # noqa: E402


def _short(text: str, n: int = 160) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspecciona las tablas extraídas de un PDF.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--rows", type=int, default=5, help="filas de muestra por tabla (default 5)")
    parser.add_argument("--max-tables", type=int, default=40, help="tope de tablas (MAX_TABLES_PER_PDF)")
    parser.add_argument("--context", action="store_true", help="mostrar el contexto de cada tabla")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        loaded = load_pdf(args.pdf.name, args.pdf.read_bytes())
    except (OSError, PdfOpenError) as exc:
        print(f"ERROR: {exc}")
        return 1
    tables, warnings = extract_tables(loaded, max_tables=args.max_tables)

    print(f"Archivo: {loaded.file_name}  páginas: {loaded.n_pages}  doc_hash: {loaded.doc_hash[:16]}…")
    print(f"Tablas lógicas: {len(tables)}  avisos: {len(warnings)}")
    with pd.option_context("display.width", 200, "display.max_columns", 20, "display.max_colwidth", 30):
        for table in tables:
            pages = ",".join(str(p) for p in table.pages)
            print()
            print("=" * 100)
            print(
                f"[{table.ref}] págs. {pages}  shape={table.df.shape}  "
                f"encabezado={'sí' if table.has_header else 'no'}"
            )
            for col in table.header:
                unit = table.unit_hints.get(col)
                print(f"   - {_short(col, 110)!r}: {table.dtypes[col]}" + (f" ({unit})" if unit else ""))
            print(table.df.head(args.rows).to_string(max_colwidth=30))
            if args.context:
                ctx = collect_context(loaded, table, tables)
                print(f"   sección: {ctx.section!r}")
                for text in ctx.above:
                    print(f"   arriba:  {_short(text)!r}")
                for text in ctx.below:
                    print(f"   abajo:   {_short(text)!r}")
    print()
    print("Avisos:")
    for w in warnings:
        where = f"pág. {w.page}" if w.page is not None else "documento"
        ref = f" [{w.table_ref}]" if w.table_ref else ""
        print(f"   - {where}{ref}: {w.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
