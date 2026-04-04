# Changelog

All notable changes to PipelineIQ are documented here.
Format:[Keep a Changelog](https://keepachangelog.com/)

##[3.1.2] — Week 8: Production Infrastructure Foundation

*Note: This release contains zero user-visible features. It is a complete architectural overhaul of the infrastructure layer to resolve 5 critical scaling bottlenecks, laying the foundation for the Unified Data Operating System roadmap.*

### Performance & Scalability
- **Multi-Worker Celery Architecture (Bottleneck #1)** — Replaced the single Celery worker with three dedicated priority queues (`critical`, `default`, `bulk`). Set `worker_prefetch_multiplier = 1` to prevent task hoarding and ensure true priority execution.
- **Gunicorn & API Scaling (Bottleneck #2)** — Migrated from a single Uvicorn process to Gunicorn with 4 workers. Configured `max-requests=10000` and jitter to prevent Python memory leaks over time.
- **Dedicated SSE Service (Bottleneck #5)** — Extracted Server-Sent Events (SSE) streaming into a completely isolated FastAPI service (`sse_app.py`) running on port 8001. This prevents long-lived pipeline monitoring connections from starving the main API coroutine slots.
- **PgBouncer Connection Pooling (Bottleneck #3)** — Integrated PgBouncer in `transaction` pool mode. Safely multiplexes up to 10,000 client connections down to 25 real PostgreSQL connections, eliminating DB connection crashes under load.
- **PostgreSQL Read Replica** — Deployed a hot-standby read replica. Split database dependencies into `get_read_db` (for GET requests) and `get_write_db` (for mutations) to distribute database load.
- **Redis Instance Splitting (Bottleneck #4)** — Split the single overloaded Redis instance into 4 dedicated, role-specific instances: `redis-broker`, `redis-pubsub`, `redis-cache`, and `redis-yjs`. Implemented module-level connection pools to eliminate per-request connection overhead.
- **Zero-Memory File Uploads (Bottleneck #7)** — Eliminated `await file.read()` memory bombs. Files ≤ 10MB now stream directly to MinIO in 1MB chunks. Files > 10MB now utilize presigned MinIO URLs, allowing the client to upload directly to object storage and bypassing FastAPI entirely.
- **Rust-Based JSON Serialization (Bottleneck #10)** — Replaced Python's standard `json` library with `orjson` across the entire codebase. Set FastAPI's `default_response_class` to `ORJSONResponse`, releasing the GIL and achieving 5-10x faster serialization.

### Added
- **SSE Lifecycle Management** — Added a 15-second heartbeat to the SSE stream to prevent Nginx `proxy_read_timeout` drops. Implemented auto-close on terminal states and immediate client disconnect detection to free server resources instantly.
- **Database Indexes** — Added Alembic migration `0007_add_performance_indexes.py` to create 9 critical performance indexes (e.g., `idx_pipeline_runs_user_created`, `idx_step_results_run_id`, `idx_data_assets_name_trgm`).
- **Infrastructure Test Suite** — Added comprehensive unit and integration tests for the new infrastructure (`test_celery_queues.py`, `test_redis_connections.py`, `test_file_upload.py`, `test_sse_lifecycle.py`, `test_infrastructure.py`).

### Changed
- **Nginx Routing** — Updated `pipelineiq.conf` to route `/api/runs/*/stream` traffic to the new dedicated SSE service on port 8001 with `proxy_buffering off`.
- **Environment Variables** — Updated `.env.example` to include new distinct Redis URLs (`REDIS_BROKER_URL`, `REDIS_PUBSUB_URL`, etc.) and split Database URLs (`DATABASE_WRITE_URL`, `DATABASE_READ_URL`).

---

## [2.1.4] — Week 7: Post-Audit Polish

### Bug Fixes
- **ThemeSelector missing theme** — added "PipelineIQ Light" to `BUILT_IN_THEMES` array in `ThemeSelector.tsx` (was only 6 of 7 themes)
- **CommandPalette missing theme** — added `'pipelineiq-light'` to themes array in `CommandPalette.tsx` (was only 6 of 7 themes)

### Documentation
- **AUDIT_REPORT.md** — updated to reflect all v2.1.0+ fixes: 7 themes (was 6), 14 models (was 10), 13 API routers (was 6), 93 frontend tests acknowledged, fixed issues marked with ✅ status, recommendations updated
- **CHANGELOG.md** — added v2.1.4 entry for post-audit fixes

## [2.1.3] — Week 6 & 7: Codebase Audit: 43 Items Implemented

### Bug Fixes
- **Pagination** — `list_pipeline_runs` now uses `page`, `limit`, and `status_filter` query params with proper `OFFSET`/`LIMIT` (was loading all runs with `.all()`)
- **UTC datetime** — replaced deprecated `datetime.utcnow()` with `datetime.now(timezone.utc)` in auth token creation
- **Shared UUID utils** — extracted duplicated `_validate_uuid_format` and `_as_uuid` to `backend/utils/uuid_utils.py` (was copy-pasted 3-4 times)
- **Redis connection pool** — health check now reuses a module-level `redis.ConnectionPool` instead of creating a new client per call
- **Unified fetch** — merged duplicate `fetchApi`/`fetchAuth` into a single `fetchWithAuth` function parameterized by base URL
- **Package name** — fixed `package.json` name from `ai-studio-applet` to `pipelineiq`

### Security
- **Metrics restricted** — `/metrics` endpoint restricted to internal networks (10.x, 172.16.x, 192.168.x, 127.0.0.1) in nginx
- **Docs disabled in production** — `/docs` and `/redoc` set to `None` when `ENVIRONMENT=production`
- **SameSite=Strict** — `piq_auth` cookie changed from `Lax` to `Strict`
- **Webhook secrets hidden** — `WebhookResponse` now returns `has_secret: bool` instead of the raw secret
- **Password complexity** — registration requires uppercase letter, number, and special character
- **Secret key validation** — `config.py` model validator fails startup if default `SECRET_KEY` is used in production

### Performance
- **ID-only query** — `validate_pipeline` now queries `db.query(UploadedFile.id).all()` instead of loading full objects
- **Referenced file loading** — pipeline tasks only load files referenced in the YAML config, not all files
- **Database indexes** — added Alembic migration with indexes on `pipeline_runs.status`, `pipeline_runs.created_at`, `step_results.pipeline_run_id`, `webhook_deliveries.webhook_id`, and `audit_logs.user_id + created_at`
- **Cache TTL** — all lineage cache entries now expire after 1 hour (`ttl=3600`)
- **SCAN over KEYS** — `cache_delete_pattern` uses `SCAN` with cursor iteration instead of blocking `KEYS` command
- **Preview optimization** — file preview uses `pd.read_csv(nrows=N)` instead of reading the entire file

### Architecture
- **Error boundaries** — React `ErrorBoundary` and `WidgetErrorBoundary` components wrap the app and individual widgets
- **SSE reconnect** — exponential backoff (1s → 2s → 4s → 8s → 16s, max 5 retries) on SSE disconnect
- **Async webhook delivery** — webhook HTTP calls run in a separate Celery task (`webhook_tasks.py`)
- **API versioning headers** — `X-API-Version` and `X-App-Version` response headers via middleware
- **Centralized metrics** — all Prometheus metric definitions extracted to `backend/metrics.py` (resolves circular import)
- **Top-level audit imports** — `from backend.services.audit_service import log_action` moved to module top level

### New Features
- **Pipeline scheduling** — cron-based recurring execution via Celery Beat with CRUD endpoints
- **Pipeline templates** — 5 pre-built templates (ETL, cleaning, validation, aggregation, merge/join)
- **Slack notifications** — notification config with webhook URL, event subscriptions, and test endpoint
- **Data preview in editor** — preview button shows sample data at each pipeline step
- **Step DAG visualization** — horizontal flow diagram showing step execution order with status colors
- **Multi-file pipeline outputs** — support for multiple save steps writing to different files
- **User dashboard** — personal analytics (runs, success rate, most-used pipelines, recent activity)
- **Per-pipeline RBAC** — owner/runner/viewer permissions per pipeline with admin override
- **Pipeline cancellation** — cancel running pipelines with `CANCELLED` status and Celery task revocation
- **File versioning** — version counter and `previous_version_id` FK on uploaded files
- **Export pipeline results** — download output CSV/JSON from completed pipeline runs
- **Light mode** — PipelineIQ Light theme added (7 total themes)
- **Mobile responsive** — stacked single-column widget layout on mobile devices
- **Presence indicators** — online user indicator in top bar (WebSocket-ready)

### Dependency Cleanup
- Removed `@google/genai` from frontend dependencies (unused, 500KB+ savings)
- Removed `firebase-tools` from devDependencies (unused)
- Removed `aioredis` from requirements.txt (deprecated, replaced by `redis.asyncio`)
- Removed `aiofiles` from requirements.txt (not imported anywhere)
- Added `croniter==2.0.1` for cron expression parsing

##[1.3.9] — Week 5: Frontend Testing

### Added
- Vitest + React Testing Library + jsdom test infrastructure
- 93 frontend tests across 8 test files:
  - API layer tests (26): token management, all API functions, error handling, 401 redirect
  - Zustand store tests (26): pipeline, widget binary tree, theme, keybinding stores
  - Page component tests (12): login/register forms, validation, error states, demo login
  - Widget tests (11): QuickStats, FileUpload, RunHistory, FileRegistry
  - Utility tests (7): cn() classname merging, API constants
  - Middleware tests (4): auth redirect logic
  - Auth context tests (4): AuthProvider login, logout, demo login
  - Hook tests (3): widget layout toggle, workspace switching
- `npm run test` and `npm run test:watch` scripts
- CI pipeline now runs frontend tests (tsc → vitest → build)
- Total project tests: 299 (206 backend + 93 frontend)

### Changed
- CI job renamed: "Frontend TypeScript + Build" → "Frontend TypeScript + Tests + Build"
- README testing section split into Backend and Frontend subsections
- AUDIT_REPORT test gaps updated: frontend unit tests now ✅

## [1.2.7] — Week 4: Auth, Observability, Deploy

### Added
- JWT authentication (register, login, roles: admin/viewer)
- Prometheus metrics endpoint with 5 custom counters
- Grafana dashboard with 10 monitoring panels
- Sentry error tracking (FastAPI + Celery + SQLAlchemy)
- Webhook system with HMAC SHA256 signatures and 3-attempt retry
- Immutable audit logging with database trigger enforcement
- Railway production deployment config
- `/auth/register`, `/auth/login`, `/auth/me`, `/auth/users` endpoints
- `/webhooks/` CRUD endpoints with delivery log
- `/audit/logs` admin endpoint, `/audit/logs/mine` user endpoint
- Login and register pages in Next.js frontend
- User info and logout in TopBar
- Debug/sentry-test endpoint (non-production only)

##[0.3.12] — Week 3: DevOps

### Added
- Nginx reverse proxy (port 80, SSE-safe, security headers)
- GitHub Actions CI/CD (backend tests + frontend check)
- Flower Celery monitoring dashboard
- Frontend Week 2 UI: schema drift badge, dry-run plan, version history,
  validate results display, schema history modal

## [0.2.3] — Week 2: Data Platform

### Added
- PostgreSQL migration (UUID PKs, JSONB, connection pooling)
- Alembic migrations (auto-run on startup)
- Redis caching (3x speedup, lineage forever, stats 30s TTL)
- Rate limiting with slowapi (4 tiers)
- Schema drift detection (breaking/warning/info)
- Validate step with 12 check types
- Pipeline versioning with git-style diffs and restore
- Dry-run execution planner (8 heuristics)
- 83 new tests (180 total)

##[0.1.2] — Week 1: Foundation

### Added
- FastAPI backend with 8 pipeline step types
- Column-level data lineage with NetworkX
- Impact analysis and column ancestry
- Celery + Redis async pipeline execution
- SSE real-time streaming
- Next.js 15 frontend with Hyprland-inspired widget system
- 6 themes including custom PipelineIQ Dark
- CommandPalette, keybindings (Vim-style)
- 7 widgets: FileUpload, FileRegistry, PipelineEditor,
  RunMonitor, LineageGraph, RunHistory, QuickStats
- Docker Compose (4 → 5 → 7 → 9 services)
- Apache 2.0 license
- 97 initial tests