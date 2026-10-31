# Contributing to PipelineIQ

Thank you for your interest in contributing to PipelineIQ. This document describes how to set up a development environment, run the test gates, and submit changes that pass CI the first time.

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). Security issues are handled separately — see [SECURITY.md](SECURITY.md) and **do not** open public issues for vulnerabilities.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start (Docker Compose)](#quick-start-docker-compose)
- [Backend Development](#backend-development)
- [Frontend Development](#frontend-development)
- [Running the Test Gates](#running-the-test-gates)
- [Lint, Types, and Formatting](#lint-types-and-formatting)
- [End-to-End Tests](#end-to-end-tests)
- [Commit Conventions](#commit-conventions)
- [Branching and Pull Requests](#branching-and-pull-requests)
- [Project Structure](#project-structure)
- [Getting Help](#getting-help)

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Docker + Docker Compose | recent | Required for the full stack (Postgres, Redis × 4, MinIO, workers) |
| Python | 3.11 | Backend runtime and tests |
| Node.js | 20 | Frontend runtime, Vitest, Playwright |
| npm | 10+ | Workspaces (root `package.json` + `frontend/`) |
| Git | any recent | Conventional commits, no force-push to shared branches |

Linux is the primary development platform. If you are on macOS/Windows, run everything through Docker Compose.

## Quick Start (Docker Compose)

```bash
git clone https://github.com/Siddharthk17/PipelineIQ.git
cd PipelineIQ

# 1. Create your environment file and fill in secrets
cp .env.example .env
# Generate a strong secret (32+ bytes hex):
python3 -c "import secrets; print(secrets.token_hex(32))"
# Paste it as SECRET_KEY, plus POSTGRES_PASSWORD, REDIS_PASSWORD, etc.

# 2. Redis needs this on the Docker host (once per boot)
sudo sysctl -w vm.overcommit_memory=1

# 3. Build and start the full stack
docker compose up -d --build

# 4. Verify
curl -f http://localhost/health        # API through Nginx → {"status":"ok",...}
curl -f http://localhost/              # Frontend
```

Default demo login: `demo@pipelineiq.app` / `Demo1234!` (seeded by `backend/scripts/seed_demo.py`; demo credentials are public by design).

Useful service URLs (all via Nginx on port 80 unless noted):

| URL | Service |
|-----|---------|
| `http://localhost/` | Frontend (Next.js) |
| `http://localhost/docs`, `/redoc`, `/openapi.json` | FastAPI docs |
| `http://localhost/flower/` | Celery monitor (basic auth: `FLOWER_USER`/`FLOWER_PASSWORD`) |
| `http://localhost/grafana/` | Dashboards |
| `http://localhost/prometheus/graph` | Metrics |

## Backend Development

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Point at your Compose Postgres/Redis, or export DATABASE_URL/REDIS_*_URL
alembic upgrade head

# Run the unit suite (SQLite in-memory + TestClient; Postgres/Redis services
# are used by CI, see .github/workflows/ci.yml)
python -m pytest tests/ -v --tb=short
```

Key backend facts:

- App entrypoint: `backend/main.py`; settings in `backend/config.py` (Pydantic, fails fast on bad config).
- Auth is JWT in an `HttpOnly` cookie (`pipelineiq_token`) plus `Authorization: Bearer` support; token revocation is Redis-backed and **fail-closed** — if Redis is unreachable, tokens are denied. Never weaken this in tests; point `REDIS_CACHE_URL` at a real Redis instead.
- Audit logging (`backend/services/audit_service.py`) must never break the request it records; keep it that way.
- Pipeline steps live in `backend/pipeline/` (`parser.py`, `steps/`, `runner.py`, `lineage.py`). `backend/pipeline/steps/__init__.py` re-exports the public surface — if you add an executor-level symbol that tests or routers import, export it there.
- Celery queues: `critical`, `default`, `bulk`, `gemini`, `streaming`. New periodic tasks must be registered in `backend/celery_app.py` **and** covered by `backend/tests/unit/infrastructure/test_celery_task_registry.py`.

## Frontend Development

```bash
# From the repo root (npm workspaces)
npm ci
cd frontend
npm run dev        # http://localhost:3000 (proxied as / through Compose Nginx)
```

Environment: `NEXT_PUBLIC_API_URL` is baked at build time (`/api/v1` in Compose). Auth state is cookie-only — `lib/api.ts` intentionally has no token storage; do not reintroduce localStorage tokens.

## Running the Test Gates

CI (`.github/workflows/ci.yml`) runs these jobs; a PR must keep all of them green:

1. **Backend Tests** — `cd backend && python -m pytest tests/ -v --tb=short` against PostgreSQL 15 + Redis 7 services. ~1100 tests; do not skip or weaken failing tests — fix the code.
2. **Frontend** — `npx tsc --noEmit`, `npm test` (Vitest), `npm run build`.
3. **Code Quality** — `ruff check backend/ --select E9,F63,F82` (blocking syntax/undefined-name checks) and `tsc --noEmit` (blocking). ESLint currently reports informationally.
4. **Docker Compose Smoke Test** — builds the whole stack and exercises health → login → upload → pipeline run → completion.
5. **Streaming E2E** — Redpanda-backed streaming suite (runs on `main`).
6. **Playwright E2E** (`e2e/`) — Chromium only in CI; runs on `main` pushes and manual dispatch.

## Lint, Types, and Formatting

```bash
# Backend (from repo root or backend/)
ruff check backend/ --select E9,F63,F82   # blocking CI gate
ruff check backend/                        # full pass, keep clean

# Frontend (from frontend/)
npx tsc --noEmit
npm run lint
```

Rules of thumb: no `Any`-typed public API boundaries without reason, no commented-out code, no emoji in logs, no test-result/build-output artifacts committed (they are git-ignored).

## End-to-End Tests

```bash
npx playwright install --with-deps chromium   # Chromium only; Firefox/WebKit are not set up
npx playwright test e2e/01-auth.spec.ts       # single spec
```

E2E specs assume a running stack (`E2E_BASE_URL`, `E2E_API_URL` for remote runs; local runs target Compose). Shared-room/collaboration specs must isolate state per test (unique `?pipeline=` rooms) — see `y-websocket/__tests__/shared-room.test.js` for the pattern.

## Commit Conventions

- Use Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`, `refactor:`, `perf:`.
- Subject line: imperative mood, **≤ 72 characters**, no trailing period.
- Keep the full message to a **subject + one body block** unless the change genuinely needs more context.
- Never commit: `.env`, secrets, tokens, `test-results/`, `.next/`, `playwright-report/`, `e2e/.auth/`, editor/OS files. When in doubt, `git status` before `git add` — stage files explicitly instead of `git add .`.
- Never force-push to shared branches (`main`, `e2e-hardening`, release branches). If a push is rejected, pull/rebase and push normally.

## Branching and Pull Requests

1. Fork the repo (external contributors) or create a feature branch (`git checkout -b fix/short-description`).
2. Make focused changes; add or update tests for every behavior change.
3. Run the gates locally: backend pytest file(s) + `tsc --noEmit` + relevant Vitest specs. Full-suite runs are slow (bcrypt rounds); at minimum run the suites touching your change plus `backend/tests/unit/api/test_auth.py` for auth-adjacent work.
4. Open a PR against `main` using the template. Fill in **Summary, Root cause, Change, Verification** — PRs without verification steps will be sent back.
5. Keep the PR small and single-purpose. CI must be green; do not merge with failing jobs.

## Project Structure

```
backend/            FastAPI app (api/, auth, pipeline/, execution/, tasks/, db/)
frontend/           Next.js 15 + TypeScript dashboard (app/, components/, lib/)
e2e/                Playwright specs (auth, upload, builder, schedules, journeys)
y-websocket/        Yjs collaboration server
nginx/              Reverse proxy (public routes: /, /docs, /flower, /grafana, /prometheus)
prometheus/ grafana/  Observability configs
k8s/                Civo k3s manifests
docs/testing/       Test ledger, plans, reports (source of truth for QA state)
.github/workflows/  CI (backend, frontend, quality, smoke, streaming, e2e)
```

## Getting Help

- Bugs and feature requests: [open an issue](../../issues) using the templates.
- Operational questions: see [RUNBOOK.md](RUNBOOK.md) and `docs/testing/SYSTEM_MAP.md`.
- Security reports: see [SECURITY.md](SECURITY.md) — private advisories only.
