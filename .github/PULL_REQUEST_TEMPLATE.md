# Pull Request

## Summary

<!-- One or two sentences: what does this change do and why? -->

## Root Cause

<!-- For fixes: what was actually wrong? Link the failing CI run, issue, or log excerpt. Omit for pure features. -->

## Change

<!-- File-level description of the fix. Call out any behavior, config, or schema changes. -->

-
-

## Verification

<!-- Evidence the change works. Check all that apply and paste proof (commands + results). -->

- [ ] Backend: `cd backend && python -m pytest tests/<path> -v --tb=short` — result:
- [ ] Frontend: `npx tsc --noEmit`, `npm test`, `npm run build` — result:
- [ ] Lint: `ruff check backend/ --select E9,F63,F82` — result:
- [ ] E2E (if UI/behavior change): Playwright spec + result:
- [ ] CI jobs green on this PR (link run):

## Checklist

- [ ] Commit message follows Conventional Commits, subject ≤ 72 chars.
- [ ] No secrets, `.env` contents, tokens, or local-only files committed (`git status` reviewed).
- [ ] No build outputs, test reports, or editor files committed.
- [ ] Tests added or updated for the behavior change; no existing test weakened or deleted to make it pass.
- [ ] Docs updated if user-facing behavior, routes, env vars, or ops steps changed.
- [ ] No force-push to shared branches.
