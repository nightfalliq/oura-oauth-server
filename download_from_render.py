# download_from_render.py
import os
import json
import requests
from datetime import datetime

# =============================
# CONFIG
# =============================
RENDER_APP_URL = os.getenv("RENDER_APP_URL", "https://oura-oauth-server.onrender.com").rstrip("/")
LOCAL_FOLDER = os.path.expanduser(os.getenv("NIQ_LOCAL_DIR", "~/Documents/NIQ_Data"))
os.makedirs(LOCAL_FOLDER, exist_ok=True)

# These must match your server slugs in oura_auth_server.py -> oura_endpoints()
DATA_TYPES = [
    "email",
    "personal_info",
    "daily_data",
    "heart_rate_data",
    "workout_data",
    "tags_data",
]

USERS_ENDPOINT = f"{RENDER_APP_URL}/users"
DOWNLOAD_ENDPOINT_TMPL = f"{RENDER_APP_URL}/download/{{email}}/{{data_type}}"

# =============================
# HELPERS
# =============================
def log(msg: str):
    print(msg, flush=True)

def get_users():
    try:
        log(f"🌐 GET {USERS_ENDPOINT}")
        resp = requests.get(USERS_ENDPOINT, timeout=30)
        if resp.status_code != 200:
            log(f"❌ /users failed: {resp.status_code} {resp.text[:200]}")
            return []
        data = resp.json()
        if isinstance(data, list):
            return data
        log(f"⚠️ Unexpected /users payload: {data}")
        return []
    except requests.RequestException as e:
        log(f"❌ Network error calling /users: {e}")
        return []

def save_json_local(email: str, data_type: str, content: bytes):
    # Save as {slug}_YYYY-MM-DD.json (same naming your server uses)
    today = datetime.now().strftime("%Y-%m-%d")
    email_dir = os.path.join(LOCAL_FOLDER, email)
    os.makedirs(email_dir, exist_ok=True)
    path = os.path.join(email_dir, f"{data_type}_{today}.json")

    with open(path, "wb") as f:
        f.write(content)
    log(f"✅ Saved {data_type} for {email} → {path} ({len(content)} bytes)")

def download_one(email: str, data_type: str):
    url = DOWNLOAD_ENDPOINT_TMPL.format(email=email, data_type=data_type)
    try:
        log(f"🔗 GET {url}")
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            # Ensure it’s valid JSON (for debugging), but save regardless
            try:
                _ = resp.json()
            except ValueError:
                log(f"⚠️ Response not JSON; saving raw bytes.")
            save_json_local(email, data_type, resp.content)
        else:
            snippet = resp.text[:300] if resp.text else ""
            log(f"❌ {data_type} for {email} failed → {resp.status_code}. Body: {snippet}")
    except requests.RequestException as e:
        log(f"❌ Network error for {email}/{data_type}: {e}")

def main():
    log(f"📁 Local storage: {LOCAL_FOLDER}")
    users = get_users()
    log(f"👥 Found {len(users)} user(s).")
    if not users:
        log("ℹ️ No users from server. Finish OAuth first, then retry.")
        return

    for email in users:
        log(f"\n📡 Processing user: {email}")
        for data_type in DATA_TYPES:
            download_one(email, data_type)

    log("\n✅ All downloads completed.")

if __name__ == "__main__":
    main()
