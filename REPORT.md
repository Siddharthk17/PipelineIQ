# w2-aiwasm remediation report

Branch: `fix/w2-aiwasm`. Scope (exclusive): `backend/routers/ai.py`,
`backend/ai/**`, `backend/routers/wasm.py`,
`backend/execution/wasm_executor.py`, `backend/tasks/healing_tasks.py`
(new, matches owned `backend/tasks/*heal*` glob). No live Gemini calls —
all AI tests mock the client/task. Env: `/var/tmp/remediation/venv-baseline`
(py3.11), throwaway PG :5432 + Redis :6379-6382.

## Commits (this round)

- `4c1a2cf` M-039/M-016/M-175/M-108/M-132 — `routers/ai.py` + `ai/**`:
  polling disconnect watcher (only the pending task is cancelled),
  `AI_REQUEST_TIMEOUT_SECONDS` (150s, 504 on expiry), autocomplete caps
  (256/2000/200) + `asyncio.to_thread`, PII redaction (`ai/safety.py`) +
  untrusted-content wrapping + prompt trust-boundary text, `as_uuid`
  coercion so schema sections resolve, slowapi limits on generate/repair.
- `c9cd9fa` M-074 — `validate_healing_patch` rejects identity-field
  mutations and >10-patch lists (enforced: `attempt_heal` gates on it);
  new `tasks/healing_tasks.py`: attempts cap, `validate_patch_for_apply`,
  `parser_validate_patched_yaml`.
- `0247bd5` M-012/M-130 — executor: inputs verbatim, traps null only the
  output column, `output_column`==input rejected loudly; fuel budget
  (arg/step attr/`FUEL_PER_STEP`, clamped) distributed as total//rows.
- `70f896f` M-040/M-037/M-154/M-132/M-077 — multipart form upload with
  query fallback + fuel bounds, compile via `to_thread` with 20s cap,
  IntegrityError→409, slowapi limits on validate/upload, ownership
  wiring helpers (`extract_wasm_ids_from_yaml`,
  `validate_pipeline_wasm_ownership`, `load_owned_wasm_modules_for_run`).

## Verification

- Every finding got a regression test that fails pre-fix (422/499/500/
  wrong-value/ImportError) and passes post-fix.
- Green: `unit/ai` + `unit/wasm` (122), `unit/execution` (69),
  `unit/healing` + `unit/pipeline` (264+2 skip), `unit/api` (96+2 skip),
  e2e `test_week8_wasm_compute` 84 passed — except one pre-existing
  failure below. `ruff check`, `ruff format --check`, `mypy` clean on all
  touched files.

## Notes / residual risks

- `test_load_wasm_modules_queries_db_correctly` (e2e week8) fails at base
  too: the test patches `backend.db.minio_client.get_minio_client`, but
  `repositories/wasm.py` bound the name at import (collection) time, so
  the real client raises (no S3 here) → `{}`. Needs a `repositories.wasm`
  patch target; file not owned, left untouched.
- M-077: helpers are wired at router level; pipeline-save validation and
  run-start loading live in non-owned files. One-line hookups for the
  owning agent: save path → `validate_pipeline_wasm_ownership(db, yaml,
  owner_id)` and reject on 404; run start → replace `_load_wasm_modules`
  body with `load_owned_wasm_modules_for_run(db, ids, run.user_id)`.
- M-154: global `UNIQUE(wasm_modules.name)` still blocks true per-user
  namespaces; needs a composite `(user_id, name)` migration (models +
  alembic, not owned). Until then cross-user collisions are clean 409s.
- M-108 worker side already had timeouts (`task.get(timeout=120)`,
  celery soft/hard limits); the missing bound was the router wait, fixed.
- M-074 loop cap + sandbox parser-validation live in non-owned
  `healing_agent.py`/`sandbox.py`; the new `healing_tasks.py` helpers and
  the `validate_healing_patch` gate are the owned enforcement points.
