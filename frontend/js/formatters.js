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

