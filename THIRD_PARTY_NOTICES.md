# Third-Party Notices

PipelineIQ is licensed under the [Apache License 2.0](LICENSE).
This file records how third-party license obligations are tracked —
it is a pointer document, not a frozen list, because the dependency set
moves weekly via Dependabot.

## How to reproduce the full inventory

Backend (pinned in `backend/requirements.txt`):

```bash
pip install -r backend/requirements.txt
pip install pip-licenses
pip-licenses --format=plain --from=mixed
```

Frontend workspaces (locked in `package-lock.json`):

```bash
npm ci
npx license-checker --summary
```

CI enforces the policy on every manifest change (`.github/workflows/security.yml`):
`pip-licenses` fails the build on any GPL/AGPL/LGPL/EPL/MPL-licensed package
in the backend tree, and the full `npm audit` report is uploaded as an
artifact. The Trivy filesystem SARIF goes to the Security tab.

## Policy

- Direct dependencies are pinned; transitive offenders get explicit pins
  with a comment naming the failure (see `CONTRIBUTING.md`, Dependency Policy).
- Copyleft licenses (GPL/AGPL/LGPL/EPL/MPL) are not accepted in the backend
  dependency tree. If a needed package introduces one, document the exception
  in the PR that introduces it and call it out for Security Policy review.
- `package.json` license fields across all three npm workspaces declare
  `Apache-2.0`, matching the repository license.
