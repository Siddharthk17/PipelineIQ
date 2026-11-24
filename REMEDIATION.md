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

## Resume session 2026-11-24/25 (16 open items + re-audit cycle 3)

Prior `/tmp` scratch (venv, proofs, agent reports) was wiped by the environment;
ledger survived on the `remediation-ledger` branch (`ledger/master.md`, 177
entries). New work dir `/tmp/remediation-pipelineiq-20261124T175057Z`
(state.md, tooling.md, plan.md, verified-*.md, readversarial3-*.md).
Resume tag `resume-20261124-175057`. Stayed on `remediation/20261116-071013`
(it is the remediation branch; tree was clean). Project `.venv` was broken
(dangling symlinks, wrong Python); rebuilt a py3.11 venv under the work dir.
Fresh venv + PG16 @ :5432 + redislite :6379-82.

### Re-verification of the 16 ledger entries with no fix commit

Three subagents + orchestrator spot-checks, all evidence in
`ledger/verified-{engine,apiops,deploy}.md`:

- ALREADY-FIXED: M-054 (weak-secret blocklist), M-120 (requirements.lock),
  M-019 readyz-half (503 when degraded).
- FALSE-POSITIVE: M-177 (ingress routes /yjs too). BY-DESIGN: M-019 health
  alias (documented 200 + truthful payload).
- CONFIRMED and fixed this session: M-046 (Medium), M-047 rate-key half
  (Medium; login-order half already fixed), M-085 narrowed (Low; N+1 not
  evidenced), M-112 (Medium), M-114 (Low), M-115 (Medium), M-125 narrowed
  (Low), M-158 /metrics half (Low; docs/debug halves OK), M-181 mitigated
  (Info), M-182 (Low), M-187 narrowed (Info). M-186 compose dev-build stays
  a documented residual.
- Own false positive of this session (corrected, not the audits'): the root
  `package-lock.json` looked stale checking top-level keys only — npm
  workspaces legitimately nest non-hoisted deps; clean `npm ci` reproduces
  the full tree (vitest 82 pass, tsc 0). No commit made.

### Fix wave (all merged --no-ff, zero conflicts)

| Finding | Commit(s) | Test |
|---|---|---|
| M-046 builder round-trip loss | fix/frontend-builder-resume | yamlRoundTripPreservation (7) |
| M-112/M-114 engine parity | fix/engine-correctness-resume | engine parity (19) |
| M-047 rate-key spoof | fix/rate-limit-resume | trusted-proxy (10) + updated budgeting suite |
| M-085 lists/counters | fix/api-lists-resume | list pagination (13) |
| M-115 storage | fix/storage-resume | fail-closed (13) + repaired presigned-endpoint mocks |
| M-125/M-182/M-187/M-158/M-181 deploy | fix/deploy-hygiene-resume | metrics exposure (2); YAML/config proofs |
| k8s worker YAML malformed (pre-existing, found via M-125) | 88c022d | safe_load_all parses |
| mypy unused-ignore from wave | afe9e41 | — |
| R-08 presign 500 | 5fcf2d3 | presign-fallback test |
| R-06 doc | d5ddfca | — |

### Environment notes (this session's hard lessons)

- `.env` points at compose hostnames. Tests need every `*_URL` overridden to
  localhost (DATABASE_WRITE/READ_URL, all REDIS_*, CELERY_*); see work-dir
  `test-env.sh`. Un-overridden hosts cost ~7.5s DNS each and fail tests
  (`duration < 1.0`, Oracle-order login timing, etc.).
- Throwaway PG needs `CREATE EXTENSION pg_trgm` (cancel-race test).
- `/tmp` has a quota (EDQUOT at ~3GB): keep venv/worktrees lean, exclude
  uploads from backups, run `npm ci` trees on `/var/tmp`.
- Initial full-suite hangs were DNS backoff against `minio:9000`, not code.
  `--timeout=300` bounds every run now.

### Re-audit cycle 3 (blind, 4 tracks)

- Security (static-only session): 0 Critical/High, 1 Medium (R-06: RFC1918
  X-Real-IP trust is bypassable behind a non-overwriting private hop —
  kept by design for default compose, documented in RUNBOOK), 3 Low, rest Info.
- Concurrency: 5 prior fixes verified; new G (idempotency waiter never sees
  completion — Medium), H (placeholder pinning — Medium), I (resume/stop
  race — Medium), J (CANCELLED clobber — Low). All fixed, merged.
- Frontend: new F-1 validate-rule params dropped (High), F-2 pivot scalar
  index (Medium), F-3 whitespace/quote mangling (Medium), F-4 JWT in WS URL
  (Medium), F-5 health error type (Low). All fixed, merged (one existing test
  updated to the new correct expectation, same strength).
- Correctness: 18/18 prior fixes verified; new B1 nullable enums, B2 numeric
  fail-open validators, B3 status_filter ignore, N1 sort-length, N2 stratify
  divergence (all Medium), N3 legacy keys (Low). All fixed, merged.
- Round-2 lint fallout (6 ruff errors) repaired by hand with targeted suites
  green (ec2ec2d).

### Final gate (post everything, this session)

- backend: **1586 passed, 27 skipped, 0 failed** on the final tree
  (`full-v5-final.log`, 237s, `--timeout=300`); ruff clean; mypy clean
  (404 files).
- frontend: full vitest 28 files / 264 passed, `tsc --noEmit` exit 0
  (clean `npm ci` tree on `/var/tmp`, byte-identical sources).
- Blocked (unchanged): docker build/push, container/IaC scans, k6
  load test (no docker daemon); e2e Playwright (needs full stack).

### Updated residuals (added this session)

- R-06: documented; code kept (see RUNBOOK "Rate limiting and trusted
  proxies"). Revisit if a private-hop-without-overwrite topology appears.
- Demo admin password stays published behind the explicit opt-in flag
  (public demo by design; production refuses seeding).
- Yjs join-time-only authz (revocation lags on long-lived sockets).
- R-11/R-12 runtime proofs (alias-cap timing, ReDoS timing) still open as
  test-only follow-ups; static evidence is in place.
- /metrics prod-gate relies on `ENVIRONMENT=production` being set correctly
  in every prod manifest (k8s configmap sets it; verify on new targets).

## QA-fix session 2026-11-25 (D1–D5, all fixed and verified)

Independent QA pass found 1 Medium + 4 Lows; all fixed on this branch,
each with fail-before/pass-after proof or exact-command verification:

| ID | Fix | Proof |
|---|---|---|
| D1 postcss GHSA chain via nested Next copy | `overrides: {next: {postcss: ^8.5.32}}` in root `package.json` + lock entry aligned to vetted hoisted 8.5.32 (`ced3711`) | clean `npm ci` from scratch ×3 (`/var/tmp/fenv{3,5,6}`), `npm audit --omit=dev`: **0 vulnerabilities**; full vitest 264 + `tsc` 0 + `next build` 0 on the overridden tree |
| D2 stale README numbers | `64355b6` | values re-derived (`alembic heads`, 43 files, `pytest --collect-only` 1613) |
| D3 flaky template-dialog e2e | poll for async selects + options instead of immediate count (`3c3a4c8`) | 5 consecutive full-file runs: 50/50, zero flakes (was 2 flakes/5 runs) |
| D4 y-websocket `npm test` | canonical script with explicit spec files (`05e910c`) | `npm test`: 30 tests, 29 pass, 0 fail, 1 env-gated skip |
| D5 split @playwright/test (repo 1.60.0 vs frontend 1.56.1) | aligned frontend devDep to 1.60.0, removed orphaned `playwright@1.56.1` lock entries (`c749825`) | clean `npm ci` reproduces single-version tree; repo-tree `./node_modules/.bin/playwright test 01-auth 03-pipeline-builder`: **15 passed** (was dual-package error) |

Notes: npm `install --package-lock-only` would not restructure the tree in
this environment (always "up to date"); structural lock changes were made by
exact hand-edit of lock entries (copied `resolved`/`integrity` from npm's own
output) and validated by clean `npm ci` + audit + full gates. `next@16`
breaking upgrade explicitly NOT done — unnecessary once the advisory is
cleared on the 8.x line.

Final gate on the finished tree: backend **1586 passed / 27 skipped / 0 failed**,
ruff clean, mypy 404 files clean, frontend 264 + `tsc` 0 + `next build` 0,
y-websocket 29/0/1-skip, repo-tree Playwright 15/15, `pip-audit` clean,
`npm audit` clean, migrations up/down/up single head.

## CI-fix session 2026-11-25 (PR #64 red → green)

GitHub Actions on the PR surfaced 3 real failures; all fixed on this branch:

| Failure | Root cause | Fix | Proof |
|---|---|---|---|
| `trivy-action@0.28.0` unresolvable | tag does not exist (newest is v0.36.0) | pin `v0.36.0` (`5c74052`) | every other pinned action verified 200 via API; codeql pin re-checked valid (earlier 404 was my split-string bug) |
| `pip-audit --disable-pip` exit 1 | current pip-audit CLI rejects `--disable-pip` with unhashed requirements unless `--no-deps` | drop the flag, keep `--desc` (`5c74052`) | `pip-audit -r backend/requirements.txt` → clean, exit 0 |
| frontend Docker `npm ci` exit 1: lockfile 1.56.1 vs package 1.60.0 + nested postcss 8.4.31 | standalone `frontend/package-lock.json` (tracked, used by the image build) was never updated by D1/D5, which only touched the root lock | aligned frontend devDep to 1.60.0, bumped direct postcss `^8.5.6→^8.5.32`, added matching `overrides`, regenerated the standalone lock (`bf2a254`) | clean-room `npm ci` + `npm audit` 0 vulns + vitest 264 + `tsc` 0 + `next build` 0 on the exact committed files (md5-verified) |

Notes: `npm install --package-lock-only` would not restructure either lock in this environment (always "up to date"); the two hand-edits (root nested-postcss pin, root playwright orphan/nested removal) copy `resolved`/`integrity` verbatim from npm's own output and were each proven by clean-room `npm ci`. The EOVERRIDE lesson is recorded in the D1 entry: an `overrides` value must match any direct `dependencies` spec on the same package.

### CI smoke-login fix (follow-up PR #66)

The docker-smoke job failed at `assert r.status_code == 200, f"Login failed: ...Invalid credentials"`. Root cause is a behavior change from this remediation, not app code: since M-002, demo seeding is opt-in (`PIPELINEIQ_SEED_DEMO=1`), but the CI-generated `.env` files never set the flag — so no demo user existed for the smoke/e2e demo login. Fixed by adding `PIPELINEIQ_SEED_DEMO=1` (with comment) to both CI `.env` heredocs (docker-smoke-test and e2e-tests jobs). Verified: flag absent → seed refuses → login 401s (the CI failure, reproduced); flag present → demo seeded → login 200 + token; seed/auth unit suites green (32 passed).

## How to verify

```bash
./scripts/bootstrap.sh --up && make verify
cd backend && python -m pytest tests/ -v --tb=short   # needs live PG+Redis
cd frontend && npm test && npx tsc --noEmit
ruff check backend/ --select E9,F63,F82 && npm run lint
```
