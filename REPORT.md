# Wave-3 authz remediation report (`fix/w3-authz`)

Scope: `backend/api/permissions.py`, `backend/api/versions.py`,
`backend/api/authz.py` (new), `backend/security/**` (reviewed),
`backend/contracts/enforcement.py` (new) + `validator.py` sanitization,
worker consumer matcher + version saver wiring. Untouched by design:
`auth.py`/`api/auth.py` (Wave-1 / w1-auth), routers, wasm, y-websocket
(w1-yjs), `config.py` (w3-config). Every finding below was verified
against current code first.

## Fixed in this worktree

- **M-076 (Medium) — worker consumer matcher.** `_block_downstream_schedules`
  matched schedules by bare `pipeline_name`. Fixed: matching is scoped to
  `(owner_id, pipeline_name)` via `backend/contracts/enforcement.py`;
  the breach call site passes `active_contract.user_id`. Regression test:
  victim schedule with the same name is untouched while the owner's own
  schedule is blocked.
- **M-064 (Medium) — canonical name.** Permission was checked against the
  request name while versions were saved under the YAML name (worker used
  `config.name`; run creation used an unvalidated `name or config.name`).
  Fixed: `canonical_pipeline_name()` in `backend/api/authz.py` is now used
  by claim validation (also closing a `re.match`-prefix hole → fullmatch),
  run creation, the worker version saver (uses `pipeline_run.name`), and
  the version/permission endpoints.
- **M-093 (Medium) — unified ownership checks.** `check_pipeline_access()`
  / `require_pipeline_owner()` encode one rule (admin → own contract →
  explicit grant; 404 with no grant, 403 on insufficient level) and are
  adopted by versions + permissions. Side effect of unification: contract
  owners can now read versions without a redundant permission row. Tests
  cover version/contract/lineage surfaces for owner, grantee, and stranger.
- **M-087 (Low).** Breach messages no longer embed `{exc}` internals;
  invalid-level grant errors no longer echo input.
- **M-105 (Low).** Grant insert catches `IntegrityError` → rollback → 409
  (never 500) on concurrent claims. Contract-side validate-before-claim
  was already Wave-2; verified intact (`allow_new` + atomic claim).
- **M-150 (Low, in-scope part).** Grant level changes now emit
  `permission_updated` audit events (creation already logged; revocation
  already logged). Anti-oracle 404s kept in versions/permissions.
- **M-153 (Low, in-scope part).** Same-level re-grant → 409 (no silent
  duplicates); level change applies + audited; revoking the last OWNER
  grant → 409 unless another ownership path remains.
- **M-141 (Low, in-scope part).** Permission/version surfaces reviewed:
  no secret/paths echo, no email handling, field labels correct; invalid
  level message de-echoed.

## Verified out of scope (no change here; evidence + owner)

- **M-045.** No `os.environ[...SECRET...]` reads remain; `SECRET_KEY` flows
  from settings (Wave-1). Residual nit for **w3-config**: `config.py`
  reads `_os.environ.get("ENVIRONMENT")` instead of `self.ENVIRONMENT`.
- **M-086** (w1-auth). Logout revokes best-effort (`revoke_token` swallows),
  always clears the cookie, always returns success with no revocation
  result. Prescription needs `auth.py` change: return per-mode result.
- **M-091** (w1-auth). `verify_token` does not require `exp`/`jti` (legacy
  tokens without them still verify; 1440-min default is sane, no 100-year
  default found). bcrypt 4.0.1 **silently truncates >72B** (verified) —
  needs explicit warn+truncate in `auth.py`.
- **M-104** (w1-auth). `login` short-circuits (`if not user or not
  verify...`) — unknown users skip bcrypt: timing oracle. Needs
  hash-before-lookup or uniform delay in `auth.py`.
- **M-151** (w1-auth). Already normalized: `LoginRequest` strips+lowers.
- **M-148** (w1-yjs). Parity gap confirmed: backend blocklists
  `pipelineiq-dev-secret-key-change-in-production-2024(-minimum-32chars)`
  (47/52 chars) but `server.js` only rejects `<32` chars / `change-me-*`,
  so those dev secrets pass y-websocket yet fail backend.
- **M-150 remainder** (w1-auth). `PATCH /users/{id}/role` has no audit log
  (logger only); register conflict message names the taken field
  (`email` vs `username` oracle) — normalize to a generic message.
- **M-153 remainder** (w1-auth). No user-deactivation endpoint and no
  explicit refusal (only `is_active` checked at login).
- **Found while testing, belongs to w2-contract:** by-id contract
  endpoints (`GET/PUT /contracts/pipelines/{name}/{contract_id}`) compare
  the `Uuid` column to the raw `str` path param → 500 on SQLite (and
  likely PG). Endpoints need `as_uuid()` conversion. Not touched (not my
  files); M-093 contract-surface test uses the list endpoint instead.

## M-149 — HS256 rotation runbook (no `kid` support exists)

Single shared `SECRET_KEY` signs all JWTs; y-websocket uses separate
`JWT_SECRET` (rotate both together). No key-id header is emitted — rotation
is a flag day, so: (1) generate `openssl rand -hex 32`; (2) set it in the
secret store as `SECRET_KEY` (+ `JWT_SECRET` for yjs) on all replicas
**before** restart (mixed keys invalidate each other's tokens — logout all
sessions or accept forced re-login); (3) rolling-restart API, workers,
beat, y-websocket together; (4) confirm `POST /auth/login` + `/auth/me`
round-trip; (5) keep the old key archived 2× token lifetime (24h default),
then destroy. Cheap `kid` support (future): add `kid` header claim at mint,
verify against `{current, previous}` key map, retire previous after TTL.

## Validation

- New: `backend/tests/unit/security/test_w3_authz.py` (11 tests; 7 failed
  on pre-fix code for the right reasons, 4 pass as guards).
- Suites green: `unit/security` + `unit/contracts` +
  `security/test_permissions.py` (111 passed), `unit/api` + `unit/scheduling`
  (162), healing/persistence/versioning/week11 (91).
- `ruff check` clean on all touched files; `mypy` clean on all touched
  modules (repo config).
- Live-stack tests requiring docker were not run (LOCAL ONLY, no stack).
