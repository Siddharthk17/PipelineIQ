# Required GitHub Secrets

The following secrets must be configured in your GitHub repository settings
(`Settings > Secrets and variables > Actions`) for CI/CD to work.

## CI Pipeline (`ci.yml`)

| Secret | Description | Example |
|--------|-------------|---------|
| `POSTGRES_PASSWORD` | PostgreSQL password for test database | `ci_test_password_2024` |
| `SECRET_KEY` | Application secret key (32+ chars) | `your-long-random-secret-key-here` |

> **Important:** The application will refuse to start in production (`ENVIRONMENT=production`) if `SECRET_KEY` is left at the default value `change-me-in-production`. Always set a strong, unique key.

## CD Pipeline (`cd.yml`)

| Secret | Description | Required |
|--------|-------------|----------|
| `RENDER_DEPLOY_HOOK_URL` | Render deploy hook URL for backend service | Required for main branch deploys |

## Deploy Pipeline (Civo k3s) (`deploy.yml`)

The following secrets are required to deploy to the Civo k3s production cluster.
All secrets are injected into Kubernetes via `kubectl create secret generic pipelineiq-secrets --from-literal`.

### Infrastructure Secrets

| Secret | Description | How to Generate |
|--------|-------------|-----------------|
| `KUBE_CONFIG` | Base64-encoded Civo kubeconfig file | `cat ~/.kube/config \| base64 \| tr -d '\n'` |
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://pipelineiq:PASSWORD@postgres:5432/pipelineiq` |
| `DATABASE_WRITE_URL` | PostgreSQL write connection string | Same as DATABASE_URL (single-node k3s) |
| `DATABASE_READ_URL` | PostgreSQL read connection string | Same as DATABASE_URL (single-node k3s) |
| `POSTGRES_USER` | PostgreSQL username | `pipelineiq` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `openssl rand -hex 16` |
| `POSTGRES_DB` | PostgreSQL database name | `pipelineiq` |

### Application Secrets

| Secret | Description | How to Generate |
|--------|-------------|-----------------|
| `JWT_SECRET` | JWT token signing key | `openssl rand -hex 32` |
| `SECRET_KEY` | Application secret key for session encryption | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `GEMINI_API_KEY` | Google AI Studio API key for Gemini worker | From https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | Default Gemini model for AI features | `gemini-2.5-flash` |
| `GEMINI_FALLBACK_MODELS` | Fallback models on rate limit (429) | `gemini-2.0-flash,gemini-1.5-flash` |

### Storage Secrets

| Secret | Description | How to Generate |
|--------|-------------|-----------------|
| `MINIO_ROOT_USER` | MinIO administrator username | `pipelineiq` |
| `MINIO_ROOT_PASSWORD` | MinIO administrator password | `openssl rand -hex 16` |
| `S3_ACCESS_KEY` | S3/MinIO access key | Same as MINIO_ROOT_USER |
| `S3_SECRET_KEY` | S3/MinIO secret key | Same as MINIO_ROOT_PASSWORD |

### How to Set Up

1. Create a Civo account at https://civo.com
2. Create a k3s cluster: `civo kubernetes create pipelineiq-prod --size g4s.kube.small --nodes 1 --region LON1 --wait`
3. Download kubeconfig: `civo kubernetes config pipelineiq-prod --save`
4. Encode kubeconfig: `cat ~/.kube/config | base64 | tr -d '\n'` → paste into `KUBE_CONFIG` secret
5. Generate all passwords using the commands above
6. Add all secrets in Settings → Secrets and variables → Actions
7. Push to main branch to trigger deployment

### Verifying Secrets

```bash
kubectl get secret pipelineiq-secrets -n pipelineiq -o jsonpath='{.data}' \
  | python3 -c "import sys,json,base64; d=json.load(sys.stdin); [print(f'{k}: {len(base64.b64decode(v))} chars') for k,v in d.items()]"
```

Expected output: every key listed above with non-zero character counts.

## How to Generate

```bash
# Generate a secure SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Generate a secure POSTGRES_PASSWORD
python3 -c "import secrets; print(secrets.token_urlsafe(24))"

# Generate a secure JWT_SECRET
openssl rand -hex 32
```
