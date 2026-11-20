# M-036 Remediation Report — Vectorize Wasm Per-Row Execution Loop

## Finding

`WasmExecutor.execute` called the Wasm function once per row from Python
(`backend/execution/wasm_executor.py`). Each wasmtime FFI call costs ~20us,
capping throughput at ~46k rows/s: measured 50k rows in 1.08s and 300k rows
in 6.46s (implies ~25s for 550k rows, as reported).

## Root Cause (confirmed by benchmark)

Profiling showed `store.set_fuel` + the Python conversion loop are negligible
(<1% of row time); the dominant cost is the per-row `wasm_func(store, *args)`
FFI crossing. Threading does not help (wasmtime calls hold the GIL — 8
threads were 4x *slower* than 1 in a scaling probe).

## Fix

Batch execution through a generated in-Wasm loop driver (same engine, no WASI):

- Per UDF arity, a tiny WAT module is compiled once and cached: it imports
  the user function as `u.f`, exposes linear memory `mem`, and exports
  `run(in_ptr, out_ptr, n)` mapping the UDF over `n` rows inside Wasm.
- Inputs are packed per 4096-row chunk (`struct`, column-major f64), one FFI
  call per chunk; outputs are unpacked from driver memory.
- A chunk that traps (row trap, fuel exhaustion) is re-executed row by row on
  the legacy slow path, so **M-012** (faults null only the output column;
  aliasing still rejected) and **M-130** (chunk owns exactly
  `per_row_fuel × chunk_len`; explicit budgets honored) are bit-for-bit
  preserved. Driver-setup failure (e.g. non-f64 exports) disables batching
  entirely. Store limits raised to `instances=2, memories=2` for the driver.

## Results

| Workload | Before | After |
|---|---|---|
| 50k-row add UDF | 1.08s (46k rows/s) | 0.05s (~1M rows/s, ~22x) |
| 300k-row add UDF | 6.46s | 0.33s (~0.9M rows/s, ~20x) |

## Verification

- New `backend/tests/unit/wasm/test_m036_vectorized.py` (10 tests): 300k-row
  `<5s` benchmark + exact parity; batch trap isolation (all-trap, sparse
  traps across chunks, string columns, i32-export fallback); batch fuel
  semantics (heavy UDF, tiny budget, infinite loop, empty table).
- All pre-existing wasm unit tests + `test_wasm_sandbox_security.py` pass
  unmodified (52 passed total); `backend/tests/unit/cost/` passes.
- `ruff check`, `ruff format --check`, and `mypy` clean on touched files.

## Files

- `backend/execution/wasm_executor.py` (modified)
- `backend/tests/unit/wasm/test_m036_vectorized.py` (new)
