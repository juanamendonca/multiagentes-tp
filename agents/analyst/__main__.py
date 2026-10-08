"""CLI del Analista: ``python -m agents.analyst "<pregunta>"``.

Responde con el grafo real (LiteLLM + Pinecone ``PINECONE_INDEX_NAME`` en solo lectura +
``TABLES_DIR``) y muestra estado, respuesta, plan, candidatas, cobertura por parte, resultados,
faltantes y avisos. Código de salida: 0 si respondió (cualquier estado), 2 si falta config.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from agents.analyst.graph import run_analyst
from agents.analyst.nodes import build_default_deps
from agents.analyst.schemas import AnalystResult
from services.config import ConfigError, load_settings
from services.llm import enable_cache_if_configured
from services.vectorstore import format_pages


def print_result(result: AnalystResult, *, max_candidates: int = 10) -> None:
    print(f"status={result.status}")
    print(f"respuesta:\n{result.answer}\n")
    if result.plan is not None:
        print("plan:")
        for part in result.plan.parts:
            print(f"  {part.id}: {part.text} [op={part.operation}; períodos={part.periods}]")
            for query in part.search_queries:
                print(f"      búsqueda: {query}")
    print(f"candidatas ({len(result.candidates)}):")
    for c in result.candidates[:max_candidates]:
        print(f"  {c.score:.3f} {c.table_id} [{c.file_name}, {format_pages(c.pages)}] {c.name}")
    if len(result.candidates) > max_candidates:
        print(f"  … (+{len(result.candidates) - max_candidates})")
    if result.selection is not None:
        print(f"seleccionadas: {result.selection.selected_table_ids}")
        for cov in result.selection.coverage:
            line = f"  {cov.part_id}: {cov.coverage} {cov.table_ids}"
            if cov.missing_reason:
                line += f" — {cov.missing_reason}"
            if cov.related_table_note:
                line += f" (relacionada: {cov.related_table_note})"
            print(line)
        if result.selection.rationale:
            print(f"  motivo: {result.selection.rationale}")
    print(f"resultados: {[r.model_dump() for r in result.results]}")
    for m in result.missing:
        note = f" (relacionada: {m.related_table_note})" if m.related_table_note else ""
        print(f"falta {m.part_id}: {m.reason}{note}")
    for w in result.warnings:
        print(f"! {w}")
    print(
        f"code_attempts={result.code_attempts} llamadas_chat={result.llm_calls} "
        f"llamadas_embeddings={result.embedding_calls} tiempo={result.elapsed_s:.1f}s"
    )
    if result.run_id:
        print(f"traza LangSmith (run raíz): {result.run_id}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.analyst", description="Responde una pregunta sobre las tablas indexadas."
    )
    parser.add_argument("question", help="pregunta en lenguaje natural")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    enable_cache_if_configured(settings)
    result = run_analyst(args.question, build_default_deps(settings))
    print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
