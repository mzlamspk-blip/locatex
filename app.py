"""
LOCATEX - Smart Location Analytics System.

Consent-based analytics: every shared link first shows a disclosure notice.
A visit is recorded only after the visitor clicks "Continue". We store an
approximate IP-based location (never precise GPS), the visit time, the
device category and the browser family.

Storage backend:
  * MongoDB Atlas - used automatically when MONGODB_URI is set (Vercel).
  * SQLite        - local development fallback (locatex.db).
"""

import hmac
import ipaddress
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache, wraps

import requests
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from user_agents import parse

app = Flask(__name__)

# Vercel sets VERCEL=1 in production. That also tells us the app runs behind
# Vercel's proxy over HTTPS (trusted X-Forwarded-For / X-Real-IP handling).
IS_VERCEL = bool(os.environ.get("VERCEL"))

app.secret_key = os.environ.get(
    "SECRET_KEY", "change-this-secret-key-before-deployment"
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_VERCEL,
)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")
MONGODB_URI = os.environ.get("MONGODB_URI", "")
GEO_TIMEOUT = 5
LOGIN_PER_MINUTE = 10
RECORD_PER_MINUTE = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def client_ip() -> str:
    """Best-effort visitor IP.

    Behind Vercel's proxy the socket address is the platform itself, so the
    real client address comes from the platform-set headers. Those headers
    are only trusted when VERCEL=1 (set by the platform, not by the client).
    """
    if IS_VERCEL:
        real_ip = request.headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip.strip()
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


# --------------------------------------------------------------------- CSRF


def get_csrf() -> str:
    if "csrf" not in session:
        session["csrf"] = uuid.uuid4().hex
    return session["csrf"]


@app.context_processor
def inject_globals():
    """Make the CSRF token available to every template."""
    return {"csrf_token": get_csrf()}


def csrf_ok() -> bool:
    supplied = request.headers.get("X-CSRF-Token") or request.form.get(
        "csrf_token", ""
    )
    expected = session.get("csrf", "")
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied, expected)


# -------------------------------------------------------------- rate limits

_rate_buckets: dict = {}


def rate_limit(name: str, limit: int, window: float = 60.0) -> None:
    """Best-effort in-memory rate limit (per instance, per client)."""
    key = f"{name}:{client_ip()}"
    now = time.monotonic()
    hits = [t for t in _rate_buckets.get(key, ()) if now - t < window]
    if len(hits) >= limit:
        abort(429)
    hits.append(now)
    _rate_buckets[key] = hits
    if len(_rate_buckets) > 5000:  # opportunistically drop stale buckets
        cutoff = now - window
        for stale in [k for k, v in _rate_buckets.items() if not v or v[-1] < cutoff]:
            _rate_buckets.pop(stale, None)


# ----------------------------------------------------------------- storage


class SqliteStore:
    """Local development backend (single SQLite file)."""

    def __init__(self, path: str):
        self.path = path
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT UNIQUE,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT,
                    visited_at TEXT,
                    city TEXT,
                    region TEXT,
                    country TEXT,
                    latitude REAL,
                    longitude REAL,
                    device TEXT,
                    browser TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def create_link(self, token: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO links (token, created_at) VALUES (?, ?)",
                (token, now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_link(self, token: str):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM links WHERE token = ?", (token,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_links(self) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT links.token, links.created_at, COUNT(visits.id) AS total_visits
                FROM links
                LEFT JOIN visits ON links.token = visits.token
                GROUP BY links.id
                ORDER BY links.id DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def add_visit(self, doc: dict) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO visits (
                    token, visited_at, city, region, country,
                    latitude, longitude, device, browser
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc["token"],
                    doc["visited_at"],
                    doc["city"],
                    doc["region"],
                    doc["country"],
                    doc["latitude"],
                    doc["longitude"],
                    doc["device"],
                    doc["browser"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def recent_visits(self, limit: int = 100) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM visits ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def total_visits(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
        finally:
            conn.close()


class MongoStore:
    """Production backend (MongoDB Atlas)."""

    def __init__(self, uri: str):
        from pymongo import MongoClient

        self.client = MongoClient(
            uri, serverSelectionTimeoutMS=5000, appname="locatex"
        )
        self.db = self.client[os.environ.get("MONGODB_DB", "locatex")]
        self.links = self.db["links"]
        self.visits = self.db["visits"]
        self.links.create_index("token", unique=True)
        self.visits.create_index([("token", 1), ("_id", -1)])

    def create_link(self, token: str) -> None:
        self.links.insert_one({"token": token, "created_at": now_iso()})

    def get_link(self, token: str):
        doc = self.links.find_one({"token": token}, {"_id": 0})
        return doc

    def list_links(self) -> list:
        counts = {
            row["_id"]: row["n"]
            for row in self.visits.aggregate(
                [{"$group": {"_id": "$token", "n": {"$sum": 1}}}]
            )
        }
        out = []
        for link in self.links.find({}, {"_id": 0}).sort("_id", -1):
            out.append(
                {
                    "token": link.get("token"),
                    "created_at": link.get("created_at"),
                    "total_visits": counts.get(link.get("token"), 0),
                }
            )
        return out

    def add_visit(self, doc: dict) -> None:
        self.visits.insert_one(dict(doc))

    def recent_visits(self, limit: int = 100) -> list:
        return list(
            self.visits.find({}, {"_id": 0}).sort("_id", -1).limit(limit)
        )

    def total_visits(self) -> int:
        return self.visits.count_documents({})


@lru_cache(maxsize=1)
def get_store():
    if MONGODB_URI:
        try:
            return MongoStore(MONGODB_URI)
        except Exception as exc:  # pragma: no cover - surfaced in logs
            print(f"[LOCATEX] MongoDB connection failed: {exc}")
            raise
    # On Vercel the project directory is not writable; use /tmp instead.
    # NOTE: this SQLite file is ephemeral on Vercel (cleared on redeploy) -
    # set MONGODB_URI for durable storage.
    default_path = "/tmp/locatex.db" if IS_VERCEL else "locatex.db"
    return SqliteStore(os.environ.get("SQLITE_PATH", default_path))


# ----------------------------------------------------------------- geolookup


def lookup_location(ip: str):
    """Approximate city/region/country for a public IP. This is NOT GPS."""
    city = region = country = "Unknown"
    latitude = longitude = None
    try:
        if not ipaddress.ip_address(ip).is_global:
            return city, region, country, latitude, longitude
    except ValueError:
        return city, region, country, latitude, longitude
    try:
        response = requests.get(
            f"https://ipapi.co/{ip}/json/",
            timeout=GEO_TIMEOUT,
            headers={"User-Agent": "LOCATEX/1.0 (consent-based analytics)"},
        )
        if response.ok:
            data = response.json()
            if not data.get("error"):
                city = data.get("city") or city
                region = data.get("region") or region
                country = data.get("country_name") or country
                latitude = data.get("latitude")
                longitude = data.get("longitude")
    except (requests.RequestException, ValueError):
        pass
    return city, region, country, latitude, longitude


# -------------------------------------------------------------------- routes


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        rate_limit("login", LOGIN_PER_MINUTE)
        if not csrf_ok():
            flash("Session expired. Please try again.")
            return redirect(url_for("login"))
        password = request.form.get("password", "")
        if hmac.compare_digest(
            password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")
        ):
            session["admin"] = True
            session.pop("csrf", None)  # rotate the token on privilege change
            return redirect(url_for("dashboard"))
        flash("Incorrect password.")
    return render_template("dashboard.html", login_mode=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    store = get_store()
    return render_template(
        "dashboard.html",
        login_mode=False,
        links=store.list_links(),
        visits=store.recent_visits(100),
        total=store.total_visits(),
        host=request.host_url,
    )


@app.route("/create", methods=["POST"])
@login_required
def create_link():
    if not csrf_ok():
        flash("Session expired. Please try again.")
        return redirect(url_for("dashboard"))
    get_store().create_link(uuid.uuid4().hex)
    return redirect(url_for("dashboard"))


@app.route("/share/<token>")
def visitor(token):
    if not get_store().get_link(token):
        abort(404)
    return render_template("visitor.html")


@app.route("/record/<token>", methods=["POST"])
def record_visit(token):
    rate_limit("record", RECORD_PER_MINUTE)
    if not csrf_ok():
        return {"success": False, "error": "csrf"}, 400
    store = get_store()
    if not store.get_link(token):
        abort(404)

    ip = client_ip()
    city, region, country, latitude, longitude = lookup_location(ip)

    agent = parse(request.headers.get("User-Agent", ""))
    if agent.is_mobile:
        device = "Mobile"
    elif agent.is_tablet:
        device = "Tablet"
    else:
        device = "Desktop"
    browser = agent.browser.family or "Unknown"

    store.add_visit(
        {
            "token": token,
            "visited_at": now_iso(),
            "city": city,
            "region": region,
            "country": country,
            "latitude": latitude,
            "longitude": longitude,
            "device": device,
            "browser": browser,
        }
    )
    return {"success": True}


# ------------------------------------------------------------- error pages


@app.errorhandler(404)
def not_found(_error):
    return (
        render_template(
            "error.html", code=404, message="This link does not exist."
        ),
        404,
    )


@app.errorhandler(400)
def bad_request(_error):
    return (
        render_template(
            "error.html", code=400, message="Invalid or expired request."
        ),
        400,
    )


@app.errorhandler(429)
def too_many(_error):
    return (
        render_template(
            "error.html",
            code=429,
            message="Too many requests. Please wait a minute and try again.",
        ),
        429,
    )


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", 5000)),
        debug=False,
    )
