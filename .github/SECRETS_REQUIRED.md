# Required GitHub Secrets

The following secrets must be configured in your GitHub repository settings
(`Settings > Secrets and variables > Actions`) for CI/CD to work.

## CI Pipeline (`ci.yml`)

| Secret | Description | Example |
|--------|-------------|---------|
| `POSTGRES_PASSWORD` | PostgreSQL password for test database | `ci_test_password_2024` |
| `SECRET_KEY` | Application secret key (32+ chars) | `your-long-random-secret-key-here` |

## CD Pipeline (`cd.yml`)

| Secret | Description | Required |
|--------|-------------|----------|
| `RAILWAY_TOKEN` | Railway deployment token | Only for main branch deploys |

## How to Generate

```bash
# Generate a secure SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Generate a secure POSTGRES_PASSWORD
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```
