# Wave-3 config/docs/tests remediation report (branch `fix/w3-config`)

Static-only env (no docker daemon): every claim below was verified by reading
code, running what can run (ruff, vitest, k6 guard paths, `Settings`
validators, `npm audit`), and byte-checking scripts with `bash -n`.
Full backend pytest needs live Postgres + Redis (see `README.md` fail-fast
conftest) and could not run here; new DB-backed tests follow existing
fixture patterns and pass ruff.

## Fixed in this branch (per-item commits)

- **M-029/M-139 — load/chaos prod guards.** `k6/*.js` required `BASE_URL`
  but accepted anything; `chaos/chaos-scenarios.sh` defaulted to
  `https://pipelineiq-api.onrender.com`. Now: k6 throws on non-local
  targets unless `ALLOW_PROD_LOAD=1`; chaos defaults to
  `http://localhost:8000` and exits unless `CHAOS_ALLOW_PROD=1`.
  Verified verbatim: prod URL refused (`Error: Refusing to load-test
  non-local target ... without ALLOW_PROD_LOAD=1`), localhost passes the
  guard, override passes. `playwright.config.ts`/`e2e/*.spec.ts` already
  gate on `E2E_BASE_URL` (skip without it) — no change needed.
- **M-188 — config validation gaps.** `backend/config.py` now rejects
  unknown `STORAGE_TYPE` at startup (normalized to lowercase) and rejects
  `STORAGE_TYPE=s3` with missing creds/bucket/endpoint in production
  (warns elsewhere). Verified all four paths against `Settings`.
  `SECRET_KEY` logic untouched (Wave 1). Gunicorn `/tmp` log dir lives in
  `backend/gunicorn.conf.py` (not owned) — no finding: dir is created with
  `os.makedirs(exist_ok=True)` at startup.
- **M-176 — telemetry/metrics.** `backend/telemetry.py`: dropped the
  frozen-at-import `OTEL_SERVICE_NAME` copy (only readers were tests; now
  use `_get_otel_service_name()`). `backend/metrics.py`: added
  `set_active_users_total()` / `set_celery_queue_depth()` write paths for
  the two gauges that dashboards (`grafana/.../pipelineiq.json`) and the
  `CeleryQueueDepthHigh` alert reference but nothing ever set — with unit
  tests incl. concurrent writes (4 passed, ruff clean). Wiring the setters
  into a beat collector is a product-code manual action (below).
  Remaining M-176 notes (out of scope, verified): `main.py`
  `_HealthCheckFilter` is defined but never attached while
  `uvicorn/gunicorn.access` loggers are disabled outright; Sentry
  `traces_sample_rate=0.1` is hardcoded vs `OTEL_SAMPLE_RATE`; the
  `ApiHigh5xxRate` alert keys on `http_requests_total` — confirm the
  instrumentator emits that exact series or the alert is dead.
- **M-127 — test hygiene + coverage.** Bounded the unbounded
  `while True` completion poll in `test_permissions_read.py` (120 s
  deadline, asserts status). New `test_audit_dashboard.py`: admin-only
  `/audit/logs` (401 anon / 403 viewer), filter + pagination + 422s, row
  shape, `/logs/mine` isolation between two users, dashboard auth + shape.
  New `test_metrics_gauges.py`: setter + thread-safety coverage.
  Real sleeps elsewhere are bounded fakes (m039/m108 simulated latency,
  rate-limit backoff) — left as-is.
- **Restore-doc fix.** `DEPLOYMENT_GUIDE.md` claimed `pg_dump (-Fc)`;
  Wave-1 `scripts/backup.sh` emits plain SQL and `restore.sh` loads via
  `psql -v ON_ERROR_STOP=1`, using `pg_restore` only for legacy `-Fc`
  archives (magic `PGDMP` sniff, `--list` pre-validation). Doc now matches
  the scripts exactly.
- **M-128 — docs drift (verified statement-by-statement).**
  `README.md` (testing/config sections): coverage table counts corrected
  (`api` 10→13, `pipeline` 12→13, `ai` 11→17, `execution` 4→8,
  `scheduling` 4→6, `streaming` 2→4, `wasm` 2→6, + unit-root metrics row),
  frontend 23→24 files / 241→245 tests (vitest run: 24 files, 245
  passed), k6/chaos commands now carry `BASE_URL` + opt-in flags (all
  commands run verbatim), `MINIO_IMAGE_TAG` `latest`→pinned tag,
  `JWT_SECRET` row corrected to `JWT_SECRET=${SECRET_KEY}` (verified in
  `docker-compose.yml:762`, `k8s/y-websocket/deployment.yaml:39-43`).
  `.env.example`: added missing `OTEL_SERVICE_NAME`, `JAEGER_UI_URL`,
  `AUTONOMOUS_HEALING_ENABLED`. Retention/version claims spot-checked
  against code (DLQ 7 d, Prometheus 15 d, orphans 90 d, `/api/catalog/orphans`
  exists, APP_VERSION 12.8.4); internal doc links resolve.
- **M-070/M-188 docs.** `.env.example` now documents dev defaults
  (SECRET_KEY auto-gen, localhost DB fallback, local STORAGE_TYPE) and the
  startup validation rules.
- **Lockfile + advisories.** New `backend/requirements.lock` (exact freeze
  of `requirements.txt`; single float `wasmtime>=18.0.0` resolved to
  `50.0.1`, py3.11-compatible) + `docs/supply-chain-security.md` (pinning
  policy incl. OTel/kombu coupling, frontend audit triage, gitleaks
  proposal, M-122 status) + `docs/security-headers.md` (M-138: both header
  layers verified; CSP `unsafe-inline/eval` kept — Next hydration needs a
  nonce rollout first).
- **M-122.** Working tree is clean: placeholders in `.env.example`, `ci_*`
  throwaways in CI, demo creds intentional, `create_admin.py` password is
  docstring example text. No history rewrite per instruction.

## Manual actions (other owners)

1. **Secrets (M-122):** if history ever held live values — rotate in
   `.env`, Actions secrets, `k8s/pipelineiq-secrets`, Render; restart all
   replicas together; enable GitHub push protection. Add the gitleaks step
   + `gitleaks protect` pre-commit (M-139 snippet in
   `docs/supply-chain-security.md`).
2. **CI (not owned):** add root `Dockerfile` to the hadolint matrix in
   `security.yml` (currently backend/frontend/nginx/y-websocket/pgbouncer
   only); consider `pip-audit` schedule (already blocking on PRs) and an
   `npm audit --audit-level=high` gate after the Next upgrade.
3. **Product wiring (M-176):** call the new gauge setters from a beat
   collector; attach (or remove) `_HealthCheckFilter` instead of disabling
   access loggers; align Sentry sample rate with `OTEL_SAMPLE_RATE`;
   verify `http_requests_total` series name for the 5xx alert.
4. **Frontend (M-166):** safe `npm audit fix` (typescript-eslint,
   minimatch) vs deferred Next 16.4.6 + typography 0.5.4 majors. Note:
   `security.yml` comment says "Two HIGH" but `npm audit` today shows
   1 high + 10 moderate — recheck wording when touching that file.
5. **CSP (M-138):** nonce/hash rollout + full Playwright before tightening.
6. **Observed drift outside my README scope (left for owner):**
   `README.md:759` still says `pg_dump -Fc`; `README.md:286` says the first
   registrant is `admin` but code/tests enforce viewer (M-048).

## Validation

`ruff check backend/` clean; `pytest test_metrics_gauges.py` 4 passed;
`npx vitest run` 24 files / 245 passed; k6 guard exercised on
refuse/allow/local paths; `Settings` validators exercised (reject,
normalize, dev-warn, prod-fail); `npm audit` exit codes 1/1 recorded.
Backend DB suite + Playwright require the live stack (no docker here).
