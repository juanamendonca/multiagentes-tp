"""Diagramas Mermaid de los dos grafos, generados con ``draw_mermaid()`` (sin red ni claves).

Uso (desde la raíz del repo):
    .venv\\Scripts\\python scripts/mermaid_graphs.py            # imprime ambos diagramas
    .venv\\Scripts\\python scripts/mermaid_graphs.py --check    # compara con los bloques del README
    .venv\\Scripts\\python scripts/mermaid_graphs.py --write    # reescribe esos bloques del README

En ``README.md`` cada diagrama es el bloque ```` ```mermaid ```` que sigue al comentario
``<!-- mermaid:ingestion -->`` o ``<!-- mermaid:analyst -->``. ``--check`` termina con código 1
si alguno falta o difiere de la salida actual de ``build_*_graph(...).get_graph().draw_mermaid()``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.analyst.graph import build_analyst_graph  # noqa: E402
from agents.analyst.nodes import build_default_deps as build_analyst_deps  # noqa: E402
from agents.ingestion.graph import build_ingestion_graph  # noqa: E402
from agents.ingestion.nodes import build_default_deps as build_ingestion_deps  # noqa: E402
from services.config import Settings, load_settings  # noqa: E402

README_PATH = ROOT / "README.md"
BLOCK_RE = r"(<!-- mermaid:{name} -->\r?\n```mermaid\r?\n)(.*?)(\r?\n```)"

# Valores ficticios: construir las dependencias no hace red (los clientes se crean sin conectar).
_DUMMY_ENV = {
    "LITELLM_BASE_URL": "http://localhost:4000",
    "LITELLM_API_KEY": "dummy",
    "LLM_MODEL": "dummy",
    "EMBEDDING_MODEL": "dummy",
    "EMBEDDING_DIM": "8",
    "PINECONE_API_KEY": "dummy",
    "LANGSMITH_API_KEY": "dummy",
    "LANGSMITH_PROJECT": "dummy",
    "LANGSMITH_TRACING": "false",
    "TABLES_DIR": "data/tables",
}


def _settings() -> Settings:
    return load_settings(_DUMMY_ENV)


def current_diagrams() -> dict[str, str]:
    """``{"ingestion": ..., "analyst": ...}`` con la salida actual de ``draw_mermaid()``."""
    settings = _settings()
    ingestion = build_ingestion_graph(build_ingestion_deps(settings)).get_graph().draw_mermaid()
    analyst = build_analyst_graph(build_analyst_deps(settings)).get_graph().draw_mermaid()
    return {"ingestion": ingestion.strip("\n"), "analyst": analyst.strip("\n")}


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip("\n").split("\n"))


def readme_blocks(text: str) -> dict[str, str | None]:
    """Bloques Mermaid marcados del README (``None`` si falta el marcador)."""
    blocks: dict[str, str | None] = {}
    for name in ("ingestion", "analyst"):
        match = re.search(BLOCK_RE.format(name=name), text, flags=re.DOTALL)
        blocks[name] = match.group(2) if match else None
    return blocks


def check(readme: Path = README_PATH) -> list[str]:
    """Lista de diferencias (vacía si el README coincide con el código)."""
    text = readme.read_text(encoding="utf-8")
    problems: list[str] = []
    for name, expected in current_diagrams().items():
        found = readme_blocks(text)[name]
        if found is None:
            problems.append(f"falta el bloque <!-- mermaid:{name} --> en {readme.name}")
        elif _normalize(found) != _normalize(expected):
            problems.append(f"el diagrama '{name}' de {readme.name} no coincide con draw_mermaid()")
    return problems


def write(readme: Path = README_PATH) -> None:
    text = readme.read_text(encoding="utf-8")
    for name, diagram in current_diagrams().items():
        pattern = BLOCK_RE.format(name=name)
        if not re.search(pattern, text, flags=re.DOTALL):
            raise SystemExit(f"falta el bloque <!-- mermaid:{name} --> en {readme.name}")
        text = re.sub(pattern, lambda m, d=diagram: m.group(1) + d + m.group(3), text, flags=re.DOTALL)
    readme.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/mermaid_graphs.py", description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="compara con README.md (exit 1 si difiere)")
    group.add_argument("--write", action="store_true", help="reescribe los bloques de README.md")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.check:
        problems = check()
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print("OK: los diagramas de README.md coinciden con draw_mermaid() de ambos grafos.")
        return 1 if problems else 0
    if args.write:
        write()
        print("README.md actualizado con draw_mermaid() de ambos grafos.")
        return 0
    for name, diagram in current_diagrams().items():
        print(f"%% {name}\n{diagram}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
