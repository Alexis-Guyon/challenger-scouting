/* ---------------- BOOT ---------------- */
async function boot() {
  if (!getToken()) { showLogin(); return; }
  // Validate token
  try {
    await API('/auth/me');
    showApp();
    // Honor deep-link in URL hash (e.g. #/player/<puuid>, #/team/G2)
    // so a shared link lands directly on the right view. Falls back
    // to leaderboard otherwise.
    const parsed = parseHash();
    if (parsed) setView(parsed.view, parsed.arg);
    else setView('leaderboard');
  } catch {
    clearAuth();
    showLogin();
  }
}
boot();
