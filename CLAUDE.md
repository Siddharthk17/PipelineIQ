# CLAUDE.md — Instructions for AI Coding Tools

Read this before touching code. Repo conventions are strict; CI enforces most of them.

## Commands (run from repo root unless noted)

```bash
./scripts/bootstrap.sh --up     # full local stack (creates .env, starts 30 services)
make status                     # container health + public endpoint codes
make verify                     # end-to-end smoke (health, login, routes)
cd backend && python -m pytest tests/ -v --tb=short   # backend suite (needs live Postgres + Redis; CI provides them)
cd frontend && npm test         # Vitest
cd frontend && npx tsc --noEmit # blocking type gate
ruff check backend/ --select E9,F63,F82   # blocking lint gate
npm run lint                    # frontend (informational; has tracked warnings)
npx playwright test e2e/01-auth.spec.ts --reporter=list  # single E2E spec (needs running stack)
```

Frontend tests run from `frontend/`; Playwright specs run from repo root. Never weaken a failing test to make it pass.

## Architecture (30 services, one Compose file)

Browser → Nginx `:80` → api `:8000`, sse-service `:8001`, frontend `:3000`, flower, grafana, prometheus, jaeger, y-websocket. Postgres primary + replica behind two PgBouncers; four Redis roles (broker 6379, pubsub 6380, cache 6381, yjs 6382); MinIO S3; Redpanda; Loki + Promtail; Grafana + Prometheus + Jaeger. Celery queues: `critical`, `default`, `bulk`, `gemini`, `streaming` (+ `celery-beat` singleton).

## Conventions

- Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, …), subject ≤72 chars, no force-push to shared branches.
- `APP_VERSION` lives in 8 files (`backend/config.py`, `frontend/lib/constants.ts`, `.env.example`, `docker-compose.yml` ×2, `k8s/configmap.yaml`, `render.yaml`, CI ×2) — never bump it by hand; release-please owns it.
- Backend: typed dataclasses, dispatch dicts, dependency injection. `backend/pipeline/steps/__init__.py` re-exports the executor surface — export new symbols there.
- Auth is JWT in the `pipelineiq_token` HttpOnly cookie plus Bearer support; revocation is Redis-backed and fail-closed. `lib/api.ts` holds no token client-side.
- `transform` parses but has no batch executor (raises at runtime); `stream_consume` runs only in the streaming daemon. Do not present either as working batch steps.
- Secrets never touch git (`.env` is ignored and `chmod 600`). Public demo creds are `demo@pipelineiq.app` / `Demo1234!` by design.
- Docs rulebook: every statement verifiable in code, exact commands only, no invented metrics, no `docs/adr/` (deleted by maintainer — record decisions in the PR).
- After backend changes: run the touched pytest files; after frontend changes: `tsc --noEmit` + relevant Vitest specs. Always `git status` before staging, and stage explicitly.
