# Challenger Scouting — Pro Edition

Internal scouting tool for League of Legends Challenger SoloQ analysis. Built for an esports organization's analyst team. Auth-gated, multi-region (EUW / KR / NA / EUNE / BR / etc.), runs on a Riot Personal API Key.

> **⚠ Internal use only** — this app is NOT meant to be published, indexed, or made accessible to the public. The CSS scoring engine, smurf flags, ranked leaderboards, and tournament data exposed here are appropriate for internal scouting only. Hosting it behind authentication on a private domain is a deliberate design choice to comply with Riot's Developer policies.

---

## Quick start

```bash
# 1. Install
cd backend
pip install -r requirements.txt
cp .env.example .env   # then edit RIOT_API_KEY, JWT_SECRET, FANDOM_USERNAME/PASSWORD

# 2. Migrate + seed admin
python scripts/migrate.py
python scripts/seed_admin.py admin <strong-password> admin <org-name>

# 3. Run
python -m uvicorn app.main:app --reload --port 8000
```

Open **<http://127.0.0.1:8000/>** → login → Admin tab → Run an ingestion to populate the DB.

For production deployment (Railway, Render, self-hosted VPS, Docker), see [DEPLOY.md](DEPLOY.md).

---

## Features

### 🪜 Ladder
Multi-region Challenger leaderboard sorted by **CSS** (Challenger Scouting Score, 0–100). Filters: role / region / tier / patch / min-games / pro status / smurf score / max age / residency / contract-end window.

- **Quick-filter pills**: 🎯 Free Agents · 🌟 Rising Stars · 👶 U21 · ⏳ Contract < 90d · ✕ Clear
- **🧬 Group accounts by pro** — collapse all of a pro's Riot accounts (main + smurfs + alt regions) into one line with a `+N accounts` badge. Click the badge → popover listing every sibling. Solves the n°1 noise source on the ladder.
- **Sticky right column** — never lose the View action when horizontal-scrolling.
- **Click anywhere on a row** to open the player profile.
- **Team chips clickable** — `G2`, `FNC`, `KC` etc. jump to the team page.

### 👤 Player profile (3 tabs)

**SoloQ tab**
- 📈 **CSS trend chart** — per-role evolution across patches, with auto headline like `↗ +12 CSS on MID (53 → 65)`
- 🔥 **Activity card** — current win/loss streak (`5W win streak 🔥`) + 7×24 heatmap of game times (UTC) showing grind schedule
- **CSS radar** — 8 categories, color-coded against role baseline
- **Aggregate stats** — full tooltip-explained breakdown
- **Pro identity card** (when matched) — Lolpros profile data, social handles, previous teams, peak rank
- **Champion pool** — top 20 with per-champion CSS vs same-champion baseline
- **vs Champion matchups** — winrate / GD@15 / KDA against opposing champions
- **Smurf signals** — multi-criterion alt-account detector with score breakdown
- **Recent matches** — clickable, opens deep-dive modal with timeline + gold curves
- **Scout notes** — private per-analyst markdown notes

**Tournament tab**
- Per-split stats from LEC + LCK + LCS + ERLs + international (KDA, KP, GD@15, CSD@15, CSPM)
- Tournament-only champion pool
- Recent tournament matches with deep-dive modal (team rosters, gold curves on demand)

**vs LEC `<role>` tab**
- Two split tables:
  - **Tournament stats**: prospect's SoloQ vs every LEC pro's official tournament stats
  - **SoloQ stats**: prospect vs the LEC pros whose Riot account is in our DB (X of N matched counter)
- Color-coded deltas (green = prospect outperforms, red = pro outperforms)

**Profile actions**
- 📋 **Markdown dossier export** — downloads a clean .md file (paste in Notion/Discord/Slack)
- 🖨 **PDF** — browser print-to-PDF
- ☆ Watch toggle, 👁 Smurf manual label

### 📊 Patch Impact
"Who profited (or got nerfed) on the new patch?" — single most-asked scout question after every meta rotation.

- Two patch dropdowns auto-populated from real ingest dates (handles `16.10` > `16.9` correctly via `MAX(match.game_creation)` per patch — no string-sort bug)
- Each option labeled with player count: e.g. `16.9 (27,055 players)`
- Filter by role + min-games-per-patch
- Sorted by Δ CSS desc, color-coded ±5 thresholds
- Click any row → player's profile (with their CSS trend in context)

### 📋 Watchlist (Recruitment Kanban)
Drag-and-drop pipeline with 6 stages:

```
👀 Watching → ✉ Contacted → 🎯 Trial → 📝 Offer → ✅ Signed | ✖ Pass
```

- HTML5 drag-and-drop with optimistic move + persistence
- Each card: name, role icon, tier+LP, CSS pill, free-text tag, "N days in stage" footer
- Click anywhere on a card → player profile
- ✕ remove with confirm
- Toggle 📋 Kanban / 📊 Table for power-users

### 🔗 Deep links + 🏟 Team scouting page
Every view has a permalink:

```
#/leaderboard
#/player/<puuid>      # → direct link to a player profile
#/team/G2             # → team scouting page
#/patch
#/watchlist  #/champions  #/compare  #/alerts  #/admin
```

**Team page** (e.g. `/#/team/G2`):
- Logo + name + league + last-10 record
- Active roster sorted by canonical role (TOP→JGL→MID→ADC→SUP), each member with image, role, country, age, latest tier+LP, primary CSS
- 10 most-recent tournament matches with opponents (logos resolved)
- Click roster → player profile · Click match → tournament match modal

### 🆚 Compare
Add up to 5 players, type-as-you-go search with chips. Side-by-side radars + delta highlights.

### 🏆 Champions
List + leaderboard view. Click a champion → modal showing the best players on that champion (Champ-CSS, win rate, KDA, GD@15).

### 🚨 Alerts
Rule-based alert engine. Webhook on watchlist trigger (CSS jump, peak rank, FA transition).

### ⚙ Admin
Trigger 5 sync pipelines — Riot SoloQ ingestion / Leaguepedia metadata / Lolpros bulk / lolesports tournaments / Score recompute. Job tracker with live status.

---

## CSS — Challenger Scouting Score

A 0–100 score per `(puuid, patch, role)` aggregate, role-weighted, sample-adjusted.

**Pipeline**

1. Compute averages of every metric: GD@15, XPD@15, CSD@15, CS/min, damage share, DPM, KP, KDA, vision/min, wards/min, solo kills, objective dmg, early deaths, deaths.
2. Z-score against the Challenger pool's distribution: `z = (x - μ_role) / σ_role`.
3. Convert to 0–100: `score = clip(50 + 15·z, 0, 100)`.
4. Aggregate into 8 categories: `lane`, `damage`, `vision`, `objective`, `mapplay`, `survival`, `champpool`, `consistency`.
5. Weight categories by role (SUP→30% vision, ADC→30% damage, etc.). See `services/scoring.py:ROLE_WEIGHTS`.
6. Adjustments:
   - **Sample factor** — less weight to <50 games
   - **Smurf factor** — ×0.7 if account level < 60
   - **Lobby-LP factor** (NEW) — ×0.90 → ×1.10 anchored at 700 LP. Players grinding 900+ LP rank-1 lobbies get an uplift; soft 400 LP off-hours grinders get a discount.
7. Compute percentile rank within `(patch, role)` cohort.

**UI labels** — 75+ Elite · 60-75 Strong · 45-60 Average · <45 Below avg

**Snapshots** — every aggregation pass appends to `CSSSnapshot`, powering the trend chart and the Patch Impact view. No env var to bump when patches change.

---

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11+ · FastAPI · SQLAlchemy 2 · SQLite (Postgres ready via `DATABASE_URL`) |
| Auth | bcrypt + PyJWT, OAuth2 password bearer |
| Riot API | `httpx` async + sliding-window rate limiter (20/s · 100/2min) + tenacity retry/backoff |
| Pro identification | Lolpros `/es/profiles/<slug>` for puuid-perfect cross-match · Leaguepedia (Cargo + wikitext) for metadata |
| Tournament data | Unofficial `esports-api.lolesports.com` + `livestats` window endpoints |
| Frontend | Vanilla JS SPA (6 modules, no build step) · Chart.js |
| Deploy | Vercel (frontend) · Cloudflare tunnel (backend) · Docker / Railway / Render configs included |

---

## Project layout

```
scouting/
├── backend/
│   ├── app/
│   │   ├── main.py                  FastAPI factory, mounts frontend at /
│   │   ├── auth.py                  bcrypt + JWT, get_current_user / require_admin
│   │   ├── config.py                Pydantic settings (env vars)
│   │   ├── db.py                    SQLAlchemy engine + WAL + 60s busy_timeout
│   │   ├── models.py                ORM (28 tables: Player, Match, PlayerMeta, OfficialMatch, …)
│   │   ├── routers/
│   │   │   ├── auth.py              /auth/login, /auth/me, /auth/users (admin)
│   │   │   ├── players.py           /players, /players/{puuid}, /patches, /patch-impact, /activity, /history, /dossier
│   │   │   ├── compare.py           /compare?puuid=...
│   │   │   ├── watchlist.py         /watchlist, /watchlist/{puuid}/stage (kanban), /notes/{puuid}
│   │   │   ├── champions.py         /champions, /champions/{id}
│   │   │   ├── tournaments.py       /players/{puuid}/tournaments, /roster-compare, /tournament-matches/*, /teams/{code}
│   │   │   ├── matches.py           /matches/{id}/timeline (SoloQ deep-dive)
│   │   │   ├── alerts.py            /alerts/* (rule engine)
│   │   │   ├── smurf.py             /smurf/* (manual labels)
│   │   │   └── admin.py             /admin/ingest, /admin/sync-*, /admin/recompute, /admin/stats, /admin/jobs
│   │   └── services/
│   │       ├── riot_client.py       Async Riot client + rate limiter
│   │       ├── ingestion.py         Match-v5 + timeline → MatchParticipant
│   │       ├── timeline_parser.py   Frame-by-frame → GD@15, solo kills, early deaths
│   │       ├── aggregation.py       MatchParticipant → PlayerAggregate (one per puuid×patch×role)
│   │       ├── scoring.py           CSS engine, role weights, lobby-LP factor
│   │       ├── alerts.py            Snapshot + delta detection + webhook fan-out
│   │       ├── rising_stars.py      Sustained CSS uptrend → is_rising_star tag
│   │       ├── smurf_ml.py          Multi-signal smurf scorer
│   │       ├── name_matching.py     SHARED canonical name normalizer (strict / loose)
│   │       ├── lolpros.py           Lolpros profile fetch + bulk crawl + puuid extraction
│   │       ├── leaguepedia/         3-file package
│   │       │   ├── __init__.py        re-exports
│   │       │   ├── sources.py         Cargo + wikitext + image lookup (~1240 lines)
│   │       │   └── sync.py            orchestration (~470 lines)
│   │       ├── lolesports_client.py Async client + rate-limit + retry for unofficial API
│   │       ├── tournament_ingestion.py  league → schedule → events → games → frame data
│   │       └── jobs.py              In-memory job tracker for long-running syncs
│   ├── scripts/
│   │   ├── migrate.py               Idempotent column adds (SQLite + Postgres)
│   │   ├── seed_admin.py            Create / reset the initial admin user
│   │   └── seed_demo.py             Synthetic Challenger data for UI demo
│   ├── data/                        SQLite DB (gitignored)
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html                   Login overlay + app shell + 9 view templates
│   ├── style.css
│   ├── js/                          (loaded as 6 ordered <script> tags, no build step)
│   │   ├── api.js                   auth + fetch wrapper (~50 lines)
│   │   ├── formatters.js            badges, role/tier icons, score helpers
│   │   ├── ui.js                    login, glossary, hash-router
│   │   ├── views.js                 leaderboard, watchlist+kanban, champions, compare, alerts, admin, patch-impact, team
│   │   ├── player.js                player profile + 3 tabs + all modals
│   │   └── boot.js                  entry point
│   ├── vercel.json
│   └── riot.txt                     Riot Developer domain verification
├── ops/
│   ├── README.md                    Cloudflare tunnel setup
│   └── cloudflare-tunnel.ps1
├── DEPLOY.md                        Railway / Render / VPS / Docker / Vercel hybrid
├── Dockerfile, docker-compose.yml, railway.json, render.yaml, Procfile
└── README.md (this file)
```

---

## Data sources

| Source | Used for | Auth |
|---|---|---|
| Riot **match-v5** | All SoloQ matches + timeline | Personal API Key |
| Riot **league-v4** | Challenger / GM / Master ladders + per-account rank | Personal API Key |
| Riot **summoner-v4** + **account-v1** | Resolve summoner names ↔ puuid ↔ Riot ID | Personal API Key |
| **Lolpros** `/es/profiles/<slug>` | Pro identification (gives `encrypted_puuid` per account → perfect Riot match) + social handles + previous teams + peak rank | Anonymous |
| **Leaguepedia** Cargo (`Special:CargoExport`) | Pro metadata: birthdate, country, residency, role, current team, contract end | Optional Fandom bot account |
| **Leaguepedia** wikitext (`action=query`) | Wiki infobox parse (when Cargo schema doesn't carry a field) | Optional Fandom bot account |
| **lolesports** unofficial API | Tournament schedule, match metadata, frame data (gold/CS/objectives @10/@15) | None — `x-api-key` header rotates occasionally |

**Pro identification pipeline** — 4 passes:

1. **Wikitext infobox batch** — pulls `birthdate / country / role / team / socials` for every Lolpros-matched name (~470 names in 73 s authenticated)
2. **Cargo backfill** — `Special:CargoExport` for fields the wikitext infobox doesn't carry (NativeName, full ISO Birthdate)
3. **Cargo global bulk** — `IsRetired=0 AND Residency IN (EMEA, Korea, North America, Brazil, Asia Pacific)` — ~9000 active pros worldwide, region-aware matching prevents false cross-region collisions (`Hide on Bush#KR1` ≠ sOAZ's "Baguette on bush")
4. **Lolpros profile crawl** (optional, slow) — `~5000 fetches at concurrency 8` → unlocks puuid-perfect matching for every active account on every pro

After the 4 passes, ~1,470 pros are tagged in the DB with their Riot account(s).

---

## API reference

All endpoints require `Authorization: Bearer <token>` except `/auth/login` and `/api/health`.

### Auth
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/login` | none | Get JWT (form-encoded `username`+`password`) |
| GET | `/auth/me` | user | Current user info |
| POST | `/auth/users` | admin | Create user |

### Players
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/players` | user | Leaderboard (CSS-sorted by default) — many filters |
| GET | `/players/{puuid}` | user | Full profile + breakdown |
| GET | `/players/search?name=` | user | Fuzzy search (also accepts `?q=`) |
| GET | `/players/{puuid}/history` | user | CSS snapshots per role per patch |
| GET | `/players/{puuid}/activity` | user | Current streak + 7×24 game heatmap |
| GET | `/players/{puuid}/matchups` | user | vs-champion winrate / GD@15 / KDA |
| GET | `/players/{puuid}/dossier` | user | Markdown scouting report (download) |
| GET | `/players/{puuid}/tournaments` | user | Per-split tournament stats |
| GET | `/players/{puuid}/roster-compare` | user | Side-by-side vs current LEC roster |
| GET | `/players/patches` | user | Patches in DB ordered by latest activity |
| GET | `/players/patch-impact?patch_to=&patch_from=` | user | Per-player CSS delta between two patches |

### Watchlist + notes (kanban)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/watchlist` | user | List watched players (with stage) |
| POST | `/watchlist` | user | Add/update entry (form `puuid`+`tag`) |
| PATCH | `/watchlist/{puuid}/stage` | user | Move to stage (`watch`/`contacted`/`trial`/`offer`/`signed`/`rejected`) |
| DELETE | `/watchlist/{puuid}` | user | Remove |
| GET/POST/DELETE | `/notes/{puuid}` | user | Scout notes CRUD |

### Tournaments + teams
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/tournament-matches/{id}` | user | Full match: rosters, stats, summary |
| GET | `/tournament-matches/{id}/timeline` | user | Gold curve via lolesports window walk (cached 30 min) |
| GET | `/teams/{code}` | user | Team page: roster, recent matches, record |

### Champions / compare / alerts
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/champions` | user | Champions with meta-stats |
| GET | `/champions/{id}` | user | Best players on this champion |
| GET | `/compare?puuid=&puuid=` | user | Side-by-side comparison |
| GET/POST/PATCH/DELETE | `/alerts/rules` | user | Alert engine CRUD |

### Admin
| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/ingest` | Start SoloQ ingestion job |
| POST | `/admin/sync-leaguepedia` | Quick sync (~75 s): wikitext + Cargo backfill + EMEA bulk |
| POST | `/admin/sync-leaguepedia-full` | Full sync (~6 min): + per-pro Lolpros profile crawl |
| POST | `/admin/sync-lolpros` | Lolpros bulk (preferred over Leaguepedia for puuid-matching) |
| POST | `/admin/sync-tournaments` | Pull LEC + KR + NA + ERLs + Intl via lolesports |
| POST | `/admin/recompute` | Recompute aggregates + lobby LP + CSS |
| GET | `/admin/jobs/{id}` | Job status (live progress) |
| GET | `/admin/stats` | DB row counts (SoloQ + Leaguepedia + Tournaments) |

---

## Tournament data — important caveats

Tournament integration uses the **unofficial lolesports.com API** (the same one the lolesports website uses). It is NOT documented or supported by Riot.

- **Internal use only** — do not redistribute. Riot can enforce TOS at any time.
- **Schema drift** — the `x-api-key` header and endpoint shapes can rotate without notice. If sync starts failing, check `services/lolesports_client.py`.
- **Frame data is sparse** — `/livestats/v1/window/` returns 10-frame chunks (100 s of game time). Game duration is estimated from broadcast timestamps and the last frame, with bounds [15 min, 80 min]. CSPM is computed per-game and games where duration estimation fails are excluded from the average.
- **Leagues covered** — LEC + LCK + LCS + EU ERLs (LFL, Prime League, Superliga, NLC, Hitpoint, EBL, Ultraliga, Elite Series, …) + international (MSI, Worlds, First Stand). KR/NA tournaments included.
- **Match-rate vs grid.gg** — ~70% of GRID's value for macro scouting (gold/KDA/KP/GD@15/objective control). Does NOT include per-tick positions, ability casts, or precise damage events. For micro analysis (ult timing, ward placement, positioning), GRID/Bayes feeds remain the only path.

---

## Recent changelog (last 30 days)

| Feature | Status |
|---|---|
| 🧬 Account grouping by pro (collapse smurfs/alts) | Shipped |
| 📋 Recruitment kanban (6-stage drag-drop) | Shipped |
| 🔗 Deep links (`#/player/<puuid>`) + team page (`#/team/G2`) | Shipped |
| 📊 Patch impact view + auto patch detection | Shipped |
| 🔥 Streak badge + 7×24 activity heatmap | Shipped |
| 📋 Markdown dossier export | Shipped |
| 📈 CSS trend chart (per-role across patches, headline delta) | Shipped |
| 🎯 Free Agents + 4 quick-filter pills | Shipped |
| LEC roster compare split into Tournament + SoloQ tables | Shipped |
| Tournament data: KR + NA + International leagues | Shipped |
| Lolpros 4-pass pro identification (~1,470 pros tagged) | Shipped |
| Region-aware pro matching (kills `Hide on Bush` ↔ sOAZ collision) | Shipped |
| Lobby-LP weighting in CSS | Shipped |
| Match-modal: index caching → 100× warm-cache speedup | Shipped |
| Backend refactor: 1762-line `leaguepedia.py` → 3-file package | Shipped |
| Frontend refactor: 2514-line `app.js` → 6 modules | Shipped |

---

## Roadmap

### Next up — high leverage, scoped

- **Tests + CI** — pytest on hot endpoints (`/players`, `/tournament-matches/{id}`, `/players/patch-impact`, `name_matching`) + GitHub Actions workflow. Mandatory filet de sécurité before further refactors.
- **"Similar players" finder** — k-NN on CSS subcomponents. Click on Caps → top 10 statistically-closest Challengers. Killer feature for finding doubures.
- **Webhook on watchlist trigger** — POST to Discord webhook when a watched player hits a new peak rank, jumps > 10 CSS, or transitions to FA. Transforms the tool from dashboard → proactive assistant.
- **Champion-meta scout** — Filter "best ADCs" by current S-tier picks. Show players whose champion pool overlaps the current meta.
- **One-off pro fetch endpoint** — `POST /admin/fetch-pro?slug=way` to fill data gaps on demand (e.g. TH Way still missing from DB).

### Bigger features (1-2 weeks each)

- **Smurf detector ML model** — Currently rule-based + manual labels. Train a gradient-boost classifier on the ~1k existing manual labels. Continuous score 0–100 instead of boolean flag.
- **Live game lookup** — Spectator-v5 integration: "🔴 In game" button on profile → see if player is currently in a game, with who (duo partner detection), on what champion.
- **Coach annotation workflow** — Time-stamped notes on specific moments of specific matches ("good early roam @ 6:30 vs X"), shareable via VOD timestamp links.
- **Synergy / duo analysis** — For each player, CSS solo vs CSS when duo'd with X. Reveals who carries vs who gets carried.
- **Mobile-responsive UI** — Most staff scout on phone too. Currently desktop-only.

### V2.0 — research-level

- Replay/timeline-level positioning analysis (would require GRID/Bayes feeds)
- Auto-cluster playstyles (carry MID vs control MID vs assassin MID) via stat-vector embeddings
- "Pro potential" classifier on Challenger players, trained on historical "made it to LEC" labels

### Technical debt to address along the way

- **Postgres migration plan** — SQLite at 196 MB now, comfortable. At ~500 MB, joins start to feel slow. `docker-compose.yml` already configures Postgres — model is portable, just needs a real migration when the time comes.
- **`player.js` at 1284 lines** — splittable into `profile.js + tournament.js + modals.js + smurf-matchup.js` if it grows further.
- **No history on `PlayerMeta`** — if a Lolpros sync mis-tags a pro and overwrites their data, the previous value is lost. Add a `PlayerMetaHistory` audit log eventually.

---

## Security notes

- **Personal API Key only** — do not submit this app for public Riot approval. Personal Keys allow internal use without policy review.
- **No public hosting** — keep this behind VPN, IP allowlist, Cloudflare Access, or local network only.
- **Strong `JWT_SECRET`** — 32+ random chars. Generate with `openssl rand -hex 32`. The dev default is intentionally insecure.
- **Bcrypt with 72-byte truncation** — passwords longer than 72 bytes are silently truncated (bcrypt limitation).
- **No public signup** — users must be created by an admin via `POST /auth/users`.
- **Lolesports rate-limit safety** — internal bulk crawls cap concurrency at 8 with polite 0.3 s pauses between chunks. Still: don't expose any tournament endpoint to traffic outside your scout team.

---

## Local development

### Initial setup
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# Edit .env:
#   RIOT_API_KEY=RGAPI-...
#   PLATFORM=euw1
#   REGION=europe
#   JWT_SECRET=<openssl rand -hex 32>
#   FANDOM_USERNAME=YourFandomName@bot-label  # optional, for higher batch limits
#   FANDOM_PASSWORD=<bot password>
python scripts/migrate.py
python scripts/seed_admin.py admin <password> admin g2
python -m uvicorn app.main:app --reload --port 8000
```

### Adding analysts
```bash
TOKEN=$(curl -s -X POST -d "username=admin&password=<pwd>" \
  http://127.0.0.1:8000/auth/login | jq -r .access_token)
curl -X POST http://127.0.0.1:8000/auth/users \
  -H "Authorization: Bearer $TOKEN" \
  -d "username=analyst1&password=secret&role=analyst&org=g2"
```

### Adding a new column to a model
1. Edit the model in `backend/app/models.py`
2. Add the `(table, column, ddl_type)` tuple to `backend/scripts/migrate.py:NEW_COLUMNS`
3. Run `python scripts/migrate.py` (idempotent — re-run safe)
4. Restart uvicorn

### Recompute scores after a code change
Login as admin → Admin tab → "Recompute" button. Or:
```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/admin/recompute
```

### Frontend dev
No build step. Edit `frontend/js/*.js` and `frontend/style.css` directly, then refresh. The 6 JS modules load as ordered `<script>` tags in `index.html`:

```
api.js → formatters.js → ui.js → views.js → player.js → boot.js
```

Each adds to the global scope, so cross-file calls (and inline `onclick="..."`) just work without import/export.

---

## Deployment

See [DEPLOY.md](DEPLOY.md) for full instructions on:
- **Option A — Railway** (recommended for MVP)
- **Option B — Render** (free tier with auto-sleep)
- **Option C — Self-hosted VPS** (Hetzner / Scaleway / Digital Ocean, ~3-5€/mo)
- **Option D — Hybrid Vercel + Railway/Render** (frontend on Vercel, backend persistent)

The current deploy uses **Vercel for the frontend** (instant on-push) and **Cloudflare tunnel** for the backend (see `ops/cloudflare-tunnel.ps1`).

---

## License

Proprietary — internal use only. Contact the owner for redistribution rights.
