# Required GitHub Secrets

Secrets live in repository settings (`Settings > Secrets and variables > Actions`). Workflows that need a missing secret fail with a message naming it or skip gracefully — see the notes below.

## CI (`ci.yml`)

CI injects hardcoded test values for databases, Redis, and app secrets, so no secrets are required to run the standard jobs. Two secrets are used by optional remote jobs:

| Secret | Used In | Purpose |
|--------|---------|---------|
| `PRODUCTION_URL` | e2e-tests, uptime | Public frontend URL (E2E runs against the deployed app on `main`/dispatch) |
| `PRODUCTION_API_URL` | e2e-tests, nightly load tests | Public API URL |

Without them: the E2E job fails naming the secret; the nightly load job fails; the uptime probe logs a notice and skips (it only monitors when configured).

## CD (`cd.yml`)

| Secret | Required For |
|--------|--------------|
| `RENDER_DEPLOY_HOOK_URL` | Triggering the Render backend deploy after green CI on `main` |

The job exits non-zero with a naming message when the hook is unset.

## Deploy Pipeline — Civo k3s (`deploy.yml`)

All secrets are injected into Kubernetes via `kubectl create secret generic pipelineiq-secrets`. Without `KUBE_CONFIG` the workflow runs in dry-run mode (builds and tags the image, skips the cluster).

| Secret | Used For |
|--------|----------|
| `KUBE_CONFIG` | Base64-encoded kubeconfig for the Civo cluster. `cat ~/.kube/config \| base64 \| tr -d '\n'` |
| `DATABASE_URL`, `DATABASE_WRITE_URL`, `DATABASE_READ_URL` | Postgres connection strings |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database credentials |
| `JWT_SECRET`, `SECRET_KEY` | Yjs signing key and app secret. `openssl rand -hex 32` / `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `GEMINI_API_KEY` (+ `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`) | Gemini worker and AI endpoints |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Object storage (S3 keys must match the MinIO credentials) |
| `LOAD_TEST_TOKEN` | Bearer token for authenticated k6 load scenarios |

## How to Generate

```bash
# Generate a secure SECRET_KEY (48 url-safe chars)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Generate a secure POSTGRES_PASSWORD (32 hex chars)
python3 -c "import secrets; print(secrets.token_hex(16))"

# Generate a secure JWT_SECRET
openssl rand -hex 32
```
