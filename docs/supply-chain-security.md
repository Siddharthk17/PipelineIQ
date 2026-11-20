# Supply chain and secrets operations (M-165, M-166, M-139, M-122)

## Backend pinning policy (M-165)

`backend/requirements.txt` pins every dependency with `==`, except the
`wasmtime>=18.0.0` floor. `backend/requirements.lock` freezes the exact
set pip resolves today (the floor resolved to `wasmtime==50.0.1`,
PyPI 2026-11-20, `requires-python >=3.9` so it installs on the
`python:3.11` builder). The lock is a freeze, not a second source of
truth: after any `requirements.txt` change, regenerate from a clean
CI-like env and commit both files:

```bash
pip install -r backend/requirements.txt && pip freeze > backend/requirements.lock
pip install -r backend/requirements.lock   # reproducible install
```

Two coupled families must move together (both noted in the files):

- The nine OTel pins (core `1.45.x` + instrumentation `0.66b2`): the
  exporter requires `sdk~=1.45.2`, which requires
  `semantic-conventions==0.66b2`. Bumping one alone breaks resolution.
- `kombu==5.7.0` is pinned explicitly because Celery's `<6.0` cap lets
  kombu float to a line that demands `redis-py>=6.1.0`.

Transitive-advisory watch: no reachable advisory is actionable from this
tree (static-only env: `pip-audit -r backend/requirements.txt` cannot
resolve here because old pins have no `cp315` wheels — run it in CI or on
the `python:3.11` builder). Owner action: add a scheduled `pip-audit`
(or Dependabot security-only) job and treat new reachable findings as
patch releases, never silent bumps of the OTel/kombu families above.

## Frontend advisories (M-166, verified 2026-11-20)

`npm audit --prefix frontend` reports 11 vulnerabilities (10 moderate,
1 high). Safe (non-breaking, `npm audit fix` applies cleanly):

- `typescript-eslint` chain (`parser`, `plugin`, `type-utils`,
  `typescript-estree`, `utils`) and `minimatch` — `fixAvailable: true`.

Deferred (fix requires a SemVer major, breaking — do not bulk-fix):

- `next` (moderate) and `postcss` (high): fixed only in Next `16.4.6`.
  Upgrading Next is a major migration (verify codemods + the standalone
  output + `frontend/Dockerfile` runner before attempting).
- `postcss-selector-parser` via `@tailwindcss/typography`: fixed only in
  `0.5.4`, also SemVer major.

```bash
npm audit --prefix frontend            # current status (11 vulns: 10 moderate, 1 high)
npm audit fix --prefix frontend        # applies the safe fixes above only (verified dry-run: breaking items untouched)
npm audit fix --force --prefix frontend  # NOT recommended: pulls Next 16 + typography majors
```

Thresholds for CI (verified exit codes): both of these currently exit 1,
so gate on `high` only after the Next upgrade, or gate files changed by
the safe fixes:

```bash
npm audit --audit-level=moderate --prefix frontend  # exit 1 while any moderate+ remains
npm audit --audit-level=high --prefix frontend      # exit 1 while the postcss/Next high remains
```

## Secret scanning in CI (M-139, proposal for the deploy cluster)

No gitleaks step exists in `.github/workflows/ci.yml` yet. Recommended
addition (paste into the `test` or a new `secret-scan` job):

```yaml
- name: Scan for leaked secrets
  uses: gitleaks/gitleaks-action@v2
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Pair with a local pre-commit hook (`gitleaks protect`) so leaks never
reach a PR. Current tree status (M-122, verified 2026-11-20): no live
secrets are committed — `.env.example` uses `REPLACE_WITH_...`
placeholders, CI uses `ci_*` throwaways, public demo creds
(`demo@pipelineiq.app` / `Demo1234!`) are intentional fixtures, and the
`'Str0ngP@ss!'` string in `backend/scripts/create_admin.py` is docstring
example text, not a credential. If history ever held a real secret, the
manual owner actions are: rotate the credential everywhere (`.env`,
GitHub Actions secrets, `k8s/pipelineiq-secrets`, Render env vars),
restart all replicas at once, then purge/push-protect via GitHub
secret scanning — history is NOT rewritten (M-122: tree cleanup only).
