#!/usr/bin/env bash
#
# update.sh — Deploy the latest code WITHOUT touching data or secrets.
#
# Pulls the newest commit, rebuilds only the changed Docker layers, and
# restarts the app. The Postgres volume and the .env file are left untouched;
# database migrations run automatically on container start.
#
# Usage (on the server):
#   bash /opt/scouting/ops/update.sh
#
set -euo pipefail

APP_DIR="/opt/scouting"
cd "${APP_DIR}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "Pulling latest code"
git pull --ff-only

say "Rebuilding + restarting (DB volume + .env preserved)"
docker compose up -d --build

say "Pruning dangling images"
docker image prune -f >/dev/null 2>&1 || true

say "Status"
docker compose ps

printf '\n\033[1;32mUpdated.\033[0m  Live logs: docker compose logs -f app\n'
