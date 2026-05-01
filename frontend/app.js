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
  return p.meta.current_team || '<span class="muted">—</span>';
}
function ageCell(p) {
  if (!p.meta || !p.meta.age) return '<span class="muted">—</span>';
  return p.meta.age;
}

/* ---------------- LEADERBOARD ---------------- */
let _watchedSet = new Set();

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
  params.set('limit', 500);
  if (proStatus === 'pro') params.set('pro_only', 'true');
  if (proStatus === 'fa') params.set('fa', 'true');
  // amateur = pro_only=false handled client-side below
  if (maxAge) params.set('max_age', maxAge);
  if (residency) params.set('residency', residency);
  if (contract) params.set('contract_within_days', contract);

  await refreshWatchedSet();
  let data = await API('/players?' + params);

  // Client-side post-filter for "amateur only" (no LP entry)
  if (proStatus === 'amateur') data = data.filter(r => !r.meta);

  const tbody = document.querySelector('#lb-table tbody');
  tbody.innerHTML = '';

  // Update / inject a "showing N players" counter under the filters bar
  let counter = document.getElementById('lb-counter');
  if (!counter) {
    counter = document.createElement('p');
    counter.id = 'lb-counter';
    counter.className = 'muted';
    counter.style.cssText = 'margin:0 0 10px;font-size:12px;';
    document.querySelector('.filters').after(counter);
  }
  counter.textContent = `Showing ${data.length} player${data.length>1?'s':''} (capped at 500). Tweak Min games / role / patch to widen or narrow.`;

  if (!data.length) {
    tbody.innerHTML = `<tr><td colspan="15" class="muted" style="text-align:center;padding:30px;">No players match these filters.</td></tr>`;
    return;
  }
  data.forEach((row, i) => {
    const watched = _watchedSet.has(row.puuid);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${i + 1}</td>
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
  document.getElementById('f-apply').addEventListener('click', loadLeaderboard);
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
  const metaLine = meta
    ? `<span class="muted">·</span> ${proBadge(p)} <span class="muted">·</span> ${meta.current_team || '<em>FA</em>'}${meta.age?` · ${meta.age}y`:''}${meta.country?` · ${meta.country}`:''}${meta.residency?` · ${meta.residency} residency`:''}${meta.contract_end?` · contract ends ${meta.contract_end}`:''}${meta.leaguepedia_url?` · <a href="${meta.leaguepedia_url}" target="_blank" rel="noopener" style="color:var(--accent);">Leaguepedia ↗</a>`:''}`
    : '<span class="muted">· no Leaguepedia entry (amateur or unmatched)</span>';

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
        <h3>Aggregate stats</h3>
        <div class="stat-row"><span class="label">GD@15</span><span class="value">${stats.gd15}</span></div>
        <div class="stat-row"><span class="label">XPD@15</span><span class="value">${stats.xpd15}</span></div>
        <div class="stat-row"><span class="label">CSD@15</span><span class="value">${stats.csd15}</span></div>
        <div class="stat-row"><span class="label">CS / min</span><span class="value">${stats.cspm}</span></div>
        <div class="stat-row"><span class="label">DPM</span><span class="value">${stats.dpm}</span></div>
        <div class="stat-row"><span class="label">Damage share</span><span class="value">${(stats.dmg_share*100).toFixed(1)}%</span></div>
        <div class="stat-row"><span class="label">Kill participation</span><span class="value">${(stats.kp*100).toFixed(1)}%</span></div>
        <div class="stat-row"><span class="label">KDA</span><span class="value">${stats.kda}</span></div>
        <div class="stat-row"><span class="label">Vision / min</span><span class="value">${stats.vspm}</span></div>
        <div class="stat-row"><span class="label">Wards placed / min</span><span class="value">${stats.wpm}</span></div>
        <div class="stat-row"><span class="label">Solo kills / game</span><span class="value">${stats.solo_kills}</span></div>
        <div class="stat-row"><span class="label">Early deaths / game</span><span class="value">${stats.early_deaths}</span></div>
        <div class="stat-row"><span class="label">Champion pool (≥3 games)</span><span class="value">${stats.champion_pool_size}</span></div>
      </div>
    </div>

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
        <h3>Score breakdown</h3>
        ${RADAR_AXES.map(k => `
          <div class="bar-row">
            <span class="lab">${k}</span>
            <div class="bar"><span style="width:${(cats[k]||0).toFixed(0)}%"></span></div>
            <span class="num">${(cats[k]||0).toFixed(0)}</span>
          </div>
        `).join('')}
        <p class="muted" style="margin-top:10px;">Sample factor: ${agg.breakdown?.sample_factor?.toFixed(2) ?? '—'} · Smurf factor: ${agg.breakdown?.smurf_factor?.toFixed(2) ?? '—'}</p>
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
        backgroundColor: 'rgba(109,140,255,0.25)',
        borderColor: '#6d8cff',
        pointBackgroundColor: '#16d9b5',
      },{
        label: 'Challenger median (50)',
        data: RADAR_AXES.map(() => 50),
        backgroundColor: 'rgba(138,147,179,0.05)',
        borderColor: 'rgba(138,147,179,0.5)',
        borderDash: [4,4],
        pointRadius: 0,
      }]
    },
    options: {
      scales: { r: { min: 0, max: 100, grid:{color:'#243056'}, angleLines:{color:'#243056'}, pointLabels:{color:'#e7ecf7'}, ticks:{display:false} } },
      plugins: { legend: { labels: { color: '#e7ecf7' } } },
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
    const colors = ['#6d8cff','#16d9b5','#ffb547','#ff6b8a','#a78bfa'];
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
        scales: { r: { min: 0, max: 100, grid:{color:'#243056'}, angleLines:{color:'#243056'}, pointLabels:{color:'#e7ecf7'}, ticks:{display:false} } },
        plugins: { legend: { labels: { color: '#e7ecf7' } } },
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
