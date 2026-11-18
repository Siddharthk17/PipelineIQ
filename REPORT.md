# Wave-2 Deploy Remediation Report (branch `fix/w2-deploy`)

Static-only: no docker daemon in this env, so no `compose config` dry-run,
no container boot, no live Redis/auth probe. YAML validated with
`yaml.safe_load_all`; shell snippets checked with `bash -n` semantics by
inspection; `py_compile` passes for `backend/config.py` and `backend/main.py`.
Base for all work is post-Wave-1 HEAD. Only owned files touched
(`promtail/promtail.yml` excepted — required companion to the M-050 socket
removal, see below).

## Per-finding verification + fix

- M-022 (root Dockerfile): confirmed `pip --user` into `/root/.local`
  (mode 700) then `USER appuser` — image cannot boot. Fixed: system-wide
  `pip install --prefix=/usr/local`, base pinned to `python:3.11.13-slim`
  (matches `backend/Dockerfile`). Base-image *digest* pinning not applied:
  no network/daemon here to resolve digests; recorded as follow-up.
- M-023 (k8s sse target): confirmed `backend.sse_app:app` vs actual
  `sse_app` object in `backend/sse_app.py` and compose's
  `gunicorn backend.sse_app:sse_app`. Fixed to `backend.sse_app:sse_app`.
- M-024 (deploy secret gaps): confirmed `deploy.yml` omitted all
  `REDIS_*`/`CELERY_*`/`FLOWER_*`/`GRAFANA_*` keys required by
  `backend/config.py`. Fixed in `deploy.yml` + `k8s/secrets.yaml.template`
  (also added `CELERY_BROKER_URL`, `FLOWER_USER`, `GRAFANA_USER`).
- M-025/M-139 (gates): confirmed `deploy.yml` ran on every `push` to
  `main` with no test gate. Fixed: `workflow_run` on green `CI` + `main`
  only (mirrors `cd.yml`), manual dispatch restricted to `main`.
- M-049 (Prometheus): confirmed `--web.enable-lifecycle` + open
  `/prometheus/` proxy. Fixed: flag removed, lifecycle paths denied,
  `/prometheus/` restricted to internal networks + loopback.
- M-050 (promtail socket): confirmed `/var/run/docker.sock` mount (root
  equivalent). Fixed: mount removed; scrape switched from `docker_sd` to
  static json-file log paths. Companion edit to `promtail/promtail.yml`
  (outside owned list) was unavoidable — without it promtail would
  crash-loop querying an unmounted socket.
- M-056/M-057 (prod boot crash): `backend/config.py` already fail-fasts on
  weak `FLOWER_PASSWORD`/`GRAFANA_PASSWORD` in production (Wave-1, kept).
  Fixed supply side: `render.yaml` now requires all `REDIS_*`,
  `CELERY_RESULT_BACKEND`, `FLOWER_*`, `GRAFANA_*` secrets.
- M-058 (secret interpolation): confirmed `${{ secrets.* }}` inline in
  `run:` scripts (`deploy.yml`, `uptime.yml`, `nightly.yml`). Fixed: all
  secrets pass via `env:` mappings; shell uses `$VAR` only.
- M-070/M-122: `.env.example` holds placeholders only, no live defaults;
  rotation documented as MANUAL owner action (see below).
- M-123: all compose Redis roles wire `--requirepass ${REDIS_PASSWORD}`;
  liveness not runnable here — static note left in compose.
- M-124/M-126/M-133/M-134: compose `mem_limit` + `no-new-privileges` on
  app services, beat/nginx healthchecks added, minio default pinned
  (`RELEASE.2024-12-13T22-19-12Z`), k8s `redis:7.4.2-alpine` pin,
  `securityContext` (`runAsNonRoot:1000` where images run non-root,
  `seccompProfile: RuntimeDefault` everywhere), workflow
  `permissions`/`timeout-minutes` added; action pins untouched.
- M-094/M-135: `CELERY_TASK_{SOFT,HARD}_TIME_LIMIT` settings added with
  matching defaults; ephemeral `SECRET_KEY` now logs loudly.
- M-138/M-148: API security-headers middleware (HSTS only over TLS);
  nginx `server_tokens off`; y-websocket `JWT_SECRET`↔`SECRET_KEY` parity
  documented in `.env.example`.
- M-170/M-186–188: `/readyz` returns 503 while degraded (k8s probe);
  `/health` stays 200 + status field (existing unit test, compose and CI
  smoke assert 200). Dev bind-mounts kept (dev workflow); beat/nginx
  healthchecks added.

## MANUAL owner actions / follow-ups (not done here)

1. Rotate secrets on compromise: update `.env`, GitHub secrets,
   k8s `pipelineiq-secrets`, Render env vars, redeploy all replicas at
   once (M-122). 2. Resolve + pin base-image digests when a daemon is
   available. 3. Wire `CELERY_TASK_*` settings into
   `backend/celery_config.py` (not an owned file). 4. `ghcr:*:latest` k8s
   tags are deployment-managed via `kubectl set image` — intentional.
   5. PG-down boot path inside `lifespan` (`database.py`) is outside owned
   files; compose `wait_for_db` + truthful `/readyz` cover it operationally.
