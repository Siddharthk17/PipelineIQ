# W3 Product remediation report (fix/w3-product)

Scope: files owned by W3 only — `backend/api/files.py` (preview/metadata/stats),
`backend/api/dashboard.py`, `backend/routers/catalog.py` (untouched — no defect found),
`backend/routers/lineage_export.py`, `backend/routers/column_policies.py` (read paths),
`backend/services/storage_service.py`, `backend/storage/**`, `backend/execution/steps/save_step.py`,
plus owned frontend pieces (`middleware.ts`, catalog/storage/login/register pages,
`lib/api.ts`, `lib/yamlGraphSync.ts`, `hooks/usePipelineEditor.ts`).
No changes to auth, contracts routes, or streaming.

## Per-finding status

- **M-061 (S3/MinIO no timeouts; tier stats hang + healthy-when-down)** — FIXED.
  Shared `S3_CLIENT_CONFIG` (SigV4, connect 5s / read 30s, 2 retries) applied to all
  boto3 clients (`storage_service`, presign client, `save_step` fallback/presign).
  MinIO lifecycle client gets urllib3 connect/read timeouts. `GET /storage/tier-health`
  now reports per-tier `ok/unavailable` and is unhealthy on any tier error (previously a
  stats dict full of `{"error": ...}` still returned `healthy: true`).
  Verified: down-tier and raising-bus cases return `healthy: false`; boto timeouts asserted.
- **M-067 (presigned upload bypasses MAX_UPLOAD_SIZE)** — FIXED in `confirm_direct_upload`:
  over-cap objects rejected with 413 and the orphaned object deleted; empty objects
  rejected with 400. Verified via unwrapped-handler regression (413+delete / 400 / accept).
- **M-090 (catalog SQLite 500; dead routes; login bounce; a11y)** — FIXED (backend + frontend).
  `list_catalog_pipelines` used PostgreSQL-only `DISTINCT ON` (500 on SQLite) and dashed
  `str(uuid)` bindings that match neither SQLite (hex) nor PG text comparison; replaced with
  a portable GROUP BY + join, hex UUID binding, and datetime-tolerant serialization.
  Verified against SQLite (2 pipelines listed, search filter works; 114 catalog tests pass).
  Frontend: catalog search/impact handle non-OK + non-JSON errors with an error state
  (`role="alert"`) instead of unhandled rejections; `/login` and `/register` redirect
  authenticated visitors to `/`; storage page renders an admin-only notice on 403.
- **M-092 (storage stats exposed to viewers)** — FIXED: `/storage/stats` and `/tier-health`
  now require `get_current_admin`; unused `get_current_user` import removed.
- **M-109 (OpenLineage wrong COMPLETE mapping)** — FIXED in `_map_status_to_event_type`:
  COMPLETED/HEALED→COMPLETE, FAILED/TIMEOUT/CONTRACT_VIOLATION→FAIL,
  CANCELLED/STREAMING_STOPPED→ABORT, RUNNING/HEALING/STREAMING_*→RUNNING,
  PENDING→START, unknown→OTHER (was: everything →COMPLETE). 13 openlineage tests pass.
- **M-136 (edge middleware skips revocation/role)** — BOUND + DOCUMENTED. Edge cannot query
  Redis, so revocation stays fail-closed at the API (`get_current_user`); the middleware now
  additionally requires `sub`/`jti`/`role` claims so malformed tokens never reach a page shell.
- **M-142 (cost estimator naive/PG-only)** — FIXED + DOCUMENTED. Historical percentile uses
  `PERCENTILE_CONT` on PG and portable `AVG` elsewhere (SQLite has no ordered-set aggregates);
  heuristic fallback is now explicit per step (`note`) and documented as order-of-magnitude in
  the module docstring. SQLite regression returns estimates with fallback notes.
- **M-163 (PII detectors)** — SKIPPED by scope: `detect_pii_columns` lives in
  `backend/security/column_security.py` (perf cluster). The read path in
  `routers/column_policies.py` was inspected; no change made here.
- **M-075 remainder (formula neutralisation in SAVE outputs)** — FIXED in `save_step.py`:
  string columns neutralised at the Arrow level (leading `'` for `= + - @` / tab / CR),
  covering CSV, JSON and Parquet uniformly, mirroring `files.py` preview logic.
  Verified live (`'=cmd` in emitted CSV).
- **M-095 (S3-default in save_step)** — FIXED: `_get_minio_client` returns `None` when no S3
  is configured (default local deployment) instead of building an AWS-bound client;
  `execute_save_step` then persists via the configured storage provider and returns a
  `storage://` object reference; `refresh_download_url` fails closed (callers fall back to
  the stored URL). Existing S3-path unit tests (mocked client) unaffected.
- **M-119 (editor destructive states)** — FIXED: `graphToYAML` throws on cycles instead of
  returning `""` (which the emit path wrote into the user's YAML); solo (non-collaborative)
  editing no longer opens a Yjs relay connection (`currentUser`/`authToken` gated on
  `collaborative`). Builder test updated to assert the throw.
- **M-184 batch (204-JSON, planner estimates, misc)** — FIXED so far as owned files go:
  `fetchWithAuth` returns `undefined` for 204/empty bodies instead of crashing in
  `res.json()` (covers 204 DELETEs, e.g. column-policy delete). Planner estimate plumbing
  verified reachable (`/api/runs/estimate` route matches frontend call); estimator fallback
  notes available per step (M-142).

## Verification
- Backend: `ruff check` clean on all touched files; `mypy` clean on all touched files;
  targeted suites green — save_step/storage (61), catalog incl. openlineage (114+13),
  cost (20), api (102 passed, 2 skipped).
- Frontend: `tsc --noEmit` exit 0; vitest targeted suites green
  (builder+collaboration+middleware+hooks: 11 files, 108 tests).
  Note: the worktree has no own `node_modules`; verification used symlinks to the shared
  repo installs (`tsc` from the workspace root). Symlinks are untracked and removed before
  handoff — `git status` shows only intended files.

## Follow-ups for other owners
- Data cluster: model-level S3 default noted in M-095 (not touched; coordinated via
  fail-closed local fallback here).
- API cluster: `estimate_run_cost` response could surface per-step `note` (field exists on
  `StepEstimate`; endpoint shapes its own dicts — `pipelines.py` untouched per scope).
