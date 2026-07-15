#!/usr/bin/env bash
#
# setup-server.sh — Provision a fresh OVH "Docker (Debian 12)" VPS for the
# Challenger Scouting app, from zero to a running HTTPS site.
#
# Idempotent: safe to re-run. Each step checks its own state first.
#
# Usage (as root, on the server):
#   curl -fsSL https://raw.githubusercontent.com/Alexis-Guyon/challenger-scouting/main/ops/setup-server.sh -o setup-server.sh
#   bash setup-server.sh
#
# Or, once the repo is cloned:
#   bash /opt/scouting/ops/setup-server.sh
#
set -euo pipefail

REPO_URL="https://github.com/Alexis-Guyon/challenger-scouting.git"
APP_DIR="/opt/scouting"
DOMAIN="vps-bae259d0.vps.ovh.net"   # OVH hostname → used for automatic HTTPS

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this as root (sudo bash setup-server.sh)." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
say "1/8  System update"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y git curl openssl ca-certificates

# ---------------------------------------------------------------------------
say "2/8  Firewall (SSH + HTTP + HTTPS only)"
if ! command -v ufw >/dev/null 2>&1; then
  apt-get install -y ufw
fi
ufw allow OpenSSH   >/dev/null 2>&1 || ufw allow 22
ufw allow 80        >/dev/null 2>&1 || true
ufw allow 443       >/dev/null 2>&1 || true
ufw --force enable

# ---------------------------------------------------------------------------
say "3/8  Docker check"
if ! command -v docker >/dev/null 2>&1; then
  warn "Docker not found (expected pre-installed on the OVH Docker image) — installing."
  curl -fsSL https://get.docker.com | sh
fi
docker --version
docker compose version

# ---------------------------------------------------------------------------
say "4/8  Fetch the project into ${APP_DIR}"
if [[ ! -d "${APP_DIR}/.git" ]]; then
  git clone "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" pull --ff-only
fi
cd "${APP_DIR}"

# ---------------------------------------------------------------------------
say "5/8  Environment file (${APP_DIR}/.env)"
# docker compose reads the ROOT .env for ${VAR} substitution AND loads it into
# the app container (env_file). This is the single source of secrets.
ENV_FILE="${APP_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  JWT="$(openssl rand -hex 32)"
  PGPW="$(openssl rand -hex 16)"
  cat > "${ENV_FILE}" <<EOF
# ===== Challenger Scouting — production env =====

# --- Riot API (REQUIRED). Get a key at https://developer.riotgames.com ---
RIOT_API_KEY=

# --- Region ---
PLATFORM=euw1
REGION=europe
CURRENT_PATCH=16.14

# --- Auth (generated) ---
JWT_SECRET=${JWT}
JWT_EXPIRY_HOURS=72

# --- Postgres (generated) ---
POSTGRES_PASSWORD=${PGPW}

# --- Ingestion tuning ---
MIN_GAMES=20
MATCH_HISTORY_COUNT=30

# --- Leaguepedia bot (optional — higher rate limits for pro metadata) ---
FANDOM_USERNAME=
FANDOM_PASSWORD=
EOF
  chmod 600 "${ENV_FILE}"
  warn "Created ${ENV_FILE} with generated JWT_SECRET + POSTGRES_PASSWORD."
  warn "You MUST set RIOT_API_KEY (and FANDOM_* if you have them):"
  warn "    nano ${ENV_FILE}"
else
  echo "${ENV_FILE} already exists — leaving it untouched."
fi

# Halt if the Riot key is still empty — the app is useless without it.
if ! grep -qE '^RIOT_API_KEY=RGAPI-' "${ENV_FILE}"; then
  warn "RIOT_API_KEY is not set yet in ${ENV_FILE}."
  warn "Edit the file (nano ${ENV_FILE}), then re-run this script to launch."
  exit 0
fi

# ---------------------------------------------------------------------------
say "6/8  Build & start containers (app + postgres)"
docker compose up -d --build
sleep 6
docker compose ps

# ---------------------------------------------------------------------------
say "7/8  Caddy — HTTPS reverse proxy for ${DOMAIN}"
if ! command -v caddy >/dev/null 2>&1; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y
  apt-get install -y caddy
fi
cp "${APP_DIR}/ops/Caddyfile" /etc/caddy/Caddyfile
systemctl reload caddy 2>/dev/null || systemctl restart caddy
systemctl enable caddy >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
say "8/8  Health check"
sleep 2
if curl -fsS http://127.0.0.1:8000/api/health >/dev/null; then
  echo "Backend healthy on http://127.0.0.1:8000/api/health"
else
  warn "Health check failed — inspect logs: docker compose logs -f app"
fi

cat <<EOF

$(printf '\033[1;32mDone.\033[0m')  App:  https://${DOMAIN}
(TLS cert is issued on first request — may take ~30s the very first time.)

Next steps:
  1. Create the admin user (once):
       cd ${APP_DIR}
       docker compose exec app python scripts/seed_admin.py admin '<strong-password>' admin g2

  2. Log in at https://${DOMAIN}, open the Admin tab, and run an ingestion
     to populate the (empty) database.

Useful commands:
  docker compose logs -f app                      # live app logs
  docker compose exec app python scripts/migrate.py   # re-run migrations
  cd ${APP_DIR} && git pull && docker compose up -d --build   # deploy an update
  docker compose exec db pg_dump -U scouting scouting | gzip > backup.sql.gz
EOF
