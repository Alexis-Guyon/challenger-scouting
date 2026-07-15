# Batch 1 — Content reference (extracted from the live code)

Ground the mockups on these EXACT fields. Source of truth: `frontend/index.html`,
`frontend/js/views.js`, `frontend/js/player.js`, `frontend/js/formatters.js`.

---

## Shared: score pill tiers (formatters.js)

CSS score → class + label (used everywhere a 0–100 score appears):

| Score | Class | Label |
|---|---|---|
| ≥ 75 | `s-elite` | Elite |
| 60–74 | `s-strong` | Strong |
| 45–59 | `s-avg` | Average |
| < 45 | `s-weak` | Below avg |

Role tokens (abbreviated, UPPERCASE): **TOP · JGL · MID · ADC · SUP**
Region tokens: **EUW · KR · NA · EUNE · BR · JP · OCE · LAN · LAS · TR · RU**
Tier tokens: **Challenger · Grandmaster · Master** (Chall/GM/Master have SVG icons; lower tiers = text badge)

---

## VIEW 1 — Ladder (`#/leaderboard`)

### Filter bar (exact controls, in order)
1. **Role** — All / TOP / JGL / MID / ADC / SUP
2. **Region** — All / EUW*(default)* / KR / NA / EUNE / BR / JP / OCE / LAN / LAS / TR / RU
3. **Tier** — All / Challenger / Grandmaster / Master
4. **Patch** — free text input, placeholder `e.g. 16.9`
5. **Min games** — number input, default `3`
6. **Sort** — CSS Score *(default)* / 🚨 Smurf score / LP / Winrate / Games / Age (asc)
7. **Pro status** — All / Pros only / Free agents / Amateurs only
8. **Smurfs** — Show all / Hide likely smurfs / Smurfs only / Clean accounts only
9. **Max age** — number input, placeholder `e.g. 21`
10. **Residency** — Any / Europe / Korea / North America
11. **Contract ends within** — — / 30 days / 90 days / 180 days / 1 year
12. **[Apply]** button

### Quick-filter pills (below the filter bar)
`🎯 Free Agents` · `🌟 Rising Stars` · `👶 U21` · `⏳ Contract < 90d` · `✕ Clear`
— right-aligned: checkbox `🧬 Group accounts by pro`

### Table columns (17, exact order + header tooltip)
| # | Header | Content |
|---|---|---|
| 1 | `#` | Rank (offset + row index) |
| 2 | `Summoner` | **Bold Riot ID** + smurf badge + rising badge + `+N accounts` badge |
| 3 | `Reg` | region pill |
| 4 | `Pro` | pill: `PRO` (amber) / `FA` (green) / `Retired` (gray) / `—` |
| 5 | `Team` | team logo + name, or *Free Agent*, or `—` (team chip links to team page) |
| 6 | `Age` | number or `—` |
| 7 | `Tier` | tier icon (Chall/GM/Master) or text badge |
| 8 | `LP` | number or `—` |
| 9 | `Role` | role icon |
| 10 | `Patch` | e.g. `16.14` |
| 11 | `Games` | number |
| 12 | `WR` | `xx%` |
| 13 | `Pool` | champion pool size (distinct champs ≥3 games) |
| 14 | `CSS` | **score pill** (tier-colored) |
| 15 | `%ile` | `Pxx` or `—` (cohort <10 = `—`) |
| 16 | `Smurf` | score pill 0–100, prefix 🚨 (≥70) / ⚠️ (≥50) |
| 17 | *(actions)* | ☆/★ watch star + `›` view arrow (sticky right column) |

### Pagination
Page size **50**. Footer: `Showing 1–50 of N matching aggregates (page 1/X)` +
`« First  ‹ Prev  Next ›  Last »`. When grouping on: `· 🧬 N alt account(s) collapsed`.

### `+N accounts` popover (render as a visible section)
Header `N accounts` + ✕. Table columns: **Account · Reg · Tier · Role · Games · CSS**.

### Empty state
`No players match these filters.` (centered, muted, colspan 17)

---

## VIEW 2 — Player Profile, SoloQ tab (`#/player/<puuid>`)

### Header (`.player-header`)
- Left: profile-icon avatar (52–64px) + **Riot ID** (h2) + smurf badge + ☆/★ star + `👁 Smurf?` button
- Line 2 (muted): region pill · tier icon + `LP` · `Account lvl N`
- Line 3 (13px): pro badge · team (logo + name) · `age`y · country · residency · `contract ends <date>` · `Leaguepedia ↗`
  — OR `· no pro entry (amateur or unmatched)`
- Right block: **big CSS number (32px, 800 weight)** + score-pill label + muted `Pxx · ROLE · N games · xx% WR`
  + buttons `📋 Markdown` `🖨 PDF`

### Tabs
`SoloQ` *(active)* · `Tournament` · `vs LEC <ROLE>`  (active = accent text + 2px underline)

### SoloQ tab body (grid-2 rows of cards)

**Row 1 — grid-2**
- Card `📈 CSS trend` — subtitle `evolution across patches — line per role` + delta headline
  (e.g. `↗ +12 CSS on MID (53 → 65)`). Line chart, one line per role.
  Role colors: TOP `#f59e0b`, JGL `#34d399`, MID `#60a5fa`, ADC `#f87171`, SUP `#a78bfa`.
  Empty state: `Need 2+ patches of data to draw a trend — currently N patch(es) on record.`
- Card `🔥 Activity` — subtitle `current streak + when they play (UTC)`.
  Streak badge (e.g. `5W win streak 🔥`) + **7×24 heatmap** (rows Mon–Sun, 24 hour cols,
  blue cells `rgba(110,168,255,α)`). Footer `Times in UTC · N games total`.

**Row 2 — grid-2**
- Card `CSS radar — <ROLE> (patch X)` — 8 axes: **Lane · Damage · Vision · Objective ·
  Mapplay · Survival · Champpool · Consistency**. Player polygon + dashed `Challenger median (50)`.
- Card `Aggregate stats` (+ `📖 explain` link) — stat rows (label / value), each with tooltip:
  - GD@15 · XPD@15 · CSD@15 · CS / min · DPM · Damage share (`%`) · Kill participation (`%`)
    · KDA · Vision / min · Wards placed / min · Solo kills / game · Early deaths / game
    · Champion pool (≥3 games)

**Pro identity card** (only if pro) — icon + name + flag, role · team, Lolpros ↗.
grid-3: **Career path** (team history rows) · **Social media** (links) ·
**Personal & contract** (Age / Nationality / Residency / Contract ends / Lolpros score).
Then **Tracked accounts (N)** grid + **Active leagues this season** pills.

**Row 3 — grid-2**
- Card `Champion pool` — table: **Champion · Games · WR · KDA · KP · GD@15 · Dmg % · Champ CSS**
  (top 10 shown; GD@15 colored green/red; Champ-CSS = score pill or `—`)
- Card `Recent matches` (subtitle `click any row to open the deep-dive`) —
  table: **Champ · Role · K/D/A · GD@15 · Dmg % · VS · W** (top 15; W = green, L = red)

**Full-width card** `vs Champion` — subtitle `opponent same role · sortable by games / WR / GD@15`

**Row 4 — grid-2**
- Card `Score breakdown` (+ `📖 explain`) — 8 bar-rows (one per radar category, 0–100),
  bar fill = emerald→amber gradient. Footer: `Sample factor · Smurf factor · Lobby factor`.
- Card `🚨 Smurf signals` — subtitle `multi-signal alt-account detector`.
  Big score pill (0–100) + sub-signal bar-rows.

**Row 5**
- Card `Scout notes` — note list (each: timestamp + ✕ + content, left-accent green border)
  + textarea `Add a private note about this player…` + `Save note` button.

### Match deep-dive modal (render as a visible section)
Title `Match deep-dive · <id>`. Header: `Patch X · N min · Winner: Blue/Red` + buttons
`📥 Download JSON` `🔗 External` `▶ How to get .rofl`.
grid-2: `🔵 Blue side (WIN)` / `🔴 Red side` roster stat-rows.
grid-2: `📊 Gold advantage` (filled area chart, blue leads +, red leads −) + events strip ·
`🗺 Kill positions` (Summoner's Rift minimap w/ blue/red kill dots).
Collapsible `Per-participant gold curves`. `📜 Events timeline` (scrollable, JetBrains Mono timestamps).
Side colors: blue `#6ea8ff`, red `#ff8b8b`.

---

## VIEW 3 — Login

Centered card (`.login-card`, radius 14, shadow `0 20px 60px rgba(0,0,0,0.4)`) over a
radial-gradient vignette `radial-gradient(circle at 30% 20%, #20242c 0%, #0d0f13 70%)`.

- Brand: `CS` logo capsule + `Challenger Scouting` (h1) + `Pro edition — internal use` (sub)
  → in the new design this becomes the **PROSPEKT** lockup.
- Field **Username** (text)
- Field **Password** (password)
- Error line (hidden by default)
- `[Sign in]` button (full width)

App shell after login — header brand `Challenger Scouting` / `SoloQ analytics · EUW`,
user label + `📖 Glossary` + `Logout`. (In the new design: sidebar nav + top bar w/ search.)

---

## Notes for the mockups
- **17 columns is a lot** — the new Ladder table must handle horizontal scroll with a
  sticky first (`#` + Summoner) and sticky last (actions) column.
- Emoji in the current code (🎯🌟👶⏳🔥🧬🚨⚠️) → replace with Lucide inline SVG per the art direction.
- Every stat has a tooltip in the source — keep the glossary-tooltip pattern.
- Numbers use tabular figures and right-align in table cells.
