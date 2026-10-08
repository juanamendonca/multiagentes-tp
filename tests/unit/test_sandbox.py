"""VC-046, VC-048, VC-060..VC-065 y NFR-004: sandbox de ejecución del código generado (BR-012/BR-013).

Los tests que ejecutan código lanzan subprocesos reales (``python -I``), sin red. Los Parquet se
generan en ``tmp_path`` con nombres de columna como los reales de INDEC (``*``, ``°``, ``/``,
espacios, acentos), así que no dependen de ``data/tables/``.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psutil
import pytest

from services.sandbox import (
    ALLOWED_IMPORTS,
    MAX_OUTPUT_CHARS,
    PERMISSION_MESSAGE,
    OutputContractError,
    ParsedOutput,
    build_preamble,
    check_code,
    parse_result_line,
    run_in_sandbox,
)

REGION = "Región"
TOTAL = "Total 31 aglomerados urbanos - Año 2026 - 2° trimestre*"
RATE = "Tasa / % (miles)"
SECRET_VARS = ("LITELLM_API_KEY", "PINECONE_API_KEY", "LANGSMITH_API_KEY")


def _write_table(path: Path) -> Path:
    """Parquet con dtypes como los de ``normalize_table``: ``str`` con faltante, ``float64`` con
    NaN e ``Int64`` (años)."""
    df = pd.DataFrame(
        {
            REGION: pd.array(["GBA", "NOA", "GBA", None], dtype="str"),
            TOTAL: [7.1, 5.2, np.nan, 4.0],
            RATE: [1.0, 2.0, 3.0, 4.0],
            "Año": pd.array([2023, 2024, 2024, None], dtype="Int64"),
        }
    )
    df.to_parquet(path, index=False, engine="pyarrow")
    return path


@pytest.fixture
def table(tmp_path: Path) -> Path:
    return _write_table(tmp_path / "0123456789abcdef-p008-t0.parquet")


# --- VC-046: ejecución y parseo de una salida válida -------------------------------------------


def test_vc046_valid_program_runs_and_its_output_is_parsed(table: Path) -> None:
    """Given código que usa `tables["t1"]` con nombres de columna exactos (`*`, `°`, `/`, acentos)
    e imprime el JSON de §7.6, when se ejecuta, then returncode 0, status ok y `parsed` es el dict."""
    code = f"""
import json
df = tables["t1"]
media = df.groupby({REGION!r})[{TOTAL!r}].mean()
print(df)
print(json.dumps({{
    "results": [{{"part_id": "p1", "label": "Promedio GBA 2° trimestre",
                  "value": round(float(media["GBA"]), 2), "unit": "%"}},
                {{"part_id": "p2", "label": "Suma por región", "value": {{k: float(v) for k, v in
                  df.groupby({REGION!r})[{RATE!r}].sum().items()}}, "unit": "miles"}}],
    "missing": [],
    "conversions": ["porcentaje → fracción"],
}}, ensure_ascii=False))
"""
    result = run_in_sandbox(code, {"t1": table}, timeout_s=30, covered_part_ids=["p1", "p2"])

    assert result.status == "ok", result.stderr
    assert result.returncode == 0 and result.error is None
    expected = {
        "results": [
            {"part_id": "p1", "label": "Promedio GBA 2° trimestre", "value": 7.1, "unit": "%"},
            {"part_id": "p2", "label": "Suma por región", "value": {"GBA": 4.0, "NOA": 2.0}, "unit": "miles"},
        ],
        "missing": [],
        "conversions": ["porcentaje → fracción"],
    }
    assert result.parsed is not None and result.parsed.model_dump() == expected
    assert "Suma por región" in result.stdout  # stdout en UTF-8: acentos intactos


def test_preamble_loads_tables_with_dtypes_from_parquet_and_common_ops_work_after_blocking_open(
    table: Path,
) -> None:
    """Given el preámbulo ya bloqueó `open`, when el código hace operaciones típicas (dtypes,
    groupby, pivot, describe, fechas con zona horaria, módulos de la allowlist), then todo funciona
    y los dtypes son los del Parquet (`str`, `float64`, `Int64`)."""
    code = f"""
import collections, datetime, decimal, fractions, functools, itertools, json, math, re, statistics
import numpy
df = tables["t1"]
print(json.dumps({{c: str(t) for c, t in df.dtypes.items()}}, ensure_ascii=False))
print(df.describe().to_string())
print(df.pivot_table(index={REGION!r}, values={RATE!r}, aggfunc="sum"))
print(df.sort_values({RATE!r}).query("`{RATE}` > 1").shape, df[{TOTAL!r}].isna().sum())
print(pd.Timestamp("2024-01-01", tz="America/Argentina/Buenos_Aires"), statistics.mean([1, 2]))
print(sorted(tables))
"""
    result = run_in_sandbox(code, {"t1": table}, timeout_s=30)

    assert result.status == "ok", result.stderr
    first_line, *_, last_line = result.stdout.strip().splitlines()
    assert json.loads(first_line) == {REGION: "str", TOTAL: "float64", RATE: "float64", "Año": "Int64"}
    assert last_line == "['t1']"
    assert "-03:00" in result.stdout


def test_runtime_error_reports_returncode_and_last_stderr_line(table: Path) -> None:
    """Given código con una columna inexistente, when se ejecuta, then status error, returncode ≠ 0
    y `error` resume el `KeyError` (feedback para el reintento, FR-037)."""
    result = run_in_sandbox('tables["t1"]["No existe"]', {"t1": table}, timeout_s=30)

    assert result.status == "error" and result.returncode not in (0, None)
    assert "KeyError" in (result.error or "") and "KeyError" in result.stderr
    assert result.parsed is None


# --- VC-048: salidas que no cumplen §7.6 --------------------------------------------------------

_VALID = {"results": [{"part_id": "p1", "label": "x", "value": 1.5, "unit": "%"}], "missing": []}


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("", "no hay salida"),
        ("resultado: 12.5\n", "no es JSON"),
        (json.dumps(_VALID) + "\nlisto\n", "no es JSON"),
        ("[1, 2]", "objeto JSON"),
        (json.dumps({"results": []}), "'missing'"),
        (json.dumps({"results": [], "missing": []}), "faltan las partes p1"),
        (json.dumps({**_VALID, "missing": [{"part_id": "p1", "reason": "x"}]}), "más de una"),
        (json.dumps({**_VALID, "missing": [{"part_id": "p9", "reason": "x"}]}), "desconocidas p9"),
        (json.dumps({"results": [{"part_id": "p1", "value": float("nan")}], "missing": []}), "número finito"),
        (json.dumps({"results": [{"part_id": "p1", "value": float("inf")}], "missing": []}), "número finito"),
        (json.dumps({"results": [{"part_id": "p1", "value": "12,5"}], "missing": []}), "número finito"),
        (json.dumps({"results": [{"part_id": "p1", "value": True}], "missing": []}), "número finito"),
        (json.dumps({"results": [{"part_id": "p1", "value": [1, None]}], "missing": []}), "número finito"),
        (json.dumps({"results": [{"part_id": "p1", "value": {"a": math.nan}}], "missing": []}), "finito"),
        (json.dumps({"results": [{"part_id": "p1"}], "missing": []}), "'value'"),
        (json.dumps({"results": [], "missing": [{"part_id": "p1"}]}), "'reason'"),
        (json.dumps({**_VALID, "conversions": "miles"}), "conversions"),
    ],
    ids=[
        "vacia",
        "sin-json-final",
        "json-no-es-la-ultima-linea",
        "no-es-objeto",
        "sin-missing",
        "falta-parte-cubierta",
        "parte-repetida",
        "parte-desconocida",
        "nan",
        "infinito",
        "texto",
        "booleano",
        "lista-con-null",
        "dict-con-nan",
        "sin-value",
        "missing-sin-reason",
        "conversions-no-lista",
    ],
)
def test_vc048_invalid_outputs_are_rejected(stdout: str, message: str) -> None:
    """Given una salida que no cumple §7.6 (sin JSON final, parte cubierta ausente, `NaN`, …),
    when se parsea con la parte p1 cubierta, then OutputContractError con un motivo explícito."""
    with pytest.raises(OutputContractError, match=message):
        parse_result_line(stdout, ["p1"])


def test_parse_result_line_accepts_valid_variants() -> None:
    """Given resultados con número, lista y dict, una parte en `missing` y sin `conversions`,
    then se parsea (la última línea no vacía es el JSON; lo anterior se ignora)."""
    stdout = "tabla impresa\n\n" + json.dumps(
        {
            "results": [
                {"part_id": "p1", "label": "a", "value": 3, "unit": None},
                {"part_id": "p2", "label": "b", "value": [1, 2.5]},
                {"part_id": "p3", "label": "c", "value": {"GBA": 1}},
            ],
            "missing": [{"part_id": "p4", "reason": "La tabla cubre 2019-2023; falta 2024"}],
        }
    ) + "\n\n"
    parsed = parse_result_line(stdout, ["p1", "p2", "p3", "p4"])

    assert isinstance(parsed, ParsedOutput)
    assert [r.value for r in parsed.results] == [3.0, [1.0, 2.5], {"GBA": 1.0}]
    assert parsed.missing[0].part_id == "p4" and parsed.conversions == []


def test_vc048_sandbox_marks_nan_output_as_invalid(table: Path) -> None:
    """Given código que termina bien pero imprime `NaN` como valor, when se ejecuta con partes
    cubiertas, then status `invalid_output` y el motivo en `error` (dispara reintento, FR-037)."""
    code = (
        "import json\n"
        'print(json.dumps({"results": [{"part_id": "p1", "value": float("nan")}], "missing": []}))'
    )
    result = run_in_sandbox(code, {"t1": table}, timeout_s=30, covered_part_ids=["p1"])

    assert result.returncode == 0 and result.status == "invalid_output"
    assert result.parsed is None and "número finito" in (result.error or "")


# --- VC-060: chequeo AST ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("import os", "import no permitido: os"),
        ("import requests", "import no permitido: requests"),
        ('open("x")', "uso no permitido de open"),
        ('__import__("os")', "uso no permitido de __import__"),
        ("().__class__.__bases__", "atributo no permitido: .__class__"),
        ("from subprocess import run", "import no permitido: subprocess"),
        ("import pandas.io.common, socket", "import no permitido: socket"),
        ('eval("1+1")', "uso no permitido de eval"),
        ('exec("x = 1")', "uso no permitido de exec"),
        ('compile("1", "f", "eval")', "uso no permitido de compile"),
        ("input()", "uso no permitido de input"),
        ("globals()", "uso no permitido de globals"),
        ("f = open", "uso no permitido de open"),
        ('__builtins__["open"]', "nombre no permitido: __builtins__"),
        ('getattr(pd, "read_csv")', "getattr sobre un módulo no permitido: pd"),
        ('import numpy as n\ngetattr(n.lib, "x")', "getattr sobre un módulo no permitido: n"),
        ('getattr(tables["t1"], "__class__")', "getattr con atributo no permitido"),
        ('pd.read_csv("C:/datos.csv")', "función de archivos no permitida: .read_csv"),
        ('tables["t1"].to_csv("salida.csv")', "función de archivos no permitida: .to_csv"),
        ('np.save("x", [1])', "función de archivos no permitida: .save"),
        ("from pandas import read_parquet", "función de archivos no permitida: read_parquet"),
        ('pd.io.common.os.system("calc")', "atributo no permitido: .os"),
        ("pd.core.common.builtins", "atributo no permitido: .builtins"),
        ('getattr(tables["t1"], "sys")', "getattr con atributo no permitido: sys"),
        ("from . import x", "import relativo"),
        ("def f(:\n  pass", "error de sintaxis"),
    ],
)
def test_vc060_static_check_rejects_forbidden_code(code: str, reason: str, table: Path) -> None:
    """Given código con imports fuera de la allowlist, `open`, `__import__`, atributos `__*`, …,
    when se pide ejecutarlo, then se rechaza antes de ejecutar y el motivo está en `error`."""
    assert any(reason in r for r in check_code(code)), check_code(code)

    result = run_in_sandbox(code, {"t1": table}, timeout_s=30)

    assert result.status == "rejected"
    assert result.pid is None and result.returncode is None  # nunca se lanzó el subproceso
    assert reason in (result.error or "")


def test_vc060_static_check_accepts_typical_analysis_code() -> None:
    """Given código pandas típico con imports de la allowlist, f-strings, lambdas, `getattr` sobre
    un objeto y `if __name__ == "__main__"`, then el chequeo AST no lo rechaza."""
    code = f"""
import json, math
import numpy as np
import pandas as pd
from collections import Counter
from datetime import date
from decimal import Decimal

def main():
    df = tables["t1"]
    s = df.groupby({REGION!r}, dropna=True)[{TOTAL!r}].agg(lambda x: x.mean())
    top = s.sort_values(ascending=False).head(1)
    largo = getattr(df, "shape")[0]
    out = {{"results": [{{"part_id": "p1", "label": f"Máximo {{top.index[0]}}",
                         "value": float(top.iloc[0])}}],
            "missing": [], "conversions": [], "n": largo, "c": dict(Counter("aab")), "d": str(date.today())}}
    print(json.dumps(out, ensure_ascii=False), df.to_dict(), df.to_json(), Decimal("1.5"), math.pi)

if __name__ == "__main__":
    main()
"""
    assert check_code(code) == []
    assert {"pandas", "numpy", "json", "math", "statistics", "datetime"} <= ALLOWED_IMPORTS


# --- VC-061: entorno mínimo ----------------------------------------------------------------------


def test_vc061_child_does_not_see_parent_secrets(table: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Given las variables de `.env` cargadas en el proceso padre, when el código (sin chequeo AST)
    lista `os.environ`, then no aparece ninguna clave: solo las variables de sistema del entorno
    mínimo (D-027). `PYTHONUSERBASE` la agrega el lanzador del Python de Microsoft Store (no viene
    del padre: `-I` ignora el site de usuario igual)."""
    for var in SECRET_VARS:
        monkeypatch.setenv(var, "valor-de-prueba-no-real")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://proxy.invalid")

    code = "import os, json\nprint(json.dumps(sorted(os.environ)))"
    result = run_in_sandbox(code, {"t1": table}, timeout_s=30, static_check=False)

    assert result.status == "ok", result.stderr
    names = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert not names & {*SECRET_VARS, "LITELLM_BASE_URL"}
    allowed = {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "PYTHONUSERBASE"}
    assert {name.upper() for name in names} <= allowed


# --- VC-062: guardas de red y archivos en runtime -------------------------------------------------


_GUARDS_CODE = """
import json, socket
outcome = {}

def attempt(name, action):
    try:
        action()
        outcome[name] = "permitido"
    except PermissionError as exc:
        outcome[name] = f"PermissionError: {exc}"
    except Exception as exc:
        outcome[name] = type(exc).__name__

attempt("socket", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("1.1.1.1", 80)))
attempt("create_connection", lambda: socket.create_connection(("1.1.1.1", 80), timeout=2))
attempt("open_escritura", lambda: open("escrito.txt", "w").write("x"))
attempt("to_csv", lambda: tables["t1"].to_csv("C:/Windows/Temp/fuga.csv"))
print(json.dumps(outcome))
open("C:/Windows/win.ini").read()
"""


def test_vc062_runtime_guards_block_network_and_files(table: Path) -> None:
    """Given el chequeo AST desactivado (modo test), when el código abre un socket a 1.1.1.1:80,
    usa `create_connection`, escribe un archivo (con `open` o `to_csv`) o hace
    `open("C:/Windows/win.ini")`, then cada intento lanza PermissionError (el último, sin
    capturar, queda en stderr)."""
    result = run_in_sandbox(_GUARDS_CODE, {"t1": table}, timeout_s=30, static_check=False)

    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    blocked = f"PermissionError: {PERMISSION_MESSAGE}"
    assert outcome == dict.fromkeys(("socket", "create_connection", "open_escritura", "to_csv"), blocked)
    assert result.status == "error"
    assert "PermissionError" in result.stderr and PERMISSION_MESSAGE in result.stderr


# --- VC-063 / NFR-004: timeout ----------------------------------------------------------------


def test_vc063_infinite_loop_times_out_and_child_is_killed(table: Path) -> None:
    """Given `while True: pass` con timeout 2 s, when se ejecuta, then el resultado es `timeout` en
    ≤ timeout + 2 s y no sobrevive ningún proceso del árbol (lanzador del venv + intérprete real)."""
    start = time.perf_counter()
    result = run_in_sandbox("while True: pass", {"t1": table}, timeout_s=2)
    elapsed = time.perf_counter() - start

    assert result.status == "timeout" and result.returncode is None
    assert "timeout" in (result.error or "")
    assert elapsed <= 2 + 2, elapsed
    assert result.pid is not None and result.pid in result.killed_pids
    assert not [pid for pid in result.killed_pids if psutil.pid_exists(pid)]


# --- VC-064: directorio de trabajo --------------------------------------------------------------


def test_vc064_working_directory_contains_only_selected_parquets(tmp_path: Path) -> None:
    """Given 3 tablas en el almacén y 2 seleccionadas, when el código (sin chequeo AST) lista su
    directorio de trabajo, then solo están `t1.parquet` y `t2.parquet` (ni el programa ni la 3.ª)."""
    store = tmp_path / "tables"
    store.mkdir()
    paths = [_write_table(store / f"0123456789abcdef-p00{i}-t0.parquet") for i in (1, 2, 3)]

    code = "import os, json\nprint(json.dumps(sorted(os.listdir('.'))))"
    result = run_in_sandbox(code, {"t1": paths[0], "t2": paths[2]}, timeout_s=30, static_check=False)

    assert result.status == "ok", result.stderr
    assert json.loads(result.stdout.strip()) == ["t1.parquet", "t2.parquet"]


# --- VC-065: truncado de salida -------------------------------------------------------------------


def test_vc065_large_output_is_truncated_keeping_the_final_json_line(table: Path) -> None:
    """Given código que imprime 100 000 caracteres y después el JSON (y 100 000 en stderr), when se
    ejecuta, then stdout y stderr guardan 20 000 caracteres cada uno (el final), quedan marcados
    como truncados y la última línea de stdout sigue siendo parseable."""
    code = (
        'import json\nprint("x" * 100_000)\n'
        'print("ñ" * 30_000, file=__import__("sys").stderr)\n'
        'print(json.dumps({"results": [{"part_id": "p1", "value": 1}], "missing": []}))'
    )
    result = run_in_sandbox(
        code, {"t1": table}, timeout_s=30, static_check=False, covered_part_ids=["p1"]
    )

    assert len(result.stdout) == MAX_OUTPUT_CHARS and result.stdout_truncated
    assert len(result.stderr) == MAX_OUTPUT_CHARS and result.stderr_truncated and result.truncated
    assert result.stderr.strip() == "ñ" * (MAX_OUTPUT_CHARS - 1)  # UTF-8 multibyte, sin cortes
    assert result.status == "ok" and result.parsed is not None


def test_short_output_is_not_truncated() -> None:
    """Given una salida corta, then se guarda entera y sin marca de truncado."""
    from services.sandbox import _TailReader  # detalle interno: sin subproceso

    class _Stream:
        def __init__(self, data: bytes) -> None:
            self.chunks = [data[i : i + 7] for i in range(0, len(data), 7)]

        def read1(self, _size: int) -> bytes:
            return self.chunks.pop(0) if self.chunks else b""

    reader = _TailReader(_Stream("año 2° *\r\nok\r\n".encode()))  # type: ignore[arg-type]
    reader.run()
    assert reader.text() == ("año 2° *\nok\n", False)


# --- validaciones de entrada ----------------------------------------------------------------------


def test_build_preamble_defines_tables_then_blocks_open_and_sockets() -> None:
    """Given alias t1/t2, then el preámbulo carga cada Parquet con `pd.read_parquet` antes de
    reemplazar `socket.socket`, `socket.create_connection` y `builtins.open`."""
    preamble = build_preamble({"t1": "t1.parquet", "t2": "t2.parquet"})

    load = preamble.index("'t2': pd.read_parquet('t2.parquet'")
    guards = ("_sandbox_socket.socket =", "_sandbox_socket.create_connection =", "_sandbox_builtins.open =")
    for guard in guards:
        assert preamble.index(guard) > load
    compile(preamble, "preamble", "exec")


@pytest.mark.parametrize(
    "aliases",
    [{"tabla": "t1.parquet"}, {"t0": "t0.parquet"}, {"t1": "../t1.parquet"}, {"t1": "t1.csv"}],
)
def test_build_preamble_rejects_invalid_aliases_and_file_names(aliases: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        build_preamble(aliases)


def test_run_in_sandbox_rejects_missing_parquet_and_bad_alias(tmp_path: Path, table: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_in_sandbox("print(1)", {"t1": tmp_path / "no-existe.parquet"}, timeout_s=5)
    with pytest.raises(ValueError):
        run_in_sandbox("print(1)", {"x": table}, timeout_s=5)
    with pytest.raises(ValueError):
        run_in_sandbox("print(1)", {"t1": table}, timeout_s=0)
