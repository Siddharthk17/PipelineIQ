# TEST_PLAN — derived from the real code (Stage 1)

Legend per line: [ ] pending, [x] done, [~] partial, SKIPPED with reason.

## Phase 1: Baseline from clean state

- [ ] `docker compose down -v && docker compose up --build` from scratch; record time-to-healthy
- [ ] Every container running + healthy, zero restarts, no crash loops
- [ ] Full logs of every container catalogued; each error/warning triaged (finding or documented-harmless)
- [ ] Reproduce + root-cause notification 502 (B01) and upload 422s (B02)
- [ ] Resolve/document W01/W02/W03
- [ ] Backend suite 3x from clean stack (pass/fail/skip + coverage per module); fix failures + flakes
- [ ] Triage every SUSPECTED S01-S43/B01-B02: OPEN with failing test or closed with evidence

## Phase 2: Static + config analysis

- [ ] ruff clean (backend)
- [ ] mypy as-strict-as-possible (backend), documented exceptions only
- [ ] bandit clean
- [ ] pip-audit clean or justified
- [ ] eslint + tsc --noEmit + next build clean (frontend); zero hydration/build warnings
- [ ] npm audit clean or justified
- [ ] hadolint on every Dockerfile
- [ ] `docker compose config` valid; every service has healthcheck or documented reason; no root where avoidable; images pinned or documented; depends_on correct (esp. S29)
- [ ] Migrations: fresh `upgrade head` clean, `downgrade` clean, schema matches ORM (no drift incl. S25/S27)
- [ ] gitleaks full history + tree clean; no secret logged at runtime
- [ ] Dead code / unused deps / circular imports / unused + undocumented env vars resolved

## Phase 3: Backend API

- [ ] Per-endpoint matrix (§SYSTEM_MAP 3): happy path, every validation rule, missing/extra fields, wrong types, boundaries, empty/long/unicode/emoji/null bytes/injection/XSS payloads, wrong content-type, malformed JSON, oversized bodies, wrong method, missing/expired/tampered/wrong-audience tokens, consistent error shape
- [ ] AuthN/Z: register/login/refresh/logout/expiry/password rules/brute-force/rate-limit; RBAC every route; IDOR user-A vs user-B on pipelines/files/runs/notifications (S20)
- [ ] schemathesis stateless + stateful vs OpenAPI; every failure investigated
- [ ] Pagination/filtering/sorting boundaries + stable ordering
- [ ] Idempotency/concurrency: double submit, parallel create, parallel update, delete-while-running
- [ ] File upload matrix: small/large-presigned/multipart/empty/wrong ext+MIME/content mismatch/malformed CSV/encodings/BOM/quoted newlines/ragged rows/wide/duplicate columns/path traversal/duplicate/concurrent/interrupted/presigned expiry+reuse (B02)
- [ ] Notifications CRUD/ordering/permissions/502 path/downstream-down (B01)

## Phase 4: Engine + workers

- [ ] Every step/transform type valid+invalid+edge data (empty/1-row/nulls/mixed dtypes/unicode/wide/long/dup keys/extreme numbers/odd dates); S06/S15/S16 focus
- [ ] DAG validation: cycles/disconnected/missing inputs/dup ids/self-refs/deep+wide graphs
- [ ] Lifecycle queued/running/success/failure/cancelled/timed-out/retried: atomic + visible in API+UI (S01/S13/S14)
- [ ] Re-run idempotency, partial failure, cancel-mid-run, delete-mid-run
- [ ] Celery: every task executed for real; priority routing 3 queues (W01); concurrency/prefetch/acks_late; retry/backoff; time limits; result expiry; beat fires; `docker kill` mid-task neither loses nor duplicates (S01/S03/S04/S05)
- [ ] Redis: each of 4 instances serves only its role; stop each in turn, confirm degrade+recover (S08/S09)
- [ ] PgBouncer transaction mode: prepared statements/session SET/advisory locks/LISTEN-NOTIFY/temp tables; pool exhaustion clean fail+recover (S27/S28)
- [ ] Postgres: constraints/isolation/races/deadlocks/EXPLAIN ANALYZE hot paths/N+1/missing indexes (S25/S28)
- [ ] Storage: Arrow tiering read/write integrity + transitions + corrupt/missing handling + presigned security/expiry (S02/S08/S09/S17)
- [ ] Realtime (SSE/polling/WS): connect/reconnect/auth/ordering/many clients/server restart mid-connection (S33)

## Phase 5: Frontend (real browser, local stack)

- [ ] Every route loads: zero console errors, zero unexpected failed requests (S35)
- [ ] Every form: valid/invalid/empty-submit/double-submit/huge-paste/unicode/connection-loss-mid-submit
- [ ] Every state: loading/empty/populated/error/partial/stale (S34/S35)
- [ ] Auth: signup/login/logout/expiry-while-on-page/expired-refresh/protected-redirect/direct-URL-logged-out/back-after-logout
- [ ] Run status/progress/logs/results/download/charts+tables large data/scroll perf
- [ ] Responsive mobile/tablet/desktop: no overflow/overlap/unreachable buttons
- [ ] Keyboard-only: focus order/traps/rings (S36)
- [ ] axe-core every page: zero serious/critical (S36)
- [ ] Chromium + Firefox + WebKit
- [ ] Visual regression baselines key pages
- [ ] Lighthouse key pages; fix actionable
- [ ] Production build (next build + start): repeat critical journeys
- [ ] Backend-killed-while-open: clear recoverable errors, no blank screens/spinners (S35)

## Phase 6: E2E journeys (real stack, no mocks; permanent Playwright specs)

- [ ] J1 signup->login->upload->build->run->watch->results->download->notification->logout
- [ ] J2 failing pipeline: visible understandable error, retry works, partial output handled
- [ ] J3 large upload via presigned path + run on it
- [ ] J4 two users strict isolation
- [ ] J5 cancel run, edit, rerun
- [ ] J6 delete dataset/pipeline with run history
- [ ] J7+ every other major SYSTEM_MAP flow (schedules, contracts breach, wasm upload+use, catalog search+impact, templates fork, healing flow, streaming pause/resume, permissions grant/revoke, versions diff/restore)

## Phase 7: Resilience + chaos (local only, journey in background)

- [ ] Kill each Celery worker / API / each Redis / Postgres / PgBouncer / MinIO / whole-stack restart: recover, no loss/dup/stuck/manual cleanup (S01/S08)
- [ ] Latency + packet loss on a dependency
- [ ] Disk/quota + worker memory pressure
- [ ] Fix anything that hangs/corrupts/fails to self-heal

## Phase 8: Performance + load (local only)

- [ ] k6/auth, pipeline-api, file-upload scenarios: p50/p95/p99, throughput, error rate at increasing load
- [ ] Large datasets through engine: time + peak memory; leak check across repeats (S18)
- [ ] Queue priority holds under load (W01)
- [ ] Profile slowest endpoints/tasks; fix real bottlenecks; record before/after

## Phase 9: Security

- [ ] OWASP Top 10 every surface (S12/S19/S20/S21/S22); JWT alg-confusion/none/expiry/strength/rotation (S26/S32); CORS/cookies/flags/CSP/HSTS (S30)
- [ ] Container exposure: published ports, default creds redis/postgres/minio/flower (S29/S30)
- [ ] pip-audit + npm audit: upgrade or justify

## Phase 10: Production (read-only)

- [ ] pipeline-iq0.vercel.app + backend: loads, API base URL/env correct, CORS from prod origin, auth works, main journey with dedicated test account. No destructive actions, no load
- [ ] Prod vs local config drift report

## Phase 11: Consolidation + CI

- [ ] One command (make test-all or equivalent): stack + static + backend + fuzz + frontend + journeys + chaos smoke
- [ ] GH Actions workflow same checks on push/PR (cache + artifacts: coverage, traces, screenshots)
- [ ] README test instructions + CHANGELOG (Unreleased heading, plain human language)
