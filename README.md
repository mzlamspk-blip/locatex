# LOCATEX

**Smart Location Analytics System** — a beginner-friendly Flask app that generates unique, consent-based visitor links.

When a recipient opens a link they first see a privacy notice and must click **Continue**. Only then does the server record the visit time, the *approximate* IP-based city/region/country (when available), the device category and the browser family. An admin can review everything in a password-protected dashboard.

> **Privacy:** LOCATEX never collects precise GPS location. IP geolocation is approximate and may be wrong. A visit is only recorded after the visitor explicitly clicks Continue on the disclosure page.

## Features

- Generate a unique link for each sharing session
- Record visits only after the visitor consents (Continue button)
- **Exact GPS location** — the browser's own permission prompt decides; a visit
  is recorded only after the visitor clicks Continue *and* allows location
- **Live tracking while the page stays open** — automatic position refreshes,
  with a visible "Location sharing: ON" indicator and a **Stop sharing**
  button the visitor controls
- **Live map** (OpenStreetMap/Leaflet) on the dashboard, auto-refreshing every 30 s
- Show timestamp, location, GPS accuracy, device and browser
- Count visits per link
- **MongoDB Atlas** storage in production, **SQLite** fallback for local dev
- CSRF protection, rate limiting, secure session cookies, security headers

## Project structure

```
LOCATEX/
├── app.py                  # Flask backend
├── requirements.txt
├── vercel.json             # Vercel config
├── locatex.db              # created automatically (local dev only)
├── templates/
│   ├── dashboard.html      # admin login + dashboard
│   ├── visitor.html        # consent page shown to recipients
│   └── error.html
├── static/
│   └── style.css
└── tests/
    └── smoke_test.py
```

## Local setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

$env:ADMIN_PASSWORD = "Choose-A-Strong-Password"
$env:SECRET_KEY     = "Use-A-Long-Random-Secret-Value"

.venv\Scripts\python app.py
```

Open <http://127.0.0.1:5000>, log in, click **Generate New Link**, copy the URL and open it (ideally in another browser/device). Click **Continue**, then check the dashboard.

Run the automated end-to-end test:

```powershell
.venv\Scripts\python tests\smoke_test.py http://127.0.0.1:5000
```

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ADMIN_PASSWORD` | yes | Password for the admin dashboard |
| `SECRET_KEY` | yes | Flask session signing key |
| `MONGODB_URI` | production | MongoDB Atlas connection string; when set, MongoDB is used instead of SQLite |
| `MONGODB_DB` | no | Database name (default `locatex`) |

Never commit these values. Locally use `$env:` in PowerShell; on Vercel add them under **Project → Settings → Environment Variables**.

> **Without `MONGODB_URI`** the app falls back to SQLite. Locally that is a
> `locatex.db` file next to `app.py`; on Vercel it is written to `/tmp` and
> **is wiped on every redeploy/cold start** — set `MONGODB_URI` for durable data.

## Deploy to Vercel

The app is zero-config for Vercel's Python runtime (`app.py` exposes `app`).

```powershell
npm i -g vercel
vercel login            # already logged in on this machine
vercel --prod
```

Then add the environment variables and redeploy:

```powershell
vercel env add ADMIN_PASSWORD production
vercel env add SECRET_KEY production
vercel env add MONGODB_URI production
vercel --prod
```

### MongoDB Atlas (free tier)

1. Create a free M0 cluster at <https://www.mongodb.com/atlas>
2. Database Access → add a user with *Read and write to any database*
3. Network Access → allow access from `0.0.0.0/0` (Vercel functions are dynamic)
4. Connect → Drivers → copy the connection string, fill in the password
5. Set it as `MONGODB_URI` on Vercel

## How the location flow works (consent first)

1. Visitor opens `/share/<token>` and reads the disclosure notice.
2. They click **Continue** — *only now* does anything get recorded.
3. The browser shows its own **"Share your location?"** prompt; the visitor
   must press **Allow** for exact GPS. If they deny, only the approximate
   IP-based location is stored.
4. While the page stays open, position updates are sent automatically. The
   visitor always sees a yellow **"Location sharing: ON"** status and can
   press **Stop sharing** (or just close the page) at any moment.

No location is collected before the visitor consents, and the browser
permission prompt is never hidden or bypassed.

## Limitations & security notes

- IP geolocation is approximate — VPNs, mobile carriers and ISPs often show the wrong city. It must **not** be presented as an exact address.
- The external IP lookup provider (ipapi.co) receives the visitor's IP address; this is disclosed on the consent page.
- Local `127.0.0.1` is only reachable on your machine — deploy publicly to share links.
- This version already includes CSRF protection, rate limiting, secure session cookies and security headers. For high-traffic or sensitive use, also add link expiry, data-retention/cleanup jobs and stronger admin auth (2FA).
- Do not trust forwarded IP headers unless your hosting proxy is explicitly configured and trusted (Vercel's `X-Real-IP` / `X-Forwarded-For` are handled only when `VERCEL=1`).
