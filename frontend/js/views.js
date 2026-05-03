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

