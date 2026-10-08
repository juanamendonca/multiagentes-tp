# Iteración 6 — Sandbox de ejecución de código

- **Fecha:** 2026-10-07 · **Rama:** `feat/qa-tablas`
- **SHA:** ver `git log -1 --format=%H -- specs/qa-tablas-pdf-spec-plan-artifacts/iterations/06-sandbox.md` (D-003)
- **Commit:** `feat(analista): sandbox de subproceso con chequeo AST, guardas de red/FS y timeout`

## Criterios de éxito

Corrida final con todo en stage, justo antes del commit (PowerShell desde la raíz). Sin red ni LLM en esta iteración.

| # | Criterio | Comando | Resultado |
|---|---|---|---|
| 1 | VC-046, VC-048, VC-060..VC-065 | `.venv\Scripts\python -m pytest tests/unit/test_sandbox.py -v` | PASS (61 passed, ~21 s). VC-046: `test_vc046_valid_program_runs_and_its_output_is_parsed` (columnas con `*`, `°`, `/`, acentos; `parsed.model_dump()` == dict, returncode 0); VC-048: `test_vc048_invalid_outputs_are_rejected[...]` (17 casos: sin JSON final, parte cubierta ausente, repetida, desconocida, `NaN`, `Infinity`, texto, bool, …) + `test_vc048_sandbox_marks_nan_output_as_invalid`; VC-060: `test_vc060_static_check_rejects_forbidden_code[...]` (26 casos, incluidos los 5 de la spec; `pid`/`returncode` `None` = no se ejecutó) + `..._accepts_typical_analysis_code`; VC-061: `test_vc061_child_does_not_see_parent_secrets`; VC-062: `test_vc062_runtime_guards_block_network_and_files` (socket a 1.1.1.1:80, `create_connection`, `open` de escritura, `to_csv`, y `open("C:/Windows/win.ini")` sin capturar → `PermissionError` en stderr); VC-063: `test_vc063_infinite_loop_times_out_and_child_is_killed`; VC-064: `test_vc064_working_directory_contains_only_selected_parquets`; VC-065: `test_vc065_large_output_is_truncated_keeping_the_final_json_line` |
| 2 | NFR-004 (VC-063 mide ≤ timeout + 2 s y el PID hijo ya no existe) | `.venv\Scripts\python -m pytest tests/unit/test_sandbox.py -k vc063 --durations=1` | PASS (2,12 s con timeout 2 s; `killed_pids` = lanzador del venv + alias de la Store + intérprete real, ninguno existe según `psutil.pid_exists`) |
| 3 | Suite sin red | `.venv\Scripts\python -m pytest -m "not integration"` | PASS (256 passed, 14 deselected, 34,6 s; NFR-011 ≤ 60 s) |
| 4 | Lint | `.venv\Scripts\ruff check .` | PASS ("All checks passed!") |
| 5 | Secretos | `git grep -nE "(sk-[A-Za-z0-9]{16,}\|pcsk[_]\|lsv2[_])"` y `git grep --cached -nE ...`; `git ls-files .env` | PASS (vacíos, exit 1; `.env` no trackeado) |
| 6 | Artefactos | revisión | PASS: este record, `CONTEXT.md` actualizado, D-026..D-028 |

Fallas durante la iteración (corregidas antes de la corrida final): VC-064 encontró un directorio `%SystemDrive%` en el cwd del hijo y VC-061 una variable `PYTHONUSERBASE` (D-027); la suite tardó una vez 104 s por carga del equipo, así que se agruparon las ejecuciones de VC-062/VC-065 (de 16 a 11 subprocesos). Verificación manual extra: `run_in_sandbox` sobre el Parquet real `b0a700bac4db8192-p005-t0` (columna `Total 31 aglomerados urbanos - Año 2026 - 2° trimestre*`) → `ok`, valor 48.9 igual al de `pd.read_parquet`.

## Archivos

Agregados: `services/sandbox.py`, `tests/unit/test_sandbox.py`, este record.
Cambiados: `requirements.txt` (`psutil==7.2.2`, ya instalado en `.venv`), `CONTEXT.md`, `DECISIONS.md` (D-026..D-028).

## Desvíos del plan / spec

1. **`SYSTEMDRIVE` en el entorno del hijo** (BR-012 c dice `PATH`, `SYSTEMROOT`, `TEMP`; el plan suma `TMP`): sin ella Windows crea `%SystemDrive%\ProgramData\Microsoft\Windows\Caches` en el cwd en cada arranque (D-027). No es un secreto.
2. **`run_in_sandbox` usa `Popen` + `psutil`** en lugar de `subprocess.run(timeout=...)`: el `python.exe` del venv es un lanzador y `run` dejaba vivo al intérprete real (D-027).
3. **Kwarg extra `covered_part_ids`** en `run_in_sandbox` (la firma de §7.7 se mantiene) y campos extra en `ExecutionResult` (`status`, `killed_pids`, …) (D-028).
4. **Chequeo AST más estricto que BR-012 (a)**: builtins de introspección, `__*` como nombre, funciones de E/S de pandas/numpy y atributos "pivote" (`pd.io.common.os…` pasaba el chequeo literal) (D-026).
5. **`io.open` no se bloquea** (solo `builtins.open`, como dice la spec): bloquearlo rompía `pd.Timestamp(..., tz=...)`.
6. **Truncado conserva el final** de stdout/stderr (BR-012 f no fija cuál parte).
7. **Main.py fuera del cwd** (`<tmp>/main.py`, cwd `<tmp>/work/`) para cumplir VC-064 literalmente.

## Problemas encontrados / gotchas

- Windows + Python de Microsoft Store: `.venv\Scripts\python.exe` → proceso alias → intérprete real (3 PID). `-I` sí ve las site-packages del venv (pandas/pyarrow importables, verificado con `sys.path`).
- Con `-I` se ignoran `PYTHONIOENCODING`/`PYTHONUTF8`: se usa `-X utf8`; si no, `°`/`ñ` salen en cp1252 o fallan.
- Bloquear `builtins.open` después de cargar no rompe groupby, pivot, describe, query, merge, aritmética, `print(df)`, `json.dumps` ni imports de la allowlist (los imports usan `io.open_code`, no `open`); igual el preámbulo preimporta la allowlist. `pd.read_parquet`/`to_parquet` usan pyarrow nativo: solo el AST los frena.
- `print(df)` en el hijo abrevia columnas (`...`) porque no hay terminal (ancho 80): para depurar conviene `df.to_string()` o imprimir columnas puntuales.
- Costo: cada ejecución ~1–2,5 s (arranque + `import pandas`); un timeout de 2 s puede vencer antes de que el código del usuario empiece.

## Texto para el prompt de codegen (Iteración 8)

Usar tal cual (en español), reemplazando `{timeout}` por `settings.sandbox_timeout_s` y `{part_ids}` por las partes `covered`:

```text
## Cómo acceder a los datos
- El programa ya tiene definidas estas variables; no las vuelvas a crear ni leas archivos:
  - `tables`: dict[str, pandas.DataFrame] con las tablas seleccionadas, por alias: `tables["t1"]`, `tables["t2"]`, …
  - `pd` (pandas) y `np` (numpy).
- Usá los nombres de columna EXACTOS listados abajo, siempre con `df["..."]` (nunca `df.nombre`): incluyen espacios, acentos, `°`, `*`, `/` y `%`.
- Los tipos ya vienen correctos: numéricas `float64`/`Int64`, texto `str`. Un dato faltante es `NaN`/`<NA>`, nunca 0. No conviertas texto a número.
- Solo podés importar: pandas, numpy, math, statistics, json, datetime, re, collections, itertools, functools, decimal, fractions.
- Prohibido: leer o escribir archivos (`open`, `pd.read_*`, `to_csv`, `np.load`, …), red, `eval`/`exec`, `getattr` sobre módulos y atributos que empiecen con `__`. El código que los use se rechaza sin ejecutarse.
- Límite de tiempo: {timeout} s. La salida se recorta a los últimos 20 000 caracteres: no imprimas tablas enteras.

## Formato de salida
Imprimí como ÚLTIMA línea de stdout, en una sola línea, este JSON (usá `print(json.dumps(salida, ensure_ascii=False))`):
{"results": [{"part_id": "p1", "label": "<qué es el valor>", "value": <número>, "unit": "<unidad>"}],
 "missing": [{"part_id": "p2", "reason": "<qué dato falta y por qué>"}],
 "conversions": ["<conversión aplicada, p. ej. miles → millones>"]}
Reglas:
- Cada parte a calcular ({part_ids}) aparece exactamente una vez: en `results` si la calculaste o en `missing` si falta el dato. No agregues otras partes.
- `value` es un número finito, una lista de números o un objeto {"texto": número}. Convertí con `float(...)` (los enteros de numpy/pandas no se serializan a JSON). Nunca NaN ni infinito: si el resultado daría NaN, la parte va a `missing`.
- No imprimas nada después de esa línea.
```

## For the next iteration

- **Iteración 7** no usa el sandbox (stubs de `generate_code`/`execute`), pero el estado `execution` debería tipar como `ExecutionResult | None` (Pydantic, serializable para LangSmith con `model_dump()`), y `decide_status` puede leer `execution.parsed.results`/`.missing` (BR-015: una parte cuenta solo si está en `results`, es `covered` y `execution.ok`).
- **Iteración 8:** `execute` = `run_in_sandbox(code, {f"t{i}": store.parquet_path(id) for i, id in enumerate(selected, 1)}, settings.sandbox_timeout_s, covered_part_ids=covered_ids)`; reintentar si `not result.ok` con `result.error` como feedback (ya trae motivo del AST, `"returncode N: KeyError: ..."`, timeout o violación de §7.6); si hace falta más contexto, la cola de `result.stderr`. El orden de alias debe coincidir con el del prompt. Agregar un test del grafo con el sandbox real (1–2 ejecuciones; cada una cuesta ~1–2 s) y el resto con un `run_in_sandbox` fake.
- Extraer el bloque ```` ```python ```` de la respuesta antes de llamar al sandbox; `check_code` da `error de sintaxis (línea N)` si el LLM devuelve texto.
- README final (Iteración 11): copiar las limitaciones de D-026 (no es frontera de seguridad del SO; sin límite de memoria; listas de bloqueo).
