# Remote Access & Tunneling Options Plan

This document outlines the architectural plan and configuration options for securing Tiki Karaoke with a permanent (or semi-permanent) URL, HTTPS/WSS, and QR code generation.

The goal is to support a flexible architecture where the host/admin can choose the remote access method that fits their network environment (home router controls, CGNAT, or domain ownership).

---

## 1. Supported Access Methods & Comparison

| Method | Cost | Permanent URL | Port Forwarding Needed | SSL / HTTPS | Guest Experience | Best For |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **DuckDNS + Caddy** | **Free** | **Yes** (`*.duckdns.org`) | **Yes** (80, 443) | Auto Let's Encrypt | Instant, clean HTTPS | Users with full router admin access & public IP |
| **Tailscale Funnel** | **Free** | **Yes** (`*.ts.net`) | **No** | Auto Let's Encrypt | Instant, clean HTTPS (No app needed for guests) | Users behind CGNAT/hotspots wanting $0 permanent URL |
| **ngrok Static Domain** | **Free** | **Yes** (`*.ngrok-free.app`) | **No** | Auto ngrok SSL | Interstitial splash screen on first visit | Fast setup, no router config |
| **Cloudflare Named Tunnel** | Free + Domain (~$5-$10/yr) | **Yes** (`karaoke.yourdomain.com`) | **No** | Auto Cloudflare SSL | Fast edge CDN, enterprise security, clean URL | Users with existing custom domains |
| **Cloudflare Quick Tunnel** | **Free** | No (Random URL on start) | **No** | Auto Cloudflare SSL | Clean HTTPS, no account needed | Ad-hoc sessions without fixed QR prints |
| **Bore (Current Fallback)** | **Free** | Semi-permanent port (`bore.pub:PORT`) | **No** | None (HTTP only) | Fast, zero setup | Local dev / testing fallback |
| **Local Network (mDNS / IP)** | **Free** | **Yes** (`tiki.local` or static IP) | **No** | None / Self-Signed | Offline, fast, Wi-Fi only | Private parties with no cellular/internet needs |

---

## 2. Detailed Breakdown of Options

### Option A: DuckDNS + Caddy (Top Zero-Cost Permanent Choice with Router Access)
* **Requirements:** Router port forwarding for ports 80 & 443; non-CGNAT public IPv4/IPv6.
* **Mechanism:** 
  1. Free account at `duckdns.org`, choosing e.g. `tiki-bar.duckdns.org`.
  2. Router forwards external port 80 & 443 to the local host machine IP.
  3. A lightweight Caddy service runs alongside Tiki Karaoke:
     ```caddyfile
     tiki-bar.duckdns.org {
         reverse_proxy 127.0.0.1:8000
     }
     ```
  4. Caddy automatically requests, verifies, and renews a free Let's Encrypt certificate.
* **Tiki Configuration:** Set `external_url` to `https://tiki-bar.duckdns.org`.

---

### Option B: Tailscale Funnel (Zero-Cost Permanent Choice WITHOUT Router Access)
* **Requirements:** Free Tailscale account; `tailscale` CLI installed on host machine.
* **Mechanism:**
  1. Host connects device to Tailscale tailnet.
  2. Host enables Tailscale Funnel on port 8000:
     `tailscale funnel 8000`
  3. Tailscale assigns a public URL: `https://<device-name>.<tailnet>.ts.net`.
  4. Public internet traffic hits Tailscale edge servers and is forwarded over WireGuard directly to port 8000.
  5. Guests do not need Tailscale installed; public SSL is automated.
* **Tiki Configuration:** Set `tunnel_provider: "tailscale"` or `external_url`.

---

### Option C: Cloudflare Tunnels (Zero Port-Forwarding, Maximum Reliability)
* **Named Tunnel (with Custom Domain):**
  - Host installs `cloudflared`.
  - Tunnel connects outward to Cloudflare edge; routes traffic to `localhost:8000`.
  - Permanent URL: `https://karaoke.yourdomain.com`.
  - Zero router configuration; enterprise DDoS protection and fast caching.
* **Quick Tunnel (Free, no domain, ephemeral):**
  - Run `cloudflared tunnel --url http://localhost:8000`.
  - Generates a random `https://*.trycloudflare.com` URL per session.

---

### Option D: ngrok Free Static Subdomain
* **Requirements:** Free ngrok account and authtoken.
* **Mechanism:** Claim one free static domain in ngrok dashboard (e.g. `my-karaoke.ngrok-free.app`).
* **Command:** `ngrok http --url=my-karaoke.ngrok-free.app 8000`

---

### Option E: Local Area Network (mDNS or Reserved LAN IP)
* **Requirements:** Venue/home Wi-Fi network.
* **Mechanism:** System broadcasts `http://tiki-karaoke.local:8000` via Avahi/mDNS or router-assigned static IP (e.g. `http://192.168.1.150:8000`).
* **Pros:** Completely offline, never changes, no internet dependence.

---

## 3. Modular Architecture Plan for Tiki Karaoke

To let each user/host choose the right option without modifying code, we plan to implement a provider-based tunnel manager in `config.py` and `main.py`:

```json
{
  "networking": {
    "provider": "duckdns",
    "duckdns": {
      "domain": "tiki-bar.duckdns.org",
      "token": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    },
    "cloudflare": {
      "mode": "named", 
      "domain": "karaoke.mybar.com",
      "tunnel_token": "..."
    },
    "tailscale": {
      "enabled": true,
      "port": 8000
    },
    "ngrok": {
      "authtoken": "...",
      "static_domain": "tiki-karaoke.ngrok-free.app"
    },
    "bore": {
      "server": "bore.pub",
      "preferred_port": 38480
    },
    "local_only": {
      "use_mdns": true,
      "hostname": "tiki-karaoke.local"
    }
  }
}
```

### Setup Wizard / Host Settings UI
- Update `/setup` and Host Settings to have a **Network & Remote Access** step where the host selects their method.
- When started via `start.sh` or `main.py`, Tiki Karaoke spins up the appropriate daemon or sets the static `external_url` for QR code generation.

---

## 4. Next Steps When Testing at Home Router
1. Check router admin panel for:
   - Port forwarding capabilities (TCP 80 & 443).
   - Public IP status (match WAN IP in router with `curl ifconfig.me` to confirm not on CGNAT).
2. Choose between:
   - **Path A (Port Forwarding Available):** DuckDNS + Caddy.
   - **Path B (Port Forwarding Restricted / CGNAT):** Tailscale Funnel or Cloudflare Tunnel.
