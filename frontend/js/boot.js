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
