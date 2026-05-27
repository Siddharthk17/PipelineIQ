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
from dataclasses import dataclass

import pyarrow as pa
from wasmtime import Config, Engine, Store, Module, Instance, Linker, Trap

logger = logging.getLogger(__name__)

FUEL_PER_STEP = 10_000_000
FUEL_PER_ROW = 1_000


@dataclass
class WasmValidationResult:
    valid: bool
    exported_functions: list[str]
    error: str | None = None


class WasmExecutor:
    """Executes Wasm UDFs against Arrow Tables."""

    def __init__(self) -> None:
        config = Config()
        config.consume_fuel = True
        self._engine = Engine(config)
        self._module_cache: dict[str, Module] = {}
        logger.info("WasmExecutor initialized with fuel-enabled Engine")

    def execute(
        self,
        table: pa.Table,
        step,
        wasm_bytes: bytes | bytearray,
    ) -> pa.Table:
        """Execute a Wasm UDF against every row of an Arrow Table.

        The Wasm function receives input_columns as f64 arguments and returns f64.
        The return value is added as a new column (output_column).
        Rows where the Wasm function raises an error receive None in the output.
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

        missing_cols = [c for c in input_columns if c not in table.schema.names]
        if missing_cols:
            raise ValueError(
                f"wasm_compute step input_columns not found in data: {missing_cols}. "
                f"Available columns: {table.schema.names}"
            )

        module = self._get_or_compile(wasm_bytes)

        store = Store(self._engine)
        store.set_fuel(FUEL_PER_STEP)

        linker = Linker(self._engine)
        instance = linker.instantiate(store, module)

        try:
            wasm_func = instance.exports(store)[function_name]
        except KeyError:
            available = list(instance.exports(store).keys())
            raise ValueError(
                f"Function '{function_name}' not found in Wasm module. "
                f"Exported functions: {available}"
            )

        df = table.to_pandas()
        results: list[float | None] = []

        for _, row in df[input_columns].iterrows():
            try:
                args = [float(row[col]) for col in input_columns]
            except (TypeError, ValueError) as e:
                logger.debug("Row conversion error: %s", e)
                results.append(None)
                continue

            try:
                current_fuel = store.get_fuel()
                if current_fuel < FUEL_PER_ROW:
                    results.append(None)
                    continue
                store.set_fuel(current_fuel - FUEL_PER_ROW)
                result = wasm_func(store, *args)
                results.append(float(result) if result is not None else None)
            except Trap:
                logger.debug("Wasm trap on row (fuel exhausted or invalid operation)")
                results.append(None)
            except Exception as e:
                logger.debug("Wasm execution error on row: %s", e)
                results.append(None)

        df[output_column] = results
        return pa.Table.from_pandas(df, preserve_index=False)

    def validate(
        self, wasm_bytes: bytes | bytearray, function_name: str | None = None
    ) -> WasmValidationResult:
        """Validate a Wasm module binary and optionally check for a specific function."""
        try:
            validation_engine = Engine()
            module = Module(validation_engine, wasm_bytes)
            store = Store(validation_engine)
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

    def _get_or_compile(self, wasm_bytes: bytes | bytearray) -> Module:
        """Get a compiled Module from cache, or compile and cache it."""
        module_key = hashlib.sha256(bytes(wasm_bytes)).hexdigest()

        if module_key not in self._module_cache:
            logger.info("Compiling new Wasm module: %s...", module_key[:16])
            module = Module(self._engine, wasm_bytes)
            self._module_cache[module_key] = module
            logger.info("Wasm module compiled and cached: %s", module_key[:16])
        else:
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
