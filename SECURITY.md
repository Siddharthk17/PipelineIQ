# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 12.x (current, `main`) | Yes — security fixes |
| < 12.0 | No — please upgrade |

Only the current release line receives security updates. If you are running an older checkout, upgrade to `main` before reporting — the issue may already be fixed.

## Reporting a Vulnerability

**Do not open a public issue, discussion, or pull request for a suspected vulnerability.**

Report privately via **GitHub Security Advisories** (preferred):

1. Go to the repository's **Security** tab → **Advisories** → **New draft security advisory**.
2. Include the details listed below.
3. Click through to notify the maintainers privately.

### What to include

- Affected component and version/commit (e.g. `backend/auth.py @ 98b5e93`, or the deployed tag).
- Steps to reproduce (request/response pairs, minimal YAML, or a failing test where possible).
- Impact assessment: what an attacker gains (auth bypass, data exfiltration, RCE, DoS) and preconditions (authenticated vs unauthenticated, admin vs viewer).
- Any relevant logs (redact secrets, tokens, and PII).

### What happens next

- We aim to **acknowledge within 48 hours**.
- We aim to ship a fix for **critical** issues (auth bypass, RCE, data breach) within **7 days**, and for lower severities within **30 days**, then publish an advisory and credit you (unless you prefer anonymity).
- Please give us a reasonable window to remediate before any public disclosure, and do not exploit the issue beyond what is necessary to demonstrate it.

## Scope and Known Design Decisions

These are intentional and **not** vulnerabilities — please do not report them:

- **Demo credentials** (`demo@pipelineiq.app` / `Demo1234!`) are published in the README and seeded by `backend/scripts/seed_demo.py`. They exist for the public demo only; rotate or remove the demo user in any private deployment.
- **Public-by-design routes** (`/docs`, `/redoc`, `/openapi.json`, `/flower/`, `/grafana/`, `/prometheus/`, `/jaeger/`) are exposed through Nginx intentionally. Flower and Grafana sit behind their own authentication; Jaeger, Prometheus, and the API docs carry no login — do not expose them on untrusted networks if their contents are sensitive. The login-free routes are additionally throttled at the proxy (`limit_req`, HTTP 429 on excess) so a single client cannot scrape or hammer them; this is abuse *mitigation*, not access control.
- **Rate limits** on auth endpoints (`5/minute` on login/register) are a baseline, not a substitute for WAF/proxy-level throttling in production.

In-scope areas we especially care about: JWT issuance/validation and revocation (`backend/auth.py`), SSRF/path-traversal in file handling and pipeline inputs, WASM sandbox escapes (`backend/execution/wasm_executor.py`), SQL injection in DuckDB/SQL steps, privilege escalation (viewer → admin), secret leakage in logs or API responses, and container/Compose hardening (`nginx/`, `docker-compose.yml`, `k8s/`).

## Hardening Guidance for Operators

- Never deploy with the example secrets: generate strong values for `SECRET_KEY` (≥ 32 bytes), `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `FLOWER_PASSWORD`, and `GRAFANA_PASSWORD`.
- Set `ENVIRONMENT=production` (enforces secure cookies and refuses weak secrets at startup) and serve behind TLS.
- Keep `TRUSTED_HOSTS` restricted to the hostnames you actually serve.
- Set `vm.overcommit_memory=1` on the Docker host (required for Redis persistence).
- Containers run least-privilege by default: backend as `appuser`, frontend as `nextjs`, nginx workers as `nginx`, pgbouncer via `su-exec postgres`, y-websocket production builds as `node`. Do not add `privileged: true` or `user: root` overrides to compose/k8s without an ADR.

## Supply-Chain Security

- Weekly Dependabot PRs (pip / npm / Docker / Actions) are the only way new dependency versions enter; merge them only when CI is green.
- `.github/workflows/security.yml` enforces: `pip-audit` (blocking), npm audit critical gate, a GPL/AGPL/LGPL/EPL/MPL fail-closed license check, `hadolint` on all Dockerfiles, and a Trivy filesystem SARIF upload.
- Report dependency vulnerabilities the same way as any other — private advisory, never a public issue.
