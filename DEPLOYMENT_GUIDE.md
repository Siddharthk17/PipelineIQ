# PipelineIQ — Fresh-VM Deployment Guide (GCP, end to end)

Deploy the entire flagship stack on a **brand-new GCP VM** so that afterwards a
single command brings up everything, all public, just like production:

```bash
git clone https://github.com/Siddharthk17/PipelineIQ.git
cd PipelineIQ
./scripts/bootstrap.sh --up
```

Time budget: ~10 min of your time + 15–30 min of machine time on first boot
(Docker pulls + frontend build). After that, the site lives at
`http://<EXTERNAL-IP>/`.

> Convention used below: `<EXTERNAL-IP>` is your VM's external IP,
> `<ZONE>` its zone (e.g. `asia-south1-a`), `<PROJECT>` your GCP project.
> Replace them verbatim in every command.

---

## Table of Contents

1. [What you get](#1-what-you-get)
2. [Prerequisites](#2-prerequisites)
3. [Step 1 — Reserve a static IP](#3-step-1--reserve-a-static-ip)
4. [Step 2 — Create the VM](#4-step-2--create-the-vm)
5. [Step 3 — Open port 80](#5-step-3--open-port-80)
6. [Step 4 — SSH in](#6-step-4--ssh-in)
7. [Step 5 — Clone](#7-step-5--clone)
8. [Step 6 — Run the bootstrap (`--up`)](#8-step-6--run-the-bootstrap---up)
9. [Step 7 — Point the deploy at its IP (only if needed)](#9-step-7--point-the-deploy-at-its-ip-only-if-needed)
10. [Step 8 — Enable AI features (optional, recommended)](#10-step-8--enable-ai-features-optional-recommended)
11. [Step 9 — Verify end to end](#11-step-9--verify-end-to-end)
12. [Step 10 — Open it in a browser](#12-step-10--open-it-in-a-browser)
13. [Operating the deployment](#13-operating-the-deployment)
14. [Updating without breaking things](#14-updating-without-breaking-things)
15. [Reboots, billing, and shutting down](#15-reboots-billing-and-shutting-down)
16. [Troubleshooting encyclopedia](#16-troubleshooting-encyclopedia)
17. [Security checklist for a public demo](#17-security-checklist-for-a-public-demo)
18. [Costs (new-account free trial)](#18-costs-new-account-free-trial)
19. [Going further (when you're ready)](#19-going-further-when-youre-ready)

---

## 1. What you get

After this guide, all of the following answer on port 80 of your VM —
no SSH tunnels, no `localhost:xxxx` ports:

| URL | What it is | Auth |
|-----|-----------|------|
| `http://<EXTERNAL-IP>/` | The PipelineIQ app | demo login (below) |
| `http://<EXTERNAL-IP>/docs`, `/redoc`, `/openapi.json` | FastAPI docs | none (public by design) |
| `http://<EXTERNAL-IP>/flower/` | Celery worker monitor | basic auth (`FLOWER_USER`/`FLOWER_PASSWORD` in `.env`) |
| `http://<EXTERNAL-IP>/grafana/` | Dashboards | Grafana login (`GRAFANA_USER`/`GRAFANA_PASSWORD` in `.env`) |
| `http://<EXTERNAL-IP>/jaeger/` | Distributed tracing | none — read the warning in §17 |
| `http://<EXTERNAL-IP>/prometheus/graph` | Metrics | none — read the warning in §17 |
| `ws://<EXTERNAL-IP>/yjs/` | Live-collaboration socket | same session as the app |

Demo login (seeded automatically): `demo@pipelineiq.app` / `Demo1234!`

## 2. Prerequisites

- A GCP account + project with billing enabled (new accounts get a $300 free trial — see §18).
- [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed and logged in (`gcloud auth login`), **or** use the Cloud Console for Steps 1–4 and the in-browser SSH button.
- Nothing else. Docker, secrets, host tuning, and trusted-host wiring are all handled by the repo's bootstrap script.

Minimum VM shape (do not go smaller — the stack OOMs below this):
**2 vCPU + 8 GB RAM** (e.g. `e2-standard-2`), **50 GB** balanced persistent disk.
An `e2-micro` (1 GB) will not run this stack, free-tier or not.

## 3. Step 1 — Reserve a static IP

Ephemeral external IPs change on every reboot, which silently breaks Grafana redirects, bookmarks, and anything that cached the address. Reserve once, keep it for the VM's life:

```bash
gcloud compute addresses create pipelineiq-ip --region=asia-south1
gcloud compute addresses describe pipelineiq-ip --region=asia-south1 \
  --format='value(address)'
```

Save the printed address — that is your `<EXTERNAL-IP>` everywhere below.
(Use your own region consistently in every command; `asia-south1` (Mumbai) is a good default for India.)

## 4. Step 2 — Create the VM

Console path: *Compute Engine → VM instances → Create instance* with:
name `pipeline-iq`, region/zone of your choice, machine `e2-standard-2`
(2 vCPU, 8 GB), boot disk *Ubuntu 24.04 LTS*, *50 GB*, *Balanced persistent disk*;
*Networking → External IPv4 address → `pipelineiq-ip`* (the one you just reserved);
*Firewall → tick **Allow HTTP traffic*** (or do Step 3 via gcloud instead).

Equivalent one-liner (run from your laptop):

```bash
gcloud compute instances create pipeline-iq \
  --zone=asia-south1-a \
  --machine-type=e2-standard-2 \
  --image-family=ubuntu-2404-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-balanced \
  --address=pipelineiq-ip \
  --tags=pipelineiq,http-server
```

The `pipelineiq` tag pairs with the firewall rule in Step 3. `http-server`
additionally opens port 80 via the default rule — harmless redundancy.

## 5. Step 3 — Open port 80

If you ticked *Allow HTTP traffic* above, skip this. Otherwise:

```bash
gcloud compute firewall-rules create pipelineiq-http \
  --allow=tcp:80 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=pipelineiq \
  --description="PipelineIQ web"
```

Verify: `gcloud compute firewall-rules describe pipelineiq-http --format='value(allowed)'`

## 6. Step 4 — SSH in

```bash
gcloud compute ssh pipeline-iq --zone=asia-south1-a
```

(Or click **SSH** next to the instance in the Console — same thing.)

## 7. Step 5 — Clone

```bash
git clone https://github.com/Siddharthk17/PipelineIQ.git
cd PipelineIQ
git log --oneline -1   # note the commit; quote it in any bug report
```

## 8. Step 6 — Run the bootstrap (`--up`)

This is the entire install:

```bash
./scripts/bootstrap.sh --up
```

What it does (safe to re-run any time; existing values are never overwritten):

1. Installs `git/curl/openssl/python3/make` if missing, then **Docker Engine + the compose plugin** if missing.
2. Creates `.env` from `.env.example` and generates every secret the stack needs (Postgres, Redis, JWT, MinIO, S3-matched creds, Flower, Grafana, replica password matched to Postgres). It also fixes two silent-failure traps: S3 credentials must equal the MinIO credentials, and the replica password must equal the Postgres password.
3. Detects the public IP (GCP metadata → host IPv4 → `localhost` fallback) and writes `PUBLIC_HOST` + merges the IP into `TRUSTED_HOSTS` (without this, every browser login fails with `400 Invalid host header`).
4. Sets `vm.overcommit_memory=1` persistently (Redis requirement), or prints the one-liner if it lacks privilege.
5. With `--up`: builds all images and starts all 30 services, then prints every URL.

Takes 15–30 min on first run (image pulls + Next.js build). Do not `Ctrl-C` the build — re-running resumes safely.

Useful variants:

```bash
./scripts/bootstrap.sh --help            # all flags
./scripts/bootstrap.sh --host 1.2.3.4    # force a host instead of auto-detect
make setup && make up                    # same thing in two steps
make status                              # containers + public endpoint codes
make logs SVC=api                        # follow one service (default: all)
make verify                              # full end-to-end smoke test
```

## 9. Step 7 — Point the deploy at its IP (only if needed)

If the bootstrap auto-detected your IP (it prints `public host: …`), **skip this.**
If it fell back to `localhost`, or you later attach a domain/static IP:

```bash
cd ~/PipelineIQ
grep '^PUBLIC_HOST=' .env
```

- To set it: edit `.env` (`PUBLIC_HOST=1.2.3.4`, no scheme, no trailing slash), then re-run `./scripts/bootstrap.sh` (it preserves your value and re-merges `TRUSTED_HOSTS`), then restart the proxy: `docker compose up -d --force-recreate nginx grafana`.
- The current value is always honored — the script never overwrites a real `PUBLIC_HOST`.

## 10. Step 8 — Enable AI features and error tracking (optional, recommended)

AI generation/repair/autocomplete need a Gemini key; without it the API returns a clear `503` (by design, not a bug). Sentry is the same shape: empty DSN means disabled, set DSN means stack traces in Sentry for the API and all Celery workers.

1. Create keys: [Google AI Studio](https://aistudio.google.com/apikey) (free tier is fine) and [Sentry](https://sentry.io) (free tier is fine; create a FastAPI project and copy its DSN).
2. Set them on the VM **without ever printing them** (input is hidden; nothing lands in shell history as a value):

```bash
cd ~/PipelineIQ
read -rsp "Paste NEW Gemini key: " K; echo
read -rsp "Paste Sentry DSN (empty to skip): " S; echo
python3 - "$K" "$S" <<'PY'
import sys
from pathlib import Path
k, s = sys.argv[1].strip(), sys.argv[2].strip()
p = Path(".env"); lines = p.read_text().splitlines()
out = [l for l in lines if not l.startswith(("GEMINI_API_KEY=", "SENTRY_DSN="))]
out.append(f"GEMINI_API_KEY={k}")
if s:
    out.append(f"SENTRY_DSN={s}")
p.write_text("\n".join(out) + "\n")
print("written, gemini key-length:", len(k), "| sentry:", "set" if s else "skipped")
PY
unset K S
docker compose up -d --force-recreate worker-gemini api worker-critical worker-default worker-bulk worker-streaming celery-beat sse-service
sleep 20
```

3. Verify (the Test key never leaves the VM):

```bash
TOKEN=$(curl -s -X POST http://localhost/auth/login -H 'Content-Type: application/json' -d '{"email":"demo@pipelineiq.app","password":"Demo1234!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s -X POST http://localhost/api/ai/generate -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{"description":"Load a CSV and show row counts","file_ids":[]}' | head -c 300; echo
unset TOKEN
```

`200` with YAML = AI works. `503 AI features are unavailable` = the key isn't in the containers (repeat step 2 and confirm the recreates happened). Sentry: trigger any 500 path (or check Sentry > Issues after the next real error) — with an empty DSN nothing is sent, by design.

> If a key is ever pasted in chat, logs, or screenshots, **revoke it at the source immediately** and create a new one — treat it as compromised.

## 11. Step 9 — Verify end to end

```bash
cd ~/PipelineIQ
make verify
```

Expected codes (anything else → jump to §16):

| Path | Expected | Meaning |
|------|----------|---------|
| `/` | 307 | frontend redirect (correct) |
| `/docs`, `/openapi.json`, `/redoc` | 200 | public API docs |
| `/flower/` | 401 | reachable, asking for basic auth (correct) |
| `/grafana/login` | 200 | Grafana through the proxy |
| `/jaeger/` | 200 | Jaeger UI through the proxy |
| `/prometheus/graph` | 302 | Prometheus redirect (correct) |
| `/health`, `/readyz` | 200 | backend alive |
| login | 200 + `set-cookie` | auth works through nginx |

## 12. Step 10 — Open it in a browser

Use an **incognito window** for the first login (rules out stale cookies from earlier attempts):

- App: `http://<EXTERNAL-IP>/` → sign in with `demo@pipelineiq.app` / `Demo1234!`
- Docs: `http://<EXTERNAL-IP>/docs`
- Flower: `http://<EXTERNAL-IP>/flower/` (user `FLOWER_USER`, password from `.env`)
- Grafana: `http://<EXTERNAL-IP>/grafana/` (user `GRAFANA_USER`, password from `.env`)
- Jaeger: `http://<EXTERNAL-IP>/jaeger/`
- Prometheus: `http://<EXTERNAL-IP>/prometheus/graph`

If the app loads but the pipeline builder shows `ws://localhost:1234` errors in the console, your frontend bundle predates the YJS fix — rebuild it: `docker compose build frontend && docker compose up -d --force-recreate frontend`, then hard-refresh (`Ctrl+Shift+R`).

## 13. Operating the deployment

```bash
cd ~/PipelineIQ
docker compose ps                       # container health at a glance
make status                             # + public endpoint codes
make logs SVC=api                       # follow one service (api, worker-default, flower, nginx, ...)
docker compose logs --tail=50 nginx     # nginx access log (who hit what, backend replies)
docker compose up -d --force-recreate <svc>   # restart one service with latest config
docker compose down                     # stop everything (data kept in volumes)
docker compose down --volumes           # WARNING: destroys all data (DB, MinIO, Redis). Never on a live demo.
```

Backups (do this before anything risky):

```bash
cd ~/PipelineIQ
make backup                                   # pg_dump (-Fc) + gzip into ./backups/, 7-day retention
ls -la backups/
```

Restore (destructive — replaces the live database; practice on a scratch box first):

```bash
cd ~/PipelineIQ
make restore BACKUP=backups/pg-<date>.sql.gz  # stops writers, drops, loads, migrates, restarts, verifies
```

The dump covers PostgreSQL only. MinIO objects (uploads, pipeline outputs,
WASM modules) are not in `pg_dump` — mirror buckets separately with a local
`mc` install. (The MinIO server image does not ship the `mc` client, so this
does not run inside the `minio` container.)

```bash
curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc -o /tmp/mc \
  && chmod +x /tmp/mc
/tmp/mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
/tmp/mc mirror --overwrite local/pipelineiq-uploads /tmp/minio-mirror/uploads
```

Full drill procedure (backup → restore → verify) lives in `RUNBOOK.md` under Disaster Recovery.

### Seed the public demo (supervised first run)

The landing page and demo account only impress with data in them.
`backend/scripts/seed_showcase.py` fills the demo account with 5 datasets,
50 pipelines covering every step type, and mixed outcomes:

```bash
cd ~/PipelineIQ
docker compose exec -T api python -m backend.scripts.seed_showcase
```

Expected output: `Showcase seeded: 50 runs (10 failed, 5 healed), 5 files, 4 schedules.`
Verify before trusting the nightly cron to it: open Runs, Files, Schedules in
the browser — all populated, failures visible.

Re-running is idempotent (existing names are skipped). `--reset` wipes ONLY
demo-owned runs/schedules/files and re-seeds — the nightly cron at 02:30 uses
exactly this flag, so this supervised run is the same code path the cron takes:

```bash
docker compose exec -T api python -m backend.scripts.seed_showcase --reset
```

## 14. Updating without breaking things

The golden rule: **a `git pull` never restarts containers by itself** — breakage comes from stale containers running old config, not from git. Safe update flow:

```bash
cd ~/PipelineIQ
git status --short                                            # know what is dirty
cp nginx/conf.d/pipelineiq.conf /tmp/pipelineiq.conf.working  # backup any local nginx edits
cp docker-compose.override.yml /tmp/override.yml 2>/dev/null || true
cp .env /tmp/env.backup 2>/dev/null || true
git pull --ff-only origin main
git status --short
```

- If the pull refuses because of local edits: those edits are still newer than `main`. Keep them, or back them up and `git checkout -- <file>` to take the repo version.
- If `main` changed `docker-compose.yml` service flags or `nginx/conf.d/`: recreate the affected services so the *running* config matches the *file* config, e.g. `docker compose build nginx && docker compose up -d --force-recreate nginx <svc>`, then re-run `make verify`.
- Never `git stash` + forget the `pop`, and never force-push shared branches.
- `docker-compose.override.yml` and `.env` are untracked on purpose — pulls never touch them. Keep VM-specific values (like a hardcoded Grafana host) in the override file, never in tracked files.

## 15. Reboots, billing, and shutting down

- **Reboots are safe by default:** the bootstrap enables the Docker service, every service has `restart: unless-stopped`, `vm.overcommit_memory` persists via `/etc/sysctl.d/99-pipelineiq.conf`, and data lives in Docker volumes. After a reboot, give it 2–3 minutes, then `make status`.
- **Ephemeral IPs are not:** a reboot can reassign the external IP. That is why Step 1 reserves a **static IP** — do not skip it, or Grafana redirects and bookmarks break silently.
- **Billing:** stopping the VM stops CPU/RAM charges (disk still bills a few dollars/month). To stop everything: `gcloud compute instances stop pipeline-iq --zone=<ZONE>`; to delete: `gcloud compute instances delete pipeline-iq --zone=<ZONE>` (delete disks too, or they keep billing). The $300 trial resets per *account*, not per VM — new accounts get a fresh trial.

## 16. Troubleshooting encyclopedia

Every entry below is a failure actually observed on this stack, with the exact fix.

### Login fails in the browser, but `curl` login returns 200

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@pipelineiq.app","password":"Demo1234!"}'
```

- If curl is 200 but the browser (even incognito) fails → stale cookies (clear site data) or the frontend bundle predates a fix → rebuild frontend.
- If both fail with `400` and the body is 19 bytes (`Invalid host header`) → the Host isn't in `TRUSTED_HOSTS`. Fix: set `PUBLIC_HOST` correctly and re-run `./scripts/bootstrap.sh`, then `docker compose up -d --force-recreate nginx`.

### `Something went wrong` on login with no useful message

Old frontend builds masked the real error (`body stream already read`). Rebuild frontend, retry, and read the new message — it names the cause.

### Auth rate-limited after repeated tries

Login/register are limited to 5/minute per IP. Wait 60 seconds and retry once — do not hammer the button.

### `/docs`, `/flower/`, `/grafana/…` return 404, or nginx serves stale config

The running nginx container predates the config file. Rebuild + recreate it and verify the *live* config:

```bash
docker compose build --no-cache nginx
docker compose up -d --force-recreate nginx
docker compose exec nginx cat /etc/nginx/conf.d/pipelineiq.conf | grep -A3 "location /docs"
docker compose exec nginx nginx -t
```

### Flower shows 502

Flower takes 30–60s to boot (broker connect + app import). Wait, then check `docker compose logs --tail=15 flower`. A steady `401` on `/flower/` is **correct** — it's the basic-auth prompt. Test with credentials: `curl -u "$FLOWER_USER:$FLOWER_PASSWORD" http://localhost/flower/ -o /dev/null -w "%{http_code}\n"` (expect 200).

### Prometheus `/prometheus/graph` 404s

No nginx route or missing `--web.route-prefix=/prometheus` flag. Both ship in the repo now — pull latest, rebuild nginx, recreate prometheus, expect a 302 (its redirect).

### Jaeger page loads but CSS/JS fail with `MIME type ('text/html')`

The proxy is serving Jaeger's fallback HTML instead of assets (variable+URI `proxy_pass` mangling — fixed in the repo: pass the URI through untouched and keep `--query.base-path=/jaeger` **without** a trailing slash; Jaeger panics on `/jaeger/`). Pull latest and recreate nginx + jaeger, then hard-refresh.

### Jaeger container crash-loops (`Restarting (2)`)

Read the panic: `docker compose logs --tail=80 jaeger | grep -i -m1 -B1 -A1 "panic:"`. The two known causes are a trailing slash in `--query.base-path` and YAML quoting damage from `sed` (check the override file parses: `docker compose config >/dev/null`).

### Collaboration shows `ws://localhost:1234` errors in the console

Old frontend bundle. Rebuild + recreate frontend, hard-refresh. The current code derives the socket URL from the page host (`/yjs` proxied by nginx).

### `POST /api/ai/generate` → 499, or `Failed to execute 'text' on 'Response'`

499 = the browser gave up before the backend answered (slow/abandoned request). Rebuild frontend for the unmasking fix, then retry — the real error now surfaces. `503 AI features are unavailable` = set `GEMINI_API_KEY` (§10).

### `pull access denied` / `unauthorized` for an image

Upstream registries remove images (this happened with MinIO). The repo pins working mirrors — `git pull` latest first. Never invent a mirror; verify the tag exists.

### Redis `overcommit_memory` warnings

One-time host fix (bootstrap does it): `sudo sysctl -w vm.overcommit_memory=1` (persisted in `/etc/sysctl.d/99-pipelineiq.conf`).

### `.env` drift after updates

```bash
diff <(grep -o '^[A-Za-z_]*' .env | sort -u) <(grep -o '^[A-Za-z0-9_]*' .env.example | sort -u)
```

Missing names (left side only) go in `.env`; extra names on the right are new example keys to adopt. Re-run `./scripts/bootstrap.sh` — it fills exactly these gaps.

## 17. Security checklist for a public demo

- [ ] This guide exposes **HTTP only**. Credentials, JWTs, and cookies travel unencrypted — acceptable for a demo, never for real user data. The follow-up is a domain + TLS (Let's Encrypt/certbot or Caddy in front of nginx).
- [ ] Jaeger and Prometheus have **no login**. Anyone can read traces and infra metrics. Keep them public only while the data is demo data.
- [ ] Flower and Grafana have basic-auth/logins — set strong passwords (bootstrap generates them; do not weaken to `admin/admin`).
- [ ] `.env` is `chmod 600` and git-ignored. Never paste secrets in chat, issues, logs, or screenshots (revoke + rotate immediately if one leaks).
- [ ] `TRUSTED_HOSTS` lists only your hosts — do not set it to `*`.
- [ ] Rotate the demo password before any private showing where visitors shouldn't mutate shared state — or better, reseed nightly.
- [ ] For private showings, prefer a **viewer account over sharing the admin demo login**: register a second user (first account stays admin, later ones are viewers), hand out the viewer credentials, and keep `demo@pipelineiq.app` for yourself. Viewers can run and inspect but cannot manage users or schedules. The nightly reseed still restores anything guests create.

## 18. Costs (new-account free trial)

Rough order of magnitude (check the [pricing calculator](https://cloud.google.com/products/calculator) for your region):

| Item | Approx. cost |
|------|--------------|
| `e2-standard-2` (2 vCPU, 8 GB), us-central1 | ~$50/mo (~$0.07/hr) |
| 50 GB balanced persistent disk | ~$5/mo |
| Static external IP (in use) | ~$3/mo |
| Egress to demo visitors | first 100 GB/mo free, then ~$0.12/GB |

A 90-day $300 trial comfortably covers months of demo hosting on one VM. Biggest cost lever: **stop the VM when nobody is demoing** (`gcloud compute instances stop …`), or drop to a smaller disk. Do not use spot/preemptible VMs for anything you can't afford to lose without a backup.

## 19. Going further (when you're ready)

- **Custom domain + HTTPS** — the single biggest flagship upgrade after this guide (also unlocks Secure cookies and HSTS).
- **Nightly demo reseed** — a cron job running the showcase seed on a schedule so visitors always land on an alive, pristine demo (already installed by bootstrap; verify with `crontab -l | grep pipelineiq`).
- **Backups** — nightly `pg_dump` + MinIO bucket sync to Cloud Storage. Prove restores with `make restore BACKUP=...` on a scratch box before you need it (see RUNBOOK.md Disaster Recovery).
- **External uptime monitor** — `.github/workflows/uptime.yml` probes the public URL every 5 minutes once you set the `PRODUCTION_URL` repository secret (Actions → repo Settings → Secrets → Actions). For paging (not only a red badge), point Better Stack or Uptime Kuma at the same `/health` endpoint.
- **Status page** — a tiny public `/health`-based status check visitors can see.
- **Release discipline** — Release-please tags versions and publishes GitHub Releases from conventional commits; keep CI green and the badges reflect it.
