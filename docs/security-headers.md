# Security headers (M-138, verified 2026-11-20)

Headers are set in two layers; both verified present in the tree:

- Nginx (`nginx/conf.d/security-headers.inc`, included by every
  `location` in `nginx/conf.d/pipelineiq.conf`): `X-Frame-Options:
  SAMEORIGIN`, `X-Content-Type-Options: nosniff`, `X-XSS-Protection:
  1; mode=block`, `Referrer-Policy: strict-origin-when-cross-origin`,
  `Strict-Transport-Security: max-age=31536000; includeSubDomains`,
  `Permissions-Policy: camera=(), microphone=(), geolocation=(),
  payment=()`.
- API middleware (`backend/main.py`, application layer so headers hold
  even when Nginx is bypassed): `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, `Permissions-Policy`, plus HSTS only when the
  request arrived over TLS (Compose terminates plain HTTP on loopback;
  production TLS terminates at the ingress).

## CSP: verified, not tightened further

The frontend `Content-Security-Policy` (`nginx/conf.d/pipelineiq.conf`)
allows `script-src 'self' 'unsafe-inline' 'unsafe-eval'
'unsafe-hashes'`. Removing those is NOT safe to do blindly: the Next.js
frontend relies on inline scripts/styles for hydration, and dropping them
without a nonce/hash rollout breaks the app. Tightening requires the
deploy cluster to (1) enable Next CSP nonces or hash the build's inline
blocks, (2) re-run the full Playwright suite plus `e2e/13-accessibility`,
(3) confirm zero console CSP violations — tracked as a manual owner
action in REPORT.md.
