/* ---------------- LOGIN ---------------- */
function showLogin() {
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('app-shell').style.display = 'none';
  setTimeout(() => document.getElementById('lg-user').focus(), 0);
}
function showApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app-shell').style.display = 'block';
  const u = currentUser();
  if (u) document.getElementById('user-label').textContent = `${u.username} · ${u.org}`;
}

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const u = document.getElementById('lg-user').value;
  const p = document.getElementById('lg-pass').value;
  const err = document.getElementById('login-err');
  err.textContent = '';
  try {
    const res = await APIform('/auth/login', { username: u, password: p });
    setToken(res.access_token, res.user);
    showApp();
    setView('leaderboard');
  } catch (ex) {
    err.textContent = 'Invalid credentials';
  }
});

document.getElementById('logout-btn').addEventListener('click', () => {
  clearAuth();
  showLogin();
});

/* ---------------- GLOSSARY ---------------- */
const GLOSSARY = {
  "Scoring": [
    { term: "CSS",  formula: "50 + 15·z(player vs Challenger pool)",
      desc: "Challenger Scouting Score on 0-100. Per-role weighted z-score across 8 categories. >75 elite, 60-75 strong, 45-60 average." },
    { term: "%ile (Percentile rank)", formula: "rank / N × 100 within (patch, role)",
      desc: "Position in the Challenger distribution for this role and patch. P95 = top 5% of MIDs on patch X.Y." },
    { term: "Champ CSS", formula: "z(player avg) vs same-champion Challenger baseline",
      desc: "Per-champion score: how the player performs ON this specific champion vs every other Challenger main of it. '—' = not enough mains in DB to baseline." },
    { term: "Smurf score", formula: "weighted sum of 5 signals → [0,1]",
      desc: "Multi-signal smurf likelihood: low account level + high LP, few lifetime games, suspicious WR, one-trick at level, high CSS at low level." },
    { term: "Lobby factor", formula: "1.0 ± clip(0.10, (avg_lobby_lp − 700)/2000)",
      desc: "Adjusts CSS for lobby quality. Higher LP lobbies → small uplift; soft 400-LP lobbies → small discount. Anchored at 700 LP." },
    { term: "Sample factor", formula: "0.5 + 0.5 · min(1, games / MIN_GAMES)",
      desc: "Reduces confidence in CSS for small samples. <MIN_GAMES games → score multiplied by 0.5..1.0." },
  ],
  "Lane phase metrics": [
    { term: "GD@15", formula: "player_gold(15') − opponent_gold(15')",
      desc: "Gold differential at 15 min vs same-role opponent. Lane dominance proxy. Typical Challenger range: ±300, big gaps reach ±1000+." },
    { term: "XPD@15", formula: "player_xp(15') − opponent_xp(15')",
      desc: "XP differential — captures level leads even when gold is matched (e.g. roams that snowball via levels)." },
    { term: "CSD@15", formula: "player_cs(15') − opponent_cs(15')",
      desc: "CS differential — pure laning skill. Less affected by skirmishes than GD." },
    { term: "CS/min", formula: "total_cs / game_minutes",
      desc: "Farming consistency. Pro range: 8-10 (carries), 6-7 (junglers), 0.5-2 (supports)." },
  ],
  "Combat & impact": [
    { term: "Damage share", formula: "player_dmg / sum(team_dmg)",
      desc: "Share of team damage to champions. ADC ≈ 30%, mid ≈ 27%, top ≈ 22%, jungle ≈ 18%, support ≈ 10%." },
    { term: "DPM",       formula: "damage_to_champs / minutes",
      desc: "Damage per minute. Threat level over time, less duration-biased than total damage." },
    { term: "KP",        formula: "(kills + assists) / team_kills",
      desc: "Kill participation. Engagement in fights. Mids/supports lead at 60%+, ADCs around 55%." },
    { term: "KDA",       formula: "(kills + assists) / max(deaths, 1)",
      desc: "Classic ratio. Use as a sanity check, not a primary signal — stomps inflate it dramatically." },
    { term: "Solo kills", formula: "kills with no assists in event",
      desc: "1v1 outplays. Strong indicator of mechanical edge for top/mid laners." },
  ],
  "Vision & objectives": [
    { term: "Vision/min (VSPM)", formula: "vision_score / minutes",
      desc: "Vision contribution per minute. Critical for SUP (target 2.0+); 1.0+ for solo-laners." },
    { term: "Wards placed/min", formula: "wards_placed / minutes",
      desc: "Offensive vision setup. Trinket usage discipline." },
    { term: "Objective dmg",    formula: "damage to drakes/heralds/baron/towers",
      desc: "Indicates jungle priority and team objective focus." },
    { term: "Early deaths",     formula: "deaths before 14:00",
      desc: "Lane-phase mistakes. High value = punished often / overextended." },
  ],
  "Pro / scouting filters": [
    { term: "Pro",            desc: "Lolpros has a profile for this player. Click their View page to see career path + social links." },
    { term: "FA (Free Agent)", desc: "Pro on Lolpros but not currently rostered. Top scouting target." },
    { term: "Residency",      desc: "Player's Riot residency (Europe / Korea / North America). Affects regional eligibility for LEC/LCK/LCS." },
    { term: "Contract end",   desc: "When contracts data is public (Leaguepedia). Filter 'within 90d' surfaces upcoming free agents." },
    { term: "Lobby LP",       desc: "Mean LP of all 10 participants in a player's matches. Used to discount soft-lobby grinds." },
  ],
  "Match deep-dive & replays": [
    { term: "Match deep-dive",
      desc: "Click any row in 'Recent matches' on a player profile. Loads the gold curve, kill/objective/tower events with timestamps, and roster K/D/A. Data is pulled live from Riot, cached 30 min." },
    { term: "Download JSON",
      desc: "Bundles match-v5 + timeline as a single JSON file for offline analysis. Includes everything Riot exposes — every frame, every event, every participant." },
    { term: ".rofl replay",
      desc: "The actual in-game replay file (binary, ~5 MB) is NOT available via Riot's public API. Only the LoL client (LCU) can download it, locally on a machine where the player is logged in. The 'How to get .rofl' button shows the manual path." },
    { term: "External links",
      desc: "Deep-links to op.gg / leagueofgraphs / Lolpros / Blitz so you can open the match on any of those services. Some may 404 if they don't index the game." },
  ],
};

function openGlossary() {
  const body = document.getElementById('glossary-body');
  body.innerHTML = Object.entries(GLOSSARY).map(([section, entries]) => `
    <div class="glossary-section">
      <h4>${section}</h4>
      ${entries.map(e => `
        <div class="glossary-entry">
          <span class="term">${e.term}</span>
          ${e.formula ? `<span class="formula">${e.formula}</span>` : ''}
          <div class="desc">${e.desc}</div>
        </div>
      `).join('')}
    </div>
  `).join('');
  document.getElementById('glossary-panel').classList.add('open');
  document.getElementById('glossary-backdrop').classList.add('open');
}
function closeGlossary() {
  document.getElementById('glossary-panel').classList.remove('open');
  document.getElementById('glossary-backdrop').classList.remove('open');
}
document.getElementById('glossary-btn').addEventListener('click', openGlossary);
document.getElementById('glossary-close').addEventListener('click', closeGlossary);
document.getElementById('glossary-backdrop').addEventListener('click', closeGlossary);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeGlossary();
});

/* ---------------- ROUTER ---------------- */
const app = document.getElementById('app');
const navLinks = document.querySelectorAll('nav a');

// `arg` is the optional second segment of the URL hash:
//   #/player/<puuid>     → setView('player', '<puuid>')
//   #/team/<code>        → setView('team', '<code>')
//   #/leaderboard        → setView('leaderboard')
function setView(name, arg) {
  navLinks.forEach(a => a.classList.toggle('active', a.dataset.view === name));
  const tpl = document.getElementById('tpl-' + name);
  if (!tpl) {
    // Unknown view → fall back to leaderboard
    setView('leaderboard');
    return;
  }
  app.innerHTML = '';
  app.appendChild(tpl.content.cloneNode(true));

  // Update the URL hash so the view is shareable. Player navigation
  // sometimes happens with arg=undefined (caller already set
  // window._selectedPuuid) — fall back to that so the URL still gets
  // the puuid suffix.
  let urlArg = arg;
  if (!urlArg && name === 'player' && window._selectedPuuid) {
    urlArg = window._selectedPuuid;
  }
  const desired = urlArg ? `#/${name}/${encodeURIComponent(urlArg)}` : `#/${name}`;
  if (window.location.hash !== desired) {
    history.replaceState(null, '', desired);
  }

  if (name === 'leaderboard') initLeaderboard();
  if (name === 'watchlist') initWatchlist();
  if (name === 'champions') initChampions();
  if (name === 'patch') initPatchImpact();
  if (name === 'player') {
    if (arg) window._selectedPuuid = arg;
    initPlayer();
  }
  if (name === 'team') initTeam(arg);
  if (name === 'compare') initCompare();
  if (name === 'alerts') initAlerts();
  if (name === 'admin') initAdmin();
}
navLinks.forEach(a => a.addEventListener('click', e => { e.preventDefault(); setView(a.dataset.view); }));

// Browser back/forward → re-route from the hash
window.addEventListener('hashchange', () => {
  const parsed = parseHash();
  if (parsed) setView(parsed.view, parsed.arg);
});

// Parse `#/<view>/<arg>` into { view, arg }. Returns null if hash is empty.
function parseHash() {
  const h = window.location.hash || '';
  const m = h.match(/^#\/([\w-]+)(?:\/(.+))?$/);
  if (!m) return null;
  return { view: m[1], arg: m[2] ? decodeURIComponent(m[2]) : undefined };
}

// Smurf-likelihood score (0..100). Higher = more suspect.
// Role icons (sourced from lolpros.gg's CDN). Each Riot role maps to one
// SVG. We accept a few naming conventions: TOP / JGL / JNG / JUNGLE /
// MID / ADC / BOT / BOTTOM / SUP / SUPPORT.
const ROLE_ICON_URLS = {
  top:     'https://lolpros.gg/_nuxt/img/top.714b08c.svg',
  jungle:  'https://lolpros.gg/_nuxt/img/jungle.a1fa469.svg',
  mid:     'https://lolpros.gg/_nuxt/img/mid.54ff92a.svg',
  bottom:  'https://lolpros.gg/_nuxt/img/bottom.a947d38.svg',
  support: 'https://lolpros.gg/_nuxt/img/support.2f8a4f6.svg',
};
