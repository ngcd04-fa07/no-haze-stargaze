# Cloudflare Tunnel Setup for Windows

Cloudflare Tunnel exposes your locally running app (`http://127.0.0.1:8000`) to the public internet over a secure HTTPS URL — without opening any ports on your router.

**Prerequisites:**
- A free Cloudflare account at https://cloudflare.com
- A domain you control, added to Cloudflare (free plan works)
- The app running locally on port 8000

---

## Step 1 — Install cloudflared

Download the Windows installer from the [cloudflared releases page](https://github.com/cloudflare/cloudflared/releases/latest).

Look for `cloudflared-windows-amd64.msi` (or `.exe`). Run the MSI installer.

After installation, verify in PowerShell:

```powershell
cloudflared --version
```

---

## Step 2 — Log in to Cloudflare

```powershell
cloudflared tunnel login
```

A browser window opens. Select the domain you want to use (e.g. `yourdomain.com`). This creates a certificate file at:

```
C:\Users\<YourUser>\.cloudflared\cert.pem
```

---

## Step 3 — Create a tunnel

```powershell
cloudflared tunnel create no-haze-stargaze
```

This creates a tunnel and a credentials JSON file at:

```
C:\Users\<YourUser>\.cloudflared\<TUNNEL_ID>.json
```

Note the `TUNNEL_ID` shown in the output (a UUID like `abc123...`).

---

## Step 4 — Create the tunnel config file

Create a file at:

```
C:\Users\<YourUser>\.cloudflared\config.yml
```

Contents (replace `<TUNNEL_ID>` and `<YourUser>` with your actual values):

```yaml
tunnel: <TUNNEL_ID>
credentials-file: C:\Users\<YourUser>\.cloudflared\<TUNNEL_ID>.json

ingress:
  - hostname: stargaze.yourdomain.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Replace `stargaze.yourdomain.com` with your actual subdomain (or bare domain).

---

## Step 5 — Route DNS to the tunnel

```powershell
cloudflared tunnel route dns no-haze-stargaze stargaze.yourdomain.com
```

This creates a CNAME DNS record in Cloudflare pointing your subdomain at the tunnel.

---

## Step 6 — Test the tunnel locally first

Make sure the app is running (see [README.md](README.md)), then:

```powershell
# Check the local app
curl http://127.0.0.1:8000/api/status

# Run the tunnel in the foreground to test
cloudflared tunnel run no-haze-stargaze
```

In a second PowerShell window:

```powershell
curl https://stargaze.yourdomain.com/api/status
```

You should get back a JSON response including `"deployment_mode": "windows_cloudflare"`.

---

## Step 7 — Install cloudflared as a Windows service

Once the tunnel works, install it as a background service so it starts automatically:

```powershell
# Run as Administrator
cloudflared service install
```

Manage the service:

```powershell
# Check status
Get-Service cloudflared

# Start / stop / restart
Start-Service cloudflared
Stop-Service cloudflared
Restart-Service cloudflared
```

The service reads config from `C:\Users\<YourUser>\.cloudflared\config.yml` automatically.

---

## Step 8 — Verify end-to-end

```powershell
# Local health check
curl http://127.0.0.1:8000/api/status

# Public health check (replace with your domain)
curl https://stargaze.yourdomain.com/api/status
```

Expected response (after first forecast refresh):

```json
{
  "deployment_mode": "windows_cloudflare",
  "cache_only_forecasts": true,
  "recommendation_fetches_openmeteo": false,
  "forecast_cache_loaded": true,
  "forecast_sites": 2640,
  "rate_limit_active": false
}
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `cloudflared: command not found` | Add `C:\Program Files\cloudflared` to your PATH or use the full path |
| Tunnel connects but site unreachable | Confirm `start_app.ps1` is running and listening on port 8000 |
| `502 Bad Gateway` | The Python app is not running; check Task Scheduler or start manually |
| DNS not resolving | Wait a few minutes for DNS propagation; verify with `nslookup stargaze.yourdomain.com` |
| Service won't start | Check `Get-EventLog -LogName System -Source cloudflared` for errors |
