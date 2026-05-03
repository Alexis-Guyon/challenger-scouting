# Challenger Scouting — Pro Edition

Internal scouting tool for League of Legends Challenger SoloQ analysis. **Not a public application** — designed for an esports organization's analyst team. Auth-gated, single-region (EUW), runs on a Personal API Key without going through Riot's public app approval process.

> **Important** — this app is NOT meant to be published, indexed, or made accessible to the wider community. Hosting it behind authentication on a private domain is a deliberate design choice to comply with Riot's Developer policies (which restrict custom ranking systems and player evaluations on public apps). The CSS scoring engine, smurf flags, and ranked leaderboards exposed here are appropriate for internal scouting only.

## Features

- **Ladder view** — Challenger SoloQ players sorted by **CSS (Challenger Scouting Score)**, filters: role / patch / min-games / pro status / max age / residency / contract-end window.
- **Player profile** with three tabs:
  - **SoloQ**: radar chart per category, aggregate stats (GD/XPD/CSD@15, dmg share, KP, vision, …), champion pool, recent matches.
  - **Tournament**: per-split stats from LEC + EU ERLs (KDA, KP, GD@15, CSD@15, CSPM), tournament champion pool, recent official matches.
  - **vs LEC `<role>`**: side-by-side comparison of the prospect's SoloQ stats against every current LEC player at that role — both their tournament stats and SoloQ stats (when matched). Color-coded deltas (green = prospect wins, red = pro wins).
- **Watchlist** — star players for follow-up, attach a free-text tag (e.g. "ADC FA target", "U21").
- **Scout notes** — private per-analyst notes attached to each player.
- **Compare** — side-by-side radars and stats for up to 5 players.
- **Admin** — trigger 4 sync pipelines (Riot SoloQ ingestion / Leaguepedia metadata / lolesports tournaments / score recompute), system stats. Admin-only.
- **Auth** — JWT + bcrypt, two roles (admin / analyst). No public signup; admins create users.

## CSS hardening

The Challenger Scouting Score now applies two additional adjustments beyond sample-size and smurf flags:

- **Lobby-LP weighting**: each match's `avg_lobby_lp` (mean LP of all 10 participants) anchors the scoring. Players in higher-LP lobbies (e.g. 900+ LP rank-1 lobbies) get a small uplift (×1.0 → ×1.10 cap). Players who farm soft 400 LP off-hour lobbies get a discount (down to ×0.90). Anchored at 700 LP.
- **Same-patch strict** (opt-in via `current_patch_only=True` in `aggregate_player`): only count games on the most-played patch in the DB. Useful for filtering out meta-shift bias.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11+ / FastAPI / SQLAlchemy / SQLite |
| Auth | bcrypt + PyJWT, OAuth2 password bearer |
| Riot API | `httpx` async client + sliding-window rate limiter (20/s · 100/2min) + retry/backoff |
| Frontend | Vanilla JS SPA + Chart.js |

## Deployment

For production deployment (Railway, Render, self-hosted VPS, Docker), see [DEPLOY.md](DEPLOY.md).

## Local setup

### 1. Install
```bash
cd backend
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env
# Edit .env:
#   RIOT_API_KEY=RGAPI-...    (get one at https://developer.riotgames.com)
#   PLATFORM=euw1
#   REGION=europe
#   JWT_SECRET=<long random string>
```

### 3. Create the first admin user
```bash
cd backend
python scripts/seed_admin.py admin <password> admin <org-name>
```

### 4. Run the server
```bash
python -m uvicorn app.main:app --reload --port 8000
```

Open **http://127.0.0.1:8000/** → login screen.

### 5. Trigger first ingestion
Login → Admin tab → "Run ingestion" with e.g. 20 players × 20 matches. Wait for job to finish (4 phases: ingest → aggregate → distributions → scoring).

### 6. Add analysts (optional)
Login as admin, then via the API:
```bash
curl -X POST http://127.0.0.1:8000/auth/users \
  -H "Authorization: Bearer <admin-token>" \
  -d "username=analyst1&password=secret&role=analyst&org=g2"
```

## Project layout

```
scouting/
├── backend/
│   ├── app/
│   │   ├── main.py              FastAPI factory, mounts frontend at /
│   │   ├── auth.py              bcrypt + JWT, get_current_user / require_admin deps
│   │   ├── config.py            Pydantic settings (env vars)
│   │   ├── db.py                SQLAlchemy engine + session
│   │   ├── models.py            ORM: Player, Match, …, User, WatchlistEntry, ScoutNote
│   │   ├── routers/
│   │   │   ├── auth.py          /auth/login, /auth/me, /auth/users (admin)
│   │   │   ├── players.py       /players (CSS-sorted leaderboard), /players/{puuid}
│   │   │   ├── compare.py       /compare?puuid=...
│   │   │   ├── watchlist.py     /watchlist, /notes/{puuid}
│   │   │   └── admin.py         /admin/ingest, /admin/recompute, /admin/stats
│   │   └── services/
│   │       ├── riot_client.py   Async Riot client + rate limiter
│   │       ├── timeline_parser.py
│   │       ├── ingestion.py
│   │       ├── aggregation.py
│   │       └── scoring.py       CSS engine, role weights
│   ├── scripts/
│   │   ├── migrate.py           Idempotent schema migrations (SQLite + Postgres)
│   │   ├── seed_admin.py        Create / reset the initial admin user
│   │   └── seed_demo.py         Generate synthetic Challenger data for UI demo
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html               Login overlay + app shell
│   ├── style.css
│   └── app.js                   SPA: auth, leaderboard, watchlist, profile, notes
└── README.md
```

## How CSS works

For each `(puuid, patch, role)` aggregate:

1. Compute averages of all metrics (GD@15, XPD@15, CSD@15, CS/min, damage share, DPM, KP, KDA, vision/min, wards/min, solo kills, …).
2. Z-score against the Challenger pool's distribution: `z = (x - μ_role) / σ_role`.
3. Convert to 0-100: `score = clip(50 + 15·z, 0, 100)`.
4. Aggregate into 8 categories (lane, damage, vision, objective, mapplay, survival, champpool, consistency).
5. Weight categories by role (SUP→30% vision, ADC→30% damage, etc.). See `services/scoring.py:ROLE_WEIGHTS`.
6. Adjustments: sample factor (less weight to <50 games), smurf factor (×0.7 if account level <60).
7. Compute percentile rank within `(patch, role)` cohort.

UI labels:
- 75+ Elite · 60-75 Strong · 45-60 Average · <45 Below avg

## Security notes

- **Personal API Key only** — do not submit this app for public Riot approval. Personal Keys allow internal use without policy review.
- **No public hosting** — keep this behind VPN, IP allowlist, or local network only.
- **Strong JWT_SECRET** — 32+ random chars. The dev default is intentionally insecure.
- **Bcrypt with 72-byte truncation** — passwords longer than 72 bytes are silently truncated (bcrypt limitation).
- **No public signup** — users must be created by an admin via `POST /auth/users`.

## API endpoints

All require `Authorization: Bearer <token>` except `/auth/login` and `/api/health`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/login` | none | Get JWT |
| GET | `/auth/me` | user | Current user info |
| POST | `/auth/users` | admin | Create user |
| GET | `/players` | user | Leaderboard (CSS-sorted by default) |
| GET | `/players/{puuid}` | user | Full profile + breakdown |
| GET | `/players/search?name=...` | user | Fuzzy search |
| GET | `/compare?puuid=...` | user | Side-by-side comparison |
| GET | `/watchlist` | user | List watched players |
| POST | `/watchlist` | user | Add/update watchlist entry |
| DELETE | `/watchlist/{puuid}` | user | Remove |
| GET | `/notes/{puuid}` | user | List notes for a player |
| POST | `/notes/{puuid}` | user | Add note |
| DELETE | `/notes/{note_id}` | user | Delete note |
| GET | `/players/{puuid}/tournaments` | user | Per-player tournament stats (split-by-split) + tournament champ pool |
| GET | `/players/{puuid}/roster-compare` | user | Side-by-side vs current LEC roster at the prospect's role |
| POST | `/admin/ingest` | admin | Start SoloQ ingestion job |
| POST | `/admin/sync-leaguepedia` | admin | Pull EU pro metadata (FA, age, country, contract) |
| POST | `/admin/sync-tournaments` | admin | Pull LEC + ERLs tournament data via lolesports |
| GET | `/admin/jobs/{id}` | admin | Job status |
| POST | `/admin/recompute` | admin | Recompute aggregates + lobby LP + CSS |
| GET | `/admin/stats` | admin | DB row counts (SoloQ + Leaguepedia + Tournaments) |

## Tournament data — important caveats

Tournament integration uses the **unofficial lolesports.com API** (the same one the lolesports website uses). It is NOT documented or supported by Riot. Implications:

- **Internal use only** — do not redistribute or expose this data publicly. Riot can enforce TOS at any time.
- **Schema drift** — the `x-api-key` header and endpoint shapes can rotate without notice. If sync starts failing, check `services/lolesports_client.py` for endpoint URL changes.
- **Frame data is sparse** — the `/livestats/v1/window/` endpoint returns 10-frame chunks (100 s of game time). Game duration is estimated from broadcast timestamps and the last available frame, with bounds [15 min, 80 min]. CSPM is computed per-game and games where duration estimation fails are excluded from the average.
- **Leagues covered**: LEC + EU ERLs (LFL, Prime League, Superliga, NLC, Hitpoint, EBL, Ultraliga, Elite Series, Esports Balkan League, LPLOL CIS, TCL, NLOC). KR/NA/CN intentionally not in scope.
- **Match-rate vs grid.gg** — this gives ~70% of GRID's value for macro-level scouting (gold/KDA/KP/GD@15/objective control). It does NOT include per-tick positions, ability casts, or precise damage events. For micro analysis (ult timing, ward placement, positioning), GRID/Bayes feeds remain the only path.

## Roadmap

- **V1.4** — Walk full game timeline (multiple windows per game) for accurate duration + per-minute graphs
- **V1.5** — PDF scout report export (1-pager per prospect)
- **V1.6** — Champion-specific CSS, matchup-adjusted GD@15
- **V2.0** — Smurf detector ML model, rising-star alerts on Slack/Discord, pro-potential classifier
