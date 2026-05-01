# Deployment guide

This app is a stateful Python (FastAPI) backend with SQLite or Postgres, plus
a static frontend served from the same origin. Long-running ingestion jobs
mean **serverless platforms (Vercel, Cloudflare Workers, AWS Lambda) won't
work**. Pick one of the options below.

## Option A — Railway (recommended for MVP)

Why: cheapest path that "just works". Persistent container, managed Postgres,
git push to deploy. Free trial covers ~30 days, then ~$5/mo.

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login + init
railway login
railway init

# Add Postgres
railway add --plugin postgresql

# Set secrets
railway variables --set RIOT_API_KEY=RGAPI-xxxx \
                  --set JWT_SECRET="$(openssl rand -hex 32)" \
                  --set PLATFORM=euw1 \
                  --set REGION=europe \
                  --set FANDOM_USERNAME=YourFandomName@bot-label \
                  --set FANDOM_PASSWORD=xxxx

# Deploy
railway up

# Open the live URL
railway open
```

After first deploy, SSH in and create the admin user:
```bash
railway run python seed_admin.py admin <strong-password> admin g2
```

## Option B — Fly.io

Why: Paris region (low latency vs EUW), 3 free VMs on hobby plan, global
deploy with persistent volumes.

```bash
# Install Fly CLI
# macOS: brew install flyctl
# Windows: iwr https://fly.io/install.ps1 -useb | iex

fly auth login
fly launch --no-deploy            # detects fly.toml
fly volumes create scouting_data --size 1 --region cdg
fly secrets set \
  RIOT_API_KEY=RGAPI-xxxx \
  JWT_SECRET="$(openssl rand -hex 32)" \
  FANDOM_USERNAME=YourFandomName@bot-label \
  FANDOM_PASSWORD=xxxx

# Either keep SQLite on the volume, or attach Fly Postgres:
fly postgres create --name scouting-db --region cdg
fly postgres attach --app challenger-scouting scouting-db
# (this auto-sets DATABASE_URL secret)

fly deploy
fly ssh console -C "python seed_admin.py admin <strong-password> admin g2"
```

## Option C — Render

Why: free tier (with sleep after 15 min of inactivity, fine for an internal
tool), git push deploy, Postgres built-in.

1. Push the repo to GitHub
2. Go to <https://render.com>, click **New → Blueprint**
3. Point at your repo. Render reads `render.yaml` and provisions:
   - Web service (Docker)
   - Postgres database
4. Fill in `RIOT_API_KEY`, `FANDOM_USERNAME`, `FANDOM_PASSWORD` in the prompt
5. After deploy, open Shell tab → `python seed_admin.py admin <pwd> admin g2`

## Option D — Self-hosted VPS (Hetzner / Scaleway / Digital Ocean)

Why: cheapest for long-term scaling. ~3-5€/mo, full control, no sleep.

```bash
# On a fresh Ubuntu 24.04 box:
curl -fsSL https://get.docker.com | sh
git clone <your-repo-url> /opt/scouting
cd /opt/scouting

cp backend/.env.example backend/.env
# Edit backend/.env: set RIOT_API_KEY, JWT_SECRET, FANDOM_USERNAME, FANDOM_PASSWORD

docker compose up -d
docker compose exec app python seed_admin.py admin <pwd> admin g2

# Optional: Caddy reverse proxy with auto-HTTPS
# (one-line install: sudo apt install caddy, then add a Caddyfile)
```

## Option E — Vercel (NOT recommended — explained)

Vercel runs only **stateless serverless functions** (max 60 s execution time
on Pro). This app has:

- 5-15 minute ingestion jobs (Riot SoloQ + Leaguepedia + lolesports)
- In-memory job tracking dict (`_jobs` in `routers/admin.py`)
- SQLite or pooled Postgres connections (no per-invocation cold-start tolerance)

If you must use Vercel:
- **Frontend only** on Vercel (push `frontend/` as a static deploy with
  `vercel --prod`)
- **Backend on Railway/Fly/Render** (one of the options above)
- Set `API_BASE_URL` in `frontend/app.js` to point at the backend URL
- Configure CORS in `backend/app/main.py` to whitelist the Vercel domain

This split adds complexity for no real benefit — the frontend is 3 vanilla-JS
files, you save no build time. **Keep them together** on one of options A-D.

## Environment variables (production)

| Variable | Required | Description |
|---|---|---|
| `RIOT_API_KEY` | yes | From <https://developer.riotgames.com> |
| `JWT_SECRET` | yes | Random ≥32 char string. Use `openssl rand -hex 32`. |
| `DATABASE_URL` | recommended | Postgres URL. If unset, falls back to SQLite at `backend/data/scouting.db` |
| `PLATFORM` | no | Default `euw1` |
| `REGION` | no | Default `europe` |
| `JWT_EXPIRY_HOURS` | no | Default 72 |
| `MIN_GAMES` | no | Default 20 |
| `MATCH_HISTORY_COUNT` | no | Default 30 |
| `CURRENT_PATCH` | no | Default `14.9` (informational) |
| `FANDOM_USERNAME` | recommended | lol.fandom.com bot username (`YourName@bot-label`) — get one at <https://lol.fandom.com/wiki/Special:BotPasswords> |
| `FANDOM_PASSWORD` | recommended | The bot password generated alongside the username |
| `LP_USERNAME` / `LP_PASSWORD` | legacy | Old aliases, still accepted as fallback |

## Post-deploy checklist

- [ ] Hit `https://your-domain/api/health` → `{"status":"ok"}`
- [ ] Create the first admin user (`seed_admin.py`)
- [ ] Login at `https://your-domain/`, change `JWT_SECRET` if exposed
- [ ] Run **Sync Leaguepedia** (Admin tab) — populates pro metadata
- [ ] Run **Sync tournaments** — populates LEC + ERLs
- [ ] Run **Run ingestion** with 20-50 players — populates Challenger SoloQ
- [ ] Check `/admin/stats` — non-zero counts everywhere

## Backups

For SQLite: snapshot `backend/data/scouting.db` periodically (Fly volume
snapshot, or rsync to S3).
For Postgres: managed services handle this; otherwise schedule a
`pg_dump | gzip > s3://...` cron.
