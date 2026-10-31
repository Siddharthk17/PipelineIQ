# Support

## Where to Get Help

| Channel | Use for |
|---------|---------|
| [GitHub Issues](../../issues) (bug report / feature request templates) | Bugs, reproductions, feature proposals |
| [RUNBOOK.md](RUNBOOK.md) | Operating the stack (bring-up, common failures, recovery) |
| [README.md](README.md) | Full product and API documentation |
| `docs/testing/SYSTEM_MAP.md` | Service map, ports, and data flow |
| [SECURITY.md](SECURITY.md) | **Vulnerabilities — private advisories only, never public issues** |

There is no chat/Discord support at this time; GitHub Issues is the official support channel and is monitored by the maintainers.

## Before Opening an Issue

1. Search existing [issues](../../issues) (open and closed) — including the CI failure history on `main` — to avoid duplicates.
2. Reproduce on the latest `main` (or state your exact commit SHA).
3. Collect the diagnostics below so the issue can be acted on without a round trip.

### Include in every bug report

- Commit SHA (`git rev-parse --short HEAD`) and how you run the stack (Compose, k8s, or local dev).
- Relevant service versions: `docker compose ps`, plus `python --version` / `node --version` for local runs.
- Minimal reproduction: smallest pipeline YAML, API request/response pair, or failing test command.
- Logs with secrets redacted: `docker compose logs --tail=50 <service>` (never paste `SECRET_KEY`, passwords, tokens, or JWTs).
- For UI bugs: browser + version, URL, and whether an incognito window behaves differently (rules out stale auth state).

## Quick Troubleshooting

| Symptom | Likely cause | First check |
|---------|--------------|-------------|
| Login fails in browser but `curl` login returns 200 | Stale cookies or `TRUSTED_HOSTS` missing your hostname | Incognito window; `grep TRUSTED_HOSTS .env` |
| `Invalid or expired token` on every API call | Cache Redis unreachable (revocation check is fail-closed) | `REDIS_CACHE_URL` reachable; `docker compose ps redis-*` |
| `pull access denied` for an image | Upstream registry removed/renamed the repo | Check the tag exists; see `.env` `*_IMAGE_TAG` |
| Redis warnings about memory overcommit | Host `vm.overcommit_memory` not set | `sudo sysctl -w vm.overcommit_memory=1` |
| 404 on `/docs`, `/flower/`, `/grafana/` | Nginx serving a stale container | `docker compose up -d --build --force-recreate nginx` |

## Response Expectations

This is a maintainer-driven project. Issues with a clear reproduction get priority; questions without version info or logs may be closed as `needs-info`. Security reports follow the [Security Policy](SECURITY.md) timelines, not the ones here.
