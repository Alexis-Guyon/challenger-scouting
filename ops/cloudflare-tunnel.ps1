# =============================================================================
# Cloudflare Named Tunnel — bootstrap script (Windows / PowerShell)
# =============================================================================
# Sets up a *persistent* tunnel URL for the Challenger Scouting backend.
# Replaces the throwaway `cloudflared tunnel --url http://localhost:8000`
# pattern, whose URL changes every restart and breaks the Vercel frontend.
#
# After this runs once, you get a stable hostname like
#   https://scouting.<your-domain>.com
# that points to your local FastAPI on :8000.
#
# Prerequisites:
#   - cloudflared.exe in PATH (winget install --id Cloudflare.cloudflared)
#   - A domain managed by Cloudflare (free tier is fine)
#
# Usage:
#   PowerShell-as-yourself (no admin needed for tunnel creation):
#     .\ops\cloudflare-tunnel.ps1 -Hostname scouting.example.com
#
#   On first run it'll open a browser to authenticate cloudflared with your
#   Cloudflare account (one-time). Then it creates the tunnel + a CNAME DNS
#   record + writes the config file. Re-running is idempotent.
#
#   To then START the tunnel:
#     cloudflared tunnel run challenger-scouting
#
#   To run as a Windows service (auto-start on boot):
#     cloudflared service install
# =============================================================================

param(
    [Parameter(Mandatory=$true)]
    [string]$Hostname,                                  # e.g. scouting.example.com

    [string]$TunnelName = "challenger-scouting",
    [string]$LocalUrl = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"

# ----- Sanity checks -----
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Error "cloudflared not found. Install with: winget install --id Cloudflare.cloudflared"
}

$cfDir = Join-Path $env:USERPROFILE ".cloudflared"
if (-not (Test-Path $cfDir)) {
    New-Item -ItemType Directory -Path $cfDir | Out-Null
}

# ----- 1. Authenticate (once) -----
$certPath = Join-Path $cfDir "cert.pem"
if (-not (Test-Path $certPath)) {
    Write-Host "==> First-time auth: opening browser to login to Cloudflare..." -ForegroundColor Cyan
    cloudflared tunnel login
    if (-not (Test-Path $certPath)) {
        Write-Error "Login did not complete — re-run after finishing in the browser."
    }
}

# ----- 2. Create the tunnel (idempotent) -----
$existing = cloudflared tunnel list 2>$null | Select-String -Pattern $TunnelName
if (-not $existing) {
    Write-Host "==> Creating tunnel '$TunnelName'..." -ForegroundColor Cyan
    cloudflared tunnel create $TunnelName
} else {
    Write-Host "==> Tunnel '$TunnelName' already exists, skipping create." -ForegroundColor Yellow
}

# ----- 3. Find the credentials file -----
$creds = Get-ChildItem -Path $cfDir -Filter "*.json" |
    Where-Object { $_.BaseName -match "^[0-9a-f-]{36}$" } |
    Select-Object -First 1
if (-not $creds) {
    Write-Error "Could not locate tunnel credentials JSON in $cfDir"
}
$tunnelId = $creds.BaseName

# ----- 4. Write config.yml -----
$configPath = Join-Path $cfDir "config.yml"
$config = @"
tunnel: $tunnelId
credentials-file: $($creds.FullName)

ingress:
  - hostname: $Hostname
    service: $LocalUrl
  - service: http_status:404
"@
$config | Set-Content -Path $configPath -Encoding UTF8
Write-Host "==> Wrote $configPath" -ForegroundColor Green

# ----- 5. DNS — create CNAME pointing to the tunnel -----
Write-Host "==> Creating CNAME '$Hostname' → $tunnelId.cfargotunnel.com..." -ForegroundColor Cyan
try {
    cloudflared tunnel route dns $TunnelName $Hostname
    Write-Host "==> DNS record created." -ForegroundColor Green
} catch {
    Write-Host "==> DNS route already exists or failed; check Cloudflare dashboard." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "===========================================" -ForegroundColor Green
Write-Host " Tunnel setup complete." -ForegroundColor Green
Write-Host ""
Write-Host " Run it with:" -ForegroundColor White
Write-Host "   cloudflared tunnel run $TunnelName" -ForegroundColor Yellow
Write-Host ""
Write-Host " Or install as a Windows service (auto-start):" -ForegroundColor White
Write-Host "   cloudflared service install" -ForegroundColor Yellow
Write-Host ""
Write-Host " Then update frontend/index.html SCOUTING_API_BASE to:" -ForegroundColor White
Write-Host "   https://$Hostname" -ForegroundColor Yellow
Write-Host "===========================================" -ForegroundColor Green
