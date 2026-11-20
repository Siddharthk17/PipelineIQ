# W3 Engine Remediation Report (branch `fix/w3-engine`)

Scope: `backend/pipeline/**` except `steps/_core.py` (Wave-1 owned, read-only).
Env: shared venv `/tmp/venv-base` (py3.11); PG localhost:5432 up; no redis-cli
in container (unit tests use fakes; Redis-backed paths untested locally).
Gates: new regression suite `backend/tests/unit/pipeline/test_w3_remediation.py`
(31 tests, written first — 27 failed pre-fix); module suites
(pipeline 218, execution, scheduling, api, streaming, templates, save_step,
contracts, healing — all green); `ruff check` clean; `mypy` clean (8 files).

## Fixed in this branch

- **M-015** — Step-cap is now enforced *early*: `validate()` returns right
  after the name/steps/count checks when over `MAX_PIPELINE_STEPS`, skipping
  the reference walk. Reference membership is set-based (was O(n²) list
  scan). The 51-step → `200 + is_valid:false` API contract is preserved
  (pinned by `test_limits.py` expectations).
- **M-031** — `InvalidYAMLError` truncates `yaml_error` to 2000 chars so
  YAML alias/anchor chains cannot blow up API error bodies and logs.
- **M-041** — `strip_diff_markers` rewritten hunk-aware: no diff headers →
  input returned byte-identical (doc markers, col-0 list items safe); with
  headers, only real headers are dropped and `+`/`-`/` ` prefixes are
  resolved inside `@@` hunks only. Dead duplicate in parser removed.
- **M-100** — `RecursionError` from deep nesting caught → `InvalidYAMLError`
  (400-class), both in the main parse and the quote-repair path. Non-string
  YAML input also rejected.
- **M-110** — Planner now parses via the single shared path
  (`PipelineParser.parse_raw` + new `extract_envelope` helper): diff
  stripping, quote repair, size cap, recursion guard identical to
  `/validate`. Non-list `steps` and non-dict step entries yield failed
  `StepPlan`s / warnings instead of 500s; all estimate branches hardened
  against ragged types. Optional `parsed_config=` kwarg avoids double parse.
- **M-113** — Parser reports wrong-typed params as `ValidationError`s with
  step context (file_id, input/left/right, columns, mapping, group_by,
  by/order incl. YAML `false`, query/expression/topic, pivot/unpivot,
  sample n/fraction/random_state/stratify_by, fill_nulls strategy+columns,
  wasm fields, stream ints, aggregations shape, rule mappings, per-check
  required params, inline contracts incl. dtype allowlist). Unknown
  fill_nulls strategies rejected (executor silently no-ops them).
  `validators.execute_validate` never raises: malformed rules →
  failed error-severity results (safety net under the parser's 400s).
  `contracts.parse_contract_from_yaml` raises precise `ValueError`s.
- **M-143** — Duplicate save filenames in one pipeline rejected at
  validation: run-scoped outputs live at `outputs/{run_id}/{filename}`, so
  a second save with the same name overwrites the first. (The storage
  fallback path already uuid-suffixes; primary path is another cluster's.)
- **M-159** — `PipelineRunner.execute()` re-validates the parsed config
  (refs, file_ids against `file_paths`, unsupported steps) before running
  and raises `StepExecutionError` on failure; opt-out via
  `revalidate=False`.
- **M-164** — `MAX_PIPELINE_YAML_BYTES = 256_000` enforced in `_parse_yaml`
  via new `PipelineYAMLTooLargeError` (carries sizes; routes should map to
  **413** — currently the generic handler yields 422, a route-cluster
  one-liner).
- **M-145** — `versioning.diff_pipelines` survives ragged stored YAML
  (non-dict steps, missing names, bool keys, scalar/mapping envelopes,
  unparsable YAML → empty index, name `"unknown"`).

## M-112 semantic pins (this cluster)

`VALID_FILL_NULLS_STRATEGIES = {constant, forward_fill, backward_fill, mean,
median, mode}` mirrors `_core.execute_fill_nulls`; test pins that every
strategy validates and anything else is rejected. `sort` with YAML
`order: false`/`by: false` → 400s. NULL-group-key / `not_equals`-NULL /
agg-alias / sample-state / LIKE-wildcard semantics live in
`steps/_core.py` + `execution/duckdb_executor.py` (other clusters) — no
LIKE/wildcard code exists in this cluster's files (verified by grep).

## Out of scope (other clusters own the code; no change made here)

- **M-111** — `.parquet/.xlsx` *load* readers live in `_core`
  (`SUPPORTED_FILE_EXTENSIONS = {.csv, .json}`); `415` mapping lives in
  routes. Existing `UnsupportedFileFormatError` already names supported
  formats. Needs: _core reader impl (or extension allowlist) + route 415.
- **M-114** — `WORKER_MAX_ROWS_TO_SCAN` consumed in
  `execution/duckdb_executor.py`; `CELERY_CONCURRENCY` likewise; dedup
  ordering and clock mixing outside `backend/pipeline/**`.
- **M-144** — pagination/status_filter/webhook/page-overflow are
  routes/schemas (no code in this cluster).
- **M-099** — model `String` lengths (no models in this cluster).
- **M-145 (templates half)** — template rendering/name helpers are not in
  `backend/pipeline/**`; only the version-diff half was fixable here.
- **M-159 (task half)** — `tasks/pipeline_tasks.py` still parses without
  `validate()`; covered defense-in-depth by the runner gate above, but the
  task should call `validate()` for early, actionable errors.
