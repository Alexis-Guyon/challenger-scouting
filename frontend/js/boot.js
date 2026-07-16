/* ---------------- BOOT ---------------- */
// The site is public and read-only for anonymous visitors: we boot straight
// into the app. A valid token unlocks write features + personal views
// (watchlist, alerts, admin). An expired/invalid token is simply cleared and
// the visitor continues as a guest instead of being kicked to a login wall.
async function boot() {
  if (getToken()) {
    try {
      await API('/auth/me');
    } catch {
      clearAuth();  // stale token → fall through to anonymous browse
    }
  }
  showApp();
  // Honor deep-link in URL hash (e.g. #/player/<puuid>, #/team/G2) so a
  // shared link lands directly on the right view. Falls back to leaderboard.
  const parsed = parseHash();
  if (parsed) setView(parsed.view, parsed.arg);
  else setView('leaderboard');
}
boot();
