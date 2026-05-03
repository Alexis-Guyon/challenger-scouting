/* Challenger Scouting — Pro edition (auth + watchlist + notes) */

// API base URL — configurable so the same frontend can run:
//   * bundled with the backend (FastAPI StaticFiles, same-origin) → empty
//   * standalone on Vercel pointing at Fly/Railway → set via window.SCOUTING_API_BASE
//     in a small inline <script> in index.html, OR via the build-time
//     `vercel.json` rewrite so paths are still relative.
const API_BASE = (typeof window !== 'undefined' && window.SCOUTING_API_BASE) || '';

const TOKEN_KEY = 'cs_token';
const USER_KEY = 'cs_user';

function getToken() { return localStorage.getItem(TOKEN_KEY); }
function setToken(t, u) {
  localStorage.setItem(TOKEN_KEY, t);
  localStorage.setItem(USER_KEY, JSON.stringify(u));
}
function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}
function currentUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
}

async function API(path, opts = {}) {
  const headers = opts.headers || {};
  const token = getToken();
  if (token) headers['Authorization'] = 'Bearer ' + token;
  // Default Content-Type for JSON string bodies (Pydantic POST/PATCH)
  if (typeof opts.body === 'string' && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  const url = path.startsWith('http') ? path : (API_BASE + path);
  const res = await fetch(url, { ...opts, headers });
  if (res.status === 401) { showLogin(); throw new Error('unauthorized'); }
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`${res.status}: ${txt}`);
  }
  return res.json();
}

async function APIform(path, formData, method = 'POST') {
  const body = new URLSearchParams();
  Object.entries(formData).forEach(([k, v]) => body.append(k, v));
  return API(path, { method, body, headers: {} });
}

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

function setView(name) {
  navLinks.forEach(a => a.classList.toggle('active', a.dataset.view === name));
  const tpl = document.getElementById('tpl-' + name);
  app.innerHTML = '';
  app.appendChild(tpl.content.cloneNode(true));
  if (name === 'leaderboard') initLeaderboard();
  if (name === 'watchlist') initWatchlist();
  if (name === 'champions') initChampions();
  if (name === 'player') initPlayer();
  if (name === 'compare') initCompare();
  if (name === 'alerts') initAlerts();
  if (name === 'admin') initAdmin();
}
navLinks.forEach(a => a.addEventListener('click', e => { e.preventDefault(); setView(a.dataset.view); }));

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
const ROLE_NORMALIZE = {
  top: 'top', t: 'top',
  jgl: 'jungle', jng: 'jungle', jng_: 'jungle', jungle: 'jungle', jg: 'jungle',
  mid: 'mid', m: 'mid', middle: 'mid',
  adc: 'bottom', bot: 'bottom', bottom: 'bottom', ad: 'bottom',
  sup: 'support', supp: 'support', support: 'support', sp: 'support', s: 'support',
};
function roleIcon(role, opts = {}) {
  if (!role || role === '—') return '<span class="muted">—</span>';
  const norm = ROLE_NORMALIZE[String(role).toLowerCase()];
  if (!norm) {
    // Unknown role — fall back to bold uppercase text (e.g. "SoloQ", "LEC", "EUW")
    return `<span class="role-tag">${String(role).toUpperCase()}</span>`;
  }
  const label = String(role).toUpperCase();
  const size = opts.size || 18;
  return `<img class="role-icon" src="${ROLE_ICON_URLS[norm]}" alt="${label}" title="${label}" width="${size}" height="${size}"/>`;
}

// Compact region pill for the ladder + profile header.
const REGION_LABELS = {
  euw1:'EUW', kr:'KR', na1:'NA', eun1:'EUNE', br1:'BR', jp1:'JP',
  oc1:'OCE', la1:'LAN', la2:'LAS', tr1:'TR', ru:'RU',
};
function regionBadge(code) {
  if (!code) return '<span class="muted" style="font-size:11px;">—</span>';
  const label = REGION_LABELS[code.toLowerCase()] || code.toUpperCase();
  return `<span class="team-pill" style="font-size:10.5px;letter-spacing:.04em;">${label}</span>`;
}

function smurfClass(s) {
  if (s == null) return 's-avg';
  if (s >= 70) return 's-weak';   // red — strong smurf signal
  if (s >= 50) return 's-strong'; // amber — suspect
  if (s >= 30) return 's-avg';    // muted — soft signal
  return 's-elite';                // green — clean account
}
function smurfCell(row) {
  const score = row.smurf_score;
  if (score == null) return '<span class="muted">—</span>';
  // Score is stored 0..1; render as 0..100
  const v = Math.round(score * 100);
  const cls = smurfClass(v);
  const prefix = v >= 70 ? '🚨 ' : (v >= 50 ? '⚠️ ' : '');
  return `<span class="score-pill ${cls}" title="Smurf likelihood — click View for breakdown">${prefix}${v}</span>`;
}
function smurfBreakdownHTML(p) {
  const score = (p.smurf_score == null) ? null : Math.round(p.smurf_score * 100);
  const signals = p.smurf_signals || null;
  if (score == null) {
    return '<p class="muted" style="margin:0;font-size:12px;">Not yet computed (run "Recompute scores only" in Admin).</p>';
  }
  const cls = smurfClass(score);
  function row(label, value, hint) {
    const pct = Math.max(0, Math.min(100, value));
    return `
      <div class="bar-row" title="${hint || ''}">
        <span class="lab">${label}</span>
        <div class="bar"><span style="width:${pct.toFixed(0)}%"></span></div>
        <span class="num">${value.toFixed(0)}</span>
      </div>
    `;
  }
  let contribHTML = '';
  if (signals && typeof signals === 'object') {
    contribHTML = Object.entries(signals)
      .filter(([k, v]) => typeof v === 'number')
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => row(k.replace(/_/g, ' '), v * 100,
        'Sub-signal contribution (higher = more suspect)'))
      .join('');
  }
  return `
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px;">
      <span class="score-pill ${cls}" style="font-size:15px;padding:4px 12px;">${score >= 70 ? '🚨 ' : score >= 50 ? '⚠️ ' : ''}${score}</span>
      <span class="muted" style="font-size:11px;">smurf likelihood 0..100</span>
    </div>
    ${contribHTML || '<p class="muted" style="font-size:12px;margin:0;">No sub-signal breakdown yet.</p>'}
  `;
}
function scoreClass(s) {
  if (s >= 75) return 's-elite';
  if (s >= 60) return 's-strong';
  if (s >= 45) return 's-avg';
  return 's-weak';
}
function scoreLabel(s) {
  if (s >= 75) return 'Elite';
  if (s >= 60) return 'Strong';
  if (s >= 45) return 'Average';
  return 'Below avg';
}
function smurfBadge(p) {
  const score = p.smurf_score || 0;
  if (score < 0.3) return '';
  // Build a tooltip with which signals fired
  let tip = `Smurf likelihood: ${Math.round(score*100)}%`;
  if (p.smurf_signals && typeof p.smurf_signals === 'object') {
    tip += '\n' + Object.entries(p.smurf_signals)
      .map(([k,v]) => `· ${k}: +${(v*100).toFixed(0)}%`).join('\n');
  }
  const cls = score >= 0.6 ? 's-weak' : score >= 0.4 ? 's-avg' : 's-strong';
  const label = score >= 0.6 ? 'smurf!' : score >= 0.4 ? 'smurf?' : 'smurf?';
  return `<span class="score-pill ${cls}" title="${tip.replace(/"/g, '&quot;')}">${label} ${Math.round(score*100)}</span>`;
}
function proBadge(p) {
  if (!p.meta) return '<span class="muted" style="font-size:11px;">—</span>';
  if (p.meta.is_retired) return '<span class="score-pill s-avg" title="retired">Retired</span>';
  if (p.meta.is_fa) return '<span class="score-pill s-elite" title="Free Agent">FA</span>';
  return '<span class="score-pill s-strong" title="rostered pro">PRO</span>';
}
function teamCell(p) {
  if (!p.meta) return '<span class="muted">—</span>';
  if (p.meta.is_fa) return '<span class="muted" style="font-style:italic;">Free Agent</span>';
  const name = p.meta.current_team || '';
  if (!name) return '<span class="muted">—</span>';
  const logo = p.meta.current_team_logo_url;
  const tag = p.meta.current_team_tag || '';
  if (logo) {
    return `<span class="team-cell"><img class="team-logo" src="${logo}" alt="${tag}" onerror="this.style.display='none'"/> <span>${name}</span></span>`;
  }
  // No logo → show small text tag pill + name
  if (tag) return `<span class="team-pill">${tag}</span> ${name}`;
  return name;
}
function ageCell(p) {
  if (!p.meta || !p.meta.age) return '<span class="muted">—</span>';
  return p.meta.age;
}
// Tier icons (sourced from lolpros.gg's CDN). Only Challenger /
// Grandmaster / Master have official SVGs; lower tiers (Diamond /
// Emerald / Platinum / etc.) keep the text badge.
const TIER_ICON_URLS = {
  CHALLENGER:  'https://lolpros.gg/_nuxt/img/challenger.3b4ad49.svg',
  GRANDMASTER: 'https://lolpros.gg/_nuxt/img/grandmaster.b96750d.svg',
  MASTER:      'https://lolpros.gg/_nuxt/img/master.f890053.svg',
};
function tierBadge(tier, opts = {}) {
  if (!tier) return '<span class="muted">—</span>';
  const upper = String(tier).toUpperCase();
  const url = TIER_ICON_URLS[upper];
  const size = opts.size || 22;
  if (url) {
    return `<img class="tier-icon tier-icon-${upper.toLowerCase()}" src="${url}" alt="${upper}" title="${upper}" width="${size}" height="${size}"/>`;
  }
  // Fallback: classic text badge for Diamond / Emerald / etc.
  return `<span class="tier-badge tier-${upper}">${tier}</span>`;
}
function risingBadge(row) {
  return row.is_rising_star
    ? '<span class="score-pill s-elite" title="CSS up ≥6 pts over 3+ consecutive snapshots" style="margin-left:6px;">🚀 rising</span>'
    : '';
}

/* ---------------- LEADERBOARD ---------------- */
let _watchedSet = new Set();
let _lbOffset = 0;
const _lbPageSize = 50;

async function refreshWatchedSet() {
  try {
    const data = await API('/watchlist');
    _watchedSet = new Set(data.map(w => w.puuid));
  } catch { _watchedSet = new Set(); }
}

async function toggleWatch(puuid, btn) {
  if (_watchedSet.has(puuid)) {
    await fetch(API_BASE + '/watchlist/' + puuid, { method: 'DELETE', headers: { 'Authorization': 'Bearer ' + getToken() } });
    _watchedSet.delete(puuid);
    btn.classList.remove('active');
    btn.textContent = '☆';
  } else {
    await APIform('/watchlist', { puuid, tag: '' });
    _watchedSet.add(puuid);
    btn.classList.add('active');
    btn.textContent = '★';
  }
}

async function loadLeaderboard() {
  const role = document.getElementById('f-role').value;
  const region = document.getElementById('f-region')?.value || '';
  const tier = document.getElementById('f-tier').value;
  const patch = document.getElementById('f-patch').value;
  const min = document.getElementById('f-min').value || 1;
  const sort = document.getElementById('f-sort').value;
  const proStatus = document.getElementById('f-prostatus').value;
  const smurfFilter = document.getElementById('f-smurf')?.value || '';
  const maxAge = document.getElementById('f-maxage').value;
  const residency = document.getElementById('f-residency').value;
  const contract = document.getElementById('f-contract').value;

  const params = new URLSearchParams();
  if (role) params.set('role', role);
  if (region) params.set('region', region);
  if (tier) params.set('tier', tier);
  if (patch) params.set('patch', patch);
  params.set('min_games', min);
  params.set('sort', sort);
  params.set('limit', _lbPageSize);
  params.set('offset', _lbOffset);
  if (proStatus === 'pro') params.set('pro_only', 'true');
  if (proStatus === 'fa') params.set('fa', 'true');
  if (smurfFilter) params.set('smurf', smurfFilter);
  // amateur = pro_only=false handled client-side below
  if (maxAge) params.set('max_age', maxAge);
  if (residency) params.set('residency', residency);
  if (contract) params.set('contract_within_days', contract);

  await refreshWatchedSet();
  let resp = await API('/players?' + params);
  let data = resp.items || [];
  const total = resp.total ?? data.length;

  // Client-side post-filter for "amateur only" (no LP entry) — note this can shrink the visible page
  if (proStatus === 'amateur') data = data.filter(r => !r.meta);

  const tbody = document.querySelector('#lb-table tbody');
  tbody.innerHTML = '';

  // Update / inject the pagination + counter row under the filters bar
  let pager = document.getElementById('lb-pager');
  if (!pager) {
    pager = document.createElement('div');
    pager.id = 'lb-pager';
    pager.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin:0 0 10px;font-size:12px;color:var(--muted);';
    document.querySelector('.filters').after(pager);
  }
  const startIdx = _lbOffset + 1;
  const endIdx = _lbOffset + data.length;
  const totalPages = Math.max(1, Math.ceil(total / _lbPageSize));
  const currentPage = Math.floor(_lbOffset / _lbPageSize) + 1;
  pager.innerHTML = `
    <span>Showing <b>${startIdx}–${endIdx}</b> of <b>${total}</b> matching aggregates (page ${currentPage}/${totalPages})</span>
    <span>
      <button id="lb-first" class="secondary" ${_lbOffset===0?'disabled':''}>« First</button>
      <button id="lb-prev"  class="secondary" ${_lbOffset===0?'disabled':''}>‹ Prev</button>
      <button id="lb-next"  class="secondary" ${endIdx>=total?'disabled':''}>Next ›</button>
      <button id="lb-last"  class="secondary" ${endIdx>=total?'disabled':''}>Last »</button>
    </span>
  `;
  document.getElementById('lb-first').onclick = () => { _lbOffset = 0; loadLeaderboard(); };
  document.getElementById('lb-prev').onclick  = () => { _lbOffset = Math.max(0, _lbOffset - _lbPageSize); loadLeaderboard(); };
  document.getElementById('lb-next').onclick  = () => { _lbOffset = _lbOffset + _lbPageSize; loadLeaderboard(); };
  document.getElementById('lb-last').onclick  = () => { _lbOffset = (totalPages - 1) * _lbPageSize; loadLeaderboard(); };

  if (!data.length) {
    tbody.innerHTML = `<tr><td colspan="17" class="muted" style="text-align:center;padding:30px;">No players match these filters.</td></tr>`;
    return;
  }
  data.forEach((row, i) => {
    const watched = _watchedSet.has(row.puuid);
    const tr = document.createElement('tr');
    tr.className = 'lb-row';
    tr.dataset.puuid = row.puuid;
    tr.innerHTML = `
      <td>${_lbOffset + i + 1}</td>
      <td><strong>${row.summoner_name || '(unknown)'}</strong> ${smurfBadge(row)}${risingBadge(row)}</td>
      <td>${regionBadge(row.region)}</td>
      <td>${proBadge(row)}</td>
      <td>${teamCell(row)}</td>
      <td>${ageCell(row)}</td>
      <td>${tierBadge(row.tier)}</td>
      <td>${row.lp ?? '—'}</td>
      <td>${roleIcon(row.meta?.lp_role || row.role)}</td>
      <td>${row.patch || '—'}</td>
      <td>${row.games_played}</td>
      <td>${row.winrate}%</td>
      <td>${row.champion_pool_size}</td>
      <td><span class="score-pill ${scoreClass(row.css_score)}">${row.css_score}</span></td>
      <td>${row.percentile_rank == null ? '<span class="muted" title="Cohort too small (<10 players) for a meaningful percentile">—</span>' : 'P'+row.percentile_rank}</td>
      <td>${smurfCell(row)}</td>
      <td class="lb-actions-cell">
        <span class="star ${watched?'active':''}" data-puuid="${row.puuid}" title="Toggle watchlist">${watched?'★':'☆'}</span>
        <span class="lb-view-arrow" aria-label="Open profile">›</span>
      </td>
    `;
    tbody.appendChild(tr);
  });

  // Whole row is clickable now — open profile unless click hit the star
  // (the star handles its own thing and stops propagation).
  document.querySelectorAll('tr.lb-row').forEach(tr => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('.star')) return;
      window._selectedPuuid = tr.dataset.puuid;
      setView('player');
    });
  });
  document.querySelectorAll('.star').forEach(s =>
    s.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleWatch(s.dataset.puuid, s);
    })
  );
}
function initLeaderboard() {
  document.getElementById('f-apply').addEventListener('click', () => {
    _lbOffset = 0;  // reset to first page when filters change
    loadLeaderboard();
  });

  // Quick-filter pills — one-click presets that map to existing filter
  // controls so the user doesn't have to hunt through 8 dropdowns.
  document.querySelectorAll('.quick-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const k = btn.dataset.quick;
      const prostatus = document.getElementById('f-prostatus');
      const sortSel = document.getElementById('f-sort');
      const maxAge = document.getElementById('f-maxage');
      const contract = document.getElementById('f-contract');
      const region = document.getElementById('f-region');
      const tier = document.getElementById('f-tier');
      const minGames = document.getElementById('f-min');

      if (k === 'reset') {
        prostatus.value = '';
        maxAge.value = '';
        contract.value = '';
        document.getElementById('f-residency').value = '';
        // Keep region default (EUW) since the user explicitly defaults to it
      } else if (k === 'fa') {
        prostatus.value = 'fa';
      } else if (k === 'rising') {
        // No backend rising_only flag wired into f-* yet — fall back to
        // sort by CSS desc on min 10 games to surface the strong recent
        // climbers. (When we wire `rising_only`, swap to setting it.)
        sortSel.value = 'css';
        if (parseInt(minGames.value) < 10) minGames.value = 10;
      } else if (k === 'u21') {
        maxAge.value = '21';
      } else if (k === 'contract90') {
        prostatus.value = 'pro';  // contract filter only makes sense for pros
        contract.value = '90';
      }

      // Visual active state — toggle highlighted class
      document.querySelectorAll('.quick-pill').forEach(b => b.classList.toggle('active', b === btn && k !== 'reset'));
      _lbOffset = 0;
      loadLeaderboard();
    });
  });

  loadLeaderboard();
}

/* ---------------- WATCHLIST ---------------- */
async function loadWatchlist() {
  const data = await API('/watchlist');
  const tbody = document.querySelector('#wl-table tbody');
  tbody.innerHTML = '';
  if (!data.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="muted" style="text-align:center;padding:30px;">No players watched yet. Go to <a href="#" id="lb-link">Ladder</a> and click ☆ next to a name.</td></tr>`;
    document.getElementById('lb-link')?.addEventListener('click', e => { e.preventDefault(); setView('leaderboard'); });
    return;
  }
  data.forEach(row => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${row.summoner_name || '(unknown)'}</strong></td>
      <td>${tierBadge(row.tier)}</td>
      <td>${row.lp ?? '—'}</td>
      <td>${roleIcon(row.meta?.lp_role || row.role)}</td>
      <td>${row.games_played}</td>
      <td>${row.css_score!==null ? `<span class="score-pill ${scoreClass(row.css_score)}">${row.css_score}</span>` : '—'}</td>
      <td>${row.percentile_rank ?? '—'}</td>
      <td><input class="tag-input" data-puuid="${row.puuid}" value="${(row.tag||'').replace(/"/g,'&quot;')}" placeholder="add tag…"/></td>
      <td>${row.added_at ? new Date(row.added_at).toLocaleDateString() : '—'}</td>
      <td>
        <button data-puuid="${row.puuid}" class="secondary view-wl">View</button>
        <button data-puuid="${row.puuid}" class="secondary remove-wl" title="Remove from watchlist">✕</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
  document.querySelectorAll('.view-wl').forEach(b =>
    b.addEventListener('click', () => { window._selectedPuuid = b.dataset.puuid; setView('player'); })
  );
  document.querySelectorAll('.remove-wl').forEach(b =>
    b.addEventListener('click', async () => {
      await fetch(API_BASE + '/watchlist/' + b.dataset.puuid, { method: 'DELETE', headers: { 'Authorization': 'Bearer ' + getToken() } });
      loadWatchlist();
    })
  );
  document.querySelectorAll('.tag-input').forEach(i =>
    i.addEventListener('change', async () => {
      await APIform('/watchlist', { puuid: i.dataset.puuid, tag: i.value });
    })
  );
}
function initWatchlist() { loadWatchlist(); }

/* ---------------- CHAMPIONS ---------------- */
let _champRaw = [];

async function loadChampions() {
  const role = document.getElementById('ch-role').value;
  const sort = document.getElementById('ch-sort').value;
  const minGames = document.getElementById('ch-min').value || 10;
  const params = new URLSearchParams();
  if (role) params.set('role', role);
  params.set('min_total_games', minGames);
  params.set('sort', sort);
  _champRaw = await API('/champions?' + params);
  renderChampionGrid();
}

function renderChampionGrid() {
  const grid = document.getElementById('ch-grid');
  const counter = document.getElementById('ch-counter');
  const search = (document.getElementById('ch-search').value || '').toLowerCase().trim();
  const filtered = search
    ? _champRaw.filter(c => c.champion_name.toLowerCase().includes(search))
    : _champRaw;

  counter.textContent = `${filtered.length} champion${filtered.length>1?'s':''} match — click any card for the player leaderboard.`;

  if (!filtered.length) {
    grid.innerHTML = `<p class="muted" style="text-align:center;padding:30px;">No champions match.</p>`;
    return;
  }

  grid.innerHTML = filtered.slice(0, 240).map(c => `
    <div class="champion-card" data-id="${c.champion_id}" data-role="${c.role}">
      <div class="champion-card-head">
        <img class="champion-icon" src="${c.icon_url}" alt="${c.champion_name}" onerror="this.style.opacity='0.2'"/>
        <div style="flex:1;min-width:0;">
          <div class="champion-card-name">${c.champion_name}</div>
          <div class="champion-card-meta">
            ${roleIcon(c.role, { size: 16 })}
            ${c.latest_patch ? ` · ${c.latest_patch}` : ''}
          </div>
        </div>
      </div>
      <div class="champion-card-stats">
        <div><div class="label">Games</div><div class="value">${c.total_games}</div></div>
        <div><div class="label">Mains</div><div class="value">${c.total_mains}</div></div>
        <div><div class="label">Avg WR</div><div class="value">${c.winrate}%</div></div>
        <div><div class="label">Avg KDA</div><div class="value">${c.avg_kda}</div></div>
      </div>
      <div class="champion-card-css">
        ${c.baselined
          ? `Best Champ-CSS <strong style="color:var(--accent);">${c.max_champ_css}</strong> · avg ${c.avg_champ_css}`
          : `<span class="muted">No baseline yet (need ≥5 mains)</span>`}
      </div>
    </div>
  `).join('');

  grid.querySelectorAll('.champion-card').forEach(card =>
    card.addEventListener('click', () => openChampionModal(card.dataset.id, card.dataset.role))
  );
}

async function openChampionModal(championId, role) {
  const modal = document.getElementById('champ-modal');
  const title = document.getElementById('champ-modal-title');
  const body = document.getElementById('champ-modal-body');
  modal.classList.add('open');
  title.textContent = 'Loading…';
  body.innerHTML = `<p class="muted">Loading top players…</p>`;

  const params = new URLSearchParams();
  if (role) params.set('role', role);
  params.set('limit', 50);
  params.set('min_games', 3);
  const data = await API(`/champions/${championId}?` + params);
  const items = data.items || [];
  title.textContent = `${data.champion_name || 'Champion'} — top ${role || 'all roles'}`;

  // Top-line summary
  const champData = _champRaw.find(c => c.champion_id == championId && c.role === role);

  body.innerHTML = `
    <div class="champ-modal-header">
      <img src="${data.icon_url}" alt="${data.champion_name||''}" onerror="this.style.opacity='0.3'"/>
      <div style="flex:1;">
        <h2 style="margin:0 0 4px;">${data.champion_name || 'Champion'}</h2>
        <div class="muted" style="font-size:12px;">${role ? `Role: ${role}` : 'All roles'} · ${items.length} player${items.length>1?'s':''} with ≥3 games shown</div>
        ${champData ? `
        <div class="stats" style="margin-top:8px;">
          <div><strong>${champData.total_games}</strong> total games</div>
          <div><strong>${champData.total_mains}</strong> distinct mains</div>
          <div><strong>${champData.winrate}%</strong> avg WR</div>
          <div><strong>${champData.avg_kda}</strong> avg KDA</div>
        </div>` : ''}
      </div>
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th><th>Summoner</th><th>Pro</th><th>Team</th><th>Tier</th><th>LP</th>
            <th>Patch</th><th>Games</th><th>WR</th><th>KDA</th><th>Dmg %</th><th>Champ-CSS</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${items.map((p, i) => `
            <tr>
              <td>${i+1}</td>
              <td><strong>${p.summoner_name||'(unknown)'}</strong></td>
              <td>${p.meta ? '<span class="score-pill s-strong">pro</span>' : '<span class="muted">—</span>'}</td>
              <td>${
                p.meta && p.meta.current_team_logo_url
                  ? `<span class="team-cell"><img class="team-logo" src="${p.meta.current_team_logo_url}" onerror="this.style.display='none'"/> <span>${p.meta.current_team}</span></span>`
                  : (p.meta && p.meta.current_team ? p.meta.current_team : '<span class="muted">—</span>')
              }</td>
              <td>${tierBadge(p.tier)}</td>
              <td>${p.lp ?? '—'}</td>
              <td>${p.patch || '—'}</td>
              <td>${p.games}</td>
              <td>${p.winrate}%</td>
              <td>${p.avg_kda}</td>
              <td>${(p.avg_dmg_share*100).toFixed(1)}%</td>
              <td>${p.has_champion_baseline ? `<span class="score-pill ${scoreClass(p.champion_css)}">${p.champion_css}</span>` : '<span class="muted">—</span>'}</td>
              <td><button class="secondary view-from-champ" data-puuid="${p.puuid}">View</button></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
  body.querySelectorAll('.view-from-champ').forEach(b =>
    b.addEventListener('click', () => {
      window._selectedPuuid = b.dataset.puuid;
      modal.classList.remove('open');
      setView('player');
    })
  );
}

function initChampions() {
  document.getElementById('ch-role').addEventListener('change', loadChampions);
  document.getElementById('ch-sort').addEventListener('change', loadChampions);
  document.getElementById('ch-min').addEventListener('change', loadChampions);
  document.getElementById('ch-search').addEventListener('input', () => {
    // debounce-light
    clearTimeout(window._chSearchT);
    window._chSearchT = setTimeout(renderChampionGrid, 150);
  });
  loadChampions();
}

document.getElementById('champ-modal-close').addEventListener('click', () =>
  document.getElementById('champ-modal').classList.remove('open')
);
document.getElementById('champ-modal').addEventListener('click', (e) => {
  if (e.target.id === 'champ-modal') e.currentTarget.classList.remove('open');
});

/* ---------------- PLAYER ---------------- */
function initPlayer() {
  const search = document.getElementById('p-search');
  const suggest = document.getElementById('p-suggest');
  let timer;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (search.value.length < 2) { suggest.innerHTML = ''; return; }
      const data = await API('/players/search?name=' + encodeURIComponent(search.value));
      suggest.innerHTML = data.map(p =>
        `<div data-puuid="${p.puuid}">${p.summoner_name} <span class="muted">(${p.region})</span></div>`
      ).join('');
      suggest.querySelectorAll('div').forEach(d =>
        d.addEventListener('click', () => {
          search.value = d.textContent;
          suggest.innerHTML = '';
          loadPlayer(d.dataset.puuid);
        })
      );
    }, 250);
  });
  if (window._selectedPuuid) loadPlayer(window._selectedPuuid);
}

const RADAR_AXES = ['lane', 'damage', 'vision', 'objective', 'mapplay', 'survival', 'champpool', 'consistency'];

const SOCIAL_ICONS = {
  twitter:    { label: 'Twitter / X', url: u => `https://x.com/${u}` },
  twitch:     { label: 'Twitch',      url: u => `https://twitch.tv/${u}` },
  discord:    { label: 'Discord',     url: u => null },  // discord is just a tag, no link
  instagram:  { label: 'Instagram',   url: u => `https://instagram.com/${u}` },
  facebook:   { label: 'Facebook',    url: u => `https://facebook.com/${u}` },
  leaguepedia:{ label: 'Leaguepedia', url: u => `https://lol.fandom.com/wiki/${u.replace(/ /g,'_')}` },
  gamesoflegends: { label: 'GoL',     url: u => `https://gol.gg/players/player-stats/${u}/` },
  sheep:      { label: 'Sheep',       url: u => null },
};

// Profile icon CDN — Community Dragon serves all icons by ID without needing a patch version
function profileIconUrl(iconId) {
  if (!iconId) return null;
  return `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/profile-icons/${iconId}.jpg`;
}

function flagEmoji(country) {
  if (!country || country.length !== 2) return '';
  const code = country.toUpperCase();
  const A = 0x1F1E6;
  return String.fromCodePoint(A + code.charCodeAt(0) - 65) + String.fromCodePoint(A + code.charCodeAt(1) - 65);
}

function renderRank(rank) {
  if (!rank) return '<span class="muted">—</span>';
  const tier = (rank.tier||'').replace(/^\d+_/, '');
  const div = rank.division || '';
  const lp = rank.league_points ?? 0;
  const wl = (rank.wins != null && rank.losses != null) ? ` (${rank.wins}W / ${rank.losses}L)` : '';
  return `<strong>${tier.toUpperCase()} ${div}</strong> · ${lp} LP${wl}`;
}

function renderProIdentity(meta) {
  const social = meta.social_media || {};
  const links = Object.entries(social)
    .filter(([_, v]) => v)
    .map(([k, v]) => {
      const cfg = SOCIAL_ICONS[k] || { label: k, url: () => null };
      const url = cfg.url ? cfg.url(v) : null;
      const inner = `<span class="social-icon">${cfg.label[0]}</span> ${cfg.label}: <strong>${v}</strong>`;
      return url
        ? `<a href="${url}" target="_blank" rel="noopener" class="social-link">${inner}</a>`
        : `<span class="social-link">${inner}</span>`;
    });

  const prev = (meta.previous_teams || []).slice(0, 8);
  const accounts = meta.accounts || [];
  const primaryAcc = accounts[0];
  // Riot in-game profile icon (we don't surface Leaguepedia headshots —
  // unreliable URLs, often outdated, no upside vs the player's actual icon).
  const iconUrl = primaryAcc ? profileIconUrl(primaryAcc.profile_icon_id) : null;

  return `
    <div class="card pro-identity">
      <div class="pro-identity-header">
        ${iconUrl ? `<img class="pro-photo" src="${iconUrl}" onerror="this.style.display='none'" alt="${meta.leaguepedia_id||''}"/>` : '<div class="pro-photo placeholder">?</div>'}
        <div style="flex:1">
          <h3 style="margin:0 0 4px;font-size:20px;">${meta.leaguepedia_id || meta.lolpros_slug || '?'} ${flagEmoji(meta.country)}</h3>
          <div class="muted" style="font-size:12px;">
            ${meta.lp_role ? `${meta.lp_role} · ` : ''}
            ${meta.current_team ? meta.current_team : '<em>Free agent</em>'}
            ${meta.in_game ? ' · <span class="score-pill s-elite" title="Currently rostered as a player">in game</span>' : ''}
          </div>
          ${(meta.other_countries || []).length ? `
            <div class="muted" style="font-size:11px;margin-top:4px;">
              Eligibility: ${[meta.country, ...(meta.other_countries||[])].filter(Boolean).map(c => flagEmoji(c) + ' ' + c).join(' · ')}
            </div>` : ''}
        </div>
        ${meta.lolpros_url ? `<a href="${meta.lolpros_url}" target="_blank" rel="noopener" class="lolpros-link" title="View on Lolpros">Lolpros ↗</a>` : ''}
      </div>

      <div class="grid-3 pro-identity-body">
        <div>
          <h4 class="muted-h4">Career path</h4>
          ${prev.length === 0 ? '<p class="muted" style="font-size:12px;">No prior teams on record.</p>' : `
          <div class="team-history">
            ${prev.map(pt => `
              <div class="team-history-row">
                ${pt.logo_url ? `<img class="team-logo" src="${pt.logo_url}" alt="${pt.tag||''}" onerror="this.style.display='none'"/>` : ''}
                <div class="team-history-info">
                  <strong>${pt.name||'?'}</strong>
                  <div class="muted">${pt.join_date ? pt.join_date.slice(0,7) : '?'} → ${pt.leave_date ? pt.leave_date.slice(0,7) : 'present'}</div>
                </div>
              </div>
            `).join('')}
          </div>`}
        </div>

        <div>
          <h4 class="muted-h4">Social media</h4>
          ${links.length === 0 ? '<p class="muted" style="font-size:12px;">No public social links.</p>' : `<div class="social-list">${links.join('')}</div>`}
        </div>

        <div>
          <h4 class="muted-h4">Personal & contract</h4>
          ${meta.age ? `<div class="stat-row"><span class="label">Age</span><span class="value">${meta.age}</span></div>` : ''}
          ${meta.country ? `<div class="stat-row"><span class="label">Nationality</span><span class="value">${flagEmoji(meta.country)} ${meta.country}</span></div>` : ''}
          ${meta.residency ? `<div class="stat-row"><span class="label">Residency</span><span class="value">${meta.residency}</span></div>` : ''}
          ${meta.contract_end
            ? `<div class="stat-row"><span class="label">Contract ends</span><span class="value">${meta.contract_end}</span></div>`
            : `<div class="stat-row"><span class="label">Contract end</span><span class="value muted" title="Lolpros doesn't expose contract dates publicly. Run Sync Leaguepedia for cases where this is in their wiki.">unknown</span></div>`}
          ${meta.score ? `<div class="stat-row" title="Lolpros' internal MMR score (peak)"><span class="label">Lolpros score</span><span class="value">${meta.score}</span></div>` : ''}
        </div>
      </div>

      ${accounts.length ? `
      <div class="pro-identity-accounts">
        <h4 class="muted-h4" style="margin-bottom:8px;">Tracked accounts (${accounts.length})</h4>
        <div class="account-grid">
          ${accounts.map(acc => `
            <div class="account-card">
              <div class="account-header">
                <span class="role-tag">${acc.server || '?'}</span>
                <strong>${acc.summoner_name || (acc.gamename + (acc.tagline?'#'+acc.tagline:'')) || '?'}</strong>
              </div>
              <div class="stat-row" title="Current rank"><span class="label">Now</span><span class="value">${renderRank(acc.rank)}</span></div>
              <div class="stat-row" title="All-time peak rank"><span class="label">Peak</span><span class="value">${renderRank(acc.peak)}</span></div>
              ${(acc.summoner_names_history || []).length ? `
              <div class="stat-row" title="Past Riot IDs Lolpros has tracked on this account">
                <span class="label">Old IGNs</span>
                <span class="value" style="font-size:11px;text-align:right;">${acc.summoner_names_history.slice(0,3).join(', ')}${acc.summoner_names_history.length>3?'…':''}</span>
              </div>` : ''}
            </div>
          `).join('')}
        </div>
      </div>` : ''}

      ${(meta.leagues || []).length ? `
      <div style="margin-top:14px;">
        <h4 class="muted-h4">Active leagues this season</h4>
        <div class="league-row">
          ${meta.leagues.map(lg => `
            <div class="league-pill" title="${lg.name}">
              ${lg.logo_url ? `<img src="${lg.logo_url}" class="league-logo" onerror="this.style.display='none'"/>` : ''}
              <span>${lg.shorthand || lg.name || '?'}</span>
            </div>
          `).join('')}
        </div>
      </div>` : ''}
    </div>
  `;
}

async function loadPlayer(puuid) {
  const data = await API('/players/' + puuid);
  const c = document.getElementById('p-content');
  const p = data.player;
  const agg = data.aggregates[0];
  if (!agg) {
    c.innerHTML = `<div class="card"><h3>${p.summoner_name}</h3><p class="muted">No aggregated data yet for this player.</p></div>`;
    return;
  }
  const cats = (agg.breakdown && agg.breakdown.categories) || {};
  const stats = agg.stats;
  const watched = data.is_watched;

  const meta = p.meta;
  let teamFragment = '<em>FA</em>';
  if (meta && meta.current_team) {
    const logo = meta.current_team_logo_url;
    teamFragment = logo
      ? `<img class="team-logo" src="${logo}" alt="${meta.current_team_tag || ''}" onerror="this.style.display='none'"/> ${meta.current_team}`
      : meta.current_team;
  }
  const metaLine = meta
    ? `<span class="muted">·</span> ${proBadge(p)} <span class="muted">·</span> ${teamFragment}${meta.age?` · ${meta.age}y`:''}${meta.country?` · ${meta.country}`:''}${meta.residency?` · ${meta.residency} residency`:''}${meta.contract_end?` · contract ends ${meta.contract_end}`:''}${meta.leaguepedia_url?` · <a href="${meta.leaguepedia_url}" target="_blank" rel="noopener" style="color:var(--accent);">Leaguepedia ↗</a>`:''}`
    : '<span class="muted">· no pro entry (amateur or unmatched)</span>';

  // Header avatar: Riot in-game profile icon only (cleaner, more reliable
  // than scraped Leaguepedia headshots which often 404 or are outdated).
  const primaryAccount = (meta && (meta.accounts || [])[0]) || null;
  const headerAvatarUrl = primaryAccount ? profileIconUrl(primaryAccount.profile_icon_id) : null;
  const headerAvatar = headerAvatarUrl
    ? `<img class="header-avatar" src="${headerAvatarUrl}" alt="" onerror="this.style.display='none'"/>`
    : '<div class="header-avatar placeholder">?</div>';

  c.innerHTML = `
    <div class="player-header">
      <div style="display:flex;align-items:center;gap:14px;flex:1;min-width:0;">
        ${headerAvatar}
        <div style="flex:1;min-width:0;">
          <h2 style="margin:0 0 2px;">${p.summoner_name} ${smurfBadge(p)} <span class="star ${watched?'active':''}" id="profile-star" data-puuid="${puuid}" style="font-size:22px;margin-left:8px;">${watched?'★':'☆'}</span> <button id="smurf-label-btn" class="secondary" style="margin-left:6px;font-size:11px;padding:4px 10px;" title="Manually label this player as a smurf (or NOT a smurf)">👁 Smurf?</button></h2>
          <div class="muted">${regionBadge(p.region)} · ${tierBadge(p.tier)} ${p.lp != null ? p.lp + ' LP' : ''} · Account lvl ${p.account_level || '?'}</div>
          <div style="margin-top:6px;font-size:13px;">${metaLine}</div>
        </div>
      </div>
      <div style="text-align:right">
        <div style="font-size:32px;font-weight:800;">${agg.css_score}</div>
        <span class="score-pill ${scoreClass(agg.css_score)}">${scoreLabel(agg.css_score)}</span>
        <div class="muted" style="margin-top:4px;">P${agg.percentile_rank} · ${agg.role} · ${agg.games_played} games · ${agg.winrate}% WR</div>
        <div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px;">
          <button id="export-md" class="export-btn" style="font-size:11px;padding:6px 12px;" title="Download a clean Markdown dossier of this player — paste in Notion/Discord/Slack or share with staff.">📋 Markdown</button>
          <button id="export-pdf" class="export-btn" style="font-size:11px;padding:6px 12px;" title="Print to PDF via the browser (Ctrl+P → Save as PDF)">🖨 PDF</button>
        </div>
      </div>
    </div>

    <div class="tabs">
      <button class="tab active" data-tab="soloq">SoloQ</button>
      <button class="tab" data-tab="tournament">Tournament</button>
      <button class="tab" data-tab="roster">vs LEC ${agg.role}</button>
    </div>
    <div id="tab-soloq" class="tab-pane">

    <div class="card" id="css-history-card">
      <h3>📈 CSS trend <span class="muted" style="font-size:11px;font-weight:400;">evolution across patches — line per role</span> <span id="css-history-delta" style="font-size:12px;font-weight:400;margin-left:8px;"></span></h3>
      <div id="css-history-empty" class="muted" style="font-size:12px;display:none;"></div>
      <canvas id="css-history-chart" height="180"></canvas>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>CSS radar — ${agg.role} (patch ${agg.patch})</h3>
        <canvas id="radar" height="280"></canvas>
      </div>
      <div class="card">
        <h3>Aggregate stats <a href="#" class="muted" id="open-glossary-2" style="font-size:11px;font-weight:400;text-decoration:none;">📖 explain</a></h3>
        <div class="stat-row" title="Gold differential at 15min vs same-role opponent. Lane dominance proxy."><span class="label">GD@15</span><span class="value">${stats.gd15}</span></div>
        <div class="stat-row" title="XP differential at 15min — captures level leads from roams"><span class="label">XPD@15</span><span class="value">${stats.xpd15}</span></div>
        <div class="stat-row" title="CS differential at 15min — pure laning skill"><span class="label">CSD@15</span><span class="value">${stats.csd15}</span></div>
        <div class="stat-row" title="Creep score per minute — farming consistency"><span class="label">CS / min</span><span class="value">${stats.cspm}</span></div>
        <div class="stat-row" title="Damage to champions per minute"><span class="label">DPM</span><span class="value">${stats.dpm}</span></div>
        <div class="stat-row" title="Share of team's total damage to champions"><span class="label">Damage share</span><span class="value">${(stats.dmg_share*100).toFixed(1)}%</span></div>
        <div class="stat-row" title="(kills + assists) / team kills"><span class="label">Kill participation</span><span class="value">${(stats.kp*100).toFixed(1)}%</span></div>
        <div class="stat-row" title="(kills + assists) / max(deaths, 1)"><span class="label">KDA</span><span class="value">${stats.kda}</span></div>
        <div class="stat-row" title="Vision score per minute"><span class="label">Vision / min</span><span class="value">${stats.vspm}</span></div>
        <div class="stat-row" title="Wards placed per minute"><span class="label">Wards placed / min</span><span class="value">${stats.wpm}</span></div>
        <div class="stat-row" title="Kills with no assistants — 1v1 outplays"><span class="label">Solo kills / game</span><span class="value">${stats.solo_kills}</span></div>
        <div class="stat-row" title="Deaths before 14:00 — laning mistakes / overextends"><span class="label">Early deaths / game</span><span class="value">${stats.early_deaths}</span></div>
        <div class="stat-row" title="Distinct champions with ≥3 games on the sample"><span class="label">Champion pool (≥3 games)</span><span class="value">${stats.champion_pool_size}</span></div>
      </div>
    </div>

    ${meta && meta.is_pro ? renderProIdentity(meta) : ''}

    <div class="grid-2">
      <div class="card">
        <h3>Champion pool</h3>
        <p class="muted" style="margin-top:0;font-size:11px;">Champ-CSS = score vs same-champion Challenger baseline (≥10 mains required). "—" = not enough data to baseline.</p>
        <table>
          <thead><tr><th>Champion</th><th>Games</th><th>WR</th><th>KDA</th><th>KP</th><th>GD@15</th><th>Dmg %</th><th>Champ CSS</th></tr></thead>
          <tbody>
            ${data.champion_pool.slice(0,10).map(cp => `
              <tr>
                <td><strong>${cp.champion_name}</strong></td>
                <td>${cp.games}</td>
                <td>${cp.winrate}%</td>
                <td>${cp.avg_kda}</td>
                <td>${cp.avg_kp != null ? (cp.avg_kp*100).toFixed(0)+'%' : '—'}</td>
                <td class="${cp.avg_gd15>=0?'delta-pos':'delta-neg'}">${cp.avg_gd15 ?? '—'}</td>
                <td>${(cp.avg_dmg_share*100).toFixed(1)}%</td>
                <td>${cp.champion_css != null ? `<span class="score-pill ${scoreClass(cp.champion_css)}">${cp.champion_css}</span>` : '<span class="muted">—</span>'}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      <div class="card">
        <h3>Recent matches <span class="muted" style="font-size:11px;font-weight:400;">click any row to open the deep-dive</span></h3>
        <table>
          <thead><tr><th>Champ</th><th>Role</th><th>K/D/A</th><th>GD@15</th><th>Dmg %</th><th>VS</th><th>W</th></tr></thead>
          <tbody>
            ${data.recent_matches.slice(0,15).map(r => `
              <tr class="match-row" data-mid="${r.match_id}" style="cursor:pointer;">
                <td>${r.champion_name}</td>
                <td>${roleIcon(r.role, { size: 16 })}</td>
                <td>${r.kills}/${r.deaths}/${r.assists}</td>
                <td class="${r.gd15>=0?'delta-pos':'delta-neg'}">${r.gd15}</td>
                <td>${(r.dmg_share*100).toFixed(1)}%</td>
                <td>${r.vision_score}</td>
                <td>${r.win ? '<span class="delta-pos">W</span>' : '<span class="delta-neg">L</span>'}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card" id="matchup-card">
      <h3>vs Champion <span class="muted" style="font-size:11px;font-weight:400;">opponent same role · sortable by games / WR / GD@15</span></h3>
      <p class="muted" style="margin-top:0;font-size:11px;">Loading matchups…</p>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>Score breakdown <a href="#" class="muted" id="open-glossary-3" style="font-size:11px;font-weight:400;text-decoration:none;">📖 explain</a></h3>
        <p class="muted" style="margin-top:0;font-size:11px;">8 categories scored 0-100 vs Challenger pool. 50 = par with median. Bar shows category score; CSS = weighted sum (weights vary by role).</p>
        ${RADAR_AXES.map(k => `
          <div class="bar-row" title="${k} category">
            <span class="lab">${k}</span>
            <div class="bar"><span style="width:${(cats[k]||0).toFixed(0)}%"></span></div>
            <span class="num">${(cats[k]||0).toFixed(0)}</span>
          </div>
        `).join('')}
        <p class="muted" style="margin-top:10px;">Sample factor: ${agg.breakdown?.sample_factor?.toFixed(2) ?? '—'} · Smurf factor: ${agg.breakdown?.smurf_factor?.toFixed(2) ?? '—'} · Lobby factor: ${agg.breakdown?.lobby_factor?.toFixed(2) ?? '—'}</p>
      </div>
      <div class="card">
        <h3>🚨 Smurf signals <span class="muted" style="font-size:11px;font-weight:400;">multi-signal alt-account detector</span></h3>
        ${smurfBreakdownHTML(p)}
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>Scout notes</h3>
        <div id="notes-list" class="note-list"></div>
        <div class="note-input">
          <textarea id="note-content" placeholder="Add a private note about this player…"></textarea>
        </div>
        <div style="text-align:right;margin-top:8px;">
          <button id="add-note">Save note</button>
        </div>
      </div>
    </div>
    </div>

    <div id="tab-tournament" class="tab-pane" style="display:none">
      <div class="card"><p class="muted">Loading tournament data…</p></div>
    </div>

    <div id="tab-roster" class="tab-pane" style="display:none">
      <div class="card"><p class="muted">Loading LEC roster comparison…</p></div>
    </div>
  `;

  // Inline "explain" links (multiple, namespaced ids would be cleaner — for MVP we just attach to all)
  document.querySelectorAll('[id^="open-glossary-"]').forEach(el => {
    el.addEventListener('click', e => { e.preventDefault(); openGlossary(); });
  });

  // Tab switching
  document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      document.querySelectorAll('.tab-pane').forEach(x => x.style.display = 'none');
      document.getElementById('tab-' + t.dataset.tab).style.display = 'block';
      if (t.dataset.tab === 'tournament') loadTournamentTab(puuid);
      if (t.dataset.tab === 'roster') loadRosterTab(puuid, agg.role);
    });
  });

  // Star toggle on profile
  document.getElementById('profile-star').addEventListener('click', async (e) => {
    await toggleWatch(puuid, e.target);
  });

  // Smurf labeling — quick 3-state toggle (smurf / not / clear)
  // Loads current state on demand and shows a chooser
  const smurfBtn = document.getElementById('smurf-label-btn');
  if (smurfBtn) {
    refreshSmurfButton(puuid, smurfBtn);
    smurfBtn.addEventListener('click', () => openSmurfLabelDialog(puuid, smurfBtn));
  }

  // vs Champion matchup card — lazy load (SQL is light, no need to defer further)
  loadMatchups(puuid, agg.role).catch(err => {
    const card = document.getElementById('matchup-card');
    if (card) card.querySelector('p').textContent = 'Failed to load matchups: ' + err.message;
  });

  // Recent matches click → deep-dive modal
  document.querySelectorAll('.match-row').forEach(tr =>
    tr.addEventListener('click', () => openMatchModal(tr.dataset.mid))
  );

  // Export PDF — switch to soloq tab first so the printed page has full content,
  // then trigger native browser print (user picks "Save as PDF" in dialog).
  document.getElementById('export-pdf').addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'soloq'));
    document.querySelectorAll('.tab-pane').forEach(x => x.style.display = 'none');
    document.getElementById('tab-soloq').style.display = 'block';
    setTimeout(() => window.print(), 250);
  });

  // Export Markdown — fetch the dossier and trigger a download. Uses
  // fetch() directly (instead of API()) because we want the raw blob,
  // not a JSON-parsed body, and we need to forward the Authorization
  // header that API() injects.
  document.getElementById('export-md').addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    const originalLabel = btn.textContent;
    btn.textContent = '⏳ Building…';
    btn.disabled = true;
    try {
      const resp = await fetch(API_BASE + '/players/' + puuid + '/dossier', {
        headers: { 'Authorization': 'Bearer ' + getToken() },
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const blob = await resp.blob();
      // Try to honor the server's filename from Content-Disposition,
      // fallback to a sane default if not present.
      let filename = `dossier-${(p.summoner_name || 'player').split('#')[0].replace(/\s+/g, '_')}.md`;
      const cd = resp.headers.get('content-disposition') || '';
      const m = cd.match(/filename="([^"]+)"/);
      if (m) filename = m[1];
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert('Markdown export failed: ' + e.message);
    } finally {
      btn.textContent = originalLabel;
      btn.disabled = false;
    }
  });

  // CSS history (best-effort — silent fail if no snapshots yet)
  loadCssHistory(puuid).catch(() => {});

  // Notes
  loadNotes(puuid);
  document.getElementById('add-note').addEventListener('click', async () => {
    const content = document.getElementById('note-content').value.trim();
    if (!content) return;
    await APIform('/notes/' + puuid, { content });
    document.getElementById('note-content').value = '';
    loadNotes(puuid);
  });

  // Radar
  new Chart(document.getElementById('radar'), {
    type: 'radar',
    data: {
      labels: RADAR_AXES.map(s => s[0].toUpperCase()+s.slice(1)),
      datasets: [{
        label: p.summoner_name,
        data: RADAR_AXES.map(k => cats[k] || 0),
        backgroundColor: 'rgba(245,158,11,0.22)',
        borderColor: '#f59e0b',
        pointBackgroundColor: '#34d399',
      },{
        label: 'Challenger median (50)',
        data: RADAR_AXES.map(() => 50),
        backgroundColor: 'rgba(138,143,153,0.05)',
        borderColor: 'rgba(138,143,153,0.5)',
        borderDash: [4,4],
        pointRadius: 0,
      }]
    },
    options: {
      scales: { r: { min: 0, max: 100, grid:{color:'#2a2e37'}, angleLines:{color:'#2a2e37'}, pointLabels:{color:'#ebeced'}, ticks:{display:false} } },
      plugins: { legend: { labels: { color: '#ebeced' } } },
    }
  });
}

let _cssHistoryChart = null;
async function loadCssHistory(puuid) {
  const card = document.getElementById('css-history-card');
  if (!card) return;
  const empty = document.getElementById('css-history-empty');
  const canvas = document.getElementById('css-history-chart');
  const deltaEl = document.getElementById('css-history-delta');
  if (deltaEl) deltaEl.innerHTML = '';

  let data;
  try {
    data = await API('/players/' + puuid + '/history');
  } catch (e) {
    if (empty) { empty.style.display = 'block'; empty.textContent = 'Failed to load history: ' + e.message; }
    if (canvas) canvas.style.display = 'none';
    return;
  }
  const byRole = data.by_role || {};
  const roles = Object.keys(byRole);

  // Empty / insufficient data state — show the card with a friendly hint
  // instead of silently hiding it. This was making the "trend" feature
  // invisible to users who had only one patch on record.
  if (!roles.length || data.patches_count < 2) {
    if (canvas) canvas.style.display = 'none';
    if (empty) {
      empty.style.display = 'block';
      const n = data.patches_count || 0;
      empty.textContent = `Need 2+ patches of data to draw a trend — currently ${n} patch(es) on record. Snapshots are appended on every ladder ingest.`;
    }
    return;
  }

  if (canvas) canvas.style.display = 'block';
  if (empty) empty.style.display = 'none';
  // Build the union of all patches across roles for the X axis
  const patchOrder = [];
  const seen = new Set();
  roles.forEach(r => byRole[r].forEach(p => {
    if (!seen.has(p.patch)) { seen.add(p.patch); patchOrder.push(p.patch); }
  }));
  // Sort patches by their first snapshot timestamp
  const firstSeen = {};
  roles.forEach(r => byRole[r].forEach(p => {
    if (firstSeen[p.patch] === undefined) firstSeen[p.patch] = p.snapshot_at || '';
  }));
  patchOrder.sort((a, b) => (firstSeen[a] || '').localeCompare(firstSeen[b] || ''));

  // One dataset per role, aligned on patchOrder
  const ROLE_COLORS = { TOP:'#f59e0b', JGL:'#34d399', MID:'#60a5fa', ADC:'#f87171', SUP:'#a78bfa' };
  const datasets = roles.map(r => {
    const byPatch = Object.fromEntries(byRole[r].map(s => [s.patch, s]));
    return {
      label: r,
      data: patchOrder.map(p => byPatch[p] ? byPatch[p].css : null),
      borderColor: ROLE_COLORS[r] || '#ebeced',
      backgroundColor: (ROLE_COLORS[r] || '#ebeced') + '22',
      tension: 0.25,
      spanGaps: true,
      pointRadius: 4,
      pointHoverRadius: 6,
    };
  });

  if (_cssHistoryChart) _cssHistoryChart.destroy();
  _cssHistoryChart = new Chart(document.getElementById('css-history-chart'), {
    type: 'line',
    data: { labels: patchOrder, datasets },
    options: {
      scales: {
        x: { grid: { color: '#2a2e37' }, ticks: { color: '#8a8f99' }, title: { display: true, text: 'Patch', color: '#8a8f99' } },
        y: { min: 0, max: 100, grid: { color: '#2a2e37' }, ticks: { color: '#ebeced' }, title: { display: true, text: 'CSS', color: '#8a8f99' } },
      },
      plugins: {
        legend: { labels: { color: '#ebeced' } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const r = ctx.dataset.label;
              const s = (byRole[r] || []).find(x => x.patch === ctx.label);
              if (!s) return `${r}: ${ctx.parsed.y}`;
              return `${r}: CSS ${s.css} · P${s.percentile} · ${s.games} games`;
            },
          },
        },
      },
    },
  });

  // Headline delta — biggest CSS swing across the player's roles. A
  // prospect that jumped 50→70 in 3 patches gets a green badge here.
  if (deltaEl) {
    let bestDelta = 0;
    let bestRole = null;
    let bestPath = null;
    roles.forEach(r => {
      const series = byRole[r].filter(s => s.css != null);
      if (series.length < 2) return;
      const first = series[0].css;
      const last = series[series.length - 1].css;
      const d = last - first;
      if (Math.abs(d) > Math.abs(bestDelta)) {
        bestDelta = d;
        bestRole = r;
        bestPath = `${first.toFixed(0)} → ${last.toFixed(0)}`;
      }
    });
    if (bestRole && Math.abs(bestDelta) >= 5) {
      const arrow = bestDelta > 0 ? '↗' : '↘';
      const cls = bestDelta > 0 ? 'delta-pos' : 'delta-neg';
      deltaEl.innerHTML = `<span class="${cls}">${arrow} ${bestDelta > 0 ? '+' : ''}${bestDelta.toFixed(0)} CSS</span> on ${bestRole} (${bestPath})`;
    } else if (bestRole) {
      deltaEl.innerHTML = `<span class="muted">stable on ${bestRole} (${bestPath})</span>`;
    }
  }
}

async function loadNotes(puuid) {
  const list = document.getElementById('notes-list');
  if (!list) return;
  const notes = await API('/notes/' + puuid);
  if (!notes.length) {
    list.innerHTML = `<p class="muted" style="font-size:12px;">No notes yet.</p>`;
    return;
  }
  list.innerHTML = notes.map(n => `
    <div class="note">
      <div class="meta">
        <span>${new Date(n.created_at).toLocaleString()}</span>
        <span class="delete" data-id="${n.id}">✕</span>
      </div>
      <div>${n.content.replace(/</g,'&lt;')}</div>
    </div>
  `).join('');
  list.querySelectorAll('.delete').forEach(d =>
    d.addEventListener('click', async () => {
      await fetch(API_BASE + '/notes/' + d.dataset.id, { method: 'DELETE', headers: { 'Authorization': 'Bearer ' + getToken() } });
      loadNotes(puuid);
    })
  );
}

/* ---------------- COMPARE ---------------- */
function initCompare() {
  // Search-as-you-type compare picker
  let _cmpRoster = [];   // [{puuid, summoner_name, tier, lp, age, ...}]
  let _cmpRadar = null;
  const search = document.getElementById('cmp-search');
  const suggest = document.getElementById('cmp-suggest');
  const chipsEl = document.getElementById('cmp-chips');

  function renderChips() {
    chipsEl.innerHTML = _cmpRoster.length === 0
      ? '<span class="muted" style="font-size:12px;">No players yet — search above and click a result to add (max 5).</span>'
      : _cmpRoster.map((r, i) => `
          <span class="team-pill" style="font-size:12px;padding:5px 10px;background:var(--card-2);border-color:var(--accent);">
            ${r.summoner_name} <span class="muted" style="margin-left:6px;cursor:pointer;" data-idx="${i}">✕</span>
          </span>`).join('');
    chipsEl.querySelectorAll('[data-idx]').forEach(el =>
      el.addEventListener('click', () => {
        _cmpRoster.splice(+el.dataset.idx, 1);
        renderChips();
        runCompare();
      })
    );
  }

  let _searchTimer = null;
  search.addEventListener('input', () => {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(async () => {
      const q = search.value.trim();
      if (q.length < 2) { suggest.innerHTML = ''; return; }
      const data = await API('/players/search?q=' + encodeURIComponent(q));
      const items = data.slice(0, 8);
      suggest.innerHTML = items.map(p =>
        `<div data-puuid="${p.puuid}" data-name="${p.summoner_name}">${p.summoner_name} <span class="muted" style="font-size:11px;">${p.tier || ''}</span></div>`
      ).join('');
      suggest.querySelectorAll('div').forEach(d => d.addEventListener('click', () => {
        if (_cmpRoster.find(r => r.puuid === d.dataset.puuid)) return;
        if (_cmpRoster.length >= 5) { alert('Max 5 players in compare.'); return; }
        _cmpRoster.push({ puuid: d.dataset.puuid, summoner_name: d.dataset.name });
        suggest.innerHTML = '';
        search.value = '';
        renderChips();
        runCompare();
      }));
    }, 200);
  });
  document.getElementById('cmp-role').addEventListener('change', runCompare);

  async function runCompare() {
    const div = document.getElementById('cmp-result');
    if (_cmpRoster.length < 1) { div.innerHTML = ''; return; }
    const role = document.getElementById('cmp-role').value;
    const params = new URLSearchParams();
    _cmpRoster.forEach(r => params.append('puuid', r.puuid));
    if (role) params.set('role', role);
    const data = await API('/compare?' + params);
    if (!data.length) { div.innerHTML = '<div class="card"><p class="muted">No comparable data — try removing the role filter.</p></div>'; return; }
    renderCompare(data);
  }

  function renderCompare(data) {
    const div = document.getElementById('cmp-result');
    // Metrics where higher = better
    const HIGHER_IS_BETTER = new Set([
      'css_score','percentile_rank','winrate','games_played',
      'gd15','xpd15','csd15','cspm','dmg_share','dpm','kp','kda','vspm','wpm','solo_kills','champion_pool_size','lp'
    ]);
    function bestIdx(values) {
      // Return index of the max (or null if all undefined)
      let best = null, bv = -Infinity;
      values.forEach((v, i) => {
        if (v == null || isNaN(v)) return;
        if (v > bv) { bv = v; best = i; }
      });
      return best;
    }
    function worstIdx(values) {
      let worst = null, wv = Infinity;
      values.forEach((v, i) => {
        if (v == null || isNaN(v)) return;
        if (v < wv) { wv = v; worst = i; }
      });
      return worst;
    }
    function metricRow(label, key, fmt = (v) => v) {
      const values = data.map(d => key in d.stats ? d.stats[key] : d[key]);
      const best = HIGHER_IS_BETTER.has(key) ? bestIdx(values) : null;
      const worst = HIGHER_IS_BETTER.has(key) ? worstIdx(values) : null;
      return `
        <tr>
          <td><strong>${label}</strong></td>
          ${values.map((v, i) => {
            const cls = v == null ? 'muted' : (i === best ? 'delta-pos' : i === worst && data.length > 1 ? 'delta-neg' : '');
            return `<td class="${cls}">${v == null ? '—' : fmt(v)}</td>`;
          }).join('')}
        </tr>`;
    }
    function metaHeader(d) {
      return `
        <th style="vertical-align:top;min-width:160px;">
          <div style="font-size:13px;font-weight:700;text-transform:none;letter-spacing:0;color:var(--text);">${d.summoner_name}</div>
          <div class="muted" style="font-size:11px;font-weight:500;text-transform:none;letter-spacing:0;margin-top:3px;">
            ${d.tier ? tierBadge(d.tier, { size: 18 }) : ''}
            ${d.lp != null ? d.lp + ' LP' : ''}
            ${d.age ? '· '+d.age+'y' : ''}
            ${d.current_team_tag ? '· '+d.current_team_tag : ''}
            ${d.is_rising_star ? '· 🚀' : ''}
          </div>
        </th>`;
    }

    div.innerHTML = `
      <div class="grid-2">
        <div class="card">
          <h3>Radar — relative to peer max</h3>
          <canvas id="cmp-radar" height="280"></canvas>
        </div>
        <div class="card">
          <h3>Headline scores</h3>
          <div class="table-wrap">
            <table class="compare-table">
              <thead><tr><th>Metric</th>${data.map(metaHeader).join('')}</tr></thead>
              <tbody>
                ${metricRow('CSS', 'css_score', v => v.toFixed(1))}
                ${metricRow('Percentile', 'percentile_rank', v => 'P' + v)}
                ${metricRow('Games', 'games_played')}
                ${metricRow('Winrate', 'winrate', v => v + '%')}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <h3>Detailed stats <span class="muted" style="font-size:11px;font-weight:400;">green = best of group · red = worst</span></h3>
        <div class="table-wrap">
          <table class="compare-table">
            <thead><tr><th>Metric</th>${data.map(metaHeader).join('')}</tr></thead>
            <tbody>
              ${metricRow('GD@15',     'gd15')}
              ${metricRow('XPD@15',    'xpd15')}
              ${metricRow('CSD@15',    'csd15')}
              ${metricRow('CS / min',  'cspm', v => v.toFixed(2))}
              ${metricRow('Dmg share', 'dmg_share', v => (v*100).toFixed(1) + '%')}
              ${metricRow('DPM',       'dpm')}
              ${metricRow('KP',        'kp', v => (v*100).toFixed(0) + '%')}
              ${metricRow('KDA',       'kda', v => v.toFixed(2))}
              ${metricRow('VS / min',  'vspm', v => v.toFixed(2))}
              ${metricRow('Wards / min', 'wpm', v => v.toFixed(2))}
              ${metricRow('Solo kills', 'solo_kills', v => v.toFixed(2))}
              ${metricRow('Champ pool', 'champion_pool_size')}
            </tbody>
          </table>
        </div>
      </div>
    `;

    // Radar (8 axes — clamped to player-set max so the shape reflects relative strength)
    const metrics = ['gd15','xpd15','dmg_share','kp','kda','vspm','solo_kills','cspm'];
    const palette = ['#5b8def','#22d3a4','#f5a524','#a78bfa','#ef4444'];
    const max = metrics.map(m => Math.max(...data.map(d => Math.abs(d.stats[m]||0)), 1));
    if (_cmpRadar) _cmpRadar.destroy();
    _cmpRadar = new Chart(document.getElementById('cmp-radar'), {
      type: 'radar',
      data: {
        labels: metrics,
        datasets: data.map((d,i) => ({
          label: d.summoner_name,
          data: metrics.map((m,j) => 50 + ((d.stats[m]||0)/max[j])*40),
          backgroundColor: palette[i] + '22',
          borderColor: palette[i],
          pointBackgroundColor: palette[i],
          borderWidth: 2,
        }))
      },
      options: {
        scales: {
          r: {
            min: 0, max: 100,
            grid: { color: 'rgba(255,255,255,0.06)' },
            angleLines: { color: 'rgba(255,255,255,0.08)' },
            pointLabels: { color: '#b6bcc8', font: { size: 11 } },
            ticks: { display: false },
          }
        },
        plugins: { legend: { labels: { color: '#e7eaf0' } } },
      }
    });
  }

  renderChips();
}

/* ---------------- ALERTS ---------------- */
function initAlerts() {
  async function refresh() {
    const data = await API('/alerts/rules');
    const rules = data.rules || [];
    const list = document.getElementById('al-rules');
    if (!rules.length) {
      list.innerHTML = '<p class="muted" style="font-size:12px;">No rules yet — create one below.</p>';
    } else {
      list.innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>Name</th><th>Conditions</th><th>Last fired</th><th>Status</th><th></th>
            </tr></thead>
            <tbody>
              ${rules.map(r => `
                <tr data-id="${r.id}">
                  <td><strong>${r.name}</strong></td>
                  <td><code style="font-size:11px;color:var(--accent-2);">${JSON.stringify(r.conditions)}</code></td>
                  <td>${r.last_fired_at ? new Date(r.last_fired_at).toLocaleString() : '<span class="muted">never</span>'}</td>
                  <td>${r.enabled ? '<span class="score-pill s-elite">enabled</span>' : '<span class="score-pill s-weak">disabled</span>'}</td>
                  <td>
                    <button class="secondary al-test" data-id="${r.id}" style="font-size:11px;padding:4px 9px;">Test</button>
                    <button class="secondary al-toggle" data-id="${r.id}" data-enabled="${r.enabled}" style="font-size:11px;padding:4px 9px;">${r.enabled?'Disable':'Enable'}</button>
                    <button class="secondary al-delete" data-id="${r.id}" style="font-size:11px;padding:4px 9px;color:var(--danger);">Delete</button>
                  </td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>`;
      list.querySelectorAll('.al-test').forEach(b => b.addEventListener('click', async () => {
        const r = await API(`/alerts/rules/${b.dataset.id}/test`, { method: 'POST' });
        alert(r.delivered ? '✅ Test sent.' : '❌ Failed: ' + (r.error || 'unknown'));
      }));
      list.querySelectorAll('.al-toggle').forEach(b => b.addEventListener('click', async () => {
        const enabled = b.dataset.enabled !== 'true';
        await API(`/alerts/rules/${b.dataset.id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) });
        refresh();
      }));
      list.querySelectorAll('.al-delete').forEach(b => b.addEventListener('click', async () => {
        if (!confirm('Delete this rule?')) return;
        await API(`/alerts/rules/${b.dataset.id}`, { method: 'DELETE' });
        refresh();
      }));
    }

    const hist = await API('/alerts/history');
    const hd = document.getElementById('al-history');
    const rows = hist.history || [];
    if (!rows.length) {
      hd.innerHTML = 'No alerts fired yet.';
    } else {
      hd.innerHTML = `
        <table>
          <thead><tr><th>When</th><th>Rule</th><th>Matches</th><th>Status</th></tr></thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td>${new Date(r.fired_at).toLocaleString()}</td>
                <td>${r.rule_name}</td>
                <td>${r.matches ?? '—'}</td>
                <td>${r.delivered ? '<span class="delta-pos">delivered</span>' : `<span class="delta-neg" title="${r.error||''}">failed</span>`}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>`;
    }
  }

  document.getElementById('al-create').addEventListener('click', async () => {
    const name = document.getElementById('al-name').value.trim();
    const webhook = document.getElementById('al-webhook').value.trim();
    if (!name || !webhook) { alert('Name + webhook URL required.'); return; }
    const conditions = {};
    const minCss = document.getElementById('al-min-css').value;     if (minCss) conditions.min_css = +minCss;
    const minSmurf = document.getElementById('al-min-smurf').value; if (minSmurf) conditions.min_smurf = +minSmurf / 100;  // stored 0..1
    const minPct = document.getElementById('al-min-pct').value;     if (minPct) conditions.min_percentile = +minPct;
    const minG   = document.getElementById('al-min-games').value;   if (minG)   conditions.min_games = +minG;
    const maxAge = document.getElementById('al-max-age').value;     if (maxAge) conditions.max_age = +maxAge;
    const role   = document.getElementById('al-role').value;        if (role)   conditions.role = role;
    const tier   = document.getElementById('al-tier').value;        if (tier)   conditions.tier = tier;
    if (document.getElementById('al-fa').checked) conditions.is_fa = true;
    if (document.getElementById('al-rising').checked) conditions.is_rising_star = true;
    if (document.getElementById('al-pro').checked) conditions.is_pro = true;

    await API('/alerts/rules', {
      method: 'POST',
      body: JSON.stringify({ name, webhook_url: webhook, conditions, enabled: true }),
    });
    document.getElementById('al-name').value = '';
    document.getElementById('al-webhook').value = '';
    refresh();
  });

  refresh();
}

/* ---------------- ADMIN ---------------- */
function initAdmin() {
  const log = document.getElementById('a-log');
  const stats = document.getElementById('a-stats');
  const refreshStats = async () => {
    const s = await API('/admin/stats');
    stats.textContent = JSON.stringify(s, null, 2);
  };
  refreshStats();

  document.getElementById('a-ingest').addEventListener('click', async () => {
    const players = document.getElementById('a-players').value;
    const matches = document.getElementById('a-matches').value;
    const tiers = [];
    if (document.getElementById('a-tier-challenger').checked) tiers.push('challenger');
    if (document.getElementById('a-tier-grandmaster').checked) tiers.push('grandmaster');
    if (document.getElementById('a-tier-master').checked) tiers.push('master');
    if (!tiers.length) { alert('Select at least one tier (Challenger / GM / Master).'); return; }
    const regions = Array.from(document.querySelectorAll('.a-region:checked')).map(el => el.value);
    if (!regions.length) { alert('Select at least one region.'); return; }
    const progressBar = document.getElementById('a-progress');
    progressBar.style.display = 'block';
    progressBar.textContent = 'Starting…';
    log.textContent = `Starting ingest — regions: ${regions.join(',')} · tiers: ${tiers.join(', ')} · ${players}/tier × ${matches} matches\n`;
    const r = await API(`/admin/ingest?player_limit=${players}&matches_per_player=${matches}&tiers=${tiers.join(',')}&regions=${regions.join(',')}`, { method: 'POST' });
    log.textContent += `Job ${r.job_id} started.\n`;
    const poll = setInterval(async () => {
      try {
        const j = await API('/admin/jobs/' + r.job_id);
        let line = `[${new Date().toLocaleTimeString()}] ${j.status} - ${j.step || ''}`;
        if (j.progress) {
          const p = j.progress;
          if (p.player_idx && p.player_total) {
            const pct = Math.round(100 * p.player_idx / p.player_total);
            progressBar.textContent = `${p.phase}: ${p.player_idx}/${p.player_total} (${pct}%) · last: ${p.current_player || '?'} (+${p.new_matches_last||0} matches)`;
            line += ` · ${p.player_idx}/${p.player_total} ${p.current_player || ''}`;
          } else if (p.attempted) {
            progressBar.textContent = `Resolving names: ${p.resolved}/${p.attempted}`;
          }
        }
        if (j.resolve_names) line += ` · resolved ${j.resolve_names.resolved} stubs`;
        if (j.alerts_sent != null) line += ` · alerts sent: ${j.alerts_sent}`;
        log.textContent += line + '\n';
        log.scrollTop = log.scrollHeight;
        if (j.status === 'done' || j.status === 'error') {
          clearInterval(poll);
          progressBar.style.display = 'none';
          refreshStats();
        }
      } catch { clearInterval(poll); progressBar.style.display = 'none'; }
    }, 4000);
  });

  document.getElementById('a-recompute').addEventListener('click', async () => {
    log.textContent = 'Recomputing aggregates and CSS...\n';
    let r;
    try {
      r = await API('/admin/recompute', { method: 'POST' });
    } catch (e) {
      log.textContent += `failed to start: ${e.message}\n`;
      return;
    }
    log.textContent += `Job ${r.job_id} started.\n`;
    const poll = setInterval(async () => {
      try {
        const j = await API('/admin/jobs/' + r.job_id);
        const extras = Object.entries(j)
          .filter(([k]) => !['status','step','kind','params','created_at','updated_at','error'].includes(k))
          .map(([k, v]) => `${k}=${v}`).join(' ');
        log.textContent += `[${new Date().toLocaleTimeString()}] ${j.status} - ${j.step || ''} ${extras}\n`;
        log.scrollTop = log.scrollHeight;
        if (j.status === 'done' || j.status === 'error') {
          clearInterval(poll);
          if (j.error) log.textContent += `ERROR: ${j.error}\n`;
          refreshStats();
        }
      } catch (e) {
        log.textContent += `poll failed: ${e.message}\n`;
        clearInterval(poll);
      }
    }, 5000);
  });

  document.getElementById('a-lolpros').addEventListener('click', async () => {
    log.textContent = 'Syncing Lolpros (EUW pros)...\n';
    const r = await API('/admin/sync-lolpros?server=EUW', { method: 'POST' });
    log.textContent += `Job ${r.job_id} started.\n`;
    const poll = setInterval(async () => {
      try {
        const j = await API('/admin/jobs/' + r.job_id);
        log.textContent += `[${new Date().toLocaleTimeString()}] ${j.status} - ${j.step || ''}${j.stats ? ' · ' + JSON.stringify(j.stats) : ''}\n`;
        log.scrollTop = log.scrollHeight;
        if (j.status === 'done' || j.status === 'error') { clearInterval(poll); refreshStats(); }
      } catch { clearInterval(poll); }
    }, 2000);
  });

  async function syncLeaguepedia(endpoint, label) {
    log.textContent = `Syncing Leaguepedia (${label})...\n`;
    const r = await API(endpoint, { method: 'POST' });
    log.textContent += `Job ${r.job_id} started.\n`;
    const poll = setInterval(async () => {
      try {
        const j = await API('/admin/jobs/' + r.job_id);
        log.textContent += `[${new Date().toLocaleTimeString()}] ${j.status} - ${j.step || ''}${j.stats ? ' · ' + JSON.stringify(j.stats) : ''}\n`;
        log.scrollTop = log.scrollHeight;
        if (j.status === 'done' || j.status === 'error') { clearInterval(poll); refreshStats(); }
      } catch { clearInterval(poll); }
    }, 2000);
  }

  document.getElementById('a-leaguepedia').addEventListener('click',
    () => syncLeaguepedia('/admin/sync-leaguepedia', 'quick ~75s'));
  document.getElementById('a-leaguepedia-full').addEventListener('click',
    () => syncLeaguepedia('/admin/sync-leaguepedia-full', 'FULL ~6 min, +Lolpros bulk'));

  async function runTournamentSync(label, leaguesParam = '') {
    log.textContent = `Syncing tournaments (${label}) — this can take 5-30 min depending on scope...\n`;
    const url = '/admin/sync-tournaments' + (leaguesParam ? `?leagues=${encodeURIComponent(leaguesParam)}` : '');
    const r = await API(url, { method: 'POST' });
    log.textContent += `Job ${r.job_id} started for: ${(r.leagues||[]).join(', ')}\n`;
    const poll = setInterval(async () => {
      try {
        const j = await API('/admin/jobs/' + r.job_id);
        const summary = j.stats ? ' · ' + JSON.stringify(j.stats).slice(0, 240) : '';
        log.textContent += `[${new Date().toLocaleTimeString()}] ${j.status} - ${j.step || ''}${summary}\n`;
        log.scrollTop = log.scrollHeight;
        if (j.status === 'done' || j.status === 'error') { clearInterval(poll); refreshStats(); }
      } catch { clearInterval(poll); }
    }, 5000);
  }

  document.getElementById('a-tournaments').addEventListener('click',
    () => runTournamentSync('ALL leagues'));
  document.getElementById('a-tournaments-eu').addEventListener('click',
    () => runTournamentSync('EMEA',
      'lec,lfl,prime_league,superliga,nlc,hitpoint,ebl,ultraliga,elite_series,esports_balkan_league,lpl_cis,tcl,northern_league_of_legends_championship'));
  document.getElementById('a-tournaments-kr').addEventListener('click',
    () => runTournamentSync('KR', 'lck,lck_challengers_league'));
  document.getElementById('a-tournaments-na').addEventListener('click',
    () => runTournamentSync('Americas', 'lcs,nacl,lta_n,lta_s,lta_cross'));
  document.getElementById('a-tournaments-intl').addEventListener('click',
    () => runTournamentSync('International', 'msi,worlds,first_stand,wqs'));

  document.getElementById('a-resolve').addEventListener('click', async () => {
    const max = prompt('How many stub players to resolve? (default 200, max ~1000 in one batch):', '200');
    if (!max) return;
    log.textContent = `Resolving up to ${max} unknown names via Riot account-v1...\n`;
    const r = await API(`/admin/resolve-names?max_resolve=${max}`, { method: 'POST' });
    log.textContent += `Job ${r.job_id} started.\n`;
    const poll = setInterval(async () => {
      try {
        const j = await API('/admin/jobs/' + r.job_id);
        const prog = j.progress ? ` · ${j.progress.resolved}/${j.progress.attempted} resolved` : '';
        const summary = j.stats ? ' · ' + JSON.stringify(j.stats) : '';
        log.textContent += `[${new Date().toLocaleTimeString()}] ${j.status} - ${j.step || ''}${prog}${summary}\n`;
        log.scrollTop = log.scrollHeight;
        if (j.status === 'done' || j.status === 'error') { clearInterval(poll); refreshStats(); }
      } catch { clearInterval(poll); }
    }, 3000);
  });
}

/* ---------------- MATCH DEEP-DIVE MODAL ---------------- */
let _matchChart = null;

async function openMatchModal(matchId) {
  const modal = document.getElementById('match-modal');
  const body = document.getElementById('match-modal-body');
  const title = document.getElementById('match-modal-title');
  modal.classList.add('open');
  title.textContent = `Match deep-dive · ${matchId}`;
  body.innerHTML = '<p class="muted">Loading timeline from Riot…</p>';
  try {
    const data = await API(`/matches/${matchId}/timeline`);
    renderMatchModal(data);
  } catch (e) {
    body.innerHTML = `<p class="muted">Failed to load: ${e.message}</p>`;
  }
}

function renderMatchModal(data) {
  const body = document.getElementById('match-modal-body');
  const blueSide = data.participants.filter(p => p.team_id === 100);
  const redSide  = data.participants.filter(p => p.team_id === 200);
  const winner = data.blue_win ? 'Blue' : 'Red';
  const dlUrl = (API_BASE || '') + `/matches/${data.match_id}/export`;
  const token = getToken();

  body.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:14px;margin-bottom:12px;flex-wrap:wrap;">
      <div class="muted" style="font-size:13px;">
        Patch ${data.patch || '?'} · ${data.duration_min} min · Winner: <strong style="color:${data.blue_win ? '#6ea8ff' : '#ff8b8b'}">${winner}</strong>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button id="match-download" class="export-btn" style="font-size:12px;padding:6px 12px;" title="Download Riot match-v5 data + timeline as JSON. NOT the .rofl in-game replay (those require the LoL client).">📥 Download JSON</button>
        <button id="match-external" class="secondary" style="font-size:12px;padding:6px 12px;" title="Open this match on external scouting sites">🔗 External</button>
        <button id="match-replay-help" class="secondary" style="font-size:12px;padding:6px 12px;" title="How to download the in-game .rofl replay">▶ How to get .rofl</button>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h4 class="muted-h4">🔵 Blue side ${data.blue_win ? '(WIN)' : ''}</h4>
        ${blueSide.map(p => `
          <div class="stat-row">
            <span class="label">${roleIcon(p.role, { size: 14 })} ${p.champion}</span>
            <span class="value">${p.summoner_name || '?'} · ${p.kills}/${p.deaths}/${p.assists}</span>
          </div>
        `).join('')}
      </div>
      <div class="card">
        <h4 class="muted-h4">🔴 Red side ${!data.blue_win ? '(WIN)' : ''}</h4>
        ${redSide.map(p => `
          <div class="stat-row">
            <span class="label">${roleIcon(p.role, { size: 14 })} ${p.champion}</span>
            <span class="value">${p.summoner_name || '?'} · ${p.kills}/${p.deaths}/${p.assists}</span>
          </div>
        `).join('')}
      </div>
    </div>

    <div class="card" style="margin-top:14px;">
      <h4 class="muted-h4">Gold curves (totals per team)</h4>
      <canvas id="match-gold-chart" height="220"></canvas>
    </div>

    <div class="card" style="margin-top:14px;">
      <h4 class="muted-h4">Events timeline</h4>
      <div style="max-height:300px;overflow-y:auto;">
        ${data.events.map(ev => renderEvent(ev)).join('')}
      </div>
    </div>
  `;

  // Compute per-team gold sum at each minute
  const minutes = data.gold_curves[0]?.minutes || [];
  const blueGold = minutes.map(() => 0);
  const redGold  = minutes.map(() => 0);
  data.gold_curves.forEach(gc => {
    const arr = gc.team_id === 100 ? blueGold : redGold;
    gc.gold.forEach((g, i) => { arr[i] += g; });
  });

  const goldDiff = minutes.map((_, i) => blueGold[i] - redGold[i]);

  if (_matchChart) _matchChart.destroy();
  _matchChart = new Chart(document.getElementById('match-gold-chart'), {
    type: 'line',
    data: {
      labels: minutes.map(m => m + 'm'),
      datasets: [
        { label: 'Blue gold', data: blueGold, borderColor: '#6ea8ff', backgroundColor: 'rgba(110,168,255,0.10)', fill: false, tension: 0.2 },
        { label: 'Red gold',  data: redGold,  borderColor: '#ff8b8b', backgroundColor: 'rgba(255,139,139,0.10)', fill: false, tension: 0.2 },
        { label: 'Blue lead', data: goldDiff, borderColor: '#f59e0b', borderDash: [4,4], yAxisID: 'y2', fill: false, tension: 0.2 },
      ],
    },
    options: {
      scales: {
        x: { grid: { color: '#2a2e37' }, ticks: { color: '#8a8f99' } },
        y: { grid: { color: '#2a2e37' }, ticks: { color: '#ebeced' }, title: { display: true, text: 'Total gold', color: '#8a8f99' } },
        y2: { position: 'right', grid: { display: false }, ticks: { color: '#f59e0b' }, title: { display: true, text: 'Blue − Red', color: '#f59e0b' } },
      },
      plugins: { legend: { labels: { color: '#ebeced' } } },
    },
  });

  // Wire buttons
  document.getElementById('match-download').addEventListener('click', async () => {
    // Fetch with auth header (can't put Bearer in <a href>), then trigger blob download
    const res = await fetch(dlUrl, { headers: { 'Authorization': 'Bearer ' + token } });
    if (!res.ok) { alert('Export failed: ' + res.status); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `match_${data.match_id}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  });

  document.getElementById('match-external').addEventListener('click', async () => {
    const linksData = await API(`/matches/${data.match_id}/external-links`);
    const links = linksData.links;
    const html = `
      <p style="margin-top:0;">Open this match on:</p>
      <ul style="line-height:2;list-style:none;padding:0;">
        ${Object.entries(links).map(([k, v]) =>
          `<li>🔗 <a href="${v}" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-weight:600;">${k}</a> <span class="muted" style="font-size:11px;">${v}</span></li>`
        ).join('')}
      </ul>
      <p class="muted" style="font-size:11px;margin-top:12px;">Some of these may 404 — third-party sites only index public/recent games. op.gg works for most EUW SoloQ matches.</p>
    `;
    showInlineDialog('External match links', html);
  });

  document.getElementById('match-replay-help').addEventListener('click', async () => {
    const linksData = await API(`/matches/${data.match_id}/external-links`);
    const html = `
      <p style="margin-top:0;"><strong>Riot's public API never exposes <code>.rofl</code> replay files.</strong> Only the LoL client itself can download them, via the local LCU on the player's machine.</p>
      <p>Two ways to get this match's replay:</p>
      <ol style="padding-left:20px;line-height:1.8;">
        <li>Open <strong>League of Legends client</strong> → <strong>Match History</strong> → find this match (use the patch + champion to locate it) → click the <strong>↓ download</strong> arrow → it saves a <code>.rofl</code> file in <code>%USERPROFILE%\\Documents\\League of Legends\\Replays</code>. Only works if your account participated.</li>
        <li>Use the <strong>Download JSON</strong> button instead — gives you all the stats Riot exposes for offline analysis (no actual replay video, but full timeline + events).</li>
      </ol>
      <p class="muted" style="font-size:11px;">Match ID: <code>${data.match_id}</code> — copy this to find it faster in the client.</p>
    `;
    showInlineDialog('How to get the .rofl replay', html);
  });
}

function showInlineDialog(title, htmlContent) {
  // Simple dialog reusing the match-modal styling. Stacks ON TOP of the
  // existing match modal so closing it returns to the deep-dive.
  let dlg = document.getElementById('inline-dialog');
  if (!dlg) {
    dlg = document.createElement('div');
    dlg.id = 'inline-dialog';
    dlg.className = 'match-modal';
    dlg.innerHTML = `
      <div class="match-modal-card" style="max-width:560px;">
        <div class="match-modal-head">
          <h3 id="inline-dialog-title"></h3>
          <button class="secondary" style="padding:4px 10px;" onclick="document.getElementById('inline-dialog').classList.remove('open')">✕</button>
        </div>
        <div id="inline-dialog-body" class="match-modal-body"></div>
      </div>
    `;
    document.body.appendChild(dlg);
    dlg.addEventListener('click', (e) => {
      if (e.target.id === 'inline-dialog') e.currentTarget.classList.remove('open');
    });
  }
  document.getElementById('inline-dialog-title').textContent = title;
  document.getElementById('inline-dialog-body').innerHTML = htmlContent;
  dlg.classList.add('open');
}

function renderEvent(ev) {
  const min = String(Math.floor(ev.ts / 60)).padStart(2, '0');
  const sec = String(ev.ts % 60).padStart(2, '0');
  const ts = `${min}:${sec}`;
  const sideClass = ev.team_id === 100 ? 'event-side-blue' : (ev.team_id === 200 ? 'event-side-red' : '');
  if (ev.type === 'kill') {
    return `<div class="event-row kill"><span class="ts">${ts}</span><span>⚔️</span>
      <span class="${sideClass}">${ev.killer || '?'}</span> <span class="muted">(${ev.killer_champion || '?'})</span>
      killed <span>${ev.victim || '?'}</span> <span class="muted">(${ev.victim_champion || '?'})</span>
      ${ev.assists.length ? `<span class="muted">— assists: ${ev.assists.join(', ')}</span>` : ''}
    </div>`;
  }
  if (ev.type === 'objective') {
    const what = ev.monster_subtype ? `${ev.monster_subtype} ${ev.subtype}` : ev.subtype;
    return `<div class="event-row objective"><span class="ts">${ts}</span><span>🐉</span>
      <span class="${sideClass}">${ev.killer || ev.team_id === 100 ? 'Blue' : 'Red'}</span> took <strong>${what}</strong>
    </div>`;
  }
  if (ev.type === 'tower') {
    return `<div class="event-row tower"><span class="ts">${ts}</span><span>🗼</span>
      <span class="${ev.team_id === 100 ? 'event-side-red' : 'event-side-blue'}">${ev.team_id === 100 ? 'Red' : 'Blue'}</span> tower (${ev.lane || ev.tower_type || '?'}) destroyed
    </div>`;
  }
  return '';
}

document.getElementById('match-modal-close').addEventListener('click', () => {
  document.getElementById('match-modal').classList.remove('open');
});
document.getElementById('match-modal').addEventListener('click', (e) => {
  if (e.target.id === 'match-modal') {
    e.currentTarget.classList.remove('open');
  }
});

/* ---------------- TOURNAMENT TAB ---------------- */
/* ---------- Smurf labeling ---------- */
async function refreshSmurfButton(puuid, btn) {
  try {
    const data = await API(`/smurf/label/${puuid}`);
    const votes = data.votes_yes + data.votes_no;
    const consensus = votes
      ? ` (${data.votes_yes}/${votes} say smurf)`
      : '';
    if (data.mine === true) {
      btn.textContent = '🚨 Marked smurf';
      btn.style.background = 'rgba(239,68,68,0.15)';
      btn.style.borderColor = 'rgba(239,68,68,0.4)';
      btn.title = `You marked this player as a smurf${consensus}`;
    } else if (data.mine === false) {
      btn.textContent = '✅ Not smurf';
      btn.style.background = 'rgba(34,211,164,0.15)';
      btn.style.borderColor = 'rgba(34,211,164,0.4)';
      btn.title = `You marked this player as NOT a smurf${consensus}`;
    } else {
      btn.textContent = '👁 Smurf?';
      btn.style.background = '';
      btn.style.borderColor = '';
      btn.title = `Click to label${consensus}`;
    }
  } catch (e) { /* anonymous read failure is fine */ }
}

async function openSmurfLabelDialog(puuid, btn) {
  const choice = prompt(
    "Label this player:\n  s = smurf\n  n = NOT a smurf\n  c = clear my label\n\nEnter s / n / c:",
    "s"
  );
  if (!choice) return;
  const c = choice.trim().toLowerCase();
  try {
    if (c === 'c') {
      await API(`/smurf/label/${puuid}`, { method: 'DELETE' });
    } else if (c === 's' || c === 'y' || c === '1') {
      await API(`/smurf/label/${puuid}?label=true`, { method: 'POST' });
    } else if (c === 'n' || c === '0') {
      await API(`/smurf/label/${puuid}?label=false`, { method: 'POST' });
    } else {
      alert('Unknown choice. Use s, n, or c.');
      return;
    }
    refreshSmurfButton(puuid, btn);
  } catch (e) {
    alert('Failed: ' + e.message);
  }
}

/* ---------- vs CHAMPION matchup card ---------- */
let _matchupSort = "games";

async function loadMatchups(puuid, role) {
  const card = document.getElementById('matchup-card');
  if (!card) return;
  const data = await API(`/players/${puuid}/matchups?role=${role}&min_games=2`);
  const list = data.matchups || [];
  if (!list.length) {
    card.innerHTML = `
      <h3>vs Champion <span class="muted" style="font-size:11px;font-weight:400;">opponent same role</span></h3>
      <p class="muted" style="margin:0;font-size:12px;">No matchups with ≥2 games against any champion. Ingest more matches.</p>`;
    return;
  }
  function rowHTML(m) {
    return `
      <tr>
        <td><strong>${m.champion}</strong></td>
        <td>${m.games}</td>
        <td>${m.winrate}%</td>
        <td class="${m.avg_gd15>=0?'delta-pos':'delta-neg'}">${m.avg_gd15}</td>
        <td class="${m.avg_csd15>=0?'delta-pos':'delta-neg'}">${m.avg_csd15}</td>
        <td>${m.avg_kda}</td>
        <td>${(m.avg_dmg_share*100).toFixed(1)}%</td>
      </tr>`;
  }
  function sortedList() {
    const k = _matchupSort;
    const cmp = {
      games:    (a,b) => b.games - a.games,
      winrate:  (a,b) => b.winrate - a.winrate,
      gd15:     (a,b) => b.avg_gd15 - a.avg_gd15,
      kda:      (a,b) => b.avg_kda - a.avg_kda,
    }[k] || ((a,b) => b.games - a.games);
    return [...list].sort(cmp);
  }
  function render() {
    const sorted = sortedList();
    card.innerHTML = `
      <h3>vs Champion <span class="muted" style="font-size:11px;font-weight:400;">opponent same role · ${data.total_games} total games</span></h3>
      <div class="row" style="margin-bottom:8px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;font-weight:600;">Sort
          <select id="matchup-sort" style="padding:4px 8px;font-size:12px;">
            <option value="games" ${_matchupSort==='games'?'selected':''}>Games</option>
            <option value="winrate" ${_matchupSort==='winrate'?'selected':''}>Winrate</option>
            <option value="gd15" ${_matchupSort==='gd15'?'selected':''}>GD@15</option>
            <option value="kda" ${_matchupSort==='kda'?'selected':''}>KDA</option>
          </select>
        </label>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Champion</th><th>Games</th><th>WR</th><th>GD@15</th><th>CSD@15</th><th>KDA</th><th>Dmg %</th>
          </tr></thead>
          <tbody>${sorted.map(rowHTML).join('')}</tbody>
        </table>
      </div>`;
    document.getElementById('matchup-sort').addEventListener('change', e => {
      _matchupSort = e.target.value;
      render();
    });
  }
  render();
}

async function loadTournamentTab(puuid) {
  const root = document.getElementById('tab-tournament');
  const data = await API('/players/' + puuid + '/tournaments');
  if (!data.matched) {
    root.innerHTML = `<div class="card"><h3>Tournament data</h3><p class="muted">No matching pro entry found in tournament data. Either this player isn't a pro on lolesports, or names don't line up. Run <strong>Sync tournaments</strong> in Admin to ingest more data.</p></div>`;
    return;
  }
  const stats = data.stats_by_league || [];
  if (!stats.length) {
    root.innerHTML = `<div class="card"><h3>Tournament data</h3><p class="muted">Player matched (lolesports id <code>${data.pro_player_id}</code>) but no completed games yet in our DB.</p></div>`;
    return;
  }
  root.innerHTML = `
    <div class="card">
      <h3>Tournament splits</h3>
      <table>
        <thead><tr><th>League</th><th>Tournament</th><th>Games</th><th>WR</th><th>KDA</th><th>KP</th><th>GD@15</th><th>CSD@15</th><th>CS/min</th><th>Pool</th></tr></thead>
        <tbody>
          ${stats.map(s => `
            <tr>
              <td><span class="role-tag">${(s.league_slug||'').toUpperCase()}</span></td>
              <td><strong>${s.tournament_name||'—'}</strong></td>
              <td>${s.games}</td>
              <td>${s.winrate ?? '—'}%</td>
              <td>${s.kda ?? '—'}</td>
              <td>${s.kp != null ? (s.kp*100).toFixed(1)+'%' : '—'}</td>
              <td class="${s.gd15>=0?'delta-pos':'delta-neg'}">${s.gd15 ?? '—'}</td>
              <td class="${s.csd15>=0?'delta-pos':'delta-neg'}">${s.csd15 ?? '—'}</td>
              <td>${s.cspm ?? '—'}</td>
              <td>${s.champion_pool_size ?? '—'}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>Tournament champion pool</h3>
        <table>
          <thead><tr><th>Champion</th><th>Games</th><th>WR</th><th>KDA</th></tr></thead>
          <tbody>
            ${(data.champion_pool||[]).map(cp => `
              <tr><td>${cp.champion}</td><td>${cp.games}</td><td>${cp.winrate}%</td><td>${cp.avg_kda}</td></tr>
            `).join('') || '<tr><td colspan="4" class="muted">No data.</td></tr>'}
          </tbody>
        </table>
      </div>
      <div class="card">
        <h3>Recent tournament matches <span class="muted" style="font-size:11px;font-weight:400;">click any row for the full deep-dive</span></h3>
        <table>
          <thead><tr><th>Date</th><th>League</th><th>Block</th><th>Champ</th><th>K/D/A</th><th>GD@15</th><th>W</th></tr></thead>
          <tbody>
            ${(data.recent_matches||[]).map(r => `
              <tr class="tn-match-row" data-mid="${r.match_id}" style="cursor:pointer;">
                <td>${r.game_date ? new Date(r.game_date).toLocaleDateString() : '—'}</td>
                <td>${(r.league_slug||'').toUpperCase()}</td>
                <td>${r.block_name||''}</td>
                <td>${r.champion||''}</td>
                <td>${r.kills}/${r.deaths}/${r.assists}</td>
                <td class="${r.gd15>=0?'delta-pos':'delta-neg'}">${r.gd15 ?? '—'}</td>
                <td>${r.win ? '<span class="delta-pos">W</span>' : '<span class="delta-neg">L</span>'}</td>
              </tr>`).join('') || '<tr><td colspan="7" class="muted">No data.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `;

  // Attach the click handler now that the rows exist in the DOM
  root.querySelectorAll('.tn-match-row').forEach(tr =>
    tr.addEventListener('click', () => openTournamentMatchModal(tr.dataset.mid))
  );
}

/* ---------------- TOURNAMENT MATCH MODAL ---------------- */
let _tnGoldChart = null;

async function openTournamentMatchModal(matchId) {
  // We reuse the match-modal element (same one used by SoloQ deep-dive)
  const modal = document.getElementById('match-modal');
  const body = document.getElementById('match-modal-body');
  const title = document.getElementById('match-modal-title');
  modal.classList.add('open');
  title.textContent = `Tournament match · ${matchId}`;
  body.innerHTML = '<p class="muted">Loading match details…</p>';
  try {
    const data = await API(`/tournament-matches/${matchId}`);
    renderTournamentMatchModal(data, matchId);
  } catch (e) {
    body.innerHTML = `<p class="muted">Failed to load: ${e.message}</p>`;
  }
}

function renderTournamentMatchModal(data, matchId) {
  const body = document.getElementById('match-modal-body');
  const blue = data.blue_team || {};
  const red = data.red_team || {};

  function teamHeader(t, sideColor) {
    return `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        ${t.logo_url ? `<img src="${t.logo_url}" style="width:32px;height:32px;object-fit:contain;" onerror="this.style.display='none'"/>` : ''}
        <strong style="font-size:16px;color:${sideColor};">${t.code || '?'} <span style="font-weight:400;color:var(--muted);">${t.name || ''}</span></strong>
        ${t.won ? '<span class="score-pill s-elite">WIN</span>' : ''}
      </div>`;
  }

  function rosterRows(team) {
    const parts = team.participants || [];
    return parts.map(p => `
      <tr ${p.riot_puuid ? `class="tn-roster-row" data-puuid="${p.riot_puuid}" style="cursor:pointer;"` : ''}>
        <td>${roleIcon(p.role, { size: 18 })}</td>
        <td><strong>${p.player_name || '?'}</strong></td>
        <td>${p.champion || ''}</td>
        <td>${p.kills}/${p.deaths}/${p.assists}</td>
        <td>${p.kda}</td>
        <td>${(p.kp*100).toFixed(0)}%</td>
        <td>${p.cs}</td>
        <td>${(p.gold/1000).toFixed(1)}k</td>
        <td class="${p.gd_at_15>=0?'delta-pos':'delta-neg'}">${p.gd_at_15 ?? '—'}</td>
      </tr>
    `).join('');
  }

  body.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:14px;margin-bottom:14px;flex-wrap:wrap;">
      <div class="muted" style="font-size:13px;">
        ${data.tournament ? `<strong>${(data.tournament.league||'').toUpperCase()}</strong> ${data.tournament.name||''} · ` : ''}
        ${data.block_name ? data.block_name + ' · ' : ''}
        ${data.game_date ? new Date(data.game_date).toLocaleDateString() : ''} ·
        Patch ${data.patch || '?'} · ${data.duration_min} min
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button id="tn-match-timeline" class="export-btn" style="font-size:12px;padding:6px 12px;" title="Pull live gold curve from lolesports">📈 Load gold curve</button>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        ${teamHeader(blue, '#6ea8ff')}
        <table>
          <thead><tr><th>R</th><th>Player</th><th>Champ</th><th>K/D/A</th><th>KDA</th><th>KP</th><th>CS</th><th>Gold</th><th>GD@15</th></tr></thead>
          <tbody>${rosterRows(blue)}</tbody>
        </table>
      </div>
      <div class="card">
        ${teamHeader(red, '#ff8b8b')}
        <table>
          <thead><tr><th>R</th><th>Player</th><th>Champ</th><th>K/D/A</th><th>KDA</th><th>KP</th><th>CS</th><th>Gold</th><th>GD@15</th></tr></thead>
          <tbody>${rosterRows(red)}</tbody>
        </table>
      </div>
    </div>

    <div class="card" id="tn-gold-card" style="display:none;margin-top:14px;">
      <h4 class="muted-h4">Gold curves</h4>
      <canvas id="tn-gold-chart" height="200"></canvas>
    </div>

    <div class="card" style="margin-top:14px;">
      <h4 class="muted-h4">Team summary</h4>
      <table>
        <thead><tr><th></th><th>Kills</th><th>Deaths</th><th>Assists</th><th>Gold</th><th>CS</th><th>GD@15 (sum)</th></tr></thead>
        <tbody>
          <tr><td><strong style="color:#6ea8ff;">${blue.code||'Blue'}</strong></td><td>${blue.summary?.kills||0}</td><td>${blue.summary?.deaths||0}</td><td>${blue.summary?.assists||0}</td><td>${((blue.summary?.gold||0)/1000).toFixed(1)}k</td><td>${blue.summary?.cs||0}</td><td>${blue.summary?.gd_at_15||0}</td></tr>
          <tr><td><strong style="color:#ff8b8b;">${red.code||'Red'}</strong></td><td>${red.summary?.kills||0}</td><td>${red.summary?.deaths||0}</td><td>${red.summary?.assists||0}</td><td>${((red.summary?.gold||0)/1000).toFixed(1)}k</td><td>${red.summary?.cs||0}</td><td>${red.summary?.gd_at_15||0}</td></tr>
        </tbody>
      </table>
    </div>
  `;

  // Click on a participant row → navigate to that player's profile (if linked)
  body.querySelectorAll('.tn-roster-row').forEach(tr =>
    tr.addEventListener('click', () => {
      window._selectedPuuid = tr.dataset.puuid;
      document.getElementById('match-modal').classList.remove('open');
      setView('player');
    })
  );

  // Optional: pull the gold curve from lolesports
  document.getElementById('tn-match-timeline').addEventListener('click', async () => {
    const card = document.getElementById('tn-gold-card');
    card.style.display = 'block';
    const placeholder = card.querySelector('h4').nextSibling;
    try {
      const tl = await API(`/tournament-matches/${matchId}/timeline`);
      if (!tl.samples) {
        card.innerHTML = '<h4 class="muted-h4">Gold curves</h4><p class="muted">No timeline data available — lolesports may have purged old games.</p>';
        return;
      }
      if (_tnGoldChart) _tnGoldChart.destroy();
      _tnGoldChart = new Chart(document.getElementById('tn-gold-chart'), {
        type: 'line',
        data: {
          labels: tl.minutes.map(m => m + 'm'),
          datasets: [
            { label: 'Blue gold', data: tl.blue_gold, borderColor: '#6ea8ff', fill: false, tension: 0.2 },
            { label: 'Red gold',  data: tl.red_gold,  borderColor: '#ff8b8b', fill: false, tension: 0.2 },
            { label: 'Blue lead', data: tl.gold_diff_blue_minus_red, borderColor: '#f59e0b', borderDash: [4,4], yAxisID: 'y2', fill: false, tension: 0.2 },
          ],
        },
        options: {
          scales: {
            x: { grid:{color:'#2a2e37'}, ticks:{color:'#8a8f99'} },
            y: { grid:{color:'#2a2e37'}, ticks:{color:'#ebeced'}, title:{display:true,text:'Total gold',color:'#8a8f99'} },
            y2: { position: 'right', grid:{display:false}, ticks:{color:'#f59e0b'}, title:{display:true,text:'Blue − Red',color:'#f59e0b'} },
          },
          plugins: { legend: { labels: { color: '#ebeced' } } },
        },
      });
    } catch (e) {
      card.innerHTML = `<h4 class="muted-h4">Gold curves</h4><p class="muted">Failed to load: ${e.message}</p>`;
    }
  });
}

/* ---------------- ROSTER COMPARE TAB ---------------- */
async function loadRosterTab(puuid, role) {
  const root = document.getElementById('tab-roster');
  const data = await API('/players/' + puuid + '/roster-compare');
  if (data.warning) {
    root.innerHTML = `<div class="card"><h3>vs LEC ${data.role||role}</h3><p class="muted">${data.warning}</p></div>`;
    return;
  }
  const prospect = data.prospect;
  const lec = data.lec_roster || [];
  const psoloq = prospect.soloq || {};

  // Helper: green if prospect's value >= pro's, red otherwise
  const cmp = (a, b, higherBetter = true) => {
    if (a == null || b == null) return '';
    const better = higherBetter ? a >= b : a <= b;
    return better ? 'better' : 'worse';
  };

  // Pros that have an ingested Riot account → SoloQ comparison table
  const lecWithSoloq = lec.filter(pro => pro.soloq);

  // Tournament table = LEC stats only (one row per pro)
  const tournamentRowsHtml = lec.map(pro => {
    const t = pro.tournament || {};
    return `
      <tr>
        <td><strong>${pro.player_name||'?'}</strong></td>
        <td><span class="team-pill">${pro.team_code||''}</span> ${pro.team_name||''}</td>
        <td>${t.games ?? 0}</td>
        <td class="delta ${cmp(psoloq.kda, t.kda)}">${t.kda ?? '<span class="no-data">—</span>'}</td>
        <td class="delta ${cmp(psoloq.kp, t.kp)}">${t.kp != null ? (t.kp*100).toFixed(1)+'%' : '<span class="no-data">—</span>'}</td>
        <td class="delta ${cmp(psoloq.gd15, t.gd15)}">${t.gd15 ?? '<span class="no-data">—</span>'}</td>
        <td class="delta ${cmp(psoloq.csd15, t.csd15)}">${t.csd15 ?? '<span class="no-data">—</span>'}</td>
        <td class="delta ${cmp(psoloq.cspm, t.cspm)}">${t.cspm ?? '<span class="no-data">—</span>'}</td>
      </tr>`;
  }).join('');

  // SoloQ table = only pros whose Riot account we've ingested + the prospect at the top
  const soloqRowsHtml = lecWithSoloq.map(pro => {
    const sq = pro.soloq;
    return `
      <tr>
        <td><strong>${pro.player_name||'?'}</strong></td>
        <td><span class="team-pill">${pro.team_code||''}</span> ${pro.team_name||''}</td>
        <td>${sq.games}</td>
        <td class="delta ${cmp(psoloq.kda, sq.kda)}">${sq.kda}</td>
        <td class="delta ${cmp(psoloq.kp, sq.kp)}">${(sq.kp*100).toFixed(1)}%</td>
        <td class="delta ${cmp(psoloq.gd15, sq.gd15)}">${sq.gd15}</td>
        <td class="delta ${cmp(psoloq.dmg_share, sq.dmg_share)}">${(sq.dmg_share*100).toFixed(1)}%</td>
        <td class="delta ${cmp(psoloq.vspm, sq.vspm)}">${sq.vspm}</td>
        <td>${sq.css != null ? `<span class="score-pill ${scoreClass(sq.css)}">${sq.css}</span>` : '—'}</td>
      </tr>`;
  }).join('');

  root.innerHTML = `
    <div class="card">
      <h3>vs current LEC ${data.role} roster — Tournament stats</h3>
      <p class="muted">Prospect's SoloQ stats vs each LEC pro's official tournament stats at the same role.</p>
      <div style="overflow-x:auto;">
      <table class="compare-table">
        <thead>
          <tr>
            <th>Player</th><th>Team</th>
            <th>Games</th><th>KDA</th><th>KP</th><th>GD@15</th><th>CSD@15</th><th>CS/min</th>
          </tr>
        </thead>
        <tbody>
          <tr class="prospect-row">
            <td><strong>${prospect.summoner_name}</strong> <span class="muted">(prospect — SoloQ)</span></td>
            <td>${prospect.tier || '—'} ${prospect.lp ? prospect.lp+' LP' : ''}</td>
            <td>${psoloq.games ?? '—'}</td>
            <td>${psoloq.kda ?? '—'}</td>
            <td>${psoloq.kp != null ? (psoloq.kp*100).toFixed(1)+'%' : '—'}</td>
            <td>${psoloq.gd15 ?? '—'}</td>
            <td>${psoloq.csd15 ?? '—'}</td>
            <td>${psoloq.cspm ?? '—'}</td>
          </tr>
          ${tournamentRowsHtml}
        </tbody>
      </table>
      </div>
      <p class="muted" style="margin-top:8px;font-size:11px;">Green = prospect outperforms; red = pro outperforms. Tournament data sourced from lolesports official games.</p>
    </div>

    <div class="card" style="margin-top:14px;">
      <h3>vs current LEC ${data.role} roster — SoloQ stats</h3>
      <p class="muted">${lecWithSoloq.length === 0
          ? 'No LEC pro at this role has been ingested into our SoloQ data yet. Run a fresh ladder ingest or sync Lolpros to backfill.'
          : `Prospect compared to ${lecWithSoloq.length} of ${lec.length} LEC pros whose Riot SoloQ account is in our DB.`}</p>
      ${lecWithSoloq.length === 0 ? '' : `
      <div style="overflow-x:auto;">
      <table class="compare-table">
        <thead>
          <tr>
            <th>Player</th><th>Team</th>
            <th>Games</th><th>KDA</th><th>KP</th><th>GD@15</th>
            <th>Dmg %</th><th>VS/min</th><th>CSS</th>
          </tr>
        </thead>
        <tbody>
          <tr class="prospect-row">
            <td><strong>${prospect.summoner_name}</strong> <span class="muted">(prospect)</span></td>
            <td>${prospect.tier || '—'} ${prospect.lp ? prospect.lp+' LP' : ''}</td>
            <td>${psoloq.games ?? '—'}</td>
            <td>${psoloq.kda ?? '—'}</td>
            <td>${psoloq.kp != null ? (psoloq.kp*100).toFixed(1)+'%' : '—'}</td>
            <td>${psoloq.gd15 ?? '—'}</td>
            <td>${psoloq.dmg_share != null ? (psoloq.dmg_share*100).toFixed(1)+'%' : '—'}</td>
            <td>${psoloq.vspm ?? '—'}</td>
            <td>${psoloq.css != null ? `<span class="score-pill ${scoreClass(psoloq.css)}">${psoloq.css}</span>` : '—'}</td>
          </tr>
          ${soloqRowsHtml}
        </tbody>
      </table>
      </div>`}
    </div>
  `;
}

/* ---------------- BOOT ---------------- */
async function boot() {
  if (!getToken()) { showLogin(); return; }
  // Validate token
  try {
    await API('/auth/me');
    showApp();
    setView('leaderboard');
  } catch {
    clearAuth();
    showLogin();
  }
}
boot();
