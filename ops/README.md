# Ops — backend tunneling

The frontend is hosted on Vercel (`*.vercel.app`); the backend runs locally on
`http://localhost:8000`. To bridge them we use Cloudflare Tunnel.

## TL;DR — why a *named* tunnel

| Feature | Quick tunnel | **Named tunnel** |
|---|---|---|
| Setup time | 0s | 5 min once |
| URL | `https://random.trycloudflare.com` | `https://scouting.your-domain.com` |
| Survives restart | ❌ — new URL every boot | ✅ stable URL |
| TLS cert | Cloudflare's wildcard | Cloudflare's per-host cert |
| Free tier | ✅ | ✅ |
| Requires domain | ❌ | ✅ (any domain on Cloudflare DNS) |

We were using the quick tunnel form (`cloudflared tunnel --url http://localhost:8000`)
which means every server restart broke the frontend's `SCOUTING_API_BASE`
constant in `frontend/index.html`. Switching to a named tunnel fixes that.

## Setup (one-time)

### 1. Install cloudflared

```powershell
winget install --id Cloudflare.cloudflared
```

### 2. Make sure your domain is on Cloudflare

If you don't have one yet, register or transfer a domain to Cloudflare's free
tier. The tunnel needs a domain whose DNS is hosted at Cloudflare so we can
add a CNAME for the subdomain.

### 3. Run the bootstrap script

From the repo root:

```powershell
.\ops\cloudflare-tunnel.ps1 -Hostname scouting.your-domain.com
```

What it does:

- Opens a browser to log you into Cloudflare (only on first run).
- Creates a tunnel named `challenger-scouting`.
- Writes `~/.cloudflared/config.yml` mapping `scouting.your-domain.com → http://localhost:8000`.
- Adds a CNAME record on your domain pointing to the tunnel.

Idempotent — re-running with the same arguments is safe.

### 4. Start the tunnel

Foreground (testing):

```powershell
cloudflared tunnel run challenger-scouting
```

As a Windows service that auto-starts on boot:

```powershell
cloudflared service install
```

### 5. Update the frontend

Edit `frontend/index.html`:

```html
<script>
  (function() {
    var h = location.hostname;
    if (h.endsWith(".vercel.app")) {
      window.SCOUTING_API_BASE = "https://scouting.your-domain.com";  // ← named tunnel
    } else {
      window.SCOUTING_API_BASE = window.SCOUTING_API_BASE || "";
    }
  })();
</script>
```

Redeploy:

```bash
cd frontend
vercel deploy --prod --yes
```

## Verifying it works

```bash
curl https://scouting.your-domain.com/api/health
# → {"status":"ok"}
```

Then open the Vercel URL — it should reach the backend without CORS issues.

## Multiple environments / sub-paths

Run multiple services through one tunnel by extending `~/.cloudflared/config.yml`:

```yaml
tunnel: <tunnel-uuid>
credentials-file: <…>.json

ingress:
  - hostname: scouting.your-domain.com
    service: http://localhost:8000
  - hostname: dev.your-domain.com
    service: http://localhost:8001
  - service: http_status:404
```

## Troubleshooting

**"Tunnel disconnected"** — check Windows Event Viewer for `cloudflared`. Most
common cause: port 8000 isn't actually listening locally (uvicorn died).

**CORS errors in the browser** — the FastAPI app has
`allow_origin_regex=…trycloudflare\.com…` in `backend/app/main.py:22`. Add your
new hostname pattern to that regex.

**DNS not resolving** — the CNAME may take a minute to propagate. Test with:

```bash
nslookup scouting.your-domain.com 1.1.1.1
```
