# oura_auth_server.py
import os
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple

import requests
from flask import Flask, jsonify, request, Response

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# -----------------------------
# App
# -----------------------------
app = Flask(__name__)

def read_secret(name: str) -> str | None:
    """Read from Render secret files if present; else None."""
    try:
        with open(f"/etc/secrets/{name}", "r") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return None

# OAuth client config
CLIENT_ID = read_secret("CLIENT_ID") or os.getenv("CLIENT_ID")
CLIENT_SECRET = read_secret("CLIENT_SECRET") or os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://oura-oauth-server.onrender.com/callback")

log.info(f"🔹 CLIENT_ID: {'Loaded' if CLIENT_ID else 'MISSING'}")
log.info(f"🔹 CLIENT_SECRET: {'Loaded' if CLIENT_SECRET else 'MISSING'}")
log.info(f"🔹 REDIRECT_URI: {REDIRECT_URI}")

# -----------------------------
# Database (SQLite)
# -----------------------------
DB_PATH = os.getenv("DB_PATH", "oura_tokens.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

def init_db():
    """Create/upgrade schema so upserts work and refresh_token exists."""
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        email TEXT UNIQUE,
        access_token TEXT,
        refresh_token TEXT
    )
    """)
    # Ensure unique index (older DBs might lack it)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    # Add refresh_token column if missing (migrate old DBs)
    cursor.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in cursor.fetchall()]
    if "refresh_token" not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN refresh_token TEXT")
    conn.commit()

init_db()
log.info(f"🗄️  SQLite DB: {os.path.abspath(DB_PATH)}")

# -----------------------------
# Helpers
# -----------------------------
def oura_endpoints() -> Dict[str, str]:
    """Official slugs this server supports → Oura v2 endpoints."""
    return {
        "email": "https://api.ouraring.com/v2/usercollection/email",
        "personal_info": "https://api.ouraring.com/v2/usercollection/personal_info",
        "daily_data": "https://api.ouraring.com/v2/usercollection/daily",
        "heart_rate_data": "https://api.ouraring.com/v2/usercollection/heartrate",
        "workout_data": "https://api.ouraring.com/v2/usercollection/workout",
        "tags_data": "https://api.ouraring.com/v2/usercollection/tags",
    }

def list_dates_range(days: int = 365) -> tuple[str, str]:
    end_date = datetime.today().date()
    start_date = end_date - timedelta(days=days)
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

def get_tokens(email: str) -> str | None:
    cursor.execute("SELECT access_token FROM users WHERE email=?", (email,))
    row = cursor.fetchone()
    return row[0] if row else None

def set_tokens(email: str, access_token: str, refresh_token: str | None):
    cursor.execute("""
        INSERT INTO users (email, access_token, refresh_token)
        VALUES (?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=excluded.refresh_token
    """, (email, access_token, refresh_token))
    conn.commit()

def get_oura_email(access_token: str) -> str:
    """Fetch the user's email (Oura v2 provides a dedicated endpoint)."""
    url = oura_endpoints()["email"]
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("email", "unknown_user")
        log.warning(f"❌ Email fetch failed: {resp.status_code} {resp.text[:150]}")
    except requests.RequestException as e:
        log.error(f"Email fetch network error: {e}")
    return "unknown_user"

def fetch_one(email: str, data_type: str) -> Tuple[Any, int]:
    """
    Live-fetch a single slug from Oura and return (payload, http_status).
    Stateless: no server-side file writes.
    """
    access_token = get_tokens(email)
    if not access_token:
        return {"error": "User not found"}, 404

    endpoints = oura_endpoints()
    if data_type not in endpoints:
        return {"error": f"Unknown data_type '{data_type}'"}, 400

    url = endpoints[data_type]
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        if data_type in ["email", "personal_info"]:
            resp = requests.get(url, headers=headers, timeout=45)
            resp.raise_for_status()
            return resp.json(), 200
        else:
            start_date, end_date = list_dates_range(365)
            params = {"start_date": start_date, "end_date": end_date}
            resp = requests.get(url, headers=headers, params=params, timeout=60)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data", []) if isinstance(payload, dict) else payload
            return data, 200
    except requests.exceptions.HTTPError as e:
        body = ""
        try:
            body = e.response.text[:300]
        except Exception:
            pass
        return {"error": f"Oura error {e.response.status_code}", "body": body}, 502
    except requests.exceptions.RequestException as e:
        return {"error": "Network error", "detail": str(e)}, 502
    except ValueError:
        return {"error": "Invalid JSON from Oura"}, 502

# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def home():
    return "✅ Oura OAuth Server (stateless) is running!"

@app.get("/health")
def health():
    return jsonify({"status": "ok", "now": datetime.utcnow().isoformat() + "Z"})

@app.get("/users")
def users():
    cursor.execute("SELECT email FROM users ORDER BY email")
    return jsonify([row[0] for row in cursor.fetchall()])

@app.get("/callback")
def callback():
    """
    OAuth callback: exchange authorization code for tokens and store them.
    """
    code = request.args.get("code")
    if not code:
        return "❌ Error: No authorization code received.", 400

    token_url = "https://api.ouraring.com/oauth/token"  # Correct OAuth endpoint
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
    }
    resp = requests.post(token_url, data=payload, timeout=30)
    if resp.status_code != 200:
        return f"❌ Error retrieving token: {resp.status_code} - {resp.text}", 400

    tok = resp.json()
    access_token = tok.get("access_token")
    refresh_token = tok.get("refresh_token")
    if not access_token:
        return "❌ Error: token missing in response.", 400

    email = get_oura_email(access_token)
    try:
        set_tokens(email, access_token, refresh_token)
    except Exception as e:
        log.error(f"❌ DB save error for {email}: {e}")
        return "❌ Error: token not saved; check server logs.", 500

    # Verify persisted
    if not get_tokens(email):
        return "❌ Error: token not saved; check server logs.", 500

    return f"✅ Access granted! Token for {email} has been stored."

@app.get("/download/<email>/<data_type>")
def download(email: str, data_type: str):
    """
    Disk-free: fetch live from Oura and stream JSON to the client.
    """
    data, status = fetch_one(email, data_type)
    if status != 200:
        return jsonify(data), status
    return Response(json.dumps(data), mimetype="application/json")

@app.get("/fetch_oura_data/<email>")
def fetch_all(email: str):
    """
    Fetch all supported slugs and return a combined JSON (debug/inspection).
    """
    results: Dict[str, Any] = {}
    for key in oura_endpoints():
        data, status = fetch_one(email, key)
        results[key] = data if status == 200 else {"_error": data}
    return jsonify(results)

# -----------------------------
# Entrypoint
# -----------------------------
if __name__ == "__main__":
    # Local debug only; Render will run via its own command
    app.run(host="0.0.0.0", port=5000, debug=True)
