# TEST_REPORT — PipelineIQ e2e-hardening (branch `e2e-hardening`)

Date: 2026-10-29. Stack: local docker compose (FastAPI + Celery + 4×Redis + PgBouncer + Postgres + MinIO + Next.js + y-websocket). Box: 8GB RAM (suites run `--workers=1`, backend single-process; full parallel runs OOM).

## Exit criteria

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | Backend unit suite green | ✅ PASS | 1018 passed, 3 skipped (`backend/tests/unit`, qapytest env) |
| 2 | Frontend vitest + tsc + eslint green | ✅ PASS | 232 passed; `tsc --noEmit` clean; `eslint .` clean |
| 3 | Journeys J1–J7 green in one run | ✅ PASS | 7/7 in a single `e2e/journeys` run (~2 min) |
| 4 | Feature E2E green (01–12, trace, subdirs, collab, a11y) | ✅ PASS | 01-auth, 02-upload, 03-builder, 04-run, 05/06/07/08/09/10/12-ai, trace-validation (incl. E2E_ASSERT_TRACES), scheduling, streaming, builder, collaboration (3/3), a11y 7/7 |
| 5 | Static/security gates | ✅ PASS | ruff check clean; mypy clean (318 files); bandit 0 High (mediums pre-existing B108/B310 in tests); eslint clean; hadolint baseline kept; npm audit: 6 high + 1 moderate fixed via safe upgrades, 2 remaining need Next 16 major (deferred, documented in CHANGELOG) |
| 6 | Chaos: worker kill, API restart, redis outage | ✅ PASS | killed worker mid-run → COMPLETED; API restart mid-run → COMPLETED + polls recover; redis-cache down → honest 503 (was 500), health 200 |
| 7 | Concurrency + latency | ✅ PASS | 3 concurrent users × full upload→run→COMPLETED in ~3s; k6: health p95 ~9–19ms, files-list p95 12.6ms @1rps, 0 failed |
| 8 | No unresolved P1 defects | ✅ PASS | all P0/P1 ledger rows VERIFIED/FIXED/CLOSED (S01–S60, B01/B02, FZ01–FZ03); residuals are P2/P3 follow-ups below |
| 9 | Fuzz (schemathesis) | ⚠️ DEFERRED | not run; 8GB box prioritized for E2E + unit. OpenAPI schemas exist; schedule post-harden |
| 10 | Production read-only smoke | ⚠️ DEFERRED | not run (no prod creds in scope); local prod-parity covered by compose stack |

## Defects fixed this stage (P0/P1 highlights)

- S48 SigV2 presigned URLs → SigV4 on all 5 boto3 clients (every >10MB browser upload was broken).
- S53 sort step 500 on list `by` + silently dropped `ascending` (+ schema-checker fix).
- S54 builder YAML typing crash (React #185): uncontrolled-CodeMirror vs yjs text binding; text binding removed, graph sync kept.
- S55 yjs never connected on cookie-restored sessions (page reload killed collaboration).
- S50/S20i run/version existence oracle 403 → 404; S51 contract owners blocked from their own pipelines.
- S49 swallowed run errors → actionable messages (paths/secrets still redacted).
- S56 redis outage 500 → honest 503.
- S52 covered by SigV4 fix; S19/S32/S33/S34/S36/S37/S39/S40/S28 verified-closed with evidence in ledger.
- S57 catalog blast-radius/lineage CTEs used illegal Postgres (subquery on recursive CTE + inner LIMIT) and swallowed errors into empty results; rewritten with array-path cycle detection + deduped edges; impact returns 500 assets instantly, lineage resolves full chains.
- S58 profiling crashed on nullable single-value frames (math.isnan on pd.NA, infinite 30s retry); added NA-safe coercion + regression tests.
- S59 gemini failure path wrote a plain-dict FAILURE state then raised, breaking Celery JSON result decode (KeyError chains); removed the manual update_state so the raised exception round-trips.
- S60 Alt+Enter launcher had no number-key selection (typing digits only filtered to nothing); added 1-9 quick-add with visible badges, cleared stale search per open, removed dead contradictory keybinding table.
- Manual browser pass: login, dashboard, builder, catalog + blast-radius panel, runs, run detail, files, schedules, templates, wasm, storage, command palette, AI modal — all render with zero console errors.
- E2E repairs: shared demo session (global-setup storageState), ~15 stale selectors/paths/shapes fixed, rate-limit-tolerant helpers, paste-instead-of-type for CodeMirror.

## Follow-ups (P2/P3, scheduled post-harden)

S05–S09, S15–S17, S21–S23, S38, S41, S42 (order-dependence), schemathesis fuzz, prod smoke, k6 at higher VUs (needs rate-limit test bypass), N+1 audit, full error-shape standardization.

## How to re-run

- Backend unit: `/tmp/qapytest.sh backend/tests/unit -q` (host venv; needs local postgres/redis up).
- Frontend unit: `cd frontend && npx vitest run`; static: `npx tsc --noEmit`, `npx eslint .`, `ruff check backend/`, `mypy backend/`.
- E2E: `E2E_BASE_URL=http://localhost npx playwright test e2e/journeys e2e/01-auth.spec.ts ... --project=chromium --workers=1` (global-setup logs in the demo user once; journeys create fresh users with 429 backoff).
- y-websocket regression: `TEST_REDIS_PASSWORD=... node --test y-websocket/__tests__/shared-room.test.js`.
