# c2-parser remediation report (M-099, M-111, M-144, M-145, M-159, M-162)

Scope: `backend/pipeline/diff_utils.py`, new tests under
`backend/tests/unit/pipeline/`, upload-validation helpers in
`backend/api/files.py`, validate-before-claim name checks in
`backend/api/pipelines.py`. Nothing else touched.
Env: `/tmp/venv-base` (py3.11); unit tests use SQLite + TestClient.

## M-099 — DB String() overflow on long input (Medium) — FIXED (boundary)
- Filenames: **no length check existed** on any upload path
  (`original_filename` is `String(255)`). Added `_MAX_FILENAME_LENGTH =
  255` + `_validate_filename_length()` in `backend/api/files.py`,
  called from `_validate_file_extension()`, which covers all three
  upload entries (multipart, pending-commit, direct). Overlong names
  are now 400 naming `Field 'filename'`; the column stays as backstop.
- Pipeline names: already validated before claim —
  `_validate_claim_name()` → `canonical_pipeline_name()` caps at 255
  chars (422) at both run entry points (`pipelines.py:613,802`),
  before the first-run OWNER grant. Pinned by tests; kept 422 (repo
  convention from M-064/M-089) rather than 400 to avoid breaking
  existing authz/ownership contracts.

## M-111 — pipeline load cannot read .parquet/.xlsx (Medium) — HALF / SKIP
- `backend/execution/steps/load*` **does not exist** (dir holds only
  `save_step.py`) — per instructions, load side documented and skipped.
- Upload boundary (mine) verified honest: `.parquet` accepted +
  magic-header enforced, `.xlsx` → 415 naming openpyxl (M-066 intact).
- Remaining gap for the owner of `backend/pipeline/steps/_core.py`:
  `SUPPORTED_FILE_EXTENSIONS` maps only `.csv`/`.json`, so
  `execute_load` raises `UnsupportedFileFormatError` for `.parquet`
  even though uploads accept it. Either add a `pd.read_parquet`
  reader or reject `.parquet` at upload with 415.

## M-144 — input-validation 500s (Low) — FIXED one real 500, rest verified
- **Real bug found by the new regression test:** `GET
  /api/v1/pipelines/?page=10**18` → `(page-1)*limit` OFFSET overflowed
  the integer binding → 500 (`OverflowError` on SQLite; same on
  Postgres int8). Fixed with a one-line page cap (`min(page,
  1_000_000)`) next to the existing clamp in `list_pipeline_runs`.
  Adjacent to owned code; flagged here for visibility.
- Verified safe (pinned by tests): negative page/limit clamped,
  non-int page → 422 via FastAPI, unknown `status_filter` ignored
  (200, no 500), invalid webhook id → 400/422 (`_parse_webhook_id`),
  oversized `file_size` bounded by `MAX_UPLOAD_SIZE`.

## M-145 — template `on:`→True + version-diff edge cases (Low) — VERIFIED
- `versioning._steps_by_name`/`diff_pipelines` already boolean-key-safe
  (skips non-mapping entries, `sorted(..., key=str)`); `parser` handles
  `raw.get("on") or raw.get(True)`; no template ships a bare `on:` key
  and `fork_template` re-parse preserves `True`-key semantics.
- `diff_utils.strip_diff_markers` never parses YAML, so boolean-key
  documents pass through byte-for-byte; pinned by tests. **No code
  change needed** in owned files.

## M-159 — task-dispatch re-validation (task half) — TEST COVERAGE ADDED
- Runner re-validation confirmed present. Added stored-YAML round-trip
  tests: valid YAML re-parses identically through `get_parsed_pipeline`
  (the dispatch cache entry), and tampered stored YAML makes
  `_run_pipeline` raise `InvalidYAMLError` instead of executing.
  No dispatch-code change (file not owned; none needed).

## M-162 — contract by-id str-vs-UUID 500s (follow-up) — VERIFIED, note
- All by-id read paths (`get/update/delete_contract`,
  `list_run_violations`) call `validate_uuid_format()` before any UUID
  bind → 422, never 500; file endpoints additionally coerce via
  `as_uuid`. Pinned by helper + authenticated-client tests.
- Convention note for the contracts owner: current behavior is **422**
  on garbage UUIDs, not 404 as the finding title suggests
  (`coerce-or-404`). The no-500 requirement is met; changing 422→404
  is a product decision outside this scope.

## Verification
- New: `backend/tests/unit/pipeline/test_c2_parser_hardening.py`
  (20 tests). Regression-first confirmed: 3 fail on pre-fix tree
  (`git stash`), 20/20 pass post-fix.
- Suites: `unit/pipeline` + `unit/api/test_upload_hardening.py` +
  `unit/api/test_api.py` → 298 passed. `ruff check` clean (incl.
  blocking E9/F63/F82). `mypy` clean on both touched API modules.
