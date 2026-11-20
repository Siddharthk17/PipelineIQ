# Wave 3 Perf (M-036/060/084/103/142/163) — Report

Worktree `fix/w3-perf`. Files touched (exclusively owned):
`backend/pipeline/runner.py`, `backend/pipeline/lineage.py`,
`backend/execution/steps/save_step.py`, `backend/tasks/profiling.py`,
`backend/cost/estimator.py` + 5 new regression test files.

## Fixed (measured, tests-first)

- **M-060 runner** (`runner.py`): `_execute_single_step` called
  `output_table.to_pandas()` on EVERY step even with no contract
  (`validate_step_contract(None, df)` returns `[]` without touching df —
  verified in `contracts.py`). Now skipped when `contract` is None.
  Measured: ~3.3ms/200k rows/step saved + peak-memory halved on that path.
  Test: `test_runner_contract_perf.py` (spy table counts `to_pandas`).
- **M-084 lineage** (`lineage.py`): `get_column_ancestry` ran a full
  `nx.topological_sort` per query and used `list.pop(0)` BFS (O(n²)).
  Added version-counter cache: all mutations go through
  `_add_node`/`_add_edge`, which invalidate `_topo_cache`; queries reuse it
  (M-140 respected: keys untouched, invalidation is airtight).
  Measured (12k nodes/24k edges): ancestry 20× 202.6ms → 96.1ms (2.1×),
  react_flow 5× 251.9ms → 199.4ms. API/Redis layers already cache
  responses (`api/lineage.py` 3600s, `get_cached_run_lineage`), so this
  speeds cache-miss + in-process (serialize/export) paths.
  Test: `test_lineage_topo_cache.py` (parity, reuse, invalidation).
- **M-142 profiling passes** (`tasks/profiling.py`): `profile_dataframe` +
  `compute_completeness` did two full `isna` passes. Completeness is now
  derived from the profile's per-column `null_count`
  (`_completeness_from_profile`) — proven exactly equal on null/empty/
  all-null frames. Measured: ~6.2ms/200k rows eliminated per profile run.
  Test: `test_profile_single_pass.py`.
- **M-142 cost estimator** (`cost/estimator.py`): three correctness bugs —
  (1) steps expose `.step_type` (StepType), code read `.type`, so EVERY
  step estimated as "load"; (2) confidence used the GLOBAL data-point
  total instead of per-type depth; (3) historical p50 scaled by global
  input rows instead of per-step `row_in` (downstream steps
  overestimated). Fixed via `_step_type_of` + per-type
  `historical_points` + row_in scaling. Test:
  `test_cost_estimator_perf.py` (3 failing→passing).
- **M-060 save JSON (incidental)**: measured Arrow `to_pylist`+orjson
  (~200ms) vs pandas `to_dict` (~130ms) on 200k rows — pandas stays
  (kept only measured wins). Same function had a real crash: any
  datetime column → `TypeError` (orjson can't serialize `pd.Timestamp`).
  `sanitize()` now normalizes `Timestamp→datetime`, `NaT→None` at zero
  measurable cost. Test: `test_save_json_arrow.py`.

## Verified, out of this wave's files (not touched)

- **M-036**: per-row loop confirmed at
  `backend/execution/wasm_executor.py:172` (`store.set_fuel` + call per
  row; pure-Python loop overhead alone ≈0.3s/550k rows, the Wasm call +
  fuel-set per row dominates). Owned by the WASM wave — left for it.
- **M-060 core**: bulk Arrow↔pandas conversions live in
  `backend/pipeline/steps/_core.py` (every executor does
  `to_pandas()` in / `from_pandas()` out) — engine wave's file.
- **M-142 analyzer correctness** (JSON cells, booleans, 0% completeness,
  JSONL counting): `backend/profiling/analyzer.py` — data wave's file.
- **M-163 CONFIRMED**: `infer_semantic_type` can emit only
  {boolean, categorical, currency, datetime, email, identifier,
  integer_id, numeric, phone, text, url} but `PII_SEMANTIC_TYPES`
  requires {address, credit_card, email, ip_address, person_name, phone,
  ssn} → `detect_pii_columns` can never fire for 5/7 types
  (address, credit_card, ip_address, person_name, ssn). Fix belongs to
  `profiling/analyzer.py` + `security/column_security.py` owners.
- **M-103**: no async-endpoint blocking I/O in owned files — runner/
  save/profiling all execute in Celery workers (sync OK);
  `profile_file` already bounded (`PROFILE_MAX_ROWS` sampling) on the
  `bulk` queue. Endpoint-level + bcrypt pool stay per finding.

## Gates

357 passed (`pipeline, profiling, save_step, execution, cost` unit dirs);
258 passed (`-k "lineage or catalog or contract or wasm or cost"`);
ruff (incl. E9,F63,F82) + mypy clean on all touched sources.
