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

  // 🧬 Account grouping by pro — collapse all of a pro's accounts into
  // one line with a "+N accounts" badge. Each row's group key is its
  // Lolpros slug (stable across all of a pro's Riot accounts) when known,
  // otherwise the puuid (each account stays its own row).
  const groupOn = document.getElementById('lb-group-toggle')?.checked;
  let groupedHidden = 0;
  if (groupOn) {
    const groups = new Map();
    for (const row of data) {
      const key = row.meta?.lolpros_slug || row.puuid;
      const cur = groups.get(key);
      // Keep the row with the higher CSS as the "primary" account display.
      // Falls back to games_played as tiebreaker.
      const score = (row.css_score ?? 0) * 1000 + (row.games_played ?? 0);
      if (!cur || score > cur._score) {
        const accounts = cur ? cur._accounts : [];
        accounts.push(row);
        groups.set(key, { ...row, _score: score, _accounts: accounts.concat(cur ? [] : []) });
      } else {
        cur._accounts.push(row);
      }
    }
    // Re-emit in original order, dropping non-primaries; track the
    // sibling list per primary so we can render the popover.
    const primaries = new Map();
    for (const [k, v] of groups.entries()) {
      // The "current row" of v is the primary (last-set winner). Build
      // its full siblings list (including itself, sorted by CSS desc).
      const siblings = [];
      for (const r of data) {
        const rk = r.meta?.lolpros_slug || r.puuid;
        if (rk === k) siblings.push(r);
      }
      siblings.sort((a, b) => (b.css_score ?? 0) - (a.css_score ?? 0));
      // Primary = highest-CSS sibling (stable).
      v.__primary_puuid = siblings[0].puuid;
      v.__siblings = siblings;
      primaries.set(k, v);
    }
    const before = data.length;
    data = data.filter(r => {
      const k = r.meta?.lolpros_slug || r.puuid;
      const p = primaries.get(k);
      return p && p.__primary_puuid === r.puuid;
    }).map(r => {
      const k = r.meta?.lolpros_slug || r.puuid;
      const p = primaries.get(k);
      return { ...r, _account_count: p.__siblings.length, _siblings: p.__siblings };
    });
    groupedHidden = before - data.length;
  }

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
    const accountBadge = row._account_count && row._account_count > 1
      ? ` <span class="account-count-badge" data-puuid="${row.puuid}" title="Click to see all ${row._account_count} accounts of this pro">+${row._account_count - 1} accounts</span>`
      : '';
    tr.innerHTML = `
      <td>${_lbOffset + i + 1}</td>
      <td><strong>${row.summoner_name || '(unknown)'}</strong> ${smurfBadge(row)}${risingBadge(row)}${accountBadge}</td>
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
    // Stash siblings on the row element for the popover handler
    if (row._siblings) tr._siblings = row._siblings;
  });

  // Update pager subtext if grouping is on (so the user knows N accounts collapsed)
  if (groupOn && groupedHidden > 0) {
    const note = document.createElement('span');
    note.style.cssText = 'color:var(--accent);margin-left:8px;font-size:11px;';
    note.textContent = `· 🧬 ${groupedHidden} alt account(s) collapsed`;
    pager.querySelector('span:first-child')?.appendChild(note);
  }

  // Whole row is clickable now — open profile unless click hit the star
  // or the account-count badge.
  document.querySelectorAll('tr.lb-row').forEach(tr => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('.star')) return;
      if (e.target.closest('.account-count-badge')) return;
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
  // Account count badge → small popover listing siblings
  document.querySelectorAll('.account-count-badge').forEach(b =>
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      const tr = b.closest('tr.lb-row');
      const siblings = tr?._siblings || [];
      showAccountsPopover(b, siblings);
    })
  );
}

function showAccountsPopover(anchor, siblings) {
  // Close any existing popover
  document.querySelectorAll('.accounts-popover').forEach(p => p.remove());
  if (!siblings.length) return;
  const pop = document.createElement('div');
  pop.className = 'accounts-popover';
  pop.innerHTML = `
    <div class="accounts-popover-head">
      <strong>${siblings.length} accounts</strong>
      <button class="accounts-popover-close" aria-label="Close">✕</button>
    </div>
    <table class="accounts-popover-table">
      <thead><tr><th>Account</th><th>Reg</th><th>Tier</th><th>Role</th><th>Games</th><th>CSS</th></tr></thead>
      <tbody>
        ${siblings.map(s => `
          <tr data-puuid="${s.puuid}" style="cursor:pointer;">
            <td><strong>${(s.summoner_name || '?').split('#')[0]}</strong><span class="muted" style="font-size:10px;">#${(s.summoner_name||'').split('#')[1] || ''}</span></td>
            <td>${regionBadge(s.region)}</td>
            <td>${tierBadge(s.tier, { size: 14 })} ${s.lp ?? ''}</td>
            <td>${roleIcon(s.role, { size: 14 })}</td>
            <td>${s.games_played}</td>
            <td>${s.css_score != null ? `<span class="score-pill ${scoreClass(s.css_score)}" style="font-size:10px;padding:1px 6px;">${s.css_score}</span>` : '—'}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
  document.body.appendChild(pop);
  // Position relative to the anchor
  const r = anchor.getBoundingClientRect();
  pop.style.position = 'absolute';
  pop.style.top = (window.scrollY + r.bottom + 6) + 'px';
  pop.style.left = (window.scrollX + r.left) + 'px';

  pop.querySelector('.accounts-popover-close').addEventListener('click', () => pop.remove());
  pop.querySelectorAll('tr[data-puuid]').forEach(tr =>
    tr.addEventListener('click', () => {
      window._selectedPuuid = tr.dataset.puuid;
      pop.remove();
      setView('player');
    })
  );
  // Click outside → close
  setTimeout(() => {
    document.addEventListener('click', function onDoc(e) {
      if (!pop.contains(e.target)) {
        pop.remove();
        document.removeEventListener('click', onDoc);
      }
    });
  }, 0);
}
function initLeaderboard() {
  document.getElementById('f-apply').addEventListener('click', () => {
    _lbOffset = 0;  // reset to first page when filters change
    loadLeaderboard();
  });

  // Account-grouping toggle (no backend call — pure post-fetch transform)
  document.getElementById('lb-group-toggle')?.addEventListener('change', () => {
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

/* ---------------- WATCHLIST / KANBAN ---------------- */
const KANBAN_STAGES = [
  { id: 'watch',     label: '👀 Watching',  color: '#5b8def' },
  { id: 'contacted', label: '✉ Contacted',  color: '#a78bfa' },
  { id: 'trial',     label: '🎯 Trial',      color: '#f5a524' },
  { id: 'offer',     label: '📝 Offer',      color: '#22d3a4' },
  { id: 'signed',    label: '✅ Signed',     color: '#10b981' },
  { id: 'rejected',  label: '✖ Pass',        color: '#7a818f' },
];

async function loadWatchlist() {
  const data = await API('/watchlist');
  const board = document.getElementById('wl-kanban');
  const tbody = document.querySelector('#wl-table tbody');
  if (!board || !tbody) return;

  // ---- Empty state ----
  if (!data.length) {
    board.innerHTML = `<p class="muted" style="text-align:center;padding:30px;">No players watched yet. Go to <a href="#" id="lb-link">Ladder</a> and click ☆ next to a name.</p>`;
    tbody.innerHTML = '';
    document.getElementById('lb-link')?.addEventListener('click', e => { e.preventDefault(); setView('leaderboard'); });
    return;
  }

  // ---- Group by stage ----
  const byStage = Object.fromEntries(KANBAN_STAGES.map(s => [s.id, []]));
  data.forEach(r => {
    const stage = (byStage[r.stage] !== undefined) ? r.stage : 'watch';
    byStage[stage].push(r);
  });

  // ---- Render kanban columns ----
  board.innerHTML = KANBAN_STAGES.map(s => `
    <div class="kanban-col" data-stage="${s.id}">
      <div class="kanban-col-head" style="border-top-color:${s.color};">
        <span class="kanban-col-label">${s.label}</span>
        <span class="kanban-col-count">${byStage[s.id].length}</span>
      </div>
      <div class="kanban-col-body" data-stage="${s.id}">
        ${byStage[s.id].map(r => kanbanCard(r)).join('')}
      </div>
    </div>
  `).join('');

  // Drag-and-drop wiring
  board.querySelectorAll('.kanban-card').forEach(card => {
    card.addEventListener('dragstart', e => {
      card.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', card.dataset.puuid);
    });
    card.addEventListener('dragend', () => card.classList.remove('dragging'));
    // Click anywhere on card → open profile (but not when dragging)
    card.addEventListener('click', e => {
      if (e.target.closest('.kanban-remove')) return;
      window._selectedPuuid = card.dataset.puuid;
      setView('player');
    });
  });
  board.querySelectorAll('.kanban-col-body').forEach(col => {
    col.addEventListener('dragover', e => { e.preventDefault(); col.classList.add('drag-over'); });
    col.addEventListener('dragleave', () => col.classList.remove('drag-over'));
    col.addEventListener('drop', async e => {
      e.preventDefault();
      col.classList.remove('drag-over');
      const puuid = e.dataTransfer.getData('text/plain');
      const newStage = col.dataset.stage;
      const card = board.querySelector(`.kanban-card[data-puuid="${puuid}"]`);
      if (!card || card.parentElement === col) return;
      // Optimistic move
      col.appendChild(card);
      // Update header counts
      KANBAN_STAGES.forEach(s => {
        const c = board.querySelector(`.kanban-col[data-stage="${s.id}"] .kanban-col-count`);
        if (c) c.textContent = board.querySelector(`.kanban-col-body[data-stage="${s.id}"]`).children.length;
      });
      try {
        const fd = new URLSearchParams(); fd.append('stage', newStage);
        const resp = await fetch(API_BASE + '/watchlist/' + puuid + '/stage', {
          method: 'PATCH', body: fd,
          headers: { 'Authorization': 'Bearer ' + getToken() },
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
      } catch (err) {
        alert('Failed to update stage: ' + err.message);
        loadWatchlist();  // resync from server
      }
    });
  });
  board.querySelectorAll('.kanban-remove').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const puuid = btn.dataset.puuid;
      if (!confirm('Remove this player from your watchlist?')) return;
      await fetch(API_BASE + '/watchlist/' + puuid, { method: 'DELETE', headers: { 'Authorization': 'Bearer ' + getToken() } });
      loadWatchlist();
    });
  });

  // ---- Render table fallback (toggle) ----
  tbody.innerHTML = data.map(row => `
    <tr>
      <td><strong>${row.summoner_name || '(unknown)'}</strong></td>
      <td>${tierBadge(row.tier)}</td>
      <td>${row.lp ?? '—'}</td>
      <td>${roleIcon(row.role)}</td>
      <td>${row.games_played}</td>
      <td>${row.css_score!==null ? `<span class="score-pill ${scoreClass(row.css_score)}">${row.css_score}</span>` : '—'}</td>
      <td>${row.percentile_rank ?? '—'}</td>
      <td><span class="kanban-stage-pill" style="background:${(KANBAN_STAGES.find(s=>s.id===row.stage)||{}).color || '#7a818f'};">${(KANBAN_STAGES.find(s=>s.id===row.stage)||{}).label || row.stage}</span></td>
      <td><input class="tag-input" data-puuid="${row.puuid}" value="${(row.tag||'').replace(/"/g,'&quot;')}" placeholder="add tag…"/></td>
      <td>${row.added_at ? new Date(row.added_at).toLocaleDateString() : '—'}</td>
      <td>
        <button data-puuid="${row.puuid}" class="secondary view-wl">View</button>
        <button data-puuid="${row.puuid}" class="secondary remove-wl" title="Remove from watchlist">✕</button>
      </td>
    </tr>
  `).join('');
  tbody.querySelectorAll('.view-wl').forEach(b =>
    b.addEventListener('click', () => { window._selectedPuuid = b.dataset.puuid; setView('player'); })
  );
  tbody.querySelectorAll('.remove-wl').forEach(b =>
    b.addEventListener('click', async () => {
      await fetch(API_BASE + '/watchlist/' + b.dataset.puuid, { method: 'DELETE', headers: { 'Authorization': 'Bearer ' + getToken() } });
      loadWatchlist();
    })
  );
  tbody.querySelectorAll('.tag-input').forEach(i =>
    i.addEventListener('change', async () => {
      await APIform('/watchlist', { puuid: i.dataset.puuid, tag: i.value });
    })
  );
}

function kanbanCard(r) {
  const cssBadge = r.css_score != null
    ? `<span class="score-pill ${scoreClass(r.css_score)}" style="font-size:10px;padding:2px 6px;">${r.css_score}</span>`
    : '<span class="muted" style="font-size:10px;">—</span>';
  const since = r.stage_changed_at
    ? Math.max(0, Math.floor((Date.now() - new Date(r.stage_changed_at).getTime()) / (1000*60*60*24)))
    : null;
  const sinceLabel = since != null ? `${since}d` : '';
  return `
    <div class="kanban-card" draggable="true" data-puuid="${r.puuid}" title="Drag to move stage · click to open profile">
      <div class="kanban-card-head">
        <strong class="kanban-card-name">${(r.summoner_name || '?').split('#')[0]}</strong>
        <button class="kanban-remove" data-puuid="${r.puuid}" title="Remove">✕</button>
      </div>
      <div class="kanban-card-meta">
        ${roleIcon(r.role, { size: 14 })}
        ${tierBadge(r.tier, { size: 14 })}
        ${r.lp != null ? `<span class="muted" style="font-size:10px;">${r.lp} LP</span>` : ''}
        ${cssBadge}
      </div>
      ${r.tag ? `<div class="kanban-card-tag">${r.tag}</div>` : ''}
      ${sinceLabel ? `<div class="kanban-card-since muted">${sinceLabel} in stage</div>` : ''}
    </div>
  `;
}

function initWatchlist() {
  loadWatchlist();
  // View toggle (kanban / table)
  const board = document.getElementById('wl-kanban');
  const tableWrap = document.getElementById('wl-table-wrap');
  const btnK = document.getElementById('wl-view-kanban');
  const btnT = document.getElementById('wl-view-table');
  if (btnK && btnT) {
    btnK.addEventListener('click', () => {
      btnK.classList.add('active'); btnT.classList.remove('active');
      board.style.display = ''; tableWrap.style.display = 'none';
    });
    btnT.addEventListener('click', () => {
      btnT.classList.add('active'); btnK.classList.remove('active');
      board.style.display = 'none'; tableWrap.style.display = '';
    });
  }
}

/* ---------------- TEAM PAGE ---------------- */
async function initTeam(code) {
  const card = document.getElementById('team-card');
  if (!card) return;
  if (!code) {
    card.innerHTML = '<p class="muted">No team specified. Try <code>#/team/G2</code>.</p>';
    return;
  }
  card.innerHTML = '<p class="muted">Loading…</p>';
  let data;
  try {
    data = await API('/teams/' + encodeURIComponent(code));
  } catch (e) {
    card.innerHTML = `<p class="muted">Team not found: <strong>${code}</strong>. ${e.message}</p>`;
    return;
  }

  const t = data.team;
  const r = data.record_recent;
  const wr = r.games ? Math.round(r.wins / r.games * 100) : null;
  const flagFor = (country) => (typeof flagEmoji === 'function' ? flagEmoji(country) : '');

  card.innerHTML = `
    <div style="display:flex;align-items:center;gap:14px;margin-bottom:14px;">
      ${t.logo_url ? `<img src="${t.logo_url}" style="width:56px;height:56px;object-fit:contain;" onerror="this.style.display='none'"/>` : ''}
      <div style="flex:1;">
        <h2 style="margin:0 0 2px;">${t.code} <span style="font-weight:400;color:var(--muted);">${t.name}</span></h2>
        <div class="muted" style="font-size:12px;">League: <strong>${(t.league_slug || '?').toUpperCase()}</strong> · Last 10: <strong>${r.wins}W ${r.losses}L</strong>${wr != null ? ` · ${wr}% WR` : ''}</div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>Active roster <span class="muted" style="font-size:11px;font-weight:400;">${data.roster.length} member(s) — sourced from Lolpros</span></h3>
        ${data.roster.length === 0 ? '<p class="muted">No roster found. Run sync-leaguepedia / sync-lolpros to populate.</p>' : `
        <table>
          <thead><tr><th></th><th>Player</th><th>Role</th><th>Country</th><th>Age</th><th>Tier</th><th>CSS</th><th></th></tr></thead>
          <tbody>
            ${data.roster.map(m => `
              <tr style="cursor:pointer;" data-puuid="${m.puuid || ''}">
                <td>${m.player_image_url ? `<img src="${m.player_image_url}" style="width:36px;height:36px;border-radius:6px;object-fit:cover;" onerror="this.style.display='none'"/>` : ''}</td>
                <td><strong>${m.leaguepedia_id || m.summoner_name || '?'}</strong>${m.summoner_name ? `<div class="muted" style="font-size:11px;">${m.summoner_name}</div>` : ''}</td>
                <td>${m.role || '<span class="muted">—</span>'}</td>
                <td>${flagFor(m.country)} ${m.country || ''}</td>
                <td>${m.age != null ? m.age : '<span class="muted">—</span>'}</td>
                <td>${tierBadge(m.tier)} ${m.lp != null ? m.lp + ' LP' : ''}</td>
                <td>${m.css != null ? `<span class="score-pill ${scoreClass(m.css)}">${m.css}</span>` : '<span class="muted">—</span>'}</td>
                <td>${m.puuid ? '<button class="secondary">View</button>' : ''}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>`}
      </div>

      <div class="card">
        <h3>Recent matches <span class="muted" style="font-size:11px;font-weight:400;">last 10 — click for deep-dive</span></h3>
        ${data.recent_matches.length === 0 ? '<p class="muted">No tournament matches in DB yet for this team.</p>' : `
        <table>
          <thead><tr><th>Date</th><th>League</th><th>Block</th><th></th><th>Opponent</th><th>Side</th><th>Patch</th></tr></thead>
          <tbody>
            ${data.recent_matches.map(m => `
              <tr class="tn-match-row" data-mid="${m.match_id}" style="cursor:pointer;">
                <td>${m.game_date ? new Date(m.game_date).toLocaleDateString() : '—'}</td>
                <td><span class="role-tag">${(m.league_slug || '').toUpperCase()}</span></td>
                <td>${m.block_name || ''}</td>
                <td>${m.won === true ? '<span class="delta-pos">W</span>' : m.won === false ? '<span class="delta-neg">L</span>' : '<span class="muted">?</span>'}</td>
                <td>${m.opponent_logo ? `<img src="${m.opponent_logo}" style="width:18px;height:18px;vertical-align:middle;margin-right:4px;object-fit:contain;" onerror="this.style.display='none'"/>` : ''}<strong>${m.opponent_code || '?'}</strong></td>
                <td>${m.side === 'blue' ? '<span style="color:#6ea8ff;">Blue</span>' : '<span style="color:#ff8b8b;">Red</span>'}</td>
                <td class="muted" style="font-size:11px;">${m.patch || '—'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>`}
      </div>
    </div>
  `;

  // Click roster row → player profile (deep-link via setView)
  card.querySelectorAll('tr[data-puuid]').forEach(tr => {
    if (!tr.dataset.puuid) return;
    tr.addEventListener('click', () => {
      window._selectedPuuid = tr.dataset.puuid;
      setView('player');
    });
  });
  // Click match row → tournament match modal
  card.querySelectorAll('.tn-match-row').forEach(tr => {
    tr.addEventListener('click', () => openTournamentMatchModal(tr.dataset.mid));
  });
}

/* ---------------- PATCH IMPACT ---------------- */
async function initPatchImpact() {
  const fromSel = document.getElementById('pi-from');
  const toSel = document.getElementById('pi-to');

  // /players/patches returns the list ordered by the most recent
  // Match.game_creation per patch — handles "16.10" > "16.9" cleanly
  // (string-sort would give the wrong answer) and updates itself the
  // instant a fresh ingest brings in games on a new patch. Each entry
  // also carries player_count + aggregate_count for the dropdown label.
  let patches = [];
  try {
    const list = await API('/players/patches');
    patches = list.map(p => ({
      patch: p.patch,
      label: `${p.patch} (${p.player_count.toLocaleString()} players)`,
    }));
  } catch {}
  if (!patches.length) {
    // Hard fallback when the DB is empty — shouldn't happen in normal use
    patches = [{ patch: '16.9', label: '16.9' }, { patch: '16.8', label: '16.8' }];
  }

  const opts = patches.map(p => `<option value="${p.patch}">${p.label}</option>`).join('');
  fromSel.innerHTML = opts;
  toSel.innerHTML = opts;
  // Default: to = newest, from = second-newest
  toSel.value = patches[0].patch;
  fromSel.value = patches[1] ? patches[1].patch : patches[0].patch;

  document.getElementById('pi-apply').addEventListener('click', loadPatchImpact);
  loadPatchImpact();
}

async function loadPatchImpact() {
  const fromP = document.getElementById('pi-from').value;
  const toP = document.getElementById('pi-to').value;
  const role = document.getElementById('pi-role').value;
  const minG = document.getElementById('pi-min').value || 10;
  const lim = document.getElementById('pi-limit').value || 100;
  const summary = document.getElementById('pi-summary');
  const tbody = document.querySelector('#pi-table tbody');

  if (!fromP || !toP || fromP === toP) {
    summary.textContent = 'Pick two different patches.';
    tbody.innerHTML = '';
    return;
  }
  summary.textContent = 'Loading…';
  tbody.innerHTML = '';

  const params = new URLSearchParams({
    patch_from: fromP, patch_to: toP,
    min_games_each: minG, limit: lim,
  });
  if (role) params.set('role', role);

  let data;
  try {
    data = await API('/players/patch-impact?' + params);
  } catch (e) {
    summary.innerHTML = `<span style="color:var(--danger);">Failed: ${e.message}</span>`;
    return;
  }

  if (data.warning) {
    summary.innerHTML = `<span style="color:var(--warn);">${data.warning}</span>`;
    return;
  }

  const rows = data.rows || [];
  summary.innerHTML = `${data.total_matched} player(s) with snapshots on both <strong>${fromP}</strong> and <strong>${toP}</strong> · showing top ${rows.length} by Δ CSS · min ${minG} games per patch`;

  await refreshWatchedSet();

  tbody.innerHTML = rows.map((r, i) => {
    const deltaCls = r.delta >= 5 ? 'delta-pos' : r.delta <= -5 ? 'delta-neg' : 'muted';
    const sign = r.delta > 0 ? '+' : '';
    const proCell = r.is_pro ? `<span class="role-tag" style="background:rgba(34,211,164,0.12);color:#22d3a4;">${r.team || 'PRO'}</span>` : '<span class="muted">—</span>';
    return `
      <tr style="cursor:pointer;" data-puuid="${r.puuid}">
        <td>${i + 1}</td>
        <td><strong>${r.summoner_name}</strong></td>
        <td>${regionBadge(r.region)}</td>
        <td>${proCell}</td>
        <td>${roleIcon(r.role, { size: 16 })}</td>
        <td>${tierBadge(r.tier)} ${r.lp != null ? r.lp + ' LP' : ''}</td>
        <td>${r.css_from}</td>
        <td>${r.css_to}</td>
        <td class="${deltaCls}"><strong>${sign}${r.delta}</strong></td>
        <td class="muted" style="font-size:11px;">${r.games_from} / ${r.games_to}</td>
        <td><button class="secondary view-pi" data-puuid="${r.puuid}">View</button></td>
      </tr>
    `;
  }).join('') || '<tr><td colspan="11" class="muted" style="text-align:center;padding:24px;">No matched players.</td></tr>';

  // Click anywhere on the row → open profile (sticky View column too)
  tbody.querySelectorAll('tr[data-puuid]').forEach(tr => {
    tr.addEventListener('click', () => {
      window._selectedPuuid = tr.dataset.puuid;
      setView('player');
    });
  });
}

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

  // ---------- Hero metrics + scheduler + recent jobs ----------
  const renderMetrics = (s) => {
    const grid = document.getElementById('a-metrics');
    if (!grid) return;
    const fmt = (n) => (n ?? 0).toLocaleString();
    const cards = [
      { label: 'SoloQ players',     value: fmt(s.soloq?.players),       sub: `${fmt(s.soloq?.matches)} matches`,        cls: '' },
      { label: 'Aggregates',         value: fmt(s.soloq?.aggregates),    sub: `${fmt(s.soloq?.participations)} part.`,   cls: '' },
      { label: 'Pros matched',       value: fmt(s.leaguepedia?.matched_pros), sub: 'Lolpros / Leaguepedia',             cls: 'metric-emerald' },
      { label: 'Tournament matches', value: fmt(s.tournaments?.official_matches), sub: `${fmt(s.tournaments?.tournaments)} tournaments`, cls: 'metric-amber' },
      { label: 'Pro teams',          value: fmt(s.tournaments?.pro_teams), sub: `${fmt(s.tournaments?.lec_roster)} LEC roster`, cls: 'metric-violet' },
    ];
    grid.innerHTML = cards.map(c => `
      <div class="metric-card ${c.cls}">
        <div class="metric-label">${c.label}</div>
        <div class="metric-value">${c.value}</div>
        <div class="metric-sub">${c.sub}</div>
      </div>
    `).join('');
  };

  const renderScheduler = (cfg) => {
    const body = document.getElementById('a-scheduler-body');
    const pill = document.getElementById('a-sched-status-pill');
    if (!body || !pill) return;

    if (!cfg.enabled) {
      pill.className = 'status-pill status-idle';
      pill.textContent = 'disabled';
      body.innerHTML = '<span class="muted" style="font-size:12px;">Scheduler disabled. Set <code>DAILY_INGEST_ENABLED=true</code> in <code>.env</code> + restart uvicorn.</span>';
      return;
    }
    pill.className = cfg.in_flight ? 'status-pill status-running' : 'status-pill status-active';
    pill.textContent = cfg.in_flight ? 'running' : 'armed';

    const next = cfg.next_run_at ? new Date(cfg.next_run_at).toLocaleString() : '—';
    const rotation = cfg.rotation || {};
    const todayTier = rotation.enabled ? rotation.today_tier : '<span class="muted">all tiers</span>';
    const tomorrowTier = rotation.enabled ? rotation.tomorrow_tier : '<span class="muted">all tiers</span>';

    body.innerHTML = `
      <dl class="sched-grid">
        <dt>Trigger</dt><dd>${cfg.trigger}</dd>
        <dt>Next run</dt><dd>${next}</dd>
        <dt>Today's tier</dt><dd><span class="sched-tier-pill">${(todayTier || '').toString().toUpperCase()}</span></dd>
        <dt>Tomorrow</dt><dd><span class="sched-tier-pill" style="background:linear-gradient(135deg,#a78bfa 0%,#7c5cff 100%);">${(tomorrowTier || '').toString().toUpperCase()}</span></dd>
        <dt>Regions</dt><dd>${(cfg.regions || []).map(r => r.toUpperCase()).join(' · ')}</dd>
        <dt>Scope</dt><dd>${cfg.players_per_tier} players × ${cfg.games_per_player} games</dd>
        <dt>Keys</dt><dd><strong>${cfg.keys_configured}</strong> · partition <code>${cfg.partition}</code></dd>
      </dl>
    `;
  };

  const renderJobs = (data) => {
    const list = document.getElementById('a-jobs-list');
    if (!list) return;
    const jobs = (data.jobs || data || []).slice(0, 10);
    if (!jobs.length) {
      list.innerHTML = '<span class="muted" style="font-size:12px;">No jobs yet.</span>';
      return;
    }
    const since = (iso) => {
      if (!iso) return '';
      const dt = Date.now() - new Date(iso).getTime();
      const m = Math.floor(dt / 60000);
      if (m < 1) return 'just now';
      if (m < 60) return `${m}m ago`;
      const h = Math.floor(m / 60);
      if (h < 24) return `${h}h ago`;
      return new Date(iso).toLocaleDateString();
    };
    list.innerHTML = jobs.map(j => {
      const cls = `status-pill status-${(j.status || 'idle').replace(/[^a-z]/g, '')}`;
      return `
        <div class="job-row" title="${j.id}">
          <div class="job-row-main">
            <span class="job-row-id">${j.id}</span>
            <span class="job-row-kind">${j.kind || '?'}</span>
            <span class="job-row-step">${j.step || ''}</span>
          </div>
          <span class="${cls}">${j.status || '?'}</span>
          <span class="job-row-when">${since(j.updated_at || j.created_at)}</span>
        </div>
      `;
    }).join('');
  };

  const refreshAll = async () => {
    try {
      const [stats, sched, jobs] = await Promise.all([
        API('/admin/stats').catch(() => ({})),
        API('/admin/scheduler/status').catch(() => ({})),
        API('/admin/jobs').catch(() => ({jobs: []})),
      ]);
      renderMetrics(stats);
      renderScheduler(sched);
      renderJobs(jobs);
    } catch (e) {
      console.error('admin refresh failed', e);
    }
  };
  // Back-compat alias for the existing per-button handlers below that
  // call refreshStats() at the end of their job-poll loops.
  const refreshStats = refreshAll;

  refreshAll();
  // Keep the dashboard live while the user has it open
  const _adminInterval = setInterval(refreshAll, 5000);
  // Stop polling when the user navigates away
  const stopPolling = () => clearInterval(_adminInterval);
  document.querySelectorAll('nav a').forEach(a => {
    if (a.dataset.view !== 'admin') {
      a.addEventListener('click', stopPolling, { once: true });
    }
  });

  document.getElementById('a-refresh')?.addEventListener('click', refreshAll);

  // Trigger-now scheduler button
  document.getElementById('a-sched-trigger')?.addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true; btn.textContent = '⏳ Starting…';
    try {
      const r = await API('/admin/scheduler/trigger-now', { method: 'POST' });
      if (r.ok) {
        btn.textContent = '✓ Started';
        setTimeout(() => { btn.textContent = '▶ Trigger now'; btn.disabled = false; }, 2500);
        refreshAll();
      } else {
        btn.textContent = '✕ Failed';
        alert('Failed: ' + (r.error || JSON.stringify(r)));
        btn.disabled = false;
      }
    } catch (e) {
      btn.textContent = '✕ Failed';
      alert('Failed: ' + e.message);
      btn.disabled = false;
    }
  });

  // ---------- Add player by Riot ID ----------
  const apSubmit = document.getElementById('ap-submit');
  if (apSubmit) {
    apSubmit.addEventListener('click', async () => {
      const idInput = document.getElementById('ap-id');
      const riotId = (idInput.value || '').trim();
      const region = document.getElementById('ap-region').value;
      const matches = document.getElementById('ap-matches').value || '30';
      const watch = document.getElementById('ap-watch').checked;
      const result = document.getElementById('ap-result');

      if (!riotId.includes('#')) {
        result.innerHTML = '<span style="color:var(--danger);">Riot ID must contain #, e.g. <code>Caps#EUW</code></span>';
        return;
      }
      apSubmit.disabled = true;
      const original = apSubmit.textContent;
      apSubmit.textContent = '⏳ Resolving…';
      result.innerHTML = '<span class="muted">Starting job…</span>';

      try {
        const params = new URLSearchParams({
          riot_id: riotId, platform: region,
          match_count: matches, auto_watch: String(watch),
        });
        const resp = await API('/admin/add-player?' + params, { method: 'POST' });
        const jobId = resp.job_id;
        result.innerHTML = `<span class="muted">Job <code>${jobId}</code> started — ${resp.riot_id} on ${resp.platform.toUpperCase()}</span>`;

        // Poll the job every 1.5s until done/error
        const pollStart = Date.now();
        const poll = setInterval(async () => {
          let job;
          try { job = await API('/admin/jobs/' + jobId); } catch { return; }
          const elapsed = ((Date.now() - pollStart) / 1000).toFixed(0);

          if (job.status === 'done') {
            clearInterval(poll);
            const s = job.extras?.stats || job.stats || {};
            const tierLabel = s.tier ? `${s.tier} ${s.rank || ''} ${s.lp ? s.lp + ' LP' : ''}` : 'unranked';
            result.innerHTML = `
              <div class="card" style="background:rgba(34,211,164,0.06);border-color:#22d3a4;padding:12px;margin-top:6px;">
                <strong>✅ ${s.riot_id || riotId}</strong>
                <span class="muted" style="margin-left:8px;font-size:11px;">${elapsed}s</span>
                <div style="margin-top:6px;font-size:12px;">
                  Region: <strong>${(s.region || region).toUpperCase()}</strong> ·
                  Rank: <strong>${tierLabel}</strong> ·
                  Account level: ${s.account_level ?? '?'} ·
                  Matches ingested: <strong>${s.matches_added}</strong>
                </div>
                <div style="margin-top:8px;">
                  <button class="export-btn" id="ap-open" data-puuid="${s.puuid}">Open profile</button>
                  ${watch ? '<span class="muted" style="margin-left:10px;font-size:11px;">★ Added to watchlist</span>' : ''}
                </div>
              </div>`;
            document.getElementById('ap-open')?.addEventListener('click', () => {
              window._selectedPuuid = s.puuid;
              setView('player');
            });
            apSubmit.disabled = false;
            apSubmit.textContent = original;
            idInput.value = '';
            refreshStats();
          } else if (job.status === 'error') {
            clearInterval(poll);
            result.innerHTML = `<span style="color:var(--danger);">❌ Failed: ${job.error || 'unknown error'}</span>`;
            apSubmit.disabled = false;
            apSubmit.textContent = original;
          } else {
            result.innerHTML = `<span class="muted">[${elapsed}s] ${job.step || job.status}…</span>`;
          }
        }, 1500);
      } catch (e) {
        result.innerHTML = `<span style="color:var(--danger);">Failed to start: ${e.message}</span>`;
        apSubmit.disabled = false;
        apSubmit.textContent = original;
      }
    });
  }

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

