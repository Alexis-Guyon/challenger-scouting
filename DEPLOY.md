# Deployment guide

This app is a stateful Python (FastAPI) backend with SQLite or Postgres, plus
a static frontend served from the same origin.

The backend can NOT run on serverless platforms (Vercel, Cloudflare Workers,
AWS Lambda) because of the 60 s execution cap and the in-memory job tracker.
Pick one of the persistent-container options A–D below for the backend. If
you specifically want Vercel for the frontend only, see option E.

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
railway run python scripts/seed_admin.py admin <strong-password> admin g2
```

## Option B — Render

Why: free tier (with sleep after 15 min of inactivity, fine for an internal
tool), git push deploy, Postgres built-in.

1. Push the repo to GitHub
2. Go to <https://render.com>, click **New → Blueprint**
3. Point at your repo. Render reads `render.yaml` and provisions:
   - Web service (Docker)
   - Postgres database
4. Fill in `RIOT_API_KEY`, `FANDOM_USERNAME`, `FANDOM_PASSWORD` in the prompt
5. After deploy, open Shell tab → `python scripts/seed_admin.py admin <pwd> admin g2`

## Option C — Self-hosted VPS (Hetzner / Scaleway / Digital Ocean)

Why: cheapest for long-term scaling. ~3-5€/mo, full control, no sleep.

```bash
# On a fresh Ubuntu 24.04 box:
curl -fsSL https://get.docker.com | sh
git clone <your-repo-url> /opt/scouting
cd /opt/scouting

cp backend/.env.example backend/.env
# Edit backend/.env: set RIOT_API_KEY, JWT_SECRET, FANDOM_USERNAME, FANDOM_PASSWORD

docker compose up -d
docker compose exec app python scripts/seed_admin.py admin <pwd> admin g2

# Optional: Caddy reverse proxy with auto-HTTPS
# (one-line install: sudo apt install caddy, then add a Caddyfile)
```

## Option D — Hybrid: Vercel (frontend) + Railway/Render (backend)

Vercel can host the frontend as a static site — but **NOT the backend**
(serverless functions cap at 60 s, our ingestion jobs run 5-15 min and the
in-memory `_jobs` tracker requires a persistent process).

So the recipe is: deploy the backend to Railway/Render (options A or B
above) and put **only the frontend** on Vercel pointing at it.

### 1. Deploy the backend somewhere persistent
Pick option A, B or C and complete those steps. Note the public URL of
your backend, e.g. `https://challenger-scouting.up.railway.app`.

### 2. Tell the frontend where the backend lives
Edit `frontend/index.html` — there's a small inline script in `<head>`:
```html
<script>
  window.SCOUTING_API_BASE = "https://challenger-scouting.fly.dev";
</script>
```
Replace the URL with your actual backend host (no trailing slash). When this
is empty, the frontend assumes same-origin (the bundled FastAPI deploy).

### 3. Deploy the frontend to Vercel
```bash
# Install once
npm i -g vercel

# From the project root
cd frontend
vercel               # first run: pick "yes" to link, default settings
vercel --prod        # deploy to a public URL
```
Vercel auto-detects the static files. The `frontend/vercel.json` already adds
sane caching headers and security headers.

### 4. Allow the Vercel origin in backend CORS
Already handled — `backend/app/main.py` has an
`allow_origin_regex` that accepts any `*.vercel.app` host plus localhost.
If you use a custom domain, extend the regex.

### 5. Verify
Open your `*.vercel.app` URL → login screen. The browser will fetch
`<backend-host>/auth/login` and JWT-authenticate against the persistent
backend. All other endpoints (players, watchlist, admin) follow the same
pattern automatically.

> Heads-up: with a Bearer-token auth model + permissive CORS, this is fine
> for an internal scouting tool. If you ever switch to cookie auth, lock the
> CORS regex down to your specific Vercel hostnames.

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

For SQLite: snapshot `backend/data/scouting.db` periodically (Railway volume
snapshot, or rsync to S3).
For Postgres: managed services handle this; otherwise schedule a
`pg_dump | gzip > s3://...` cron.
