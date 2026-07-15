# PROSPEKT — Challenger Scouting Redesign · Design System

Ultra-dark premium SaaS scouting platform. Dual-accent (electric violet + magenta),
monospace micro-typography, dense-but-legible data tables. Linear × Vercel dashboard feel,
zero gaming clichés.

This spec is the developer contract for **Batch 1**: Ladder, Player Profile (SoloQ tab), Login.
Every value below is lifted directly from the mockup files (`Ladder.dc.html`,
`Player Profile.dc.html`, `Login.dc.html`). Copy the `:root` block verbatim.

---

## 1. Color tokens

```css
:root {
  /* Surfaces — near-black */
  --bg:        #0a0a0d;  /* page canvas */
  --bg-2:      #0d0d11;  /* sidebar, inputs, table head, recessed wells */
  --card:      #131318;  /* cards, table body, topbar chrome */
  --card-2:    #17171d;  /* raised sub-surface (buttons, skeleton highlight) */

  /* Borders — thin, low-contrast, 1px everywhere */
  --line:      #22222a;  /* default hairline */
  --line-2:    #2c2c36;  /* stronger hairline (modal edge, hover border) */

  /* Text */
  --text:      #e5e5ea;  /* primary — names, numbers, headings */
  --text-2:    #b4b4be;  /* secondary — cell values, body */
  --muted:     #8a8a94;  /* labels, metadata, captions */
  --dim:       #5c5c66;  /* disabled, axis ticks, faint separators */

  /* Primary accent — electric violet */
  --violet:      #8b5cf6;
  --violet-soft: rgba(139,92,246,0.12);  /* pill fills, active-nav bg */
  --violet-line: rgba(139,92,246,0.38);  /* accent borders, focus ring */

  /* Secondary accent — magenta / pink */
  --magenta:      #ec4899;
  --magenta-soft: rgba(236,72,153,0.12);
  --magenta-line: rgba(236,72,153,0.38);

  /* Semantic */
  --green:      #34d399;  --green-soft: rgba(52,211,153,0.12);  /* good / positive delta / Elite */
  --amber:      #f59e0b;  --amber-soft: rgba(245,158,11,0.13);  /* PRO badge, warn, smurf ⚠ */
  --red:        #f87171;  --red-soft:   rgba(248,113,113,0.12); /* negative delta / error / smurf 🚨 */
  --gold:       #fbbf24;  /* watchlist star (active) */

  /* In-game side identity (match modal only — never chrome) */
  --blue-side:  #6ea8ff;
  --red-side:   #ff8b8b;

  /* Role colors */
  --role-top: #60a5fa;  --role-jgl: #34d399;  --role-mid: #f59e0b;
  --role-adc: #f87171;  --role-sup: #a78bfa;

  /* Type */
  --f-sans: 'Inter', -apple-system, system-ui, sans-serif;
  --f-mono: 'JetBrains Mono', ui-monospace, Menlo, monospace;

  /* Radii */
  --r-2: 4px;  --r-3: 6px;  --r-4: 8px;  --r-6: 12px;  --r-8: 16px;
  --r-14: 14px; /* login card */  --r-round: 999px; /* pills, bars, stars */
}
```

**Usage rules**
- Accents are used **sparingly** — most of the UI is grayscale text on near-black. Violet = active
  nav, primary CTA, overline eyebrows, sparkline, `+N accounts` badge, focus ring. Magenta = notification
  dot, rising-star signal, alert badges, gradient ends.
- **Role colors** appear only in role tags/legends. **Side colors** appear only inside the match modal.
- Never introduce a color outside these tokens.

---

## 2. Typography

- **Inter** (400/500/600/700/800) — all UI. **JetBrains Mono** (400/500/600) — every micro-label,
  metadata line, section eyebrow, table header, timestamp, formula, and numeric annotation.
- Monospace is **always uppercase with wide tracking** when used as a label.

| Role | Family | Size | Weight | Tracking | Case |
|---|---|---|---|---|---|
| Page title (h1) | Inter | 30px | 800 | -0.02em | Sentence |
| Player name (h1) | Inter | 24px | 800 | -0.01em | As-is |
| Card title | Inter | 14px | 700 | — | Sentence |
| Big KPI / CSS number | Inter | 40px | 800 | — | tabular-nums |
| Body / cell value | Inter | 13–13.5px | 400–600 | — | — |
| Overline eyebrow | Mono | 11px | 500 | 0.18em | UPPER |
| Section label | Mono | 9–9.5px | 600 | 0.14–0.2em | UPPER |
| Table header | Mono | 9.5px | 600 | 0.1em | UPPER |
| Metadata / caption | Mono | 10–11px | 400–500 | 0.04–0.11em | mixed |
| Badge / pill text | Mono | 9–10px | 700 | 0.06em | UPPER |

- **Tabular numerals** (`font-variant-numeric: tabular-nums`) on every numeric cell; numbers right-align
  in tables. Letter-spacing only on uppercased strings.

---

## 3. Spacing

Scale (px): `4 · 6 · 8 · 10 · 12 · 14 · 16 · 18 · 20 · 24 · 26 · 28`.

- **Card padding**: 18px (data cards) / 22–24px (player header, login).
- **Main content**: `26px 28px` padding, `max-width: 1500px` (Ladder) / `1360px` (Profile), centered.
- **Table cells**: `11px 12px` (Ladder) / `9px 12–14px` (profile tables).
- **Grid gaps**: 20px between cards, 14–16px within filter/KPI rows.
- Dense by design — stat rows separated by `1px dashed var(--line)`, not whitespace.

---

## 4. Radii, borders, elevation

- **Radii ladder**: 4 (tag) · 6 (button, badge) · 8 (input, note, role tag) · 12 (card, table wrap) ·
  14 (login card) · 999 (score pill, bar, star, quick-filter pill).
- **Borders**: `1px solid var(--line)` everywhere; `1px solid var(--line-2)` on modal/popover edges and
  hover. `1px dashed var(--line)` only for stat-row separators.
- **Shadows** — reserved for floating surfaces:
  - Card: none (flat, hairline-separated). Optional `0 8px 24px rgba(0,0,0,0.35)` on the login/hero card.
  - Modal / popover: `0 24px 60px rgba(0,0,0,0.5)`.
  - Login card: `0 20px 60px rgba(0,0,0,0.5)`.
  - Accent glow on primary CTA only: `0 0 0 1px var(--violet-line), 0 6–8px 18–22px rgba(139,92,246,0.28–0.4)`.
- **Note pattern**: `background: var(--bg-2)` + `border-left: 3px solid var(--green)` — reserved for scout notes.

---

## 5. Gradients & backgrounds

- Canvas is **flat near-black**. Sidebar (`--bg-2`) sits one step darker than the content.
- **Brand mark**: `linear-gradient(135deg, var(--violet), var(--magenta))`.
- **Progress / score bars**: `linear-gradient(90deg, var(--green), var(--amber))` fill.
- **Login vignette**: `radial-gradient(circle at 30% 20%, #17131f 0%, #0a0a0d 62%)` + two soft
  corner glows (violet top-left, magenta bottom-right).
- **Topbar**: `rgba(10,10,13,0.82)` + `backdrop-filter: blur(12px)`. No other blur anywhere.
- No background images, patterns, textures, or illustrations.

---

## 6. Components & states

**App shell** — fixed 236px sidebar (`--bg-2`, right hairline) + sticky topbar + content.
Sidebar: brand lockup → nav groups (`WORKSPACE` / `ANALYSIS` / `ORG`, mono uppercase labels) →
`margin-top:auto` patch footer (`16.14 · 4d ago`). Active nav = violet text + `--violet-soft` bg +
2px violet spine on the left. Count badges: violet-soft (info) / magenta-soft (alerts).

**Topbar** — global search (max 460px, `⌘K` chip, violet focus ring) · notification bell w/ magenta dot ·
theme toggle · user chip (`Head Scout` / `ORG WORKSPACE`, gradient avatar).

**Score pill** — `--r-round`, alpha-tinted, tier-colored. Thresholds:
`≥75 Elite` green · `60–74 Strong` violet · `45–59 Average` neutral-gray · `<45 Below avg` red.

**Role tag** — 4–8px radius, role-colored fill+text at 14% / 30% alpha, mono uppercase (TOP JGL MID ADC SUP).

**Region / team / pro pills** — `--bg-2` + hairline, mono. Pro: `PRO` amber · `FA` green · `Retired` gray · `—` dim.

**KPI / stat card, stat row, bar row, tabs (violet 2px underline), quick-filter pills, +N popover,
match modal** — see mockups for exact markup.

**Interaction (150ms `ease`/`ease-out` unless noted)**
- Buttons: `filter: brightness(1.08)` hover. Primary lifts `translateY(-1px)`. Disabled `opacity:0.4`.
- Nav: muted → full text + `--card` bg. Table rows: `background: rgba(255,255,255,0.022)` (120ms).
- Watchlist star: `transform: scale(1.15)` hover (100ms); active = gold, filled.
- Inputs/search: border → `--violet-line` + focus ring on focus-within.
- No bounce, no spring, no entrance animations. Modals appear; backdrop `rgba(0,0,0,0.6)`.

**States shipped per view**: each view includes one **empty state** (centered icon + terse message +
recovery action) and one **loading skeleton** (shimmer sweep `--bg-2 → --card-2 → --bg-2`, 1.4s linear).

---

## 7. Iconography

- **Lucide** (monoline, 1.5–1.8px stroke), inline SVG so files stay self-contained. Nav, buttons,
  badges, empty states all use Lucide. Chart glyphs and 🔵/🔴 side markers in the match modal are the
  only place emoji-style markers remain (functional, not decorative).
- Data imagery (champion squares, summoner icons, team logos, pro photos) = placeholder squares:
  `--bg-2` fill + 1px `--line` border, radius 3–8px. Wire to Riot Data Dragon / Lolpros in production.

---

## 8. Charts (production = Chart.js; mockups = static SVG)

Match these visual targets:
- **Grid**: `--line` 1px horizontal rules; axis ticks in `--dim`, JetBrains Mono 8–9px.
- **CSS trend**: one 2px line per role using role colors (TOP `#60a5fa`, JGL `#34d399`, MID `#f59e0b`,
  ADC `#f87171`, SUP `#a78bfa`); primary line gets a violet area fill fading to transparent.
- **Radar**: 8 axes (Lane · Damage · Vision · Objective · Mapplay · Survival · Champpool · Consistency);
  player polygon = `rgba(139,92,246,0.18)` fill + 2px violet stroke; `Challenger median (50)` = dashed `--dim`.
- **7×24 heatmap**: Mon–Sun rows × 24 hour cols; cells `rgba(110,168,255,α)`, α scaled by game count;
  empty cells `--bg-2`. Times in UTC.
- **Gold-advantage** (match modal): filled area, blue leads positive / red leads negative, zero baseline `--line-2`.

---

## 9. Layout & responsive strategy

Desktop-first. Breakpoints and collapse behavior (production):

| Width | Behavior |
|---|---|
| ≥1200px | Full shell: 236px sidebar + multi-column card grids (2–3 up). |
| 1000–1199px | Card grids collapse to 1 column; tables keep horizontal scroll. |
| 768–999px | Sidebar → off-canvas drawer (hamburger in topbar); search shrinks to icon-expand. |
| <768px | **Sidebar → bottom tab bar** (5 primary items + "More"). **Tables → stacked cards**: each row becomes a card with the summoner + CSS pill as the header and remaining columns as label/value rows. Sticky action column → a footer action strip on each card. |

- **17-column Ladder table**: never reflow on desktop — use `overflow-x:auto` with a **sticky first
  group** (`#` + Summoner) and **sticky last** (actions) column so identity and the View action stay
  pinned while scrolling stats.
- KPI grids: 4-up → 2-up (≤1100px) → 1-up (≤700px).
- Modals top-aligned with 40px top padding (long content scrolls), never vertically centered.

---

## 10. File manifest (Batch 1)

- `Ladder.dc.html` — leaderboard: 17-col table, 12 filters + quick pills, `+N accounts` popover, empty + skeleton.
- `Player Profile.dc.html` — SoloQ tab: header, tabs, CSS trend, activity heatmap, radar, aggregate stats,
  champion pool, recent matches, vs-champion, score breakdown, smurf signals, pro identity, scout notes,
  match deep-dive modal, empty + skeleton.
- `Login.dc.html` — auth gate: PROSPEKT lockup, username/password, hidden error line, sign-in CTA.
- `design-system.md` — this file.

**Batch 2** (next): Watchlist Kanban, Compare, Patch Impact.
**Batch 3**: Team, Champions, Alerts, Admin.
