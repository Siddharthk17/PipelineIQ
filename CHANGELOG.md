# Changelog

All notable changes to PipelineIQ are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/)

## [1.3.9] — Frontend Testing

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

## [0.3.12] — Week 3: DevOps

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

## [0.1.2] — Week 1: Foundation

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
