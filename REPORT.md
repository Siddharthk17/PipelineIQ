# W2 Upload Remediation Report

Branch: `fix/w2-upload`. Files changed (exclusively): `backend/api/files.py`,
`backend/tasks/profiling.py`. Verified with throwaway local PG + redis,
venv `/var/tmp/remediation/venv-baseline` (py3.11), ruff 0.17.5, mypy.

## Method

Per finding: wrote a regression test FIRST against unpatched code, confirmed
it failed, then fixed, then re-ran. Scratch tests lived in
`backend/tests/unit/api/test_w2_scratch.py` during development and were
deleted before committing (10/10 failed pre-fix, 10/10 pass post-fix).
Existing suites kept green: `unit/api` (incl. `test_upload_encodings`,
`test_api`), `unit/profiling`, `healing/test_drift`,
`contracts/test_contract_isolation` — 156 passed, 3 skipped.
`backend/pipeline/steps/load*` does not exist in this tree (only `_core`,
`sql_step`, `wasm_compute`); nothing to change there. No separate validation
endpoints file exists; validation helpers live in `files.py`.

## Findings fixed

- **M-017 (High) / M-102 (Medium, same code paths)** — `upload_file` had no
  size cap (`storage_service.upload` streams uncapped) and no early
  `Content-Length` reject; metadata/preview/profile called `handle.read()`
  / full `pd.read_*` (60MB file -> ~524MB peak).
  Fix: `_enforce_declared_size` (413 on declared `Content-Length` over cap +
  1MB multipart slack; exact-cap uploads still accepted) on `/upload` and
  direct-upload; `_CappedReader` proxy (seek/tell-delegating, S3-safe) aborts
  over-cap copies with 413 and deletes the partial object. CSV metadata is a
  single streaming pass (incremental decoder, `strict=True`, 16MB/field cap,
  early row-cap abort); dtype sample rebuilt from the first 1000 parsed
  records via `csv.writer` round-trip. JSON metadata counts via a streaming
  record splitter (validates every value, keeps 100 for schema). Previews
  read only header+N rows (CSV), first N records (JSON), first N rows across
  row groups (parquet). Profile JSON sampling streams take-N instead of the
  `lines=True`-then-full-read fallback; parquet profiling was already
  row-group bounded and is now covered by a regression guard.
- **M-101 (Low)** — malformed CSV crashed `finalize` and orphaned the stored
  object (NUL file was even accepted with 201). Fix: `_finalize_uploaded_file`
  deletes the stored object on any `HTTPException` from validation/metadata;
  NUL bytes and strict-mode csv errors map to 400.
- **M-038 (Medium)** — sniffing rejected pretty-printed objects (parsed only
  the first line) and arrays >1MB (parsed a truncated 1MB window).
  Fix: `_check_json_sample` checks the first non-ws char, then validates the
  first complete record; truncation past the sniff window is provisionally
  accepted and fully validated by streaming metadata. Accepts objects,
  pretty arrays, JSONL, scalar sequences; rejects non-JSON and trailing
  garbage with 422 + cleanup.
- **M-066 (Medium)** — `.xlsx` advertised but `openpyxl` is not installed
  (confirmed absent from venv and requirements; adding a dependency was out
  of scope for this worktree). Fix: honest 415 at extension validation,
  metadata, and preview; removed dead `read_excel` paths (also resolves the
  ruff SIM102 at old `files.py:631`, plus TRY301/I001 clean, ruff + mypy
  green).
- **M-075 (Low)** — preview returned raw formula cells. Fix:
  `_neutralize_formula_cells` prefixes `= + - @`, tab, CR-leading string
  cells with `'` (OWASP CSV-injection guidance), numerics untouched.

## Follow-ups for other worktrees (out of scope here)

- `backend/config.py` `ALLOWED_EXTENSIONS` still lists `.xlsx`, and any docs
  advertising xlsx need updating to match the 415.
- Pipeline-run exports (`execution/steps/save_step.py`,
  `api/pipelines.py` export/download) also emit CSV/JSON cell data and need
  the same formula neutralisation.
- `S3StorageProvider.download` buffers the whole object into `BytesIO`
  (storage_service is not owned by this worktree); true end-to-end streaming
  for S3 needs a ranged/streaming download there.
- Profile full reads remain for files under `PROFILE_MAX_ROWS`
  (inherent DataFrame cost); consider lowering `PROFILE_SAMPLE_ROWS` or a
  byte budget if 1M-row JSON profiles still spike.
- `csv.field_size_limit` is raised process-wide (16MB) while scanning; a
  single >16MB CSV field is rejected 400.
- `/mnt/data2` (main-checkout disk) hit 100% twice during this work from
  accumulated `backend/uploads` test debris; commits were blocked until space
  was freed. Worth a periodic sweep or moving test storage to tmpfs.
