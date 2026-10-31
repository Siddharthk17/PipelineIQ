#!/usr/bin/env bash
#
# PipelineIQ first-boot bootstrap.
#
# Turns a fresh `git clone` (new VM, new account, empty .env) into a working
# deployment with a single command:
#
#   ./scripts/bootstrap.sh [--host HOST] [--yes]
#
# What it does:
#   1. Installs git/curl/openssl + Docker Engine + compose plugin if missing.
#   2. Creates .env from .env.example (if absent) and fills every secret the
#      compose stack requires. Existing real values are NEVER overwritten.
#   3. Detects the public host (flag > $PUBLIC_HOST > GCP metadata > first
#      IPv4 from `hostname -I` > localhost) and writes PUBLIC_HOST +
#      TRUSTED_HOSTS so the reverse proxy accepts browser traffic.
#   4. Enables vm.overcommit_memory=1 (host-level, Redis requirement).
#   5. Prints firewall/static-IP commands and the `make up` next step.
#
# Safe to re-run at any time: it only fills gaps, never rotates live secrets.
# To rotate a secret, edit .env by hand and `docker compose up -d <service>`.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
EXAMPLE_FILE="$ROOT/.env.example"

HOST_OVERRIDE=""
ASSUME_YES=0
RUN_UP=0

usage() {
  cat <<'EOF'
Usage: ./scripts/bootstrap.sh [--host HOST] [--up] [--yes]

  --host HOST   Public hostname or IP of this deployment
                (default: auto-detect via GCP metadata, else first IPv4)
  --up          After bootstrapping, also build + start the stack
                (same as running 'make up' afterwards)
  --yes         Non-interactive; assume yes for optional steps
  -h, --help    Show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST_OVERRIDE="${2:?--host requires a value}"; shift 2 ;;
    --up) RUN_UP=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

log()  { printf '\033[1;34m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bootstrap:WARN]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[bootstrap:ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

command_exists() { command -v "$1" >/dev/null 2>&1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command_exists sudo; then SUDO="sudo"; else warn "not root and no sudo; privileged steps will print manual instructions"; fi
fi

# --------------------------------------------------------------------------
# 1. Base packages + Docker Engine
# --------------------------------------------------------------------------

if ! command_exists git || ! command_exists curl || ! command_exists openssl || ! command_exists python3 || ! command_exists make; then
  log "Installing base packages (git, curl, openssl, python3, make)..."
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq git curl openssl ca-certificates python3 make
fi

if ! command_exists docker; then
  log "Docker not found — installing Docker Engine + compose plugin..."
  if [ -f /etc/os-release ]; then . /etc/os-release; else die "cannot detect OS; install Docker manually: https://docs.docker.com/engine/install/"; fi
  case "${ID:-}" in
    ubuntu|debian)
      $SUDO install -m 0755 -d /etc/apt/keyrings
      $SUDO sh -c "curl -fsSL https://download.docker.com/linux/${ID}/gpg -o /etc/apt/keyrings/docker.asc && chmod a+r /etc/apt/keyrings/docker.asc"
      # shellcheck disable=SC1091
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
      $SUDO apt-get update -qq
      $SUDO apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      ;;
    *) die "unsupported distro '${ID:-unknown}'; install Docker manually: https://docs.docker.com/engine/install/" ;;
  esac
  $SUDO systemctl enable --now docker 2>/dev/null || warn "could not enable docker service; start it manually"
  if ! $SUDO docker compose version >/dev/null 2>&1; then
    die "docker compose plugin missing after install; install it manually and re-run"
  fi
  log "Docker installed."
  if ! docker info >/dev/null 2>&1; then
    warn "current user cannot reach the docker socket (re-login or 'sudo usermod -aG docker \$USER'). Continuing with sudo for this run."
    SUDO="sudo"
  fi
fi

if ! $SUDO docker compose version >/dev/null 2>&1; then
  die "docker compose plugin not found; install it and re-run"
fi

# --------------------------------------------------------------------------
# 2. .env from .env.example + secret generation (fill gaps only)
# --------------------------------------------------------------------------

[ -f "$EXAMPLE_FILE" ] || die "$EXAMPLE_FILE not found; are you in the repo root?"
if [ ! -f "$ENV_FILE" ]; then
  log "Creating .env from .env.example..."
  cp "$EXAMPLE_FILE" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
else
  log ".env exists — only filling missing/placeholder values."
fi

gen_hex() { openssl rand -hex "$1"; }

# A value counts as "missing" when empty or an obvious template placeholder.
is_placeholder() {
  case "${1,,}" in
    ""|your_*|replace_with*|change-me*|changeme*|example*|test|test123*|password|password123|secret|secret123|xxx*) return 0 ;;
    *) return 1 ;;
  esac
}

get_var() { grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2-; }

set_var() { # set_var NAME VALUE — replace existing line or append
  local name="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^${name}=" "$ENV_FILE"; then
    sed -E "s|^${name}=.*|${name}=${value}|" "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$name" "$value" >> "$tmp"
  fi
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}

ensure_secret() { # ensure_secret NAME HEXBYTES
  local cur; cur="$(get_var "$1" || true)"
  if is_placeholder "$cur"; then set_var "$1" "$(gen_hex "$2")"; log "generated $1"; fi
}

log "Filling required secrets (existing values are never touched)..."
ensure_secret POSTGRES_PASSWORD 24
ensure_secret REDIS_PASSWORD 16
ensure_secret SECRET_KEY 32

if is_placeholder "$(get_var MINIO_ROOT_USER || true)"; then set_var MINIO_ROOT_USER "minioadmin"; log "set MINIO_ROOT_USER=minioadmin"; fi
ensure_secret MINIO_ROOT_PASSWORD 20

# S3 access MUST equal the MinIO credentials or object storage breaks.
S3U="$(get_var S3_ACCESS_KEY || true)"; S3S="$(get_var S3_SECRET_KEY || true)"
MU="$(get_var MINIO_ROOT_USER)"; MS="$(get_var MINIO_ROOT_PASSWORD)"
if is_placeholder "$S3U" || [ "$S3U" != "$MU" ]; then set_var S3_ACCESS_KEY "$MU"; log "set S3_ACCESS_KEY (matches MinIO user)"; fi
if is_placeholder "$S3S" || [ "$S3S" != "$MS" ]; then set_var S3_SECRET_KEY "$MS"; log "set S3_SECRET_KEY (matches MinIO password)"; fi

[ -z "$(get_var FLOWER_USER || true)" ] && { set_var FLOWER_USER "admin"; log "set FLOWER_USER=admin"; }
ensure_secret FLOWER_PASSWORD 16
[ -z "$(get_var GRAFANA_USER || true)" ] && { set_var GRAFANA_USER "admin"; log "set GRAFANA_USER=admin"; }
ensure_secret GRAFANA_PASSWORD 16

# Replication password must equal POSTGRES_PASSWORD when explicitly set,
# otherwise the replica cannot authenticate (compose default only applies
# when the variable is entirely absent).
RPW="$(get_var REPLICATION_PASSWORD || true)"; PPW="$(get_var POSTGRES_PASSWORD)"
if is_placeholder "$RPW" || [ "$RPW" != "$PPW" ]; then set_var REPLICATION_PASSWORD "$PPW"; log "set REPLICATION_PASSWORD (matches Postgres)"; fi

# --------------------------------------------------------------------------
# 3. Public host detection → PUBLIC_HOST + TRUSTED_HOSTS
# --------------------------------------------------------------------------

detect_host() {
  if [ -n "$HOST_OVERRIDE" ]; then printf '%s' "$HOST_OVERRIDE"; return; fi
  if [ -n "${PUBLIC_HOST:-}" ]; then printf '%s' "$PUBLIC_HOST"; return; fi
  local ip
  ip="$(curl -fsS -m 5 -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || true)"
  if [ -z "$ip" ]; then
    ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -m1 -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || true)"
  fi
  printf '%s' "${ip:-localhost}"
}

HOST="$(detect_host)"
if [ "$HOST" = "localhost" ]; then
  warn "could not detect a public IP; using localhost. Pass --host <IP-or-domain> for a public deployment."
else
  log "public host: $HOST"
fi

PH="$(get_var PUBLIC_HOST || true)"
if is_placeholder "$PH" || [ "$PH" = "localhost" ]; then
  set_var PUBLIC_HOST "$HOST"; log "set PUBLIC_HOST=$HOST"
else
  log "keeping existing PUBLIC_HOST=$PH (override with --host to change)"
  HOST="$PH"
fi

# Merge required entries into TRUSTED_HOSTS without dropping custom ones.
BASE_HOSTS="localhost 127.0.0.1 testserver api frontend nginx grafana flower prometheus jaeger sse-service y-websocket host.docker.internal"
CUR="$(get_var TRUSTED_HOSTS || true)"
NEW="$CUR"
for h in $BASE_HOSTS "$HOST"; do
  case ",$NEW," in *,"$h",*) ;; *) NEW="${NEW:+$NEW,}$h" ;; esac
done
if [ "$NEW" != "$CUR" ]; then set_var TRUSTED_HOSTS "$NEW"; log "set TRUSTED_HOSTS (added missing entries)"; fi

if is_placeholder "$(get_var GEMINI_API_KEY || true)"; then
  warn "GEMINI_API_KEY is empty — AI generation/heuristics return a clear 503 until you set it in .env"
fi

chmod 600 "$ENV_FILE" 2>/dev/null || true

# --------------------------------------------------------------------------
# 4. Host sysctl for Redis (vm.overcommit_memory=1)
# --------------------------------------------------------------------------

if [ "$(cat /proc/sys/vm/overcommit_memory 2>/dev/null || echo "?")" != "1" ]; then
  # Best-effort: never abort the bootstrap over this (warn + manual command).
  if ( $SUDO sysctl -w vm.overcommit_memory=1 >/dev/null 2>&1 \
    && echo "vm.overcommit_memory=1" | $SUDO tee /etc/sysctl.d/99-pipelineiq.conf >/dev/null 2>&1 ); then
    log "vm.overcommit_memory=1 set (persisted in /etc/sysctl.d/99-pipelineiq.conf)."
  else
    warn "could not set vm.overcommit_memory (no privilege?). Run once as root: sysctl -w vm.overcommit_memory=1"
  fi
else
  log "vm.overcommit_memory=1 already set."
fi

# --------------------------------------------------------------------------
# 5. Next steps
# --------------------------------------------------------------------------

cat <<EOF

==============================================
 PipelineIQ bootstrap complete
==============================================
 Public host : $HOST
 .env        : $ENV_FILE (600, gaps filled; re-run anytime, values preserved)

 Next:
   make up        # build + start everything, then verify
   make status    # container + endpoint status
   make logs      # follow all logs (make logs SVC=api for one service)

 URLs (after 'make up'):
   App      : http://$HOST/
   API docs : http://$HOST/docs  (/redoc, /openapi.json)
   Flower   : http://$HOST/flower/  (basic auth, see .env FLOWER_*)
   Grafana  : http://$HOST/grafana/login
   Jaeger   : http://$HOST/jaeger/
   Prometheus: http://$HOST/prometheus/graph (proxied UI)

 If this is a NEW cloud VM, open port 80 first (example for GCP):
   gcloud compute firewall-rules create pipelineiq-http \\
     --allow=tcp:80 --source-ranges=0.0.0.0/0 --target-tags=pipelineiq
   ...and attach the 'pipelineiq' network tag to the VM (or allow HTTP at
   creation time). Reserve a static external IP so reboots don't move you:
   gcloud compute addresses create pipelineiq-ip --region=<REGION>

 Optional (enables AI features): set GEMINI_API_KEY in .env, then
   docker compose up -d --force-recreate worker-gemini api
==============================================
EOF

if [ "$RUN_UP" -eq 1 ]; then
  log "starting the full stack..."
  if command_exists make && [ -f "$ROOT/Makefile" ]; then
    ( cd "$ROOT" && make up )
  else
    ( cd "$ROOT" && $SUDO docker compose up -d --build )
  fi
fi
