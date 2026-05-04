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

    <div class="grid-2">
      <div class="card" id="css-history-card">
        <h3>📈 CSS trend <span class="muted" style="font-size:11px;font-weight:400;">evolution across patches — line per role</span> <span id="css-history-delta" style="font-size:12px;font-weight:400;margin-left:8px;"></span></h3>
        <div id="css-history-empty" class="muted" style="font-size:12px;display:none;"></div>
        <canvas id="css-history-chart" height="180"></canvas>
      </div>
      <div class="card" id="activity-card">
        <h3>🔥 Activity <span class="muted" style="font-size:11px;font-weight:400;">current streak + when they play (UTC)</span></h3>
        <div id="activity-streak" style="margin-bottom:10px;"></div>
        <div id="activity-heatmap"></div>
      </div>
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
  loadActivity(puuid).catch(() => {});

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

async function loadActivity(puuid) {
  const card = document.getElementById('activity-card');
  if (!card) return;
  const streakEl = document.getElementById('activity-streak');
  const heatEl = document.getElementById('activity-heatmap');

  let data;
  try {
    data = await API('/players/' + puuid + '/activity');
  } catch (e) {
    streakEl.innerHTML = `<span class="muted">Failed: ${e.message}</span>`;
    return;
  }

  if (!data.total_games) {
    streakEl.innerHTML = '<span class="muted">No matches ingested yet.</span>';
    return;
  }

  // --- Streak badge ---
  const s = data.streak;
  if (s.length >= 3) {
    const isWin = s.type === 'W';
    const cls = isWin ? 'streak-win' : 'streak-loss';
    const verb = isWin ? 'win streak' : 'losing streak';
    const intensity = s.length >= 7 ? '🔥🔥' : s.length >= 5 ? '🔥' : '';
    streakEl.innerHTML = `<span class="streak-badge ${cls}">${s.length}${s.type} ${verb}</span> ${intensity}`;
  } else if (s.type) {
    streakEl.innerHTML = `<span class="muted" style="font-size:12px;">Last result: ${s.type === 'W' ? 'Win' : 'Loss'} (no notable streak)</span>`;
  }

  // --- Heatmap: 7 rows × 24 cols ---
  // Find max for color scaling. Cells with 0 stay neutral.
  let maxCount = 0;
  for (const row of data.heatmap) for (const v of row) if (v > maxCount) maxCount = v;

  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  // Hour labels: show every 3rd to keep compact
  const hourLabels = Array.from({length: 24}, (_, h) => h % 3 === 0 ? h : '');

  let html = `<div class="heatmap-wrap" title="Game count per (day × UTC hour). Darker = more games.">
    <div class="heatmap-row heatmap-header">
      <span class="heatmap-day"></span>
      ${hourLabels.map(h => `<span class="heatmap-hour">${h === '' ? '' : h}</span>`).join('')}
    </div>`;
  data.heatmap.forEach((row, di) => {
    html += `<div class="heatmap-row">`;
    html += `<span class="heatmap-day">${days[di]}</span>`;
    row.forEach((count, hi) => {
      const intensity = maxCount ? count / maxCount : 0;
      const bg = count === 0 ? 'transparent' : `rgba(110,168,255,${0.15 + 0.85 * intensity})`;
      html += `<span class="heatmap-cell" style="background:${bg};" title="${days[di]} ${String(hi).padStart(2,'0')}:00 UTC — ${count} games"></span>`;
    });
    html += `</div>`;
  });
  html += `</div><div class="muted" style="font-size:10px;margin-top:4px;">Times in UTC · ${data.total_games} games total</div>`;
  heatEl.innerHTML = html;
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

