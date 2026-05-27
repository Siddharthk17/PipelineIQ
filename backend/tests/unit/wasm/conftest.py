"""Test Wasm module fixtures compiled from WAT (WebAssembly Text Format)."""

import pytest
from wasmtime import wat2wasm


@pytest.fixture
def simple_add_wasm() -> bytes:
    """Wasm module: (f64, f64) -> f64 add function."""
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
    """Risk score function: 4 f64 inputs -> 1 f64 output."""
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
    """Wasm module with an infinite loop — killed by fuel budget."""
    return wat2wasm("""
        (module
            (func (export "infinite") (param f64) (result f64)
                (block $b (loop $l br $l))
                local.get 0
            )
        )
    """)


@pytest.fixture
def divide_by_zero_wasm() -> bytes:
    """Wasm module: division by zero."""
    return wat2wasm("""
        (module
            (func (export "div_zero") (param f64) (result f64)
                local.get 0
                f64.const 0.0
                f64.div
            )
        )
    """)


@pytest.fixture
def multi_export_wasm() -> bytes:
    """Wasm module with three exported functions."""
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
    """Bytes that are not a valid Wasm module."""
    return b"this is not a wasm module"
