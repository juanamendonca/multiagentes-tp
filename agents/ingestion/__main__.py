"""CLI de ingesta: ``python -m agents.ingestion <pdf> [<pdf> ...]``.

Ingiere cada PDF con el grafo real (LiteLLM + Pinecone ``PINECONE_INDEX_NAME`` + ``TABLES_DIR``)
y muestra, por tabla, id, páginas, calidad de metadatos y nombre, más los avisos. Termina con
código 1 si algún PDF no se pudo abrir o no quedó indexado completo.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agents.ingestion.graph import IngestionResult, run_ingestion
from agents.ingestion.nodes import DEFAULT_ENRICH_CONCURRENCY, build_default_deps
from services.config import ConfigError, load_settings
from services.llm import enable_cache_if_configured
from services.vectorstore import format_pages


def print_result(result: IngestionResult) -> None:
    print(f"== {result.file_name} — {result.status}: {result.message}")
    print(
        f"   doc_hash={result.doc_hash[:16]}… tablas={result.n_detected} indexadas={result.n_indexed} "
        f"degradadas={result.n_degraded} llamadas_llm={result.llm_calls} "
        f"vueltas_enrich={result.enrich_rounds} tiempo={result.elapsed_s:.1f}s"
    )
    if result.run_id:
        print(f"   traza LangSmith (run raíz): {result.run_id}")
    for record in result.records:
        print(
            f"   - {record.table_id} [{format_pages(record.source.pages)}] "
            f"{record.n_rows}x{record.n_cols} {record.metadata_quality} "
            f"(intentos={record.validation_attempts}): {record.name}"
        )
    if result.deleted_ids:
        print(f"   obsoletas borradas: {', '.join(result.deleted_ids)}")
    for warning in result.warnings:
        where = " ".join(
            part for part in (f"pág. {warning.page}" if warning.page else "", warning.table_ref or "") if part
        )
        print(f"   ! {where + ': ' if where else ''}{warning.reason}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.ingestion", description="Ingiere PDFs con tablas."
    )
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDFs a ingerir")
    parser.add_argument("--as-name", help="nombre de archivo a registrar (solo con un PDF)")
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_ENRICH_CONCURRENCY, help="llamadas LLM en paralelo"
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.as_name and len(args.pdfs) != 1:
        parser.error("--as-name solo se puede usar con un PDF")

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    enable_cache_if_configured(settings)
    deps = build_default_deps(settings, enrich_concurrency=max(1, args.concurrency))

    failed = False
    for path in args.pdfs:
        name = args.as_name or path.name
        result = run_ingestion(name, path.read_bytes(), deps)
        print_result(result)
        failed |= not result.ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
