import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple

import requests
from flask import Flask, request, jsonify, Response
import sqlite3
import urllib.parse

# -------------------------------------------------
# Logging
# -------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# -------------------------------------------------
# App
# -------------------------------------------------
app = Flask(__name__)

def read_secret(secret_name: str) -> str | None:
    try:
        with open(f"/etc/secrets/{secret_name}", "r") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return None

CLIENT_ID = read_secret("CLIENT_ID") or os.getenv("CLIENT_ID")
CLIENT_SECRET = read_secret("CLIENT_SECRET") or os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://oura-oauth-server.onrender.com/callback")

log.info(f"🔹 CLIENT_ID: {'Loaded' if CLIENT_ID else 'MISSING'}")
log.info(f"🔹 CLIENT_SECRET: {'Loaded' if CLIENT_SECRET else 'MISSING'}")
log.info(f"🔹 REDIRECT_URI: {REDIRECT_URI}")

# -------------------------------------------------
# Database (SQLite by default; Postgres if DATABASE_URL set)
# -------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")  # e.g., postgresql://user:pass@host/db
USING_SQLITE = not DATABASE_URL

if USING_SQLITE:
    DB_PATH = os.getenv("DB_PATH", "oura_tokens.db")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    def db_execute(q, params=()):
        cursor.execute(q, params)
        conn.commit()
    def db_query(q, params=()):
        cursor.execute(q, params)
        return cursor.fetchall()
else:
    # Minimal Postgres adapter without extra deps: use sqlite3 style through pysqlite? Not available.
    # For Postgres, prefer adding 'psycopg[binary]' to your Render build. Until then, keep SQLite.
    raise RuntimeError("Postgres requires psycopg; either add it to requirements or unset DATABASE_URL to use SQLite.")

def init_db():
    db_execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        email TEXT UNIQUE,
        access_token TEXT,
        refresh_token TEXT
    )
    """)
    # Ensure unique index on email
    try:
        db_execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    except Exception:
        pass
init_db()
log.info(f"🗄️  Using {'SQLite ' + os.path.abspath(DB_PATH) if USING_SQLITE else 'Postgres via DATABASE_URL'}")

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def oura_endpoints() -> Dict[str, str]:
    return {
        "email": "https://api.ouraring.com/v2/usercollection/email",
        "personal_info": "https://api.ouraring.com/v2/usercollection/personal_info",
        "daily_data": "https://api.ouraring.com/v2/usercollection/daily",
        "heart_rate_data": "https://api.ouraring.com/v2/usercollection/heartrate",
        "workout_data": "https://api.ouraring.com/v2/usercollection/workout",
        "tags_data": "https://api.ouraring.com/v2/usercollection/tags",
    }

def get_oura_email(access_token: str) -> str:
    url = oura_endpoints()["email"]
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 200:
        try:
            data = resp.json()
            return data.get("email", "unknown_user")
        except ValueError:
            pass
    log.warning(f"❌ Failed to retrieve user email: {resp.status_code} {resp.text[:120]}")
    return "unknown_user"

def get_tokens(email: str) -> str | None:
    rows = db_query("SELECT access_token FROM users WHERE email=?", (email,))
    return rows[0][0] if rows else None

def set_tokens(email: str, access_token: str, refresh_token: str | None):
    db_execute("""
        INSERT INTO users (email, access_token, refresh_token)
        VALUES (?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
          access_token=excluded.access_token,
          refresh_token=excluded.refresh_token
    """, (email, access_token, refresh_token))

def list_dates_range(days: int = 365) -> tuple[str, str]:
    end_date = datetime.today().date()
    start_date = end_date - timedelta(days=days)
    return start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')

def fetch_one(email: str, data_type: str) -> Any:
    """
    Live-fetch a single slug from Oura and return the JSON payload (no disk writes).
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
            data = resp.json()
        else:
            start_date, end_date = list_dates_range(365)
            params = {"start_date": start_date, "end_date": end_date}
            resp = requests.get(url, headers=headers, params=params, timeout=60)
            resp.raise_for_status()
            payload = resp.json()
            # Oura list endpoints return {"data":[...]}
            data = payload.get("data", []) if isinstance(payload, dict) else payload

        # Always return valid JSON to the client
        return data, 200
    except requests.exceptions.HTTPError as e:
        return {"error": f"Oura error {e.response.status_code}", "body": e.response.text[:300]}, 502
    except requests.exceptions.RequestException as e:
        return {"error": "Network error", "detail": str(e)}, 502
    except ValueError:
        return {"error": "Invalid JSON from Oura"}, 502

# -------------------------------------------------
# Routes
# -------------------------------------------------
@app.get("/")
def home():
    return "✅ Oura OAuth Server (stateless) is running!"

@app.get("/health")
def health():
    return jsonify({"status": "ok", "now": datetime.utcnow().isoformat() + "Z"})

@app.get("/users")
def users():
    rows = db_query("SELECT email FROM users ORDER BY email")
    return jsonify([r[0] for r in rows])

@app.get("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "❌ Error: No authorization code received.", 400

    token_url = "https://api.ouraring.com/oauth/token"
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
        log.error(f"DB save error for {email}: {e}")
        return "❌ Error: token not saved; check server logs.", 500

    # Verify
    if not get_tokens(email):
        return "❌ Error: token not saved; check server logs.", 500

    return f"✅ Access granted! Token for {email} has been stored."

@app.get("/download/<email>/<data_type>")
def download(email, data_type):
    """
    Disk-free: fetch live from Oura and stream JSON to the client.
    """
    data, status = fetch_one(email, data_type)
    if status != 200:
        return jsonify(data), status
    # Stream as JSON
    return Response(json.dumps(data), mimetype="application/json")

@app.get("/fetch_oura_data/<email>")
def fetch_all(email):
    """
    Fetch all supported slugs and return them together (no writes).
    """
    results: Dict[str, Any] = {}
    endpoints = oura_endpoints()
    for key in endpoints:
        data, status = fetch_one(email, key)
        results[key] = data if status == 200 else {"_error": data}
    return jsonify(results)
