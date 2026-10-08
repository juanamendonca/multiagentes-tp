"""Sandbox de ejecución del código generado por el Analista (spec BR-012, BR-013, §7.6, §7.7).

Defensas, en capas (ninguna es una frontera de seguridad del sistema operativo, ver D-026):

1. **Chequeo estático (AST)** — ``check_code``: allowlist de imports y prohibición de
   ``open``/``exec``/``eval``/``compile``/``__import__``/``input``/``globals`` (y otros
   builtins de introspección), ``getattr`` sobre módulos, atributos ``__*``, atributos
   "pivote" hacia módulos peligrosos (``pd.io.common.os``…) y funciones de lectura/escritura
   de archivos de pandas/numpy (``read_*``, ``to_csv``, ``np.save``…).
2. **Subproceso aislado** — ``run_in_sandbox``: ``python -I -X utf8`` con ``cwd`` en un
   directorio temporal propio que contiene **solo** las copias Parquet de las tablas
   seleccionadas (``t1.parquet``, ``t2.parquet``…); el programa (``main.py``) y el ``TEMP``
   del hijo viven en directorios hermanos. Entorno mínimo (``PATH``, ``SYSTEMROOT``,
   ``SYSTEMDRIVE``, ``TEMP``, ``TMP``): ninguna variable de ``.env`` llega al hijo. Sin
   ``SYSTEMDRIVE``, el arranque del Python de Windows crea un directorio literal
   ``%SystemDrive%`` en el ``cwd`` (D-027).
3. **Guardas de runtime** — el preámbulo (``build_preamble``), después de cargar las tablas,
   reemplaza ``socket.socket``, ``socket.create_connection`` y ``builtins.open`` por funciones
   que lanzan ``PermissionError``. ``io.open`` no se toca: lo usa pandas para leer los datos
   de zonas horarias (``tzdata``), y el chequeo AST no deja importar ``io``.
4. **Límites** — timeout con muerte del árbol de procesos (en Windows el ``python.exe`` de un
   venv es un lanzador que crea el intérprete real como hijo: hay que matar a los dos) y
   stdout/stderr acotados a 20 000 caracteres (se conserva el final, donde están la línea
   JSON y el traceback).

El contrato de salida (§7.6) se valida con ``parse_result_line``.
"""

from __future__ import annotations

import ast
import collections
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import IO, Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field

# --- BR-012 (a): chequeo estático -------------------------------------------------------------

ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "pandas",
        "numpy",
        "math",
        "statistics",
        "json",
        "datetime",
        "re",
        "collections",
        "itertools",
        "functools",
        "decimal",
        "fractions",
    }
)

# Spec BR-012: open, exec, eval, compile, __import__, input, globals. Se agregan builtins de
# introspección/escape equivalentes (locals, vars, setattr, delattr, breakpoint, help, exit,
# quit) — ver D-026.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "input",
        "globals",
        "locals",
        "vars",
        "setattr",
        "delattr",
        "breakpoint",
        "help",
        "exit",
        "quit",
    }
)

# Funciones de E/S de pandas/numpy: el código generado no lee archivos (BR-013) ni escribe.
# pyarrow lee/escribe Parquet sin pasar por ``builtins.open``, así que la guarda de runtime no
# las cubre: se rechazan por nombre de atributo.
_IO_ATTR_RE = re.compile(
    r"^(read_\w+|to_(csv|parquet|excel|pickle|feather|hdf|sql|stata|orc|html|xml|latex|clipboard)"
    r"|tofile|fromfile|save|savez|savez_compressed|savetxt|load|loadtxt|genfromtxt|fromregex|memmap"
    r"|ExcelWriter|HDFStore)$"
)

# Atributos que sirven de "pivote" hacia módulos peligrosos re-exportados por pandas/numpy
# (p. ej. ``pd.io.common.os.system(...)``): se rechazan por nombre (D-026). Es una lista de
# bloqueo, por lo tanto incompleta: ver limitaciones en DECISIONS.md.
FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "socket",
        "builtins",
        "importlib",
        "io",
        "pathlib",
        "ctypes",
        "environ",
        "system",
        "popen",
        "modules",
        "compat",
        "testing",
        "f_globals",
        "f_builtins",
        "gi_frame",
        "cr_frame",
    }
)

# Nombres "dunder" que el código puede usar sin riesgo.
_ALLOWED_DUNDER_NAMES: frozenset[str] = frozenset({"__name__"})


def check_code(code: str) -> list[str]:
    """Chequeo AST de BR-012 (a). Devuelve la lista de motivos de rechazo (vacía = aceptado).

    Los mensajes están en español y llevan el número de línea, para usarlos como feedback del
    reintento de ``generate_code``.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return [f"error de sintaxis (línea {exc.lineno}): {exc.msg}"]

    errors: list[str] = []
    module_names: set[str] = set()

    def add(node: ast.AST, message: str) -> None:
        errors.append(f"{message} (línea {getattr(node, 'lineno', '?')})")

    # Primera pasada: nombres ligados a módulos importados (para "getattr sobre módulos").
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                module_names.add(alias.asname or alias.name)
    module_names.update({"pd", "np"})  # definidos por el preámbulo

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    add(node, f"import no permitido: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                add(node, "import relativo no permitido")
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                add(node, f"import no permitido: {node.module}")
            for alias in node.names:
                if alias.name == "*":
                    add(node, f"import * no permitido: from {node.module} import *")
                elif alias.name.startswith("_"):
                    add(node, f"import de nombre privado no permitido: {alias.name}")
                elif _IO_ATTR_RE.match(alias.name):
                    add(node, f"función de archivos no permitida: {alias.name}")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                add(node, f"uso no permitido de {node.id}")
            elif node.id.startswith("__") and node.id not in _ALLOWED_DUNDER_NAMES:
                add(node, f"nombre no permitido: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in FORBIDDEN_ATTRIBUTES:
                add(node, f"atributo no permitido: .{node.attr}")
            elif _IO_ATTR_RE.match(node.attr):
                add(node, f"función de archivos no permitida: .{node.attr} (las tablas ya están en `tables`)")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
            "getattr",
            "hasattr",
        }:
            if node.args:
                target = _root_name(node.args[0])
                if target in module_names:
                    add(node, f"{node.func.id} sobre un módulo no permitido: {target}")
            if len(node.args) > 1:
                attr = node.args[1]
                if not (isinstance(attr, ast.Constant) and isinstance(attr.value, str)):
                    add(node, f"{node.func.id} con nombre de atributo no literal no permitido")
                elif (
                    attr.value.startswith("_")
                    or attr.value in FORBIDDEN_ATTRIBUTES
                    or _IO_ATTR_RE.match(attr.value)
                ):
                    add(node, f"{node.func.id} con atributo no permitido: {attr.value}")
    return errors


def _root_name(node: ast.AST) -> str | None:
    """Nombre raíz de una cadena ``a.b.c`` / ``a[0].b`` (o ``None``)."""
    while isinstance(node, ast.Attribute | ast.Subscript | ast.Call):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


# --- BR-013 / BR-012 (d): preámbulo ----------------------------------------------------------

ALIAS_RE = re.compile(r"^t[1-9]\d*$")
PERMISSION_MESSAGE = "sandbox: acceso a archivos y red bloqueado"


def build_preamble(aliases: Mapping[str, str]) -> str:
    """Preámbulo fijo que antepone el sistema al código generado (BR-013).

    ``aliases`` mapea alias → nombre del archivo Parquet en el directorio de trabajo
    (``{"t1": "t1.parquet"}``). Define ``pd``, ``np`` y ``tables: dict[str, pd.DataFrame]``
    (los dtypes vienen en el Parquet) y, **después** de cargar, bloquea red y archivos
    (BR-012 d). Los módulos de la allowlist se importan antes del bloqueo para que nada
    dependa de ``open`` más tarde.
    """
    for alias, file_name in aliases.items():
        if not ALIAS_RE.fullmatch(alias):
            raise ValueError(f"alias inválido: {alias!r} (se esperaba t1, t2, …)")
        if Path(file_name).name != file_name or not file_name.endswith(".parquet"):
            raise ValueError(f"nombre de archivo inválido para {alias}: {file_name!r}")
    loads = "".join(
        f"    {alias!r}: pd.read_parquet({file_name!r}, engine='pyarrow'),\n"
        for alias, file_name in aliases.items()
    )
    return (
        "# --- preámbulo del sandbox (generado por el sistema) ---\n"
        "import builtins as _sandbox_builtins\n"
        "import socket as _sandbox_socket\n"
        "import collections, datetime, decimal, fractions, functools, itertools, json, math, re, statistics\n"
        "import numpy as np\n"
        "import pandas as pd\n"
        "\n"
        "tables = {\n"
        f"{loads}"
        "}\n"
        "\n"
        "def _sandbox_denied(*args, **kwargs):\n"
        f"    raise PermissionError({PERMISSION_MESSAGE!r})\n"
        "\n"
        "_sandbox_socket.socket = _sandbox_denied\n"
        "_sandbox_socket.create_connection = _sandbox_denied\n"
        "_sandbox_builtins.open = _sandbox_denied\n"
        "del _sandbox_builtins, _sandbox_socket, _sandbox_denied\n"
        "# --- fin del preámbulo ---\n"
    )


# --- BR-012 (b, c, e, f): ejecución ----------------------------------------------------------

MAX_OUTPUT_CHARS = 20_000
_READ_CHUNK = 65_536
_KEEP_BYTES = MAX_OUTPUT_CHARS * 4 + 8  # UTF-8: ≤ 4 bytes por carácter
_ENV_KEYS = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE")  # + TEMP/TMP propios (D-027)

ExecutionStatus = Literal["ok", "error", "timeout", "rejected", "invalid_output"]


class ResultItem(BaseModel):
    """Un resultado de §7.6. ``value``: número finito, lista de números o dict ``str → número``."""

    model_config = ConfigDict(frozen=True)
    part_id: str
    label: str = ""
    value: float | list[float] | dict[str, float]
    unit: str | None = None


class MissingItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    part_id: str
    reason: str


class ParsedOutput(BaseModel):
    """Última línea de stdout del código generado, ya validada (§7.6)."""

    model_config = ConfigDict(frozen=True)
    results: list[ResultItem] = Field(default_factory=list)
    missing: list[MissingItem] = Field(default_factory=list)
    conversions: list[str] = Field(default_factory=list)


class OutputContractError(ValueError):
    """La salida no cumple §7.6; el mensaje (en español) sirve como feedback del reintento."""


class ExecutionResult(BaseModel):
    """Resultado de ``run_in_sandbox`` (FR-036).

    ``status``: ``ok`` (returncode 0 y, si se pidió, salida válida), ``error`` (returncode ≠ 0),
    ``timeout``, ``rejected`` (chequeo AST) o ``invalid_output`` (returncode 0 pero la salida
    no cumple §7.6). ``error`` resume el problema en español (``None`` si ``ok``).
    """

    model_config = ConfigDict(frozen=True)
    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    duration_s: float = 0.0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    parsed: ParsedOutput | None = None
    error: str | None = None
    rejected_reasons: list[str] = Field(default_factory=list)
    pid: int | None = None
    killed_pids: list[int] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated


class _TailReader(threading.Thread):
    """Drena un pipe hasta EOF guardando solo los últimos bytes (evita memoria sin límite)."""

    def __init__(self, stream: IO[bytes]) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.chunks: collections.deque[bytes] = collections.deque()
        self.kept = 0
        self.total = 0

    def run(self) -> None:
        try:
            while chunk := self.stream.read1(_READ_CHUNK):  # type: ignore[attr-defined]
                self.total += len(chunk)
                self.chunks.append(chunk)
                self.kept += len(chunk)
                while self.kept - len(self.chunks[0]) >= _KEEP_BYTES:
                    self.kept -= len(self.chunks.popleft())
        except (OSError, ValueError):
            pass

    def text(self) -> tuple[str, bool]:
        data = b"".join(self.chunks)
        dropped = self.total > len(data)
        if len(data) > _KEEP_BYTES:
            data = data[-_KEEP_BYTES:]
            dropped = True
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
        if len(text) > MAX_OUTPUT_CHARS:
            return text[-MAX_OUTPUT_CHARS:], True
        return text, dropped


def _kill_tree(pid: int) -> list[int]:
    """Mata el proceso y todos sus descendientes (lanzador del venv + intérprete real).

    Devuelve los PID que mató, para poder verificar NFR-004.
    """
    try:
        parent = psutil.Process(pid)
        procs = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        return [pid]
    for proc in procs:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=5)
    return [proc.pid for proc in procs]


def _child_env(temp_dir: Path, parent_env: Mapping[str, str]) -> dict[str, str]:
    # os.environ en Windows normaliza las claves a mayúsculas; un Mapping cualquiera quizá no.
    upper = {key.upper(): value for key, value in parent_env.items()}
    env = {key: upper[key] for key in _ENV_KEYS if upper.get(key)}
    env["TEMP"] = env["TMP"] = str(temp_dir)
    return env


def run_in_sandbox(
    code: str,
    tables: Mapping[str, Path],
    timeout_s: int,
    static_check: bool = True,
    *,
    covered_part_ids: Sequence[str] | None = None,
) -> ExecutionResult:
    """Ejecuta ``code`` en el sandbox de BR-012 con las tablas ``{"t1": Path(...), ...}``.

    ``static_check=False`` solo se usa en tests, para verificar las defensas de runtime.
    Si se pasa ``covered_part_ids``, además valida la salida con ``parse_result_line`` y llena
    ``parsed`` (o devuelve ``status="invalid_output"`` con el motivo en ``error``).
    """
    if timeout_s <= 0:
        raise ValueError(f"timeout_s debe ser positivo: {timeout_s}")
    sources: dict[str, Path] = {}
    for alias, path in tables.items():
        if not ALIAS_RE.fullmatch(alias):
            raise ValueError(f"alias inválido: {alias!r} (se esperaba t1, t2, …)")
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"no existe el Parquet de {alias}: {path}")
        sources[alias] = path

    if static_check:
        reasons = check_code(code)
        if reasons:
            return ExecutionResult(
                status="rejected",
                error="código rechazado por el chequeo de seguridad: " + "; ".join(reasons),
                rejected_reasons=reasons,
            )

    with tempfile.TemporaryDirectory(prefix="sandbox-", ignore_cleanup_errors=True) as root_name:
        root = Path(root_name)
        work, temp = root / "work", root / "tmp"
        work.mkdir()
        temp.mkdir()
        aliases = {alias: f"{alias}.parquet" for alias in sources}
        for alias, path in sources.items():
            shutil.copyfile(path, work / aliases[alias])
        script = root / "main.py"
        script.write_text(build_preamble(aliases) + "\n" + code + "\n", encoding="utf-8")

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        start = time.perf_counter()
        proc = subprocess.Popen(
            [sys.executable, "-I", "-X", "utf8", str(script)],
            cwd=work,
            env=_child_env(temp, os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        assert proc.stdout is not None and proc.stderr is not None
        readers = [_TailReader(proc.stdout), _TailReader(proc.stderr)]
        for reader in readers:
            reader.start()
        timed_out = False
        killed: list[int] = []
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            killed = _kill_tree(proc.pid)
            proc.kill()
            proc.wait()
        except BaseException:  # p. ej. KeyboardInterrupt: nunca dejar el hijo vivo
            _kill_tree(proc.pid)
            raise
        finally:
            for reader in readers:
                reader.join(timeout=5)
            proc.stdout.close()
            proc.stderr.close()
        duration = time.perf_counter() - start

    (stdout, out_trunc), (stderr, err_trunc) = (reader.text() for reader in readers)
    base = {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": None if timed_out else proc.returncode,
        "duration_s": round(duration, 3),
        "stdout_truncated": out_trunc,
        "stderr_truncated": err_trunc,
        "pid": proc.pid,
        "killed_pids": killed,
    }
    if timed_out:
        return ExecutionResult(
            status="timeout", error=f"timeout: la ejecución superó {timeout_s} s y se cortó", **base
        )
    if proc.returncode != 0:
        return ExecutionResult(
            status="error",
            error=f"returncode {proc.returncode}: {summarize_stderr(stderr)}",
            **base,
        )
    if covered_part_ids is None:
        return ExecutionResult(status="ok", **base)
    try:
        parsed = parse_result_line(stdout, covered_part_ids)
    except OutputContractError as exc:
        return ExecutionResult(status="invalid_output", error=f"salida inválida: {exc}", **base)
    return ExecutionResult(status="ok", parsed=parsed, **base)


def summarize_stderr(stderr: str, max_chars: int = 300) -> str:
    """Última línea no vacía de stderr (p. ej. ``KeyError: 'x'``), recortada."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "(sin stderr)"
    last = lines[-1]
    return last if len(last) <= max_chars else last[: max_chars - 1] + "…"


# --- §7.6: contrato de salida ----------------------------------------------------------------


def _is_finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _check_value(part_id: str, value: object) -> None:
    if _is_finite_number(value):
        return
    if isinstance(value, list) and value and all(_is_finite_number(v) for v in value):
        return
    if (
        isinstance(value, dict)
        and value
        and all(isinstance(k, str) and _is_finite_number(v) for k, v in value.items())
    ):
        return
    raise OutputContractError(
        f"el valor de {part_id} debe ser un número finito, una lista de números finitos o un dict "
        f"texto → número finito (se recibió {json.dumps(value, ensure_ascii=False, default=str)[:200]})"
    )


def parse_result_line(stdout: str, covered_part_ids: Iterable[str]) -> ParsedOutput:
    """Valida el contrato de §7.6 sobre la última línea no vacía de ``stdout``.

    Lanza ``OutputContractError`` si: no hay salida o la última línea no es un objeto JSON;
    ``results``/``missing`` no son listas; alguna parte cubierta no aparece o aparece más de una
    vez entre ``results`` y ``missing``; aparece una parte que no estaba cubierta; o un ``value``
    no es finito (``NaN``/``Infinity``) ni lista/dict de números finitos.
    """
    covered = list(dict.fromkeys(covered_part_ids))
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise OutputContractError("no hay salida: la última línea de stdout debe ser el JSON de resultados")
    last = lines[-1].strip()
    try:
        data = json.loads(last)
    except json.JSONDecodeError as exc:
        raise OutputContractError(
            f"la última línea de stdout no es JSON válido ({exc.msg}): {last[:200]}"
        ) from exc
    if not isinstance(data, dict):
        raise OutputContractError(
            "la última línea debe ser un objeto JSON con 'results', 'missing' y 'conversions'"
        )
    for key in ("results", "missing"):
        if not isinstance(data.get(key), list):
            raise OutputContractError(f"falta la lista '{key}' en el JSON de salida")
    conversions = data.get("conversions", [])
    if not isinstance(conversions, list) or not all(isinstance(c, str) for c in conversions):
        raise OutputContractError("'conversions' debe ser una lista de textos")

    seen: collections.Counter[str] = collections.Counter()
    results: list[ResultItem] = []
    missing: list[MissingItem] = []
    for item in data["results"]:
        if not isinstance(item, dict) or not isinstance(item.get("part_id"), str) or "value" not in item:
            raise OutputContractError("cada elemento de 'results' necesita 'part_id' (texto) y 'value'")
        part_id = item["part_id"]
        _check_value(part_id, item["value"])
        label, unit = item.get("label", ""), item.get("unit")
        if not isinstance(label, str) or not (unit is None or isinstance(unit, str)):
            raise OutputContractError(f"'label' y 'unit' de {part_id} deben ser texto")
        seen[part_id] += 1
        results.append(ResultItem(part_id=part_id, label=label, value=item["value"], unit=unit))
    for item in data["missing"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("part_id"), str)
            or not isinstance(item.get("reason"), str)
        ):
            raise OutputContractError("cada elemento de 'missing' necesita 'part_id' y 'reason' (texto)")
        seen[item["part_id"]] += 1
        missing.append(MissingItem(part_id=item["part_id"], reason=item["reason"]))

    absent = [p for p in covered if seen[p] == 0]
    repeated = [p for p in covered if seen[p] > 1]
    unknown = [p for p in seen if p not in covered]
    problems = []
    if absent:
        problems.append(f"faltan las partes {', '.join(absent)} (deben estar en 'results' o en 'missing')")
    if repeated:
        problems.append(f"las partes {', '.join(repeated)} aparecen más de una vez")
    if unknown:
        problems.append(f"partes desconocidas {', '.join(unknown)} (solo se calculan {', '.join(covered)})")
    if problems:
        raise OutputContractError("; ".join(problems))
    return ParsedOutput(results=results, missing=missing, conversions=conversions)
