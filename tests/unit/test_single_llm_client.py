"""VC-001 (FR-001): ChatOpenAI/OpenAIEmbeddings solo se construyen en services/llm.py."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXCLUDED_DIRS = {".venv", "venv", "notebooks", "tests", ".git", "__pycache__"}
ALLOWED_FILE = Path("services/llm.py")
CONSTRUCTOR = re.compile(r"\b(ChatOpenAI|OpenAIEmbeddings)\s*\(")
# Cliente crudo del SDK openai (importar `openai` para atrapar sus excepciones sí está permitido).
RAW_OPENAI_CLIENT = re.compile(r"\bopenai\.(Async)?(Azure)?OpenAI\s*\(")
# Clientes de LLM que no pasan por el proxy vía langchain_openai.
FORBIDDEN_MODULES = (
    "anthropic",
    "litellm",
    "langchain_anthropic",
    "langchain_google_genai",
    "langchain_google_vertexai",
    "langchain_mistralai",
    "langchain_ollama",
    "langchain_community.chat_models",
    "langchain_community.llms",
    "langchain_community.embeddings",
)
FORBIDDEN_NAMES = {
    "AzureChatOpenAI",
    "AzureOpenAIEmbeddings",
    "init_chat_model",
    "init_embeddings",
    "OpenAI",
    "AsyncOpenAI",
    "AzureOpenAI",
    "AsyncAzureOpenAI",
}


def project_python_files() -> list[Path]:
    """`*.py` del repo, podando .venv/notebooks/tests y directorios ocultos sin recorrerlos."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]
        base = Path(dirpath).relative_to(REPO_ROOT)
        files.extend(base / name for name in filenames if name.endswith(".py"))
    return sorted(files)


def test_repo_scan_finds_the_llm_module() -> None:
    assert ALLOWED_FILE in project_python_files()


def test_llm_constructors_only_in_services_llm() -> None:
    offenders = [
        str(path)
        for path in project_python_files()
        if path != ALLOWED_FILE and CONSTRUCTOR.search((REPO_ROOT / path).read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_services_llm_builds_both_clients_with_litellm_base_url() -> None:
    source = (REPO_ROOT / ALLOWED_FILE).read_text(encoding="utf-8")

    assert re.search(r"\bChatOpenAI\s*\(", source)
    assert re.search(r"\bOpenAIEmbeddings\s*\(", source)
    assert source.count("settings.litellm_base_url") >= 2


def test_no_other_llm_client_imports() -> None:
    offenders: list[str] = []
    for path in project_python_files():
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        if RAW_OPENAI_CLIENT.search(source):
            offenders.append(f"{path}: cliente openai directo")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            names: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
                names = [alias.name for alias in node.names]
            for module in modules:
                if any(module == f or module.startswith(f + ".") for f in FORBIDDEN_MODULES):
                    offenders.append(f"{path}: import {module}")
            for name in names:
                if name in FORBIDDEN_NAMES:
                    offenders.append(f"{path}: {name}")

    assert offenders == []
