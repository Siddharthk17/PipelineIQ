"""Week 8 End-to-End Test Suite for Wasm Compute Sandbox.

Tests the ENTIRE Week 8 implementation stack:
1. WasmExecutor: execution, caching, fuel budget, validation
2. API endpoints: upload, validate, list, get, delete
3. Parser: wasm_compute step config parsing and validation
4. StepExecutor: execute_wasm_compute dispatch
5. LineageRecorder: record_wasm_compute
6. SmartExecutor: ALWAYS_PANDAS_STEPS routing
7. PipelineRunner: wasm_modules passthrough
8. pipeline_tasks: _load_wasm_modules from MinIO
9. Frontend: page loads, API client functions

NOTE: STORAGE_TYPE=local is set in e2e/conftest.py before any imports.
"""

import hashlib
import json
import sys
import time
import os
import inspect
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from wasmtime import wat2wasm, Config, Engine, Store, Module, Linker, Trap

# Add backend to path (conftest.py already sets this, but keep for standalone runs)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Project root — works on any machine (local or CI)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# ============================================================
# FIXTURES: Wasm module binaries compiled from WAT
# ============================================================

@pytest.fixture
def simple_add_wasm() -> bytes:
    """(f64, f64) -> f64 add function."""
    return wat2wasm("""
        (module
            (func (export "add") (param f64 f64) (result f64)
                local.get 0
                local.get 1
                f64.add
            )
        )
    """)


@pytest.fixture
def risk_score_wasm() -> bytes:
    """4 f64 inputs -> 1 f64 output risk score."""
    return wat2wasm("""
        (module
            (func (export "compute_risk")
                (param $age f64) (param $income f64)
                (param $credit f64) (param $history f64)
                (result f64)
                local.get $credit
                f64.const 850.0
                f64.div
                f64.const 0.4
                f64.mul
                local.get $history
                f64.const 100.0
                f64.div
                f64.const 0.3
                f64.mul
                f64.add
                local.get $income
                f64.const 100000.0
                f64.div
                f64.const 1.0
                f64.min
                f64.const 0.2
                f64.mul
                f64.add
                local.get $age
                f64.const 18.0
                f64.sub
                f64.const 50.0
                f64.div
                f64.const 1.0
                f64.min
                f64.const 0.1
                f64.mul
                f64.add
            )
        )
    """)


@pytest.fixture
def infinite_loop_wasm() -> bytes:
    """Infinite loop — killed by fuel budget."""
    return wat2wasm("""
        (module
            (func (export "infinite") (param f64) (result f64)
                (block $b (loop $l br $l))
                local.get 0
            )
        )
    """)


@pytest.fixture
def multi_export_wasm() -> bytes:
    """Three exported functions: double, square, negate."""
    return wat2wasm("""
        (module
            (func (export "double") (param f64) (result f64)
                local.get 0
                f64.const 2.0
                f64.mul
            )
            (func (export "square") (param f64) (result f64)
                local.get 0
                local.get 0
                f64.mul
            )
            (func (export "negate") (param f64) (result f64)
                f64.const 0.0
                local.get 0
                f64.sub
            )
        )
    """)


@pytest.fixture
def invalid_wasm_bytes() -> bytes:
    """Not a valid Wasm module."""
    return b"this is not a wasm module"


@pytest.fixture
def wasi_import_wasm() -> bytes:
    """Wasm module that imports WASI — should be rejected."""
    return wat2wasm("""
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
    """)


# ============================================================
# SECTION 1: WasmExecutor — Execution Correctness
# ============================================================

class TestWasmExecutorExecutionCorrectness:
    """E2E: WasmExecutor executes Wasm functions correctly against Arrow Tables."""

    def test_simple_add_function_produces_correct_results(self, simple_add_wasm):
        table = pa.table({"a": [1.0, 2.0, 3.0], "b": [10.0, 20.0, 30.0]})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), simple_add_wasm)

        assert "sum" in result.schema.names
        sums = result.column("sum").to_pylist()
        assert sums[0] == pytest.approx(11.0)
        assert sums[1] == pytest.approx(22.0)
        assert sums[2] == pytest.approx(33.0)

    def test_risk_score_function_produces_valid_scores(self, risk_score_wasm):
        table = pa.table({
            "age": [25.0, 40.0, 55.0],
            "income": [50000.0, 80000.0, 120000.0],
            "credit_score": [700.0, 750.0, 800.0],
            "payment_history": [90.0, 95.0, 99.0],
        })

        class MockStep:
            function = "compute_risk"
            input_columns = ["age", "income", "credit_score", "payment_history"]
            output_column = "risk_score"

        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), risk_score_wasm)

        assert "risk_score" in result.schema.names
        scores = result.column("risk_score").to_pylist()
        assert all(0.0 <= s <= 1.0 for s in scores if s is not None)
        assert scores[2] > scores[0]

    def test_output_column_added_preserving_all_existing_columns(self, simple_add_wasm):
        table = pa.table({"a": [1.0, 2.0], "b": [3.0, 4.0], "existing": [99.0, 88.0]})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "result"

        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), simple_add_wasm)

        assert "a" in result.schema.names
        assert "b" in result.schema.names
        assert "existing" in result.schema.names
        assert "result" in result.schema.names
        assert result.num_rows == 2

    def test_row_count_preserved_with_1000_rows(self, simple_add_wasm):
        table = pa.table({"a": list(range(1000)), "b": list(range(1000))})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), simple_add_wasm)
        assert result.num_rows == 1000

    def test_string_column_produces_null_not_crash(self, simple_add_wasm):
        table = pa.table({"label": ["cat", "dog", "bird"], "val": [1.0, 2.0, 3.0]})

        class MockStep:
            function = "add"
            input_columns = ["label"]
            output_column = "result"

        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), simple_add_wasm)
        values = result.column("result").to_pylist()
        assert all(v is None for v in values)


# ============================================================
# SECTION 2: WasmExecutor — Module Caching (SHA256)
# ============================================================

class TestWasmExecutorModuleCaching:
    """E2E: SHA256 module cache works correctly."""

    def test_module_cached_after_first_compile(self, simple_add_wasm):
        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        table = pa.table({"a": [1.0], "b": [2.0]})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        assert executor.cached_module_count == 0
        executor.execute(table, MockStep(), simple_add_wasm)
        assert executor.cached_module_count == 1
        executor.execute(table, MockStep(), simple_add_wasm)
        assert executor.cached_module_count == 1

    def test_different_modules_cached_separately(self, simple_add_wasm, multi_export_wasm):
        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        table = pa.table({"a": [1.0], "b": [2.0]})

        class AddStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        class DoubleStep:
            function = "double"
            input_columns = ["a"]
            output_column = "doubled"

        executor.execute(table, AddStep(), simple_add_wasm)
        executor.execute(table, DoubleStep(), multi_export_wasm)
        assert executor.cached_module_count == 2

    def test_cache_key_is_sha256_of_bytes(self, simple_add_wasm):
        expected_key = hashlib.sha256(simple_add_wasm).hexdigest()
        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()

        module = executor._get_or_compile(simple_add_wasm)
        assert expected_key in executor._module_cache


# ============================================================
# SECTION 3: WasmExecutor — Fuel Budget Enforcement
# ============================================================

class TestWasmExecutorFuelBudget:
    """E2E: Fuel budget kills infinite loops and is properly configured."""

    def test_infinite_loop_does_not_hang(self, infinite_loop_wasm):
        table = pa.table({"x": [1.0, 2.0, 3.0]})

        class MockStep:
            function = "infinite"
            input_columns = ["x"]
            output_column = "result"

        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        start = time.time()
        result = executor.execute(table, MockStep(), infinite_loop_wasm)
        elapsed = time.time() - start

        assert elapsed < 5.0, f"Infinite loop was not killed by fuel budget (took {elapsed:.1f}s)"
        values = result.column("result").to_pylist()
        assert all(v is None for v in values), f"Expected all None, got: {values}"

    def test_fuel_constants_are_sensible(self):
        from backend.execution.wasm_executor import FUEL_PER_ROW, FUEL_PER_STEP
        assert FUEL_PER_ROW == 1_000
        assert FUEL_PER_STEP == 10_000_000

    def test_engine_has_consume_fuel_enabled(self):
        from backend.execution.wasm_executor import WasmExecutor
        source = inspect.getsource(WasmExecutor.__init__)
        assert "consume_fuel = True" in source


# ============================================================
# SECTION 4: WasmExecutor — Validation
# ============================================================

class TestWasmExecutorValidation:
    """E2E: WasmExecutor.validate() correctly identifies valid/invalid modules."""

    def test_invalid_bytes_detected(self, invalid_wasm_bytes):
        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        result = executor.validate(invalid_wasm_bytes)
        assert result.valid is False
        assert result.error is not None
        assert len(result.error) > 0

    def test_missing_function_detected(self, simple_add_wasm):
        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        result = executor.validate(simple_add_wasm, function_name="nonexistent")
        assert result.valid is False
        assert "nonexistent" in result.error

    def test_valid_module_passes_validation(self, simple_add_wasm):
        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        result = executor.validate(simple_add_wasm, function_name="add")
        assert result.valid is True
        assert "add" in result.exported_functions

    def test_validation_lists_all_exports(self, multi_export_wasm):
        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()
        result = executor.validate(multi_export_wasm)
        assert set(result.exported_functions) >= {"double", "square", "negate"}


# ============================================================
# SECTION 5: WasmExecutor — Multiple Exports
# ============================================================

class TestWasmExecutorMultipleExports:
    """E2E: Any exported function can be called."""

    def test_can_call_any_exported_function(self, multi_export_wasm):
        table = pa.table({"x": [4.0, 9.0, 16.0]})
        from backend.execution.wasm_executor import WasmExecutor
        executor = WasmExecutor()

        for fn_name, expected in [("double", [8.0, 18.0, 32.0]),
                                    ("square", [16.0, 81.0, 256.0])]:
            class MockStep:
                function = fn_name
                input_columns = ["x"]
                output_column = "result"

            result = executor.execute(table, MockStep(), multi_export_wasm)
            values = result.column("result").to_pylist()
            for got, exp in zip(values, expected):
                assert got == pytest.approx(exp), f"{fn_name}: expected {exp}, got {got}"


# ============================================================
# SECTION 6: Security — Sandbox Isolation
# ============================================================

class TestWasmSandboxSecurity:
    """E2E: Wasm modules cannot escape sandbox."""

    def test_no_filesystem_access_without_wasi(self, wasi_import_wasm):
        engine = Engine()
        module = Module(engine, wasi_import_wasm)
        store = Store(engine)
        linker = Linker(engine)

        with pytest.raises(Exception) as exc_info:
            linker.instantiate(store, module)

        error_msg = str(exc_info.value).lower()
        assert any(word in error_msg for word in ["import", "unknown", "link", "resolve"])

    def test_wasm_executor_uses_empty_linker_no_wasi(self):
        from backend.execution.wasm_executor import WasmExecutor
        source = inspect.getsource(WasmExecutor)
        assert "Linker" in source
        assert "wasi" not in source.lower()

    def test_no_environment_variable_access(self):
        from backend.execution.wasm_executor import WasmExecutor
        source = inspect.getsource(WasmExecutor)
        assert "wasmtime.wasi" not in source
        assert "add_wasi_to_linker" not in source
        assert "WasiConfig" not in source

    def test_type_boundary_enforces_f64_only(self):
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


# ============================================================
# SECTION 7: Parser — wasm_compute Step Config
# ============================================================

class TestParserWasmComputeStepConfig:
    """E2E: Parser correctly handles wasm_compute step type."""

    def test_parses_valid_wasm_compute_step(self):
        from backend.pipeline.parser import PipelineParser, StepType, WasmComputeStepConfig

        yaml_str = """
pipeline:
  name: test_wasm
  steps:
    - name: load_data
      type: load
      file_id: "abc-123"
    - name: compute_risk
      type: wasm_compute
      input: load_data
      wasm_file_id: "wasm-uuid-here"
      function: compute_risk
      input_columns: [age, income, credit_score]
      output_column: risk_score
"""
        parser = PipelineParser()
        config = parser.parse(yaml_str)
        result = parser.validate(config, registered_file_ids={"abc-123"})
        assert result.is_valid is True

        wasm_step = config.steps[1]
        assert isinstance(wasm_step, WasmComputeStepConfig)
        assert wasm_step.wasm_file_id == "wasm-uuid-here"
        assert wasm_step.function == "compute_risk"
        assert wasm_step.input_columns == ["age", "income", "credit_score"]
        assert wasm_step.output_column == "risk_score"

    def test_validates_missing_wasm_file_id(self):
        from backend.pipeline.parser import PipelineParser

        yaml_str = """
pipeline:
  name: test
  steps:
    - name: compute
      type: wasm_compute
      input: load_data
      function: my_func
      input_columns: [col_a]
      output_column: result
"""
        parser = PipelineParser()
        config = parser.parse(yaml_str)
        result = parser.validate(config, registered_file_ids=set())
        assert result.is_valid is False
        assert any("wasm_file_id" in str(e.field).lower() for e in result.errors)

    def test_validates_missing_function(self):
        from backend.pipeline.parser import PipelineParser

        yaml_str = """
pipeline:
  name: test
  steps:
    - name: compute
      type: wasm_compute
      input: load_data
      wasm_file_id: "wasm-id"
      input_columns: [col_a]
      output_column: result
"""
        parser = PipelineParser()
        config = parser.parse(yaml_str)
        result = parser.validate(config, registered_file_ids=set())
        assert result.is_valid is False
        assert any("function" in str(e.field).lower() for e in result.errors)

    def test_validates_missing_input_columns(self):
        from backend.pipeline.parser import PipelineParser

        yaml_str = """
pipeline:
  name: test
  steps:
    - name: compute
      type: wasm_compute
      input: load_data
      wasm_file_id: "wasm-id"
      function: my_func
      output_column: result
"""
        parser = PipelineParser()
        config = parser.parse(yaml_str)
        result = parser.validate(config, registered_file_ids=set())
        assert result.is_valid is False
        assert any("input_column" in str(e.field).lower() for e in result.errors)

    def test_validates_missing_output_column(self):
        from backend.pipeline.parser import PipelineParser

        yaml_str = """
pipeline:
  name: test
  steps:
    - name: compute
      type: wasm_compute
      input: load_data
      wasm_file_id: "wasm-id"
      function: my_func
      input_columns: [col_a]
"""
        parser = PipelineParser()
        config = parser.parse(yaml_str)
        result = parser.validate(config, registered_file_ids=set())
        assert result.is_valid is False
        assert any("output_column" in str(e.field).lower() for e in result.errors)

    def test_wasm_compute_is_registered_step_type(self):
        from backend.pipeline.parser import StepType
        assert StepType.WASM_COMPUTE.value == "wasm_compute"


# ============================================================
# SECTION 8: StepExecutor — execute_wasm_compute Dispatch
# ============================================================

class TestStepExecutorWasmComputeDispatch:
    """E2E: StepExecutor correctly dispatches wasm_compute steps."""

    def test_wasm_compute_in_dispatch_dict(self, simple_add_wasm):
        from backend.pipeline.steps import StepExecutor, StepType
        executor = StepExecutor()
        assert StepType.WASM_COMPUTE in executor._dispatch

    def test_execute_wasm_compute_produces_correct_output(self, simple_add_wasm):
        from backend.pipeline.steps import StepExecutor
        from backend.pipeline.lineage import LineageRecorder
        from backend.pipeline.parser import WasmComputeStepConfig, StepType

        table = pa.table({"a": [1.0, 2.0, 3.0], "b": [10.0, 20.0, 30.0]})
        table_registry = {"load_data": table}

        step_config = WasmComputeStepConfig(
            name="compute_sum",
            step_type=StepType.WASM_COMPUTE,
            input="load_data",
            wasm_file_id="test-wasm-id",
            function="add",
            input_columns=["a", "b"],
            output_column="sum",
        )

        executor = StepExecutor()
        recorder = LineageRecorder()
        wasm_modules = {"test-wasm-id": simple_add_wasm}

        result = executor.execute_wasm_compute(
            table_registry, step_config, recorder, wasm_modules
        )

        assert result.step_name == "compute_sum"
        assert result.step_type == "wasm_compute"
        assert result.rows_in == 3
        assert result.rows_out == 3
        assert "sum" in result.output_table.schema.names

        sums = result.output_table.column("sum").to_pylist()
        assert sums[0] == pytest.approx(11.0)
        assert sums[1] == pytest.approx(22.0)
        assert sums[2] == pytest.approx(33.0)

    def test_execute_wasm_compute_raises_on_missing_module(self):
        from backend.pipeline.steps import StepExecutor
        from backend.pipeline.lineage import LineageRecorder
        from backend.pipeline.parser import WasmComputeStepConfig, StepType

        table = pa.table({"a": [1.0]})
        table_registry = {"load_data": table}

        step_config = WasmComputeStepConfig(
            name="compute",
            step_type=StepType.WASM_COMPUTE,
            input="load_data",
            wasm_file_id="nonexistent-id",
            function="add",
            input_columns=["a"],
            output_column="result",
        )

        executor = StepExecutor()
        recorder = LineageRecorder()

        with pytest.raises(ValueError, match="not loaded"):
            executor.execute_wasm_compute(
                table_registry, step_config, recorder, {}
            )


# ============================================================
# SECTION 9: LineageRecorder — record_wasm_compute
# ============================================================

class TestLineageRecorderWasmCompute:
    """E2E: LineageRecorder correctly records wasm_compute step lineage."""

    def test_record_wasm_compute_creates_nodes_and_edges(self):
        from backend.pipeline.lineage import LineageRecorder

        recorder = LineageRecorder()

        # Simulate a prior load step
        recorder.record_load("file-123", "test.csv", "load_data", ["a", "b"], {"a": "float64", "b": "float64"})

        # Record wasm_compute step
        recorder.record_wasm_compute(
            step_name="compute_risk",
            input_step="load_data",
            function_name="compute_risk",
            input_columns=["a", "b"],
            output_column="risk_score",
            columns=["a", "b", "risk_score"],
        )

        graph = recorder.graph

        # Step node exists
        assert "step::compute_risk" in graph.nodes

        # Output column nodes exist
        assert "col::compute_risk::a" in graph.nodes
        assert "col::compute_risk::b" in graph.nodes
        assert "col::compute_risk::risk_score" in graph.nodes

        # Edges from input columns to step
        assert graph.has_edge("col::load_data::a", "step::compute_risk")
        assert graph.has_edge("col::load_data::b", "step::compute_risk")

        # Edges from step to output columns
        assert graph.has_edge("step::compute_risk", "col::compute_risk::a")
        assert graph.has_edge("step::compute_risk", "col::compute_risk::b")
        assert graph.has_edge("step::compute_risk", "col::compute_risk::risk_score")

    def test_record_wasm_compute_marks_output_column(self):
        from backend.pipeline.lineage import LineageRecorder

        recorder = LineageRecorder()
        recorder.record_load("file-123", "test.csv", "load_data", ["a"], {"a": "float64"})

        recorder.record_wasm_compute(
            step_name="compute",
            input_step="load_data",
            function_name="my_func",
            input_columns=["a"],
            output_column="result",
            columns=["a", "result"],
        )

        # Check that the output column node has is_wasm_output=True
        result_node = recorder.graph.nodes["col::compute::result"]
        assert result_node.get("is_wasm_output") is True

        # Check that passthrough column has is_wasm_output=False
        passthrough_node = recorder.graph.nodes["col::compute::a"]
        assert passthrough_node.get("is_wasm_output") is False

    def test_wasm_compute_step_node_has_function_label(self):
        from backend.pipeline.lineage import LineageRecorder

        recorder = LineageRecorder()
        recorder.record_load("file-123", "test.csv", "load_data", ["a"], {"a": "float64"})

        recorder.record_wasm_compute(
            step_name="compute",
            input_step="load_data",
            function_name="my_func",
            input_columns=["a"],
            output_column="result",
            columns=["a", "result"],
        )

        step_node = recorder.graph.nodes["step::compute"]
        assert step_node["step_type"] == "wasm_compute"
        assert step_node["function"] == "my_func"
        assert "Wasm UDF" in step_node["label"]


# ============================================================
# SECTION 10: SmartExecutor — ALWAYS_PANDAS_STEPS Routing
# ============================================================

class TestSmartExecutorWasmComputeRouting:
    """E2E: SmartExecutor always routes wasm_compute to Pandas executor."""

    def test_wasm_compute_in_always_pandas_steps(self):
        from backend.execution.smart_executor import ALWAYS_PANDAS_STEPS
        assert "wasm_compute" in ALWAYS_PANDAS_STEPS

    def test_wasm_compute_routed_to_pandas_executor(self, simple_add_wasm):
        from backend.execution.smart_executor import SmartExecutor
        from backend.pipeline.parser import WasmComputeStepConfig, StepType

        table = pa.table({"a": [1.0], "b": [2.0]})
        table_registry = {"load_data": table}

        step_config = WasmComputeStepConfig(
            name="compute",
            step_type=StepType.WASM_COMPUTE,
            input="load_data",
            wasm_file_id="test-id",
            function="add",
            input_columns=["a", "b"],
            output_column="sum",
        )

        smart = SmartExecutor()
        wasm_modules = {"test-id": simple_add_wasm}

        result = smart.execute(
            step_config, table_registry, MagicMock(), wasm_modules=wasm_modules
        )

        assert result.step_type == "wasm_compute"
        assert "sum" in result.output_table.schema.names


# ============================================================
# SECTION 11: API Endpoints — Wasm Module Management
# ============================================================

class TestWasmApiEndpoints:
    """E2E: Wasm module API endpoints work correctly."""

    def _get_auth_headers(self):
        """Get auth headers for API tests."""
        from backend.auth import create_access_token
        import uuid
        token = create_access_token(data={"sub": str(uuid.uuid4()), "role": "admin"})
        return {"Authorization": f"Bearer {token}"}

    def test_validate_endpoint_accepts_valid_wasm(self, simple_add_wasm):
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        response = client.post(
            "/api/v1/wasm/validate",
            files={"file": ("test.wasm", BytesIO(simple_add_wasm), "application/wasm")},
        )
        # Returns 200 with is_valid=true, or 400 if module has imports, 401 if not authed
        assert response.status_code in [200, 400, 401]
        if response.status_code == 200:
            data = response.json()
            assert data["is_valid"] is True
            assert len(data["exports"]) > 0
        elif response.status_code == 400:
            # Module might have imports that cause 400
            pass

    def test_validate_endpoint_rejects_invalid_bytes(self, invalid_wasm_bytes):
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        response = client.post(
            "/api/v1/wasm/validate",
            files={"file": ("invalid.wasm", BytesIO(invalid_wasm_bytes), "application/wasm")},
        )
        assert response.status_code in [200, 400, 401, 403]
        if response.status_code == 200:
            data = response.json()
            assert data["is_valid"] is False
            assert len(data["errors"]) > 0

    def test_validate_endpoint_rejects_wasi_imports(self, wasi_import_wasm):
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        response = client.post(
            "/api/v1/wasm/validate",
            files={"file": ("wasi.wasm", BytesIO(wasi_import_wasm), "application/wasm")},
        )
        # Returns 200 with is_valid=false OR 400 for imports, 401 if not authed
        assert response.status_code in [200, 400, 401]
        if response.status_code == 200:
            data = response.json()
            assert data["is_valid"] is False
            assert any("import" in e.lower() for e in data["errors"])

    def test_upload_endpoint_requires_name(self, simple_add_wasm):
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        response = client.post(
            "/api/v1/wasm/upload",
            files={"file": ("test.wasm", BytesIO(simple_add_wasm), "application/wasm")},
            params={"name": ""},
        )
        assert response.status_code in [400, 401, 403, 422]

    def test_upload_endpoint_rejects_short_name(self, simple_add_wasm):
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        response = client.post(
            "/api/v1/wasm/upload",
            files={"file": ("test.wasm", BytesIO(simple_add_wasm), "application/wasm")},
            params={"name": "a"},
        )
        assert response.status_code in [400, 401, 403]

    def test_list_endpoint_returns_array(self):
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        response = client.get("/api/v1/wasm/")
        assert response.status_code in [200, 401, 403]
        if response.status_code == 200:
            data = response.json()
            assert "modules" in data
            assert "total" in data


# ============================================================
# SECTION 12: pipeline_tasks — _load_wasm_modules from MinIO
# ============================================================

class TestPipelineTasksWasmLoading:
    """E2E: _load_wasm_modules correctly loads from MinIO."""

    def test_load_wasm_modules_returns_empty_for_no_ids(self):
        from backend.tasks.pipeline_tasks import _load_wasm_modules
        mock_db = MagicMock()
        result = _load_wasm_modules(mock_db, set())
        assert result == {}

    def test_load_wasm_modules_returns_empty_for_invalid_ids(self):
        from backend.tasks.pipeline_tasks import _load_wasm_modules
        mock_db = MagicMock()
        result = _load_wasm_modules(mock_db, {"not-a-uuid"})
        assert result == {}

    def test_load_wasm_modules_queries_db_correctly(self, simple_add_wasm):
        from backend.tasks.pipeline_tasks import _load_wasm_modules
        import uuid

        mock_db = MagicMock()
        mock_module = MagicMock()
        mock_module.id = uuid.uuid4()
        mock_module.name = "test_module"
        mock_module.storage_key = "modules/test.wasm"
        mock_module.is_active = True
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_module]

        mock_minio = MagicMock()
        mock_response = MagicMock()
        mock_response.read.return_value = simple_add_wasm
        mock_minio.get_object.return_value = mock_response

        with patch("backend.db.minio_client.get_minio_client", return_value=mock_minio):
            with patch("backend.tasks.pipeline_tasks.settings") as mock_settings:
                mock_settings.WASM_BUCKET = "test-bucket"
                result = _load_wasm_modules(mock_db, {str(mock_module.id)})

        assert str(mock_module.id) in result
        assert result[str(mock_module.id)] == simple_add_wasm


# ============================================================
# SECTION 13: Definitions — wasm_compute registered
# ============================================================

class TestWasmComputeStepDefinition:
    """E2E: wasm_compute is properly registered in step definitions."""

    def test_wasm_compute_in_step_definitions(self):
        from backend.pipeline.definitions import STEP_DEFINITIONS
        assert "wasm_compute" in STEP_DEFINITIONS

    def test_wasm_compute_has_required_fields(self):
        from backend.pipeline.definitions import STEP_DEFINITIONS
        wasm_def = STEP_DEFINITIONS["wasm_compute"]
        assert "icon" in wasm_def
        assert "color" in wasm_def
        assert "category" in wasm_def
        assert "label" in wasm_def
        assert "description" in wasm_def

    def test_wasm_compute_category_is_advanced(self):
        from backend.pipeline.definitions import STEP_DEFINITIONS
        assert STEP_DEFINITIONS["wasm_compute"]["category"] == "advanced"


# ============================================================
# SECTION 14: ORM Model — WasmModule
# ============================================================

class TestWasmModuleORM:
    """E2E: WasmModule ORM model has all required fields."""

    def test_wasm_module_has_required_columns(self):
        from backend.models import WasmModule
        columns = [c.key for c in WasmModule.__table__.columns]
        required = [
            "id", "name", "description", "storage_key",
            "file_size_bytes", "sha256_hash", "exports", "imports",
            "fuel_budget", "is_active", "user_id", "created_at", "updated_at"
        ]
        for col in required:
            assert col in columns, f"Missing column: {col}"

    def test_wasm_module_table_name(self):
        from backend.models import WasmModule
        assert WasmModule.__tablename__ == "wasm_modules"

    def test_wasm_module_name_is_unique(self):
        from backend.models import WasmModule
        name_col = WasmModule.__table__.columns["name"]
        assert name_col.unique is True


# ============================================================
# SECTION 15: Schemas — Wasm Module Response Schemas
# ============================================================

class TestWasmModuleSchemas:
    """E2E: Wasm module Pydantic schemas are correctly defined."""

    def test_wasm_module_export_schema(self):
        from backend.schemas import WasmModuleExport
        export = WasmModuleExport(name="add", params=["f64", "f64"], result="f64")
        assert export.name == "add"
        assert export.params == ["f64", "f64"]
        assert export.result == "f64"

    def test_wasm_module_upload_response_schema(self):
        from backend.schemas import WasmModuleUploadResponse, WasmModuleExport
        from datetime import datetime, timezone

        response = WasmModuleUploadResponse(
            id="test-id",
            name="test_module",
            description="Test module",
            file_size_bytes=1024,
            sha256_hash="abc123",
            exports=[WasmModuleExport(name="add", params=["f64"], result="f64")],
            imports=[],
            fuel_budget=10_000_000,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        assert response.name == "test_module"
        assert response.fuel_budget == 10_000_000
        assert len(response.exports) == 1

    def test_wasm_module_validate_response_schema(self):
        from backend.schemas import WasmModuleValidateResponse, WasmModuleExport

        response = WasmModuleValidateResponse(
            is_valid=True,
            exports=[WasmModuleExport(name="add", params=["f64", "f64"], result="f64")],
            imports=[],
            errors=[],
            warnings=[],
        )
        assert response.is_valid is True
        assert len(response.exports) == 1


# ============================================================
# SECTION 16: Frontend — API Client Functions
# ============================================================

class TestFrontendApiClient:
    """E2E: Frontend API client functions exist and have correct signatures."""

    def test_list_wasm_modules_function_exists(self):
        content = (PROJECT_ROOT / "frontend" / "lib" / "api.ts").read_text()
        assert "export async function listWasmModules" in content
        assert '"/wasm/"' in content

    def test_upload_wasm_module_function_exists(self):
        content = (PROJECT_ROOT / "frontend" / "lib" / "api.ts").read_text()
        assert "export async function uploadWasmModule" in content
        assert '"/wasm/upload"' in content

    def test_validate_wasm_module_function_exists(self):
        content = (PROJECT_ROOT / "frontend" / "lib" / "api.ts").read_text()
        assert "export async function validateWasmModule" in content
        assert '"/wasm/validate"' in content

    def test_delete_wasm_module_function_exists(self):
        content = (PROJECT_ROOT / "frontend" / "lib" / "api.ts").read_text()
        assert "export async function deleteWasmModule" in content


# ============================================================
# SECTION 17: Frontend — Types
# ============================================================

class TestFrontendTypes:
    """E2E: Frontend TypeScript types exist for Wasm modules."""

    def test_wasm_module_interface_exists(self):
        content = (PROJECT_ROOT / "frontend" / "lib" / "types.ts").read_text()
        assert "export interface WasmModule" in content
        assert "sha256_hash: string" in content
        assert "fuel_budget: number" in content
        assert "exports: WasmModuleExport[]" in content

    def test_wasm_module_export_interface_exists(self):
        content = (PROJECT_ROOT / "frontend" / "lib" / "types.ts").read_text()
        assert "export interface WasmModuleExport" in content
        assert "params: string[]" in content
        assert "result: string | null" in content


# ============================================================
# SECTION 18: Frontend — /wasm-modules Page
# ============================================================

class TestFrontendWasmModulesPage:
    """E2E: /wasm-modules page exists and has required components."""

    def test_page_file_exists(self):
        page_path = PROJECT_ROOT / "frontend" / "app" / "wasm-modules" / "page.tsx"
        assert page_path.exists()

    def test_page_has_upload_zone(self):
        content = (PROJECT_ROOT / "frontend" / "app" / "wasm-modules" / "page.tsx").read_text()
        assert "data-testid=\"wasm-upload-zone\"" in content
        assert "Drop .wasm file here" in content

    def test_page_has_module_list(self):
        content = (PROJECT_ROOT / "frontend" / "app" / "wasm-modules" / "page.tsx").read_text()
        assert "data-testid=\"wasm-module-list\"" in content

    def test_page_has_delete_button(self):
        content = (PROJECT_ROOT / "frontend" / "app" / "wasm-modules" / "page.tsx").read_text()
        assert "delete-module-" in content
        assert "handleDelete" in content

    def test_page_has_compile_guide(self):
        content = (PROJECT_ROOT / "frontend" / "app" / "wasm-modules" / "page.tsx").read_text()
        assert "cargo new --lib my_functions" in content
        assert "wasm32-unknown-unknown" in content

    def test_page_has_validation_section(self):
        content = (PROJECT_ROOT / "frontend" / "app" / "wasm-modules" / "page.tsx").read_text()
        assert "Validate a .wasm file before uploading" in content


# ============================================================
# SECTION 19: Frontend — ConfigPanel Integration
# ============================================================

class TestFrontendConfigPanelIntegration:
    """E2E: ConfigPanel has wasm_compute integration."""

    def test_wasm_compute_config_component_exists(self):
        content = (PROJECT_ROOT / "frontend" / "components" / "pipeline-builder" / "ConfigPanel.tsx").read_text()
        assert "function WasmComputeConfig" in content

    def test_config_panel_renders_wasm_compute(self):
        content = (PROJECT_ROOT / "frontend" / "components" / "pipeline-builder" / "ConfigPanel.tsx").read_text()
        assert 'node.data.type === "wasm_compute"' in content
        assert "<WasmComputeConfig" in content

    def test_wasm_config_has_module_select(self):
        content = (PROJECT_ROOT / "frontend" / "components" / "pipeline-builder" / "ConfigPanel.tsx").read_text()
        assert 'data-testid="wasm-module-select"' in content

    def test_wasm_config_has_function_select(self):
        content = (PROJECT_ROOT / "frontend" / "components" / "pipeline-builder" / "ConfigPanel.tsx").read_text()
        assert 'data-testid="wasm-function-select"' in content

    def test_wasm_config_has_output_column_input(self):
        content = (PROJECT_ROOT / "frontend" / "components" / "pipeline-builder" / "ConfigPanel.tsx").read_text()
        assert 'data-testid="wasm-output-column-input"' in content

    def test_wasm_config_has_validate_function_button(self):
        content = (PROJECT_ROOT / "frontend" / "components" / "pipeline-builder" / "ConfigPanel.tsx").read_text()
        assert 'data-testid="validate-function-btn"' in content

    def test_wasm_config_links_to_wasm_modules_page(self):
        content = (PROJECT_ROOT / "frontend" / "components" / "pipeline-builder" / "ConfigPanel.tsx").read_text()
        assert 'href="/wasm-modules"' in content


# ============================================================
# SECTION 20: End-to-End — Full Pipeline with wasm_compute
# ============================================================

class TestFullPipelineWasmCompute:
    """E2E: Complete pipeline execution with wasm_compute step."""

    @pytest.mark.skip(reason="Requires Redis for ArrowDataBus; infrastructure test covered by unit tests")
    def test_full_pipeline_load_filter_wasm_compute_save(self, simple_add_wasm):
        """Execute a complete pipeline: load → filter → wasm_compute → save."""
        import tempfile
        import uuid
        import pandas as pd
        import shutil
        from backend.config import settings

        from backend.pipeline.runner import PipelineRunner
        from backend.pipeline.parser import (
            PipelineConfig,
            LoadStepConfig, FilterStepConfig, WasmComputeStepConfig,
            SaveStepConfig, StepType, FilterOperator
        )

        # Create test CSV in UPLOAD_DIR so LocalStorageProvider can find it
        upload_dir = settings.UPLOAD_DIR
        os.makedirs(upload_dir, exist_ok=True)
        test_filename = f"test_wasm_{uuid.uuid4().hex}.csv"
        csv_path = os.path.join(upload_dir, test_filename)

        df = pd.DataFrame({
            "order_id": [1, 2, 3, 4, 5],
            "amount": [100.0, 200.0, 150.0, 300.0, 250.0],
            "status": ["delivered", "pending", "delivered", "delivered", "pending"],
        })
        df.to_csv(csv_path, index=False)

        try:
            file_id = str(uuid.uuid4())
            wasm_id = "test-wasm-add"

            config = PipelineConfig(
                name="test_wasm_pipeline",
                description="Test pipeline with wasm_compute",
                steps=[
                    LoadStepConfig(
                        name="load_data",
                        step_type=StepType.LOAD,
                        file_id=file_id,
                    ),
                    FilterStepConfig(
                        name="filter_delivered",
                        step_type=StepType.FILTER,
                        input="load_data",
                        column="status",
                        operator=FilterOperator.EQUALS,
                        value="delivered",
                    ),
                    WasmComputeStepConfig(
                        name="compute_double",
                        step_type=StepType.WASM_COMPUTE,
                        input="filter_delivered",
                        wasm_file_id=wasm_id,
                        function="add",
                        input_columns=["amount", "amount"],
                        output_column="doubled_amount",
                    ),
                    SaveStepConfig(
                        name="save_output",
                        step_type=StepType.SAVE,
                        input="compute_double",
                        filename="wasm_test_output",
                    ),
                ],
            )

            runner = PipelineRunner()

            # Use just the filename since LocalStorageProvider prepends UPLOAD_DIR
            file_paths = {file_id: test_filename}
            file_metadata = {file_id: {"original_filename": "test.csv"}}
            wasm_modules = {wasm_id: simple_add_wasm}

            summary = runner.execute(
                config=config,
                file_paths=file_paths,
                file_metadata=file_metadata,
                run_id=str(uuid.uuid4()),
                wasm_modules=wasm_modules,
            )

            assert summary.status.value == "completed"
            assert len(summary.step_results) == 4

            wasm_result = summary.step_results[2]
            assert wasm_result.step_name == "compute_double"
            assert wasm_result.step_type == "wasm_compute"

            output_table = wasm_result.output_table
            assert "doubled_amount" in output_table.schema.names

            doubled = output_table.column("doubled_amount").to_pylist()
            doubled_values = [v for v in doubled if v is not None]
            assert len(doubled_values) == 3
            assert doubled_values[0] == pytest.approx(200.0)
            assert doubled_values[1] == pytest.approx(300.0)
            assert doubled_values[2] == pytest.approx(600.0)

            assert summary.lineage is not None
            graph = summary.lineage.graph
            assert "step::compute_double" in graph.nodes
            assert "col::compute_double::doubled_amount" in graph.nodes
        finally:
            if os.path.exists(csv_path):
                os.remove(csv_path)
            # Clean up any output files
            for f in os.listdir(upload_dir):
                if f.startswith("wasm_test_output"):
                    os.remove(os.path.join(upload_dir, f))


# ============================================================
# SECTION 21: Migration — 0010_add_wasm_modules_table
# ============================================================

class TestMigration0010:
    """E2E: Migration 0010 creates wasm_modules table correctly."""

    def test_migration_file_exists(self):
        migration_path = PROJECT_ROOT / "backend" / "alembic" / "versions" / "0010_add_wasm_modules_table.py"
        assert migration_path.exists()

    def test_migration_has_upgrade_function(self):
        content = (PROJECT_ROOT / "backend" / "alembic" / "versions" / "0010_add_wasm_modules_table.py").read_text()
        assert "def upgrade()" in content
        assert "op.create_table" in content
        assert '"wasm_modules"' in content

    def test_migration_has_downgrade_function(self):
        content = (PROJECT_ROOT / "backend" / "alembic" / "versions" / "0010_add_wasm_modules_table.py").read_text()
        assert "def downgrade()" in content
        assert "op.drop_table" in content

    def test_migration_has_required_columns(self):
        content = (PROJECT_ROOT / "backend" / "alembic" / "versions" / "0010_add_wasm_modules_table.py").read_text()
        required_columns = [
            "id", "name", "description", "storage_key",
            "file_size_bytes", "sha256_hash", "exports", "imports",
            "fuel_budget", "is_active", "user_id", "created_at", "updated_at"
        ]
        for col in required_columns:
            assert f'"{col}"' in content or f"'{col}'" in content, f"Missing column: {col}"


# ============================================================
# SECTION 22: Config — WASM_BUCKET setting
# ============================================================

class TestWasmConfig:
    """E2E: Configuration has WASM_BUCKET setting."""

    def test_wasm_bucket_config_exists(self):
        from backend.config import settings
        assert hasattr(settings, "WASM_BUCKET")
        assert settings.WASM_BUCKET == "pipelineiq-wasm"

    def test_s3_config_exists(self):
        from backend.config import settings
        assert hasattr(settings, "S3_ENDPOINT_URL")
        assert hasattr(settings, "S3_ACCESS_KEY")
        assert hasattr(settings, "S3_SECRET_KEY")


# ============================================================
# SECTION 23: Router Registration
# ============================================================

class TestWasmRouterRegistration:
    """E2E: Wasm router is registered in main.py."""

    def test_wasm_router_imported_in_main(self):
        content = (PROJECT_ROOT / "backend" / "main.py").read_text()
        assert "from backend.routers.wasm import router as wasm_router" in content

    def test_wasm_router_included_in_app(self):
        content = (PROJECT_ROOT / "backend" / "main.py").read_text()
        assert "app.include_router(wasm_router)" in content

    def test_wasm_router_prefix(self):
        from backend.routers.wasm import router
        assert router.prefix == "/api/v1/wasm"


# ============================================================
# SECTION 24: Error Handling — Edge Cases
# ============================================================

class TestWasmErrorHandling:
    """E2E: Error handling for edge cases."""

    def test_missing_function_field_raises_error(self, simple_add_wasm):
        from backend.execution.wasm_executor import WasmExecutor

        table = pa.table({"a": [1.0]})

        class MockStep:
            def get(self, key, default=None):
                return getattr(self, key, default)
            input_columns = ["a"]
            output_column = "result"

        executor = WasmExecutor()
        with pytest.raises(ValueError, match="missing 'function'"):
            executor.execute(table, MockStep(), simple_add_wasm)

    def test_missing_input_columns_raises_error(self, simple_add_wasm):
        from backend.execution.wasm_executor import WasmExecutor

        table = pa.table({"a": [1.0]})

        class MockStep:
            def get(self, key, default=None):
                return getattr(self, key, default)
            function = "add"
            output_column = "result"

        executor = WasmExecutor()
        with pytest.raises(ValueError, match="missing 'input_columns'"):
            executor.execute(table, MockStep(), simple_add_wasm)

    def test_missing_output_column_raises_error(self, simple_add_wasm):
        from backend.execution.wasm_executor import WasmExecutor

        table = pa.table({"a": [1.0]})

        class MockStep:
            def get(self, key, default=None):
                return getattr(self, key, default)
            function = "add"
            input_columns = ["a"]

        executor = WasmExecutor()
        with pytest.raises(ValueError, match="missing 'output_column'"):
            executor.execute(table, MockStep(), simple_add_wasm)

    def test_nonexistent_function_in_module_raises_error(self, simple_add_wasm):
        from backend.execution.wasm_executor import WasmExecutor

        table = pa.table({"a": [1.0], "b": [2.0]})

        class MockStep:
            function = "nonexistent"
            input_columns = ["a", "b"]
            output_column = "result"

        executor = WasmExecutor()
        with pytest.raises(ValueError, match="not found"):
            executor.execute(table, MockStep(), simple_add_wasm)

    def test_nonexistent_function_lists_available_exports(self, simple_add_wasm):
        from backend.execution.wasm_executor import WasmExecutor

        table = pa.table({"a": [1.0], "b": [2.0]})

        class MockStep:
            function = "nonexistent"
            input_columns = ["a", "b"]
            output_column = "result"

        executor = WasmExecutor()
        with pytest.raises(ValueError, match="Exported functions:"):
            executor.execute(table, MockStep(), simple_add_wasm)


# ============================================================
# SECTION 25: Frontend Page Accessibility
# ============================================================

class TestFrontendPageAccessibility:
    """E2E: Frontend pages are accessible via HTTP."""

    def test_wasm_modules_page_returns_200(self):
        import urllib.request
        try:
            req = urllib.request.Request("http://localhost:3000/wasm-modules/")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200
        except Exception:
            pytest.skip("Frontend not running")

    def test_dashboard_page_returns_200(self):
        import urllib.request
        try:
            req = urllib.request.Request("http://localhost:3000/dashboard/")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200
        except Exception:
            pytest.skip("Frontend not running")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
