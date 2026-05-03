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
