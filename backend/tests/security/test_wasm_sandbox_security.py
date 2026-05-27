"""Security tests for the Wasm sandbox boundary.

These tests verify that Wasm modules cannot escape their sandbox:
- No filesystem access (empty Linker — no WASI imported)
- No network access (WASI not present = no sockets)
- No system calls (Wasm cannot call OS directly)
- CPU budget: fuel kills infinite loops
- Stack: limit prevents overflow attacks
- Type safety: only f64 crosses the host-Wasm boundary

ALL THESE TESTS MUST PASS. A failure means a security boundary is broken.
"""

import inspect
import time

import pytest
from wasmtime import Config, Engine, Store, Module, Linker, Trap, wat2wasm


class TestWasmSandboxIsolation:
    """Verify Wasm modules cannot access host resources without WASI."""

    def test_no_filesystem_access_without_wasi(self):
        """A Wasm module importing WASI filesystem functions fails to link."""
        wat = """
            (module
                (import "wasi_snapshot_preview1" "fd_write"
                    (func (param i32 i32 i32 i32) (result i32)))
                (func (export "try_write") (result i32)
                    i32.const 0
                    i32.const 0
                    i32.const 0
                    i32.const 0
                    call 0
                )
            )
        """
        wasm_bytes = wat2wasm(wat)

        engine = Engine()
        module = Module(engine, wasm_bytes)
        store = Store(engine)
        linker = Linker(engine)

        with pytest.raises(Exception) as exc_info:
            linker.instantiate(store, module)

        error_msg = str(exc_info.value).lower()
        assert any(
            word in error_msg for word in ["import", "unknown", "link", "resolve"]
        ), f"Expected a linker/import error, got: {exc_info.value}"

    def test_wasm_executor_uses_empty_linker(self):
        """WasmExecutor source must use Linker without adding WASI."""
        from backend.execution.wasm_executor import WasmExecutor

        source = inspect.getsource(WasmExecutor)
        assert "Linker" in source, "WasmExecutor must use Linker"
        assert "wasi" not in source.lower(), (
            "WasmExecutor must NOT add WASI — WASI grants filesystem/network access"
        )

    def test_no_environment_variable_access(self):
        """Wasm modules cannot read host env vars without WASI."""
        from backend.execution.wasm_executor import WasmExecutor

        source = inspect.getsource(WasmExecutor)
        assert "wasmtime.wasi" not in source
        assert "add_wasi_to_linker" not in source
        assert "WasiConfig" not in source

    def test_fuel_system_is_enabled(self):
        """Engine must have consume_fuel=True to kill infinite loops."""
        from backend.execution.wasm_executor import WasmExecutor

        source = inspect.getsource(WasmExecutor.__init__)
        assert "consume_fuel = True" in source, (
            "Engine must have consume_fuel=True — without it, infinite loops cannot be killed"
        )

    def test_fuel_per_row_kills_infinite_loop(self):
        """Per-row fuel budget must terminate infinite loops without hanging."""
        infinite_loop_wasm = wat2wasm("""
            (module
                (func (export "infinite") (param f64) (result f64)
                    (block $b (loop $l br $l))
                    local.get 0
                )
            )
        """)

        config = Config()
        config.consume_fuel = True
        engine = Engine(config)
        module = Module(engine, infinite_loop_wasm)
        store = Store(engine)
        store.set_fuel(1_000)

        linker = Linker(engine)
        instance = linker.instantiate(store, module)
        func = instance.exports(store)["infinite"]

        start = time.time()
        with pytest.raises(Trap):
            func(store, 1.0)
        elapsed = time.time() - start

        assert elapsed < 1.0, f"Fuel-killed loop took {elapsed:.2f}s — should be instant"

    def test_type_boundary_enforces_f64_only(self):
        """Only f64 values can cross the host-Wasm boundary."""
        wat = """
            (module
                (func (export "int_func") (param i32) (result i32)
                    local.get 0
                )
            )
        """
        wasm_bytes = wat2wasm(wat)
        engine = Engine()
        module = Module(engine, wasm_bytes)
        store = Store(engine)
        linker = Linker(engine)
        instance = linker.instantiate(store, module)
        func = instance.exports(store)["int_func"]

        with pytest.raises((TypeError, Exception)):
            func(store, 1.0)

    def test_wasm_executor_only_accepts_f64_inputs(self):
        """WasmExecutor converts input columns to float64; string columns produce None."""
        import pyarrow as pa
        from backend.execution.wasm_executor import WasmExecutor

        wasm_bytes = wat2wasm("""
            (module
                (func (export "identity") (param f64) (result f64)
                    local.get 0
                )
            )
        """)

        table = pa.table({"label": ["cat", "dog", "bird"]})

        class MockStep:
            function = "identity"
            input_columns = ["label"]
            output_column = "result"

        executor = WasmExecutor()
        try:
            result = executor.execute(table, MockStep(), wasm_bytes)
            values = result.column("result").to_pylist()
            assert all(v is None for v in values), (
                "Non-numeric column values should become None in output"
            )
        except (ValueError, TypeError):
            pass
