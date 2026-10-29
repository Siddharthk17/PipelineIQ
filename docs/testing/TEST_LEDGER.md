# TEST_LEDGER — single source of truth for findings

Columns: ID | area | description | severity | status | evidence | fix commit | regression test.
Severity: P0 data loss/security, P1 broken feature, P2 degraded behavior, P3 polish.
Status: SUSPECTED (from reading, unconfirmed) -> OPEN (failing test proves it) -> IN_PROGRESS -> FIXED -> VERIFIED (suite green) | BLOCKED.

## Tool inventory (Stage 1)

MCP servers available in this session: arch_linux (system/pacman/mirrors/news), chrome_devtools (pages/snapshot/click/fill/evaluate/network/console/lighthouse/trace), context7 (resolve-library-id, query-docs), deepwiki (structure/contents/ask), duckduckgo (web search), github + github_actions (repos/issues/PRs/commits/files/workflows/runs/logs), hf-mcp-server (hub search/details/fs/spaces), markitdown/fetch (URL fetch), memory (knowledge graph), mongodb (connect/find/aggregate/schema), parallel_search (web_search/web_fetch), playwright_browser (navigate/snapshot/click/fill/evaluate/network/console), sequential-thinking, serena (repl: fs/edit/lsp/shell/mem), openapi (GET /health).
No dedicated postgres/redis/docker MCP: use bash CLIs + python instead.
Skills: api-and-interface-design, avoid-ai-design, caveman(+commit/compress/help/review), compress, customize-opencode, design-taste-frontend, diagnose-crash, find-skills, firecrawl(+agent/browser/crawl/download/map/scrape/search), frontend-design, graphify, last30days, omarchy(+app), performance-optimization, spec-driven-development, systematic-debugging, test-driven-development, writing-plans.
Subagents: Code Reviewer, Database Architect, Debugger, DevOps Engineer, explore, general, Implementer, Performance Engineer, Planner, QA Engineer, Security Auditor, Technical Writer, Test Engineer.
Slash commands/plugins: none installed (.opencode/ absent; no custom commands found).
CLIs: docker 29.9 + compose 5.6 OK; psql 16 OK; curl/jq OK; node 26/npm 11 OK; python3 15/pip 26/uv 0.11 OK; k6 1.1 OK; playwright 1.64 (browsers to verify); ruff 0.17 OK; MISSING: redis-cli, mc, locust, gitleaks, bandit, hadolint, mypy, pytest shim (use .venv or uvx).

## Findings (Stage 1 reading; all SUSPECTED until reproduced)

| ID | area | description | severity | status | evidence | fix commit | regression test |
|---|---|---|---|---|---|---|---|
| S01 | workers | Kill-redelivery wedges run RUNNING forever (acks_late + max_retries=0 + PENDING-only guard; TIMEOUT never written) | P1 | SUSPECTED | celery_config.py:41; pipeline_tasks.py:248-253,324-333; api/sse.py:28-34 | — | — |
| S02 | storage | S3/local provider basename() flattens keys: arrow_bus/{run}/file.parquet and outputs/{run}/file collide across runs | P0 | SUSPECTED | storage_service.py:64-67,139,148,174,183,187,195,203,215 vs save_step.py:70-85 | — | — |
| S03 | workers | Orphan task routes (tasks.generate_pipeline_ai, tasks.repair_pipeline_ai, tasks.schedule_run_completion_callback) have no @task: Beat/apply_async will "Received unregistered task" | P1 | SUSPECTED | celery_config.py:26-29 | — | — |
| S04 | workers | Dual schedule firing: beat_manager.register_schedules overwrites conf.beat_schedule, wiping static schedules.check/maintain_storage; polling + dynamic both fire | P1 | SUSPECTED | beat_manager.py:37-56; schedule_tasks.py:26 | — | — |
| S05 | workers | Non-atomic Redis lock release (GET+DELETE, no Lua) in schedules.check + maintain_storage: slow worker can delete another's lock | P2 | SUSPECTED | schedule_tasks.py:120-125; storage_maintenance.py:52-57 | — | — |
| S06 | engine | `transform` + batch `stream_consume` parse OK but have no executor: late ValueError at runtime, planner silent | P1 | SUSPECTED | _core.py:128-147; smart_executor.py:170-176; parser.py:660-676; planner.py:202-205 | — | — |
| S07 | engine | WASM SIGALRM not thread/prefork-safe (process-wide alarm, main-thread-only) + old_handler race | P2 | SUSPECTED | wasm_executor.py:133-134,179-180 | — | — |
| S08 | storage | ArrowBus.get consults only per-process _locations; manifest never hydrated: retry on another worker KeyErrors despite object existing | P1 | SUSPECTED | arrow_bus.py:86,406-465,708-721 | — | — |
| S09 | storage | ArrowBus put delete-then-write race + evict GET/shm.write/record/DELETE non-atomic: concurrent put/evict loses or duplicates tier entries | P2 | SUSPECTED | arrow_bus.py:325,569-581 | — | — |
| S10 | contracts | Contract config mutation (contract_cfg["step_name"]=...) mutates cached shared PipelineConfig; DTYPE_MAP incomplete; regex field never evaluated | P2 | SUSPECTED | pipeline/contracts.py:271,34-49,252 | — | — |
| S11 | engine | YAML cache inconsistency: miss path strips diff markers + _repair_quoted_values, hit path bypasses; cached PipelineConfig shared mutable | P2 | SUSPECTED | pipeline/cache.py:122-141; parser.py:434-435 | — | — |
| S12 | security | Webhook SSRF TOCTOU + open redirects: validate_public_http_url then httpx follow_redirects max3 without re-validation | P0 | SUSPECTED | webhook_tasks.py:47-48,92-95; url_security.py:33-51 | — | — |
| S13 | workers | Cancel overwrite: API sets CANCELLED but in-flight _persist_results unconditionally commits COMPLETED/FAILED (no fencing) | P1 | SUSPECTED | pipelines.py:889-903; pipeline_tasks.py:792-813 | — | — |
| S14 | engine | TIMEOUT dead status + StepResult.status always COMPLETED even on failure (per-step FAILED only in SSE) | P2 | SUSPECTED | models/__init__.py:63; sse.py:28-34; pipeline_tasks.py:834 | — | — |
| S15 | engine | validate rule payloads unvalidated (missing value/min/max/pattern -> raw KeyError at runtime, not ValidationError) | P2 | SUSPECTED | parser.py:1271-1288; validators.py:100,118-135,158,180 | — | — |
| S16 | engine | fill_nulls/sample late failures (strategy allow-list, n/fraction bounds, stratify_by unchecked; ffill vs forward_fill alias mismatch) | P2 | SUSPECTED | parser.py:998-1024; _core.py:1090-1120; sql_builder.py:443-445 | — | — |
| S17 | observability | Storage event logging (pool_size=1 + global lock + 2s connect on WRITE DB) runs synchronously in Arrow hot path: DB slowness throttles execution | P2 | SUSPECTED | arrow_bus.py:346-351; event_logger.py:20-26,39-62 | — | — |
| S18 | engine | Unbounded lineage/memory: table_registry retains all step tables + per-column lineage nodes, no spill/evict; join double-counts rows | P2 | SUSPECTED | runner.py:131,161-162; _core.py:573 | — | — |
| S19 | api | Unauthenticated: GET /api/v1/templates[/{id}], GET /debug/sentry-test (non-prod, always 500 oracle) | P1 | SUSPECTED | templates.py:43,66; debug.py:11 | — | — |
| S20 | authz | IDOR/ownership gaps: contracts same-name bypass (api/contracts.py:44-49); versions no user scoping (versions.py:46-61); catalog global no ownership; SSE denies valid grantees (sse.py:100-117); lineage_export no admin bypass; string-vs-UUID id compares (contracts.py:405,457; streaming.py:254-260; runs.py:26); audit bare uuid.UUID -> 500 | P0 | SUSPECTED | files cited | — | — |
| S21 | api | Mass assignment/unvalidated store: notification config/events arbitrary; webhook events arbitrary; schedule yaml_config never parsed; template file_mappings never owner-checked; column-policy allowed_roles free strings | P2 | SUSPECTED | notifications.py:32-37,112-118; webhooks.py:28-31; schedules.py:153-161; permissions/column_policies | — | — |
| S22 | api | Error leaks: lineage exception text in 404; S3 str(e) in 500; str(exc) 500s; storage errors-in-200; enum serialization variance | P2 | SUSPECTED | lineage.py:115-120,173-177; pipelines.py:1230; files.py:344; storage.py:30-41 | — | — |
| S23 | api | Inconsistent error shape/pagination: dict-vs-string details; positional HTTPException; webhook test 200-vs-502; files 503 policy; audit no total; notifications/schedules/versions/files unbounded lists | P3 | SUSPECTED | files cited | — | — |
| S24 | api | Duplicate/conflicting routes: contracts v1 vs v2 (same table, incompatible); pipelines timing vs runs timing shapes; SSE absent from main api; webhooks/audit unversioned; sse double-mount OpenAPI dup | P1 | SUSPECTED | routers/contracts.py vs api/contracts.py; routers/runs.py:20 vs pipelines.py:1239 | — | — |
| S25 | db | Missing indexes: step_results(run) ORM none + DB idx dropped; schema_snapshots/audit_logs/notification_configs/webhook_deliveries(run) none; permissions UQ dropped -> dup grants; contracts(pipeline,is_active) none; files(user,filename,version) none; column_policies ORM metadata drift | P2 | SUSPECTED | 1a158ef49016:81,98,120; models files | — | — |
| S26 | db | Auth fail-open: _is_token_revoked except->False + revoke_token silent warning -> revoked token usable when Redis down | P0 | SUSPECTED | auth.py:189-191,209-211 | — | — |
| S27 | db | PgBouncer-unsafe: raw COMMIT/ALTER TYPE enum backfill (database.py:126-142); ALTER TYPE w/o autocommit in 3 migrations; CREATE INDEX CONCURRENTLY (forbidden in pooling); SET LOCAL (meaningless); pool 80-120 conns; LOCK TABLE users PG-only | P1 | SUSPECTED | files cited | — | — |
| S28 | db | N+1/select-lazy everywhere; cb3e11c88344 backfills ALL orphan files to first user (cross-user leak); inconsistent CASCADE vs SET NULL; HealingAttempt synonym columns invite wrong queries; minio_client None-crash; Redis() per healthcheck leaks sockets | P1 | SUSPECTED | models/__init__ rels; cb3e11c88344:42-51; minio_client.py:10-16; main.py:632-640 | — | — |
| S29 | infra | worker-streaming skips pg/pgbouncer/minio deps -> boot crash-loop; nginx->flower service_started (not healthy); missing healthchecks (streaming/beat/flower/nginx/console); flower READ_URL primary defeats read-split | P2 | SUSPECTED | compose:1063-1067,861-862,824 + healthcheck gaps | — | — |
| S30 | infra | Unpinned images (minio latest, node:20-alpine floating); worker-default overlaps critical queue (breaks priority); redis_broker volume wasted (no persistence); OTEL 1.0 overrides 0.1 (100% vs Jaeger 100K cap); /docs,/redoc,/openapi.json,/flower ->404 contradicts README/AUDIT; CSP unsafe-inline/eval + absent on /api; HSTS on :80 ineffective; CORS_ORIGINS drift (.env vs render); orphan README vars; WASM_BUCKET triple mismatch; k8s jaeger-collector svc missing; k8s missing frontend/pgbouncer/replica/redpanda/prom/grafana/jaeger manifests; no CREATE EXTENSION for pg_trgm/pgvector; overcommit-fix is echo no-op; y-websocket maxPayload 100MB vs mem 80m (OOM DoS); k8s certificate placeholder email | P2 | SUSPECTED | compose + k8s + nginx files cited | — | — |
| S31 | frontend | Collab token always null after refresh (getToken no-op) -> Yjs silently disabled, no UI warning | P1 | SUSPECTED | lib/api.ts:138-148; auth-context.tsx:40,68; PipelineEditorWidget.tsx:151; useCollaborativePipeline.ts:75-82 | — | — |
| S32 | frontend | JWT secret naming split (AUTH_JWT_SECRET||SECRET_KEY edge vs JWT_SECRET>=32ch y-websocket vs SECRET_KEY docs): mismatch -> edge passes but socket 4001s | P2 | SUSPECTED | middleware.ts:4; server.js:5,9-12 | — | — |
| S33 | frontend | SSE auth fragility: EventSource relative URL, no withCredentials flag; cross-origin drops cookie; failure -> forced FAILED even if RUNNING | P1 | SUSPECTED | usePipelineRun.ts:93-94,222-250 | — | — |
| S34 | frontend | Missing client guards: /storage authLoading only; /catalog + /wasm-modules spinner-forever when !user (middleware-only reliance) | P2 | SUSPECTED | storage/page.tsx:672; catalog/page.tsx:106; wasm-modules/page.tsx:125 | — | — |
| S35 | frontend | Error swallowing -> wrong empty states: runs/[runId] .catch(()=>{}); files/schedules/templates .catch(set([])); catalog non-abort swallow; no error UI | P2 | SUSPECTED | runs/[runId]/page.tsx:30,40; files/page.tsx:24; catalog/page.tsx:74-76 | — | — |
| S36 | frontend | A11y: clickable divs w/o role/tabindex (templates/files/catalog); wasm page has role correctly | P3 | SUSPECTED | templates/page.tsx:61-68; files/page.tsx:56-61; catalog/page.tsx:172-180 | — | — |
| S37 | frontend | Stale collab seed (mount-only, async files arrive later -> late peer gets empty doc + split-brain); 4001 stop only console.error, no banner | P2 | SUSPECTED | useCollaborativePipeline.ts:104-116,193-207 | — | — |
| S38 | frontend | graphToYAML returns "" on cycle -> emitGraphToYaml wipes editor; yamlToGraph maps unknown type -> transform visual-only (backendSupported false) but builder allows adding | P2 | SUSPECTED | yamlGraphSync.ts:559-561; usePipelineEditor.ts:216-230,351-353; stepDefinitions.ts:127 | — | — |
| S39 | frontend | API version drift: frontend /api/v1/pipelines/?page vs trace-validation /api/v1/pipelines/runs + array shape; e2e unversioned /api/pipelines/run, /api/contracts, pipeline_yaml vs yaml_config | P1 | SUSPECTED | api.ts:360; trace-validation.spec; e2e run/contract/schedule specs | — | — |
| S40 | frontend | Upload contract hole: method==="api" ignored (falls to multipart); upload_endpoint never used; direct PUT credentials heuristic by URL substring | P2 | SUSPECTED | api.ts:228-250; types.ts:19-25 | — | — |
| S41 | frontend | Misleading stats (hardcoded +12%/+5% trends; AnimatedNumber NaN when 0); blocking confirm()/alert(); StreamingRunCard full reload after pause/resume/stop, no error handling | P3 | SUSPECTED | QuickStatsWidget.tsx:55-58,17; wasm-modules/page.tsx:110,117; StreamingRunCard.tsx:59-63 | — | — |
| S42 | tests | Import-time skip gates hide failures (evaluated at collection; only ConnectionError caught; hardcoded localhost ignores E2E_BASE_URL); always-skip test_sandbox; retries/sleeps mask races; time-dependent asserts; order-dependent shared-state; conftest mock-admin + mocked .delay bypass real JWT/RBAC/worker paths; hardcoded expired JWTs in scripts; k6 fake file_id | P2 | SUSPECTED | test_public_endpoints.py:8-16 et al; test_sandbox.py:183-186; conftest.py:39-45,111-156 | — | — |
| S43 | infra | Redis cache key/secret naming drift + compose-only vars with no backend reads; MAX_UPLOAD_SIZE 500MB vs k8s 50MB vs render /tmp/uploads; ALLOW_REGISTRATION=false k8s-only undocumented | P3 | SUSPECTED | configmap.yaml:32; compose:172; k8s:27 | — | — |
| S44 | audit | `log_action` bypasses request DB session AND blocks the request path on a fresh pooled connection with no short connect timeout: fail-slow under DB stress (measured 7.5-15s/request on unresolvable host; error only logged) | P2 | OPEN | audit_service.py:38-55; engine pool database.py:18-34 (no connect_timeout); measured test_api.py:349 7.499s vs <1.0 assert | — | — |

| B01 | api | Notification test endpoint returns 502 when downstream unreachable (inconsistent with webhook test 200+delivered:false; 502 implies our outage) | P1 | OPEN | live probe 2026-10-29: POST /api/v1/notifications/{id}/test w/ https://example.com/hooks/invalid -> 502 {detail: Failed to send test...}; code notifications.py:248-252 maps send==False to 502 | — | — |
| B02 | api | Valid non-UTF8 CSVs rejected + BOM corrupts column name: latin-1 -> 422 'does not match extension'; utf-16 -> 422; BOM file -> 201 but column stored as \ufeffa (validation uses utf-8-sig, extraction utf-8) | P1 | OPEN | live probes 2026-10-29 (see phase-1 notes); files.py:558-561 vs files.py:641-656 decode mismatch | — | — |

## Architectural warnings from compose-log analysis (to resolve in Phase 1)

| ID | warning | status | evidence |
|---|---|---|---|
| W01 | worker-default consumes critical+default (priority isolation broken) | SUSPECTED | compose:364-367 vs docs/celery-task-queues.md:19-25 |
| W02 | OTEL_SAMPLE_RATE=1.0 in compose vs 0.1 default/docs (Jaeger 100K cap overrun risk) | SUSPECTED | compose:179,254 vs .env.example:108 |
| W03 | nginx->flower service_started + /flower/->404 + missing healthchecks (silent/undetected dependency failure) | SUSPECTED | compose:861-862; pipelineiq.conf:221-223 |

## Rules reminder

Reproduce with failing test -> root-cause -> smallest fix -> keep regression test -> rerun suite -> log + commit. No weakening tests. No try/except swallowing, no hiding races, no sleep fixes.

## Phase 1 baseline notes (2026-10-29)

- Pre-rebuild: pipelineiq-api was Restarting (255) on the stale stack (finding: API crash loop before clean rebuild; fresh build resolved, root cause TBD from old logs - volumes since removed).
- `docker compose down -v` on LOCAL stack (local creds, 127.0.0.1 bindings) then `up --build`: all 25 containers healthy, zero restarts. Time to healthy ~110s.
- Log triage: redis overcommit x4 (host sysctl, dev-harmless; redis-overcommit-fix container is echo no-op - S30); postgres 'SET LOCAL can only be used in transaction blocks' at 05:35:27 (matches S27 migration concern, under investigation); grafana missing provisioning dirs + sqlite lock retries at boot (transient, healthy after); minio single-drive, redpanda dev-mode/btrfs/perf_event, jaeger 0.0.0.0 warnings (all dev-noise, harmless).
- Stack probes: /health 200, /readyz all-ok, SSE 200, nginx / 307, /api 401 w/o auth (correct), B01 502 reproduced, B02 latin-1/utf-16 422 + BOM column corruption reproduced.

## Baseline rerun findings (2026-10-29)

- Unit baseline part 1 (no DB overrides): chunk1 4 failed/270 passed, chunk2 403 passed, chunk3 250 passed. Total 923 passed, 4 failed, 3 skipped.
- The 4 failures (timing assert 7.5s>1s; 3x rate-limit 202-vs-429) reran GREEN serially (7 passed in 2.65s) after pointing DATABASE_* at 127.0.0.1:5432. Root cause: `log_action` (audit_service.py:38) opens a real SessionLocal to `pgbouncer` hostname (unresolvable from host) and commit()s synchronously in the request path; each mutating request burned ~8-15s in DNS/connect stall. Slow requests stretched the 11-request loop past the 60s sliding window (rate limits never tripped) and broke the <1s timing assert. No product change to the tests; env fix only. Lesson: local unit runs must export DATABASE_URL/WRITE/READ_URL to 127.0.0.1:5432 (mirrors CI localhost PG).

## Baseline unit run 2 (with DB overrides, 2026-10-29)

- chunk1: 274 passed, 2 skipped (97s). chunk2: 403 passed, 1 skipped (5s). chunk3: 250 passed (8s).
- Total: 927 passed, 3 skipped, 0 failed. Counts as clean-baseline run 1 of 3.
- Env required for host-side runs: STORAGE_TYPE=local, UPLOAD_DIR=/tmp/qa-uploads, REDIS_* -> 127.0.0.1 ports, DATABASE_* -> 127.0.0.1:5432. (Mirrors CI localhost services; repo .env docker hostnames are unreachable from host.)

## Coverage baseline (2026-10-29)

- Unit suite with coverage: 927 passed, 3 skipped, 0 failed (121s).
- TOTAL incl. tests/scripts: 68% (20999 stmts). App code only (omit tests/scripts/alembic/benchmark): 63% (12124 stmts, 4427 miss). Target 90% -> ~3400 lines to cover.
- Lowest app modules: tasks/scheduled_pipeline 18%, tasks/webhook_tasks 19%, storage/lifecycle 20%, tasks/streaming_pipeline 20%, execution/healing_agent 21%, tasks/schedule_tasks 22%, routers/catalog_pipelines 23%, services/notification_service 24%, routers/streaming 25%, api/contracts 26%, routers/wasm 26%, tasks/gemini_tasks 26%, routers/storage 28%, streaming/redpanda_client 28%.

## Frontend baseline (2026-10-29)

- `npx vitest run` in frontend/: 22 files, 230 tests, all passed (12.5s). Counts as clean-baseline run 1 of 3.
- Note: `--reporter=basic` is not a valid vitest reporter in this repo (startup error); use default reporter.

## Static analysis baseline (2026-10-29)

- ruff (default all rules): 1940 errors. CI enforces only E9,F63,F82 (pass). Biggest: UP045 308, EXE002 293 (executable bit on non-scripts), UP006 272, B008 236, I001 200, BLE001 156, F401 79, RUF012 31, S110 31. 204 files need reformatting. Decision for fix loop: add pyproject.toml with enforced rule set, fix violations, document rest.
- tsc --noEmit (frontend): clean, exit 0.
- eslint (frontend): 20 errors, 3 warnings. Real: react-hooks/refs-during-render (usePipelineEditor.ts:588 collabRef, useCollaborativePipeline authTokenRef), setState-in-effect, display-name, purity. Config gap: .next-build/ output linted (needs ignore).
- bandit: 0 High, 83 Medium, 2171 Low. Medium = B608 SQL-string building (sql_builder by design w/ validator; catalog.py:359,617 raw SQL to review; tests), B113 requests-without-timeout (tests only), B310 (tests). Low = B101 asserts (tests), S110 try-pass.
- pip-audit: pyjwt 2.10.1 (5 advisories, fix 2.14/2.15), starlette 0.35.1 (multiple, fix up to 1.x), opentelemetry-api 1.42.0 (CVE-2026-114603, fix 1.45.1). Upgrades must stay compatible with fastapi 0.109.

## Secrets + Docker lint (2026-10-29)

- hadolint: Dockerfile DL3008/DL3015 (unpinned apt, no --no-install-recommends); backend/Dockerfile DL3013 (pip from requirements file but flagged: uses requirements.txt so acceptable w/ pinned file); pgbouncer/Dockerfile DL3018 (unpinned apk). frontend/nginx/y-websocket clean.
- gitleaks working tree: .env hits are the ignored local dev file (gitignored, never committed); .next*/.next-build hits are build-chunk hashes (false positives, build dirs untracked... verify gitignored); scripts/run_tests.py:5, run_stress.py:4, security_tests.py:3 hardcoded JWTs (tracked, expired 2026-04 but must remove).
- gitleaks history (173 commits): 5 hits. tests/{security_tests,run_tests,run_stress}.py JWTs in old commit (files moved to scripts/, still present -> same fix). AGENTS.md:1500 + A.md:1654 generic-api-key are doc placeholders ("eyJhbGciOi...") -> false positives, files removed from HEAD.

## npm audit (2026-10-29)

- Prod (omit=dev): 8 vulns (1 moderate, 6 high, 1 critical): baseline-browser-mapping(m), browserslist(h), js-yaml(h), nanoid(h), next(critical, via postcss+sharp), postcss(h), sharp(h, libheif+librsvg), source-map-js(h). All transitive via Next toolchain; fix via npm audit fix + rebuild + retest in fix loop.
- All (incl dev): 17 vulns (5 moderate, 10 high, 2 critical).

## Production build (2026-10-29)

- `next build` in frontend/: PASSES (route table emitted, middleware 34.7kB). First attempt OOM-killed by systemd-oomd (swap >90%%); retry with taskset -c 0-3 + NODE_OPTIONS=--max-old-space-size=2048 succeeded.
- next.config.ts: eslint.ignoreDuringBuilds=true (lint not enforced at build; CI must enforce separately — verify eslint step is blocking), typescript.ignoreBuildErrors=false (good), output standalone, duplicate /api/:path*/ + /api/:path* rewrites (harmless).
- OOM note: box has 8GB RAM; full parallel runs (-n 8 pytest, unconstrained next build) get OOM-killed. Use -n 2 and CPU/heap caps. Swap hogs: gunicorn workers, redpanda, celery ( Infine: normal).

## mypy baseline (2026-10-29)

- mypy (default settings, ignore-missing-imports): 178 errors in 48 files (294 checked). Top: autogenerated migration 1a158ef49016 (29 arg-type on alter_column server_default), pipeline/validators.py (17 index on Any|None), api/pipelines.py (9), api/files.py (8), execution/wasm_executor.py (7), contracts/validator.py (6). Fix loop: add mypy config, fix app-code errors, exclude or annotate migrations/tests deliberately.
