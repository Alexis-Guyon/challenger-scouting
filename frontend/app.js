/* Challenger Scouting — Pro edition (auth + watchlist + notes) */

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
  const res = await fetch(path, { ...opts, headers });
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
  if (name === 'player') initPlayer();
  if (name === 'compare') initCompare();
  if (name === 'admin') initAdmin();
}
navLinks.forEach(a => a.addEventListener('click', e => { e.preventDefault(); setView(a.dataset.view); }));

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
  if (p.meta.is_retired) return '<span class="score-pill s-avg" title="retired">retired</span>';
  if (p.meta.is_fa) return '<span class="score-pill s-elite" title="Free agent">FA</span>';
  return '<span class="score-pill s-strong" title="rostered pro">pro</span>';
}
function teamCell(p) {
  if (!p.meta) return '<span class="muted">—</span>';
  if (p.meta.is_fa) return '<span class="muted" style="font-style:italic;">free agent</span>';
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
    await fetch('/watchlist/' + puuid, { method: 'DELETE', headers: { 'Authorization': 'Bearer ' + getToken() } });
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
  const patch = document.getElementById('f-patch').value;
  const min = document.getElementById('f-min').value || 1;
  const sort = document.getElementById('f-sort').value;
  const proStatus = document.getElementById('f-prostatus').value;
  const maxAge = document.getElementById('f-maxage').value;
  const residency = document.getElementById('f-residency').value;
  const contract = document.getElementById('f-contract').value;

  const params = new URLSearchParams();
  if (role) params.set('role', role);
  if (patch) params.set('patch', patch);
  params.set('min_games', min);
  params.set('sort', sort);
  params.set('limit', _lbPageSize);
  params.set('offset', _lbOffset);
  if (proStatus === 'pro') params.set('pro_only', 'true');
  if (proStatus === 'fa') params.set('fa', 'true');
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
    tbody.innerHTML = `<tr><td colspan="15" class="muted" style="text-align:center;padding:30px;">No players match these filters.</td></tr>`;
    return;
  }
  data.forEach((row, i) => {
    const watched = _watchedSet.has(row.puuid);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${_lbOffset + i + 1}</td>
      <td><strong>${row.summoner_name || '(unknown)'}</strong> ${smurfBadge(row)}</td>
      <td>${proBadge(row)}</td>
      <td>${teamCell(row)}</td>
      <td>${ageCell(row)}</td>
      <td><span class="role-tag">${row.tier || '—'}</span></td>
      <td>${row.lp ?? '—'}</td>
      <td><span class="role-tag">${row.role || '—'}</span></td>
      <td>${row.patch || '—'}</td>
      <td>${row.games_played}</td>
      <td>${row.winrate}%</td>
      <td>${row.champion_pool_size}</td>
      <td><span class="score-pill ${scoreClass(row.css_score)}">${row.css_score}</span></td>
      <td>${row.percentile_rank}</td>
      <td>
        <span class="star ${watched?'active':''}" data-puuid="${row.puuid}">${watched?'★':'☆'}</span>
        <button data-puuid="${row.puuid}" class="secondary view-player">View</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
  document.querySelectorAll('.view-player').forEach(b =>
    b.addEventListener('click', () => { window._selectedPuuid = b.dataset.puuid; setView('player'); })
  );
  document.querySelectorAll('.star').forEach(s =>
    s.addEventListener('click', () => toggleWatch(s.dataset.puuid, s))
  );
}
function initLeaderboard() {
  document.getElementById('f-apply').addEventListener('click', () => {
    _lbOffset = 0;  // reset to first page when filters change
    loadLeaderboard();
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
      <td><span class="role-tag">${row.tier || '—'}</span></td>
      <td>${row.lp ?? '—'}</td>
      <td><span class="role-tag">${row.role || '—'}</span></td>
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
      await fetch('/watchlist/' + b.dataset.puuid, { method: 'DELETE', headers: { 'Authorization': 'Bearer ' + getToken() } });
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
  // Prefer Leaguepedia headshot (real photo); fall back to Riot in-game icon.
  const iconUrl = meta.player_image_url
    || (primaryAcc ? profileIconUrl(primaryAcc.profile_icon_id) : null);

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

  c.innerHTML = `
    <div class="player-header">
      <div>
        <h2>${p.summoner_name} ${smurfBadge(p)} <span class="star ${watched?'active':''}" id="profile-star" data-puuid="${puuid}" style="font-size:22px;margin-left:8px;">${watched?'★':'☆'}</span></h2>
        <div class="muted">${(p.region||'').toUpperCase()} · ${p.tier || '—'} ${p.lp ? p.lp + ' LP' : ''} · Account lvl ${p.account_level || '?'}</div>
        <div style="margin-top:6px;font-size:13px;">${metaLine}</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:32px;font-weight:800;">${agg.css_score}</div>
        <span class="score-pill ${scoreClass(agg.css_score)}">${scoreLabel(agg.css_score)}</span>
        <div class="muted" style="margin-top:4px;">P${agg.percentile_rank} · ${agg.role} · ${agg.games_played} games · ${agg.winrate}% WR</div>
      </div>
    </div>

    <div class="tabs">
      <button class="tab active" data-tab="soloq">SoloQ</button>
      <button class="tab" data-tab="tournament">Tournament</button>
      <button class="tab" data-tab="roster">vs LEC ${agg.role}</button>
    </div>
    <div id="tab-soloq" class="tab-pane">
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
        <h3>Recent matches</h3>
        <table>
          <thead><tr><th>Champ</th><th>Role</th><th>K/D/A</th><th>GD@15</th><th>Dmg %</th><th>VS</th><th>W</th></tr></thead>
          <tbody>
            ${data.recent_matches.slice(0,15).map(r => `
              <tr>
                <td>${r.champion_name}</td>
                <td>${r.role}</td>
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
      await fetch('/notes/' + d.dataset.id, { method: 'DELETE', headers: { 'Authorization': 'Bearer ' + getToken() } });
      loadNotes(puuid);
    })
  );
}

/* ---------------- COMPARE ---------------- */
function initCompare() {
  document.getElementById('cmp-go').addEventListener('click', async () => {
    const raw = document.getElementById('cmp-input').value.trim();
    const role = document.getElementById('cmp-role').value;
    if (!raw) return;
    const puuids = raw.split(',').map(s => s.trim()).filter(Boolean);
    const params = new URLSearchParams();
    puuids.forEach(p => params.append('puuid', p));
    if (role) params.set('role', role);
    const data = await API('/compare?' + params);

    const div = document.getElementById('cmp-result');
    if (!data.length) { div.innerHTML = '<p class="muted">No data.</p>'; return; }

    div.innerHTML = `
      <div class="card"><canvas id="cmp-radar" height="300"></canvas></div>
      <div class="card">
        <h3>Side-by-side stats</h3>
        <table>
          <thead><tr><th>Metric</th>${data.map(d => `<th>${d.summoner_name}</th>`).join('')}</tr></thead>
          <tbody>
            ${['css_score','games_played','percentile_rank'].map(k => `<tr><td>${k}</td>${data.map(d => `<td>${d[k]}</td>`).join('')}</tr>`).join('')}
            ${['gd15','xpd15','csd15','cspm','dmg_share','dpm','kp','kda','vspm','wpm','solo_kills','champion_pool_size'].map(k => `<tr><td>${k}</td>${data.map(d => `<td>${d.stats[k]}</td>`).join('')}</tr>`).join('')}
          </tbody>
        </table>
      </div>
    `;

    const metrics = ['gd15','xpd15','dmg_share','kp','kda','vspm','solo_kills','cspm'];
    const colors = ['#f59e0b','#34d399','#a78bfa','#f87171','#60a5fa'];
    const max = metrics.map(m => Math.max(...data.map(d => Math.abs(d.stats[m]||0)),1));
    new Chart(document.getElementById('cmp-radar'), {
      type: 'radar',
      data: {
        labels: metrics,
        datasets: data.map((d,i) => ({
          label: d.summoner_name,
          data: metrics.map((m,j) => 50 + ((d.stats[m]||0)/max[j])*40),
          backgroundColor: colors[i] + '33',
          borderColor: colors[i],
        }))
      },
      options: {
        scales: { r: { min: 0, max: 100, grid:{color:'#2a2e37'}, angleLines:{color:'#2a2e37'}, pointLabels:{color:'#ebeced'}, ticks:{display:false} } },
        plugins: { legend: { labels: { color: '#ebeced' } } },
      }
    });
  });
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
    log.textContent = `Starting ingest (${players} players × ${matches} matches)...\n`;
    const r = await API(`/admin/ingest?player_limit=${players}&matches_per_player=${matches}`, { method: 'POST' });
    log.textContent += `Job ${r.job_id} started.\n`;
    const poll = setInterval(async () => {
      try {
        const j = await API('/admin/jobs/' + r.job_id);
        log.textContent += `[${new Date().toLocaleTimeString()}] ${j.status} - ${j.step || ''}\n`;
        log.scrollTop = log.scrollHeight;
        if (j.status === 'done' || j.status === 'error') { clearInterval(poll); refreshStats(); }
      } catch { clearInterval(poll); }
    }, 3000);
  });

  document.getElementById('a-recompute').addEventListener('click', async () => {
    log.textContent = 'Recomputing aggregates and CSS...\n';
    const r = await API('/admin/recompute', { method: 'POST' });
    log.textContent += JSON.stringify(r, null, 2) + '\n';
    refreshStats();
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

  document.getElementById('a-leaguepedia').addEventListener('click', async () => {
    log.textContent = 'Syncing Leaguepedia metadata for EU pros...\n';
    const r = await API('/admin/sync-leaguepedia', { method: 'POST' });
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

  document.getElementById('a-tournaments').addEventListener('click', async () => {
    log.textContent = 'Syncing tournaments (LEC + ERLs) — this can take 5-15 minutes...\n';
    const r = await API('/admin/sync-tournaments', { method: 'POST' });
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
  });

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

/* ---------------- TOURNAMENT TAB ---------------- */
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
        <h3>Recent tournament matches</h3>
        <table>
          <thead><tr><th>Date</th><th>League</th><th>Block</th><th>Champ</th><th>K/D/A</th><th>GD@15</th><th>W</th></tr></thead>
          <tbody>
            ${(data.recent_matches||[]).map(r => `
              <tr>
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

  root.innerHTML = `
    <div class="card">
      <h3>vs current LEC ${data.role} roster</h3>
      <p class="muted">Prospect's SoloQ stats compared to each LEC player at the same role. Pros' SoloQ stats shown when matched (their Riot account is in our DB).</p>
      <div style="overflow-x:auto;">
      <table class="compare-table">
        <thead>
          <tr>
            <th>Player</th><th>Team</th>
            <th>Source</th>
            <th>Games</th><th>KDA</th><th>KP</th><th>GD@15</th><th>CSD@15</th>
            <th>Dmg %</th><th>VS/min</th><th>CS/min</th><th>CSS</th>
          </tr>
        </thead>
        <tbody>
          <tr class="prospect-row">
            <td><strong>${prospect.summoner_name}</strong> <span class="muted">(prospect)</span></td>
            <td>${prospect.tier || '—'} ${prospect.lp ? prospect.lp+' LP' : ''}</td>
            <td><span class="role-tag">SoloQ</span></td>
            <td>${psoloq.games ?? '—'}</td>
            <td>${psoloq.kda ?? '—'}</td>
            <td>${psoloq.kp != null ? (psoloq.kp*100).toFixed(1)+'%' : '—'}</td>
            <td>${psoloq.gd15 ?? '—'}</td>
            <td>${psoloq.csd15 ?? '—'}</td>
            <td>${psoloq.dmg_share != null ? (psoloq.dmg_share*100).toFixed(1)+'%' : '—'}</td>
            <td>${psoloq.vspm ?? '—'}</td>
            <td>${psoloq.cspm ?? '—'}</td>
            <td>${psoloq.css != null ? `<span class="score-pill ${scoreClass(psoloq.css)}">${psoloq.css}</span>` : '—'}</td>
          </tr>
          ${lec.map(pro => {
            const t = pro.tournament || {};
            const sq = pro.soloq;
            return `
            <tr>
              <td><strong>${pro.player_name||'?'}</strong></td>
              <td><span class="team-pill">${pro.team_code||''}</span> ${pro.team_name||''}</td>
              <td><span class="role-tag" title="LEC tournament games">LEC</span></td>
              <td>${t.games ?? 0}</td>
              <td class="delta ${cmp(psoloq.kda, t.kda)}">${t.kda ?? '<span class="no-data">—</span>'}</td>
              <td class="delta ${cmp(psoloq.kp, t.kp)}">${t.kp != null ? (t.kp*100).toFixed(1)+'%' : '<span class="no-data">—</span>'}</td>
              <td class="delta ${cmp(psoloq.gd15, t.gd15)}">${t.gd15 ?? '<span class="no-data">—</span>'}</td>
              <td class="delta ${cmp(psoloq.csd15, t.csd15)}">${t.csd15 ?? '<span class="no-data">—</span>'}</td>
              <td>—</td>
              <td>—</td>
              <td class="delta ${cmp(psoloq.cspm, t.cspm)}">${t.cspm ?? '<span class="no-data">—</span>'}</td>
              <td>—</td>
            </tr>
            ${sq ? `
            <tr style="opacity:0.78;">
              <td style="padding-left:24px;font-style:italic;">${pro.player_name||'?'} <span class="muted">(SoloQ)</span></td>
              <td></td>
              <td><span class="role-tag">SoloQ</span></td>
              <td>${sq.games}</td>
              <td class="delta ${cmp(psoloq.kda, sq.kda)}">${sq.kda}</td>
              <td class="delta ${cmp(psoloq.kp, sq.kp)}">${(sq.kp*100).toFixed(1)}%</td>
              <td class="delta ${cmp(psoloq.gd15, sq.gd15)}">${sq.gd15}</td>
              <td>—</td>
              <td class="delta ${cmp(psoloq.dmg_share, sq.dmg_share)}">${(sq.dmg_share*100).toFixed(1)}%</td>
              <td class="delta ${cmp(psoloq.vspm, sq.vspm)}">${sq.vspm}</td>
              <td>—</td>
              <td>${sq.css != null ? `<span class="score-pill ${scoreClass(sq.css)}">${sq.css}</span>` : '—'}</td>
            </tr>` : ''}
          `;}).join('')}
        </tbody>
      </table>
      </div>
      <p class="muted" style="margin-top:12px;font-size:11px;">Green = prospect outperforms; red = pro outperforms. SoloQ rows for pros appear only when their Riot account is in our DB and has been ingested.</p>
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
