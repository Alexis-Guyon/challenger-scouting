# Claude Design Prompt — Challenger-Scouting Redesign

Act as a Senior Product Designer (UI/UX) specializing in premium esports analytics platforms.

Redesign the UI of **Challenger-Scouting**, an existing internal scouting platform for League of Legends used by an esports organization's analyst team. The app is auth-gated, multi-region (EUW / KR / NA / EUNE / BR…), and combines SoloQ Challenger data, tournament data (LEC / LCK / LCS / ERLs), and pro-player identity data. The core metric is the **CSS — Challenger Scouting Score (0–100)**, computed per player × patch × role across 8 categories.

Follow the art direction below **exactly** — it is the approved visual identity, not a suggestion.

## Art Direction (approved — reproduce faithfully)

**Overall mood**: ultra-dark premium SaaS. Near-black canvas, elegant cards, dual accent system (electric violet + magenta/pink), monospace micro-typography for data labels. Think Linear × Vercel dashboard, zero "gaming" clichés.

**Layout — app shell**:

- **Fixed left sidebar** (~230px), slightly darker than the canvas, containing: logo block at top (icon + product name + small letter-spaced monospace subtitle), then navigation grouped in sections with tiny uppercase letter-spaced monospace section labels (e.g. `WORKSPACE`, `ANALYSIS`). Each nav item = icon + label; active item gets a subtle filled pill background with a violet accent; items can carry small count badges. Bottom of sidebar: persistent context footer (current patch + "4d ago" in monospace).
- **Top bar**: large global search input on the left (rounded, dark, with search icon and `⌘K` shortcut hint chip), right side: notification bell with accent dot, theme toggle, and a user chip (avatar square with initials + name + tiny uppercase monospace org label).
- **Content area**: starts with a tiny uppercase letter-spaced monospace overline in violet (e.g. `SCOUTING OVERVIEW`), then a large bold page title, then a muted metadata subtitle line (e.g. "Ladder synced 26 min ago · EUW · NA · KR live"). Page-level actions top-right: one secondary outlined button + one primary violet button.

**Color palette**:

- Canvas: near-black (#0a0a0d range). Sidebar: slightly darker. Cards: #131318 range.
- Borders: thin, low-contrast (#22222a range), 1px everywhere.
- Primary accent: electric violet (#8b5cf6 range) — primary buttons, active nav, sparklines, overlines.
- Secondary accent: magenta/pink (#ec4899 range) — rising/trending signals, alert banners, gradient ends.
- Semantic: green for positive deltas, amber/orange for patch/warning context, red sparingly.
- Text: soft white (#e5e5ea) for primary, muted gray (#8a8a94) for secondary.

**Typography**:

- UI text: modern grotesque sans (Inter-like), bold weights for names and numbers.
- **Monospace as a signature**: all micro-labels, metadata lines, stat annotations, and section labels use a monospace font, uppercase, wide letter-spacing (e.g. `PLAYERS TRACKED`, `#1 · 1487 LP · Azir`).
- Big KPI numbers: large, bold, tight.

**Signature components** (reproduce these patterns):

- **KPI stat cards**: dark card, tiny uppercase monospace label color-coded per card (violet / green / amber / pink), huge bold number, small colored annotation line below (`▲ 128 this patch`). One accent color per card, applied to label + annotation.
- **Player rows**: square avatar with initial, bold name followed by inline **role badge** (small filled chip, color-coded per role: TOP blue, JGL green, MID amber, ADC red/pink, SUP violet) and **region chip** (dark outlined), muted monospace meta line underneath (`#2 · 1402 LP · Kalista`), then to the right: mini **sparkline** (violet or pink), thin **gradient progress bar** (violet→pink), and the CSS score in a small outlined box.
- **Rising/trend list**: same row anatomy but pink-themed, with `+214 LP` outlined pink chips and pink sparklines.
- **Callout banner**: tinted pink background + pink border, bold lead-in (`Breakout watch —`) followed by regular text. Same pattern in violet for info.
- **Cards**: generous internal padding, card title bold + muted subtitle line, optional top-right outlined action button (`Open explorer →`), radius ~14px, subtle shadows.
- **Activity feed**: dot-prefixed entries (accent-colored dot), bold event title, muted monospace detail line, right-aligned relative timestamp.

**Motion**: subtle — 150–200ms ease transitions on hover (border lightens, background lifts slightly), no flashy animations.

## Views to design (all exist in the current app)

1. **Dashboard / Ladder** (`#/leaderboard`) — the core view: multi-region Challenger leaderboard sorted by CSS. Columns: player, pro badge + clickable team chip, region, role, tier + LP, games, win rate, KDA, GD@15, CSS pill, smurf flag, rising-star tag, watch toggle, sticky action column. Filters: role / region / tier / patch / min-games / pro status / smurf score / age / residency / contract-end. Quick-filter pills (Free Agents · Rising Stars · U21 · Contract < 90d), "group accounts by pro" toggle with `+N accounts` badge + popover.
2. **Player Profile** (`#/player/<puuid>`) — hero with pro identity card (photo, socials, previous teams, peak rank, age, contract end), KPI cards (CSS, WR, KDA, GD@15, XPD@15, CSD@15, DPM, damage share, KP, vision/min), 3 tabs: **SoloQ** (CSS trend chart per role with auto headline, streak + 7×24 activity heatmap, 8-category CSS radar, champion pool with per-champion CSS, vs-champion matchups, smurf signals, recent matches → deep-dive modal with gold curves, private scout notes), **Tournament** (per-split stats, tournament champion pool, matches → modal), **vs LEC** (prospect vs every LEC pro of the role, color-coded deltas). Actions: markdown dossier export, PDF, watch toggle.
3. **Patch Impact** (`#/patch`) — two patch dropdowns, Δ CSS table with ±5 color thresholds.
4. **Watchlist Kanban** (`#/watchlist`) — 6-stage drag-and-drop recruitment pipeline (Watching → Contacted → Trial → Offer → Signed | Pass), cards with name, role, tier+LP, CSS pill, tag, days-in-stage; Kanban/Table toggle.
5. **Team page** (`#/team/<code>`) — logo, league, last-10 record, roster sorted TOP→SUP, recent tournament matches.
6. **Compare** (`#/compare`) — up to 5 players, search-with-chips, side-by-side radars + delta highlights.
7. **Champions** (`#/champions`) — list + per-champion best-players modal.
8. **Alerts** (`#/alerts`) — rule builder + rule list (CSS jump, peak rank, FA transition, webhooks).
9. **Admin** (`#/admin`) — 5 sync pipelines with live job tracker + DB stats cards.
10. **Login** — auth gate consistent with the identity.

## Components

Sidebar, top bar with global search, data tables (sticky header, sticky action column, sortable, dense but readable), KPI cards, CSS pill (tier-colored: 75+ Elite · 60–75 Strong · 45–60 Average · <45 Below), role/region badges, team chips, quick-filter pills, kanban cards/columns, radar chart, trend chart, sparklines, 7×24 heatmap, gold-curve chart, tabs, dropdowns, multi-select filters, pagination, tooltips (every stat has a glossary tooltip), modals, popovers, callout banners, toasts, empty states, loading skeletons, job status indicators.

## Constraints

- Frontend is a vanilla-JS SPA with no build step (Chart.js only) — designs must be achievable with plain CSS/JS.
- Internal tool behind auth — no marketing pages.
- Data density is a feature: analysts want maximum information per screen with clear hierarchy.
- Desktop-first, with a mobile adaptation strategy.

## Deliverables

Produce **high-fidelity HTML/CSS mockups as artifacts, one per view**, using realistic placeholder data (Challenger-style player names, LP values, CSS scores). Also produce a `design-system.md` spec: exact color tokens (CSS variables), typography scale, spacing scale, radius, shadows, component states. The mockups must be pixel-consistent with the art direction above so a developer can lift the tokens directly.
