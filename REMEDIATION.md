# PipelineIQ Remediation Record

Branch: `remediation/20261116-071013` (from `a56b6e8`, tag
`pre-remediation-20261116-071013`). All work local-only: no pushes, no
history rewrites, no destructive commands outside throwaway resources
(PG `pipelineiq` @ localhost:5432, redislite ports 6379–6382, `/var/tmp`).

## Method (strict, per finding)

1. **Verify before fix** — every audit claim re-checked against current
   code (runtime repro where possible, exact static path otherwise).
   Verdicts live on the `remediation-ledger` branch (`ledger/master.md`,
   177 entries: 175 CONFIRMED incl. 1 Critical + 30 High, 2 FP
   — M-140/M-146, 2 NOT-REPRODUCIBLE, 1 BY-DESIGN — M-157).
2. **Regression test per fix** — each fix ships a test written to FAIL on
   the pre-fix code. Tests are never weakened to pass.
3. **Small commits** — `fix(<area>): <what + why> (<finding>)`, one
   finding per commit; wave branches merged with `--no-ff`.
4. **Full gate after each batch** — backend suite, ruff, mypy, vitest,
   tsc, frontend build, alembic up/down/up, docker build, k6 smoke.

## Baseline

Suite green at start (69% coverage starting point). Tooling: py3.11
venv + `pip install -r backend/requirements.txt`; scanners venv with
ruff/mypy/bandit/semgrep.

## Decisions (breaking / structural)

- **First-user-becomes-admin removed.** Explicit
  `python -m backend.scripts.create_admin` bootstrap CLI instead
  (gated by `PIPELINEIQ_BOOTSTRAP_ADMIN=1`). Documented breaking change.
- **Demo creds** (`demo@pipelineiq.app` / `Demo1234!`) kept in README for
  the public demo only; prod paths gated (`PIPELINEIQ_SEED_DEMO`,
  cron opt-in, scoped reseed).
- **SQLite** kept as dev/test fallback; migrations must pass on SQLite
  and throwaway PG16.
- **404 (not 403)** for no-grant access as anti-oracle; Yjs deny-by-default
  with backend membership check.
- `wasmtime==50.0.1`; `fastapi→0.133.1` / `starlette→1.10.0` /
  `pydantic→2.7.0` to clear advisories (`pip-audit` clean).
- `CHANGELOG.md` left to release-please (conventional commits); no manual
  version bumps. Exposed secrets (if any found) are owner-rotated; history
  is never rewritten.

## Waves 0–3 + cleanup (original 177 findings)

Foundations, SQL sandbox, tenant isolation, Yjs authz, streaming,
auth/secrets, dataops, contracts, webhooks, AI/WASM, uploads, deploy/IaC,
SSE, engine, perf, reliability, data, authz, product, config/docs,
M-036 loop vectorization, parser hardening, M-103 event-loop timeouts,
M-111 parquet load, M-027/M-121/M-165 dep upgrades. Re-audit cycle 1
fixes (R-R-001…010, R-D-001…003, R-S-002/003, M-103/M-111/M-164,
nested-regex guard, aggregate-list 400) and cycle-2 track fixes
(frontend mocks, Yjs prod-guard + `PIPELINE_API_URL`, k8s probes/image
pins/secrets template, render dead vars, y-websocket image build).

## Re-audit cycle 2 (4 tracks, all Highs fixed)

**Reliability** (`readversarial2-rel`): R2-001 transient commit failure
poisoned DLQ + unrestartable FAILED → guarded retries
(`StreamingCommitError`, never DLQs healthy data), FAILED restartable;
R2-002 resume dispatched no daemon → resume reaps dead workers;
R2-003 cancel check-then-act → atomic conditional claim + idempotent
200 + conditional `_mark_failed`; R2-004 Idempotency-Key on schedule /
webhook / contract-PUT create paths; R2-005/C2-010 SSE terminal sets +
stop publishes terminal event; R2-006 schedule admin scope + webhook
404 oracle; R2-007 output producer `acks=all` + idempotence.
R2-008 (notification retry resend, no outbox) recorded as residual.

**Security** (`readversarial2-sec`): S2-002 `.env.example` placeholder in
weak-secret blocklist; S2-001 worker WASM/file loads owner-scoped;
S2-003 sync webhook/Slack pinned to validated IP, redirects disabled;
S2-004 Yjs admin claims re-checked against backend; S2-005 dead SSE
`?token=` shim removed; S2-010 Jaeger allowlisted like Prometheus.
S2-006 (spoofable `X-Real-IP` off-nginx), S2-007 (24h JWT fail-closed),
S2-008 (ReDoS: nested-quantifier reject in all three regex layers),
S2-009 (frontend advisories: build-time/dev) recorded with reasons.

**Ops** (`readversarial2-ops`): O2-001 Render `preDeployCommand: alembic
upgrade head`; O2-002 y-websocket readinessProbe; O2-004 CI least-privilege
`permissions: contents: read`; O2-005 dead k8s keys removed,
`ALLOWED_EXTENSIONS` parsing fixed + documented in `.env.example`;
O2-006 Render secret-gate + ephemeral-upload notes.

**Correctness** (`readversarial2-correctness`): C2-001 contract `regex`
evaluated (fail-closed, ReDoS-guarded); C2-002 dtype map expanded +
fail-closed on unmapped; C2-003 kind-aware min/max (bool/datetime +
fail-closed otherwise); C2-004 `not_empty` flags NULLs; C2-005 dedup
`keep` validated at parse; C2-006 aggregate aliases uniform
(`col_func` + explicit alias honored); C2-007 join `rows_in` parity;
C2-008 pivot `fill_value` post-filled on DuckDB path; C2-009 stratified
samples stay on pandas; C2-011 Arrow bus index run-scoped;
C2-012 duplicate `__table_args__` removed; C2-013 backup/restore
`--dry-run`.

## Final gate (2026-11-21, post cycle-2 fixes)

- backend suite: **1580 passed, 106 skipped**, 3 failed → all 3
  root-caused, none code-real: (1) webhook-oracle test collected before
  its rename to `_404` (passes in isolation); (2) lineage perf test
  timing flake under load (passes in isolation); (3) R-R-004 cancel-race
  test needs `pg_trgm` (fresh throwaway PG lacked the extension;
  migrations create it in prod — `CREATE EXTENSION` + rerun green).
- ruff: **All checks passed**. mypy: **clean (393 files, exit 0)**.
- vitest: **245 passed (24 files)**. tsc: **exit 0**. y-websocket
  room-authz: **10 pass, 0 fail**.
- alembic upgrade/downgrade/upgrade on scratch PG: green, single head
  `0021_perm_heal_uq_backfill`.
- docker build: no daemon in this env (static-only; Dockerfiles
  unchanged by cycle-2 except y-websocket wiring already covered).
- k6 smoke vs local uvicorn: **pass within throttle** — app boots,
  `/health` + `/readyz` 200, register/login 200. `k6/load-auth.js`
  fixed (`/api/auth/login` → `/auth/login`, the app's actual mount).
  20 rapid logins trip the 5/min login throttle by design (429s, 0%
  login_success); 4 iterations under the throttle: 100% checks,
  0% `http_req_failed`. Same throttle breach seen in prior gates.

## Residual accepted risks (with reasons)

- Compose y-websocket runs as root + `npm install` at start (dev-mode;
  prod uses the k8s image which runs as `node`).
- npm frontend advisories (build-time/dev; backend `pip-audit` clean).
- 24h JWT + fail-closed revocation (availability coupling by design).
- Yjs 10s authz cache + cron UTC-only (documented).
- R2-008 notification retry resend (no transactional outbox; inherent).
- O2-007 live-container/CVE verification not run here (no docker daemon;
  backend/frontend suites + static checks are the gate).

## How to verify

```bash
./scripts/bootstrap.sh --up && make verify
cd backend && python -m pytest tests/ -v --tb=short   # needs live PG+Redis
cd frontend && npm test && npx tsc --noEmit
ruff check backend/ --select E9,F63,F82 && npm run lint
```
