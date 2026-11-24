"""WebAssembly UDF execution engine for PipelineIQ.

Architecture:
  - One WasmExecutor instance per Celery worker process (module-level singleton)
  - Module cache: SHA256(wasm_bytes) -> compiled Module (compilation is expensive ~100ms)
  - Per-execution: fresh Store (owns fuel budget, memory, instantiated module)
  - Security: no WASI imports, no filesystem, no network, no env vars
  - CPU budget: 10M fuel total per step, 1K fuel per row
  - Stack limit: not configurable in wasmtime 44.x; fuel is the primary budget mechanism

Why one Engine per worker?
  Module compilation happens in Engine.compile() which is thread-safe and
  expensive. Compiled modules are cached in _module_cache by SHA256 of the bytes.
  Reusing the same engine for compilation means the cache is effective.

Why a fresh Store per execution?
  Store owns all runtime state: fuel, memory, table entries, global values.
  Reusing a Store across different pipeline runs would leak fuel budget and
  potentially leak execution state between users.
"""

from __future__ import annotations

import hashlib
import logging
import signal
import struct
from collections import OrderedDict
from dataclasses import dataclass

import pyarrow as pa
from fastapi import HTTPException
from wasmtime import Config, Engine, Func, Linker, Memory, Module, Store, Trap

logger = logging.getLogger(__name__)

FUEL_PER_STEP = 10_000_000
FUEL_PER_ROW = 1_000
VALIDATION_FUEL = 100_000
# M-130: absolute ceiling for a single step's fuel budget. The per-row
# slice is total // rows (floored at FUEL_PER_ROW) so a configured budget
# is honored across rows instead of being reset to a fixed trickle.
MAX_STEP_FUEL = 100_000_000
MAX_CACHED_MODULES = 100
MAX_WASM_MEMORY_BYTES = 16 * 1024 * 1024
MAX_WASM_TABLE_ELEMENTS = 10_000
WASM_EXECUTION_TIMEOUT_SECONDS = 30
# M-036: vectorized batch execution. Per-row wasmtime FFI calls cost ~20us
# each (~46k rows/s), so large tables are executed through a tiny generated
# "batch driver" Wasm module: inputs are copied into the driver's linear
# memory once per chunk and a single exported `run` call loops over the rows
# *inside* Wasm (internal calls are ~100x cheaper than host FFI calls).
# A trap inside a batch aborts that chunk, which is then re-executed row by
# row (the legacy slow path) so M-012 per-row trap isolation and M-130 fuel
# semantics are bit-for-bit preserved.
WASM_BATCH_CHUNK_ROWS = 4096
WASM_BATCH_MAX_BYTES = 2 * 1024 * 1024
WASM_BATCH_DRIVER_PAGES = 64  # 4 MiB of linear memory for batch I/O


@dataclass
class WasmValidationResult:
    valid: bool
    exported_functions: list[str]
    error: str | None = None


class _WasmTimeout(Exception):
    pass


def _build_batch_driver_wat(arity: int) -> str:
    """Build WAT for a batch-driver module wrapping a ``(f64 * arity) -> f64`` UDF.

    The driver imports the user function as ``u.f``, exposes its linear
    memory as ``mem`` and exports ``run(in_ptr, out_ptr, n)`` which maps the
    UDF over ``n`` rows: column ``k`` lives at ``in_ptr + k*n*8`` (f64
    little-endian), results are written to ``out_ptr + i*8``. The module is
    pure compute (no WASI imports) so the sandbox boundary is unchanged.
    """
    params = " ".join(["f64"] * arity)
    base_locals = " ".join(f"(local $b{k} i32)" for k in range(arity))
    base_inits = "\n".join(
        f"    (local.set $b{k} "
        f"(i32.add (local.get $in) (i32.mul (local.get $n) (i32.const {k * 8}))))"
        for k in range(arity)
    )
    loads = "\n".join(
        f"      (f64.load (i32.add (local.get $b{k}) (local.get $o)))" for k in range(arity)
    )
    return f"""(module
  (import "u" "f" (func $f (param {params}) (result f64)))
  (memory (export "mem") {WASM_BATCH_DRIVER_PAGES})
  (func (export "run") (param $in i32) (param $out i32) (param $n i32)
    (local $i i32)
    (local $o i32)
    {base_locals}
{base_inits}
    (local.set $i (i32.const 0))
    (local.set $o (i32.const 0))
    (block $exit
      (loop $l
        (br_if $exit (i32.ge_u (local.get $i) (local.get $n)))
        (i32.add (local.get $out) (local.get $o))
{loads}
        (call $f)
        (f64.store)
        (local.set $o (i32.add (local.get $o) (i32.const 8)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))))"""


def _timeout_handler(signum, frame):
    raise _WasmTimeout("WASM execution exceeded timeout")


class WasmExecutor:
    """Executes Wasm UDFs against Arrow Tables."""

    def __init__(self) -> None:
        config = Config()
        config.consume_fuel = True
        self._engine = Engine(config)
        self._module_cache: OrderedDict[str, Module] = OrderedDict()
        # M-036: batch-driver modules keyed by UDF arity (param count).
        self._batch_drivers: dict[int, Module] = {}
        logger.info("WasmExecutor initialized with fuel-enabled Engine")

    def execute(
        self,
        table: pa.Table,
        step,
        wasm_bytes: bytes | bytearray,
        fuel_budget: int | None = None,
    ) -> pa.Table:
        """Execute a Wasm UDF against every row of an Arrow Table.

        The Wasm function receives input_columns as f64 arguments and returns f64.
        The return value is added as a new column (output_column).
        Rows where the Wasm function raises an error receive None in the output.

        M-012: a trap isolates to the output column only — input columns are
        taken verbatim from the input table and are never nulled by row
        failures. ``output_column`` must differ from every input column;
        aliasing is rejected loudly instead of duplicating/corrupting rows.

        M-130: ``fuel_budget`` (explicit arg, else the step's fuel_budget
        attr, else FUEL_PER_STEP) is the total step budget, distributed
        across rows as total // num_rows (floored at FUEL_PER_ROW) so
        non-trivial UDFs run instead of being starved by a fixed trickle.
        """
        function_name = getattr(step, "function", None) or step.get("function")
        input_columns = getattr(step, "input_columns", []) or step.get("input_columns", [])
        output_column = getattr(step, "output_column", None) or step.get("output_column")

        if not function_name:
            raise ValueError("wasm_compute step missing 'function' field")
        if not input_columns:
            raise ValueError("wasm_compute step missing 'input_columns' field")
        if not output_column:
            raise ValueError("wasm_compute step missing 'output_column' field")

        if output_column in list(input_columns):
            raise ValueError(
                f"wasm_compute step output_column '{output_column}' must differ "
                f"from input_columns {list(input_columns)}"
            )

        missing_cols = [c for c in input_columns if c not in table.schema.names]
        if missing_cols:
            raise ValueError(
                f"wasm_compute step input_columns not found in data: {missing_cols}. "
                f"Available columns: {table.schema.names}"
            )

        if fuel_budget is None:
            if isinstance(step, dict):
                fuel_budget = step.get("fuel_budget")
            else:
                fuel_budget = getattr(step, "fuel_budget", None)
        try:
            total_fuel = int(fuel_budget) if fuel_budget is not None else FUEL_PER_STEP
        except (TypeError, ValueError):
            total_fuel = FUEL_PER_STEP
        total_fuel = max(FUEL_PER_ROW, min(total_fuel, MAX_STEP_FUEL))
        num_rows = table.num_rows
        per_row_fuel = max(FUEL_PER_ROW, total_fuel // max(1, num_rows))

        module = self._get_or_compile(wasm_bytes)

        store = Store(self._engine)
        store.set_limits(
            memory_size=MAX_WASM_MEMORY_BYTES,
            table_elements=MAX_WASM_TABLE_ELEMENTS,
            instances=2,
            tables=1,
            memories=2,
        )
        store.set_fuel(total_fuel)

        linker = Linker(self._engine)
        instance = linker.instantiate(store, module)

        try:
            wasm_func = instance.exports(store)[function_name]
        except KeyError as exc:
            available = list(instance.exports(store).keys())
            raise ValueError(
                f"Function '{function_name}' not found in Wasm module. "
                f"Exported functions: {available}"
            ) from exc

        if not callable(wasm_func):
            raise ValueError(f"Export '{function_name}' is not a callable function")

        # M-012: inputs come straight from the table — row failures can only
        # ever affect the output list, never the input data.
        # M-036: inputs are converted to float columns once up front (a
        # conversion failure marks the row invalid -> None, exactly as the
        # old per-row loop did) so both the batch fast path and the per-row
        # slow path share identical input semantics.
        arity = len(input_columns)
        raw_cols = [table.column(col).to_pylist() for col in input_columns]
        float_cols: list[list[float]] = []
        row_valid = bytearray(b"\x01" * num_rows)
        for pylist in raw_cols:
            fcol: list[float] = [0.0] * num_rows
            for i, v in enumerate(pylist):
                try:
                    fcol[i] = float(v)
                except (TypeError, ValueError):
                    row_valid[i] = 0
            float_cols.append(fcol)

        output_values: list[float | None] = [None] * num_rows
        error_count = 0
        max_errors = max(10, num_rows // 10)

        # M-036: batch fast path. The driver is only usable when the user
        # function's type matches (f64 * arity) -> f64; any setup failure
        # (e.g. an i32-typed export) disables batching and the execution
        # falls back to the legacy per-row path with unchanged behavior.
        batch_run: Func | None = None
        batch_mem: Memory | None = None
        if num_rows > 0 and arity >= 1:
            try:
                linker.define(store, "u", "f", wasm_func)
                driver_instance = linker.instantiate(store, self._get_batch_driver(arity))
                driver_exports = driver_instance.exports(store)
                run_export = driver_exports["run"]
                mem_export = driver_exports["mem"]
                if isinstance(run_export, Func) and isinstance(mem_export, Memory):
                    batch_run = run_export
                    batch_mem = mem_export
                else:
                    logger.debug("Wasm batch driver exports have unexpected types")
            except Exception:
                logger.debug("Wasm batch driver unavailable; using per-row path")
                batch_run = None
                batch_mem = None

        bytes_per_row = (arity + 1) * 8
        chunk_rows = max(1, min(WASM_BATCH_CHUNK_ROWS, WASM_BATCH_MAX_BYTES // bytes_per_row))

        def _run_rows_slow(lo: int, hi: int) -> None:
            """Legacy per-row execution for rows [lo, hi)."""
            nonlocal error_count
            for i in range(lo, hi):
                if not row_valid[i]:
                    output_values[i] = None
                    continue
                args = [fcol[i] for fcol in float_cols]
                try:
                    store.set_fuel(per_row_fuel)
                    result = wasm_func(store, *args)
                    output_values[i] = float(result) if result is not None else None
                except Trap as trap:
                    error_count += 1
                    if error_count > max_errors:
                        raise RuntimeError(
                            f"WASM execution failed on too many rows ({error_count}). "
                            f"Last trap: {trap}"
                        ) from trap
                    logger.debug("Wasm trap on row (fuel exhausted or invalid operation)")
                    output_values[i] = None
                except _WasmTimeout:
                    raise
                except Exception as e:
                    error_count += 1
                    if error_count > max_errors:
                        raise RuntimeError(
                            f"WASM execution failed on too many rows ({error_count}). "
                            f"Last error: {e}"
                        ) from e
                    logger.debug("Wasm execution error on row: %s", e)
                    output_values[i] = None

        def _run_chunk_fast(lo: int, hi: int) -> bool:
            """Execute rows [lo, hi) via one batched driver call.

            Returns True on success. Any Trap (row trap, fuel exhaustion) or
            host error returns False so the caller can retry the chunk row
            by row, preserving M-012 isolation and M-130 fuel semantics.
            """
            assert batch_run is not None and batch_mem is not None
            n = hi - lo
            in_size = arity * n * 8
            try:
                payload = bytearray(in_size)
                for k, fcol in enumerate(float_cols):
                    payload[k * n * 8 : (k + 1) * n * 8] = struct.pack(f"<{n}d", *fcol[lo:hi])
                # M-130: the chunk owns exactly per_row_fuel per row it
                # covers, so the step budget is distributed across rows
                # exactly as in the per-row path.
                store.set_fuel(per_row_fuel * n)
                batch_mem.write(store, bytes(payload), 0)
                batch_run(store, 0, in_size, n)
                raw = batch_mem.read(store, in_size, in_size + n * 8)
                vals = struct.unpack(f"<{n}d", raw)
            except _WasmTimeout:
                raise
            except Exception:
                return False
            for j in range(n):
                output_values[lo + j] = float(vals[j]) if row_valid[lo + j] else None
            return True

        try:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(WASM_EXECUTION_TIMEOUT_SECONDS)
            try:
                lo = 0
                while lo < num_rows:
                    hi = min(num_rows, lo + chunk_rows)
                    if batch_run is not None and batch_mem is not None:
                        if not _run_chunk_fast(lo, hi):
                            _run_rows_slow(lo, hi)
                    else:
                        _run_rows_slow(lo, hi)
                    lo = hi
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        except _WasmTimeout as exc:
            logger.warning(
                "WASM execution timed out after %d seconds", WASM_EXECUTION_TIMEOUT_SECONDS
            )
            raise HTTPException(
                status_code=408,
                detail=f"WASM execution timed out after {WASM_EXECUTION_TIMEOUT_SECONDS}s",
            ) from exc

        result_arrays = {col: table.column(col).to_pylist() for col in table.schema.names}
        result_arrays[output_column] = output_values
        result_table = pa.table(result_arrays)
        ordered_columns = list(table.schema.names)
        if output_column not in ordered_columns:
            ordered_columns.append(output_column)
        return result_table.select(ordered_columns)

    def validate(
        self, wasm_bytes: bytes | bytearray, function_name: str | None = None
    ) -> WasmValidationResult:
        """Validate a Wasm module binary and optionally check for a specific function."""
        try:
            validation_config = Config()
            validation_config.consume_fuel = True
            validation_engine = Engine(validation_config)
            module = Module(validation_engine, bytes(wasm_bytes))
            store = Store(validation_engine)
            store.set_limits(
                memory_size=MAX_WASM_MEMORY_BYTES,
                table_elements=MAX_WASM_TABLE_ELEMENTS,
                instances=1,
                tables=1,
                memories=1,
            )
            store.set_fuel(VALIDATION_FUEL)
            linker = Linker(validation_engine)
            instance = linker.instantiate(store, module)
            exports = instance.exports(store)

            exported_funcs = list(exports.keys())

            if function_name and function_name not in exported_funcs:
                return WasmValidationResult(
                    valid=False,
                    exported_functions=exported_funcs,
                    error=(
                        f"Function '{function_name}' not found in module. "
                        f"Available exports: {exported_funcs}"
                    ),
                )

            return WasmValidationResult(
                valid=True,
                exported_functions=exported_funcs,
            )

        except Exception as e:
            return WasmValidationResult(
                valid=False,
                exported_functions=[],
                error=f"Invalid Wasm module: {str(e)[:500]}",
            )

    def _get_batch_driver(self, arity: int) -> Module:
        """Get a cached batch-driver Module for UDFs taking ``arity`` f64 params."""
        driver = self._batch_drivers.get(arity)
        if driver is None:
            logger.info("Compiling Wasm batch driver for arity %d", arity)
            driver = Module(self._engine, _build_batch_driver_wat(arity))
            self._batch_drivers[arity] = driver
        return driver

    def _get_or_compile(self, wasm_bytes: bytes | bytearray) -> Module:
        """Get a compiled Module from cache, or compile and cache it."""
        module_key = hashlib.sha256(bytes(wasm_bytes)).hexdigest()

        if module_key not in self._module_cache:
            logger.info("Compiling new Wasm module: %s...", module_key[:16])
            module = Module(self._engine, bytes(wasm_bytes))
            self._module_cache[module_key] = module
            self._module_cache.move_to_end(module_key)
            if len(self._module_cache) > MAX_CACHED_MODULES:
                evicted_key, _ = self._module_cache.popitem(last=False)
                logger.info("Evicted Wasm module cache entry: %s", evicted_key[:16])
            logger.info("Wasm module compiled and cached: %s", module_key[:16])
        else:
            self._module_cache.move_to_end(module_key)
            logger.debug("Wasm module cache HIT: %s", module_key[:16])

        return self._module_cache[module_key]

    @property
    def cached_module_count(self) -> int:
        """Number of compiled Wasm modules in the cache."""
        return len(self._module_cache)


_wasm_executor: WasmExecutor | None = None


def get_wasm_executor() -> WasmExecutor:
    """Get the worker's WasmExecutor singleton. Lazy-initialized on first call."""
    global _wasm_executor
    if _wasm_executor is None:
        _wasm_executor = WasmExecutor()
    return _wasm_executor
