import requests
import os
import sqlite3
from datetime import datetime, timedelta

# Render App URL (replace with your actual Render domain)
RENDER_APP_URL = "https://your-render-app.onrender.com"

# Local folder for storing JSON files
LOCAL_FOLDER = os.path.expanduser("~/Documents/OuraData")
os.makedirs(LOCAL_FOLDER, exist_ok=True)

# Connect to SQLite database
conn = sqlite3.connect("oura_tokens.db")
cursor = conn.cursor()

def refresh_token(email):
    """
    Refresh the Oura token if it has expired.
    """
    cursor.execute("SELECT refresh_token FROM users WHERE email=?", (email,))
    row = cursor.fetchone()

    if not row:
        print(f"❌ No refresh token found for {email}")
        return None

    refresh_token = row[0]
    token_url = "https://cloud.ouraring.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "YOUR_CLIENT_ID",
        "client_secret": "YOUR_CLIENT_SECRET"
    }

    response = requests.post(token_url, data=payload)

    if response.status_code == 200:
        new_token_data = response.json()
        new_access_token = new_token_data.get("access_token")
        new_refresh_token = new_token_data.get("refresh_token")

        # ✅ Update the database with the new tokens
        cursor.execute("UPDATE users SET access_token=?, refresh_token=? WHERE email=?", 
                       (new_access_token, new_refresh_token, email))
        conn.commit()

        print(f"✅ Token refreshed for {email}")
        return new_access_token

    else:
        print(f"❌ Failed to refresh token for {email}: {response.text}")
        return None

def download_file(email, data_type):
    """
    Download JSON files for a client.
    """
    # ✅ Get the latest access token, refreshing if needed
    cursor.execute("SELECT access_token FROM users WHERE email=?", (email,))
    row = cursor.fetchone()

    if not row:
        print(f"❌ No access token found for {email}")
        return

    access_token = row[0]

    # ✅ Refresh token if access token is expired
    if not access_token:
        access_token = refresh_token(email)
        if not access_token:
            return

    url = f"{RENDER_APP_URL}/download/{email}/{data_type}"
    response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})

    if response.status_code == 200:
        client_folder = os.path.join(LOCAL_FOLDER, email)
        os.makedirs(client_folder, exist_ok=True)

        file_path = os.path.join(client_folder, f"{data_type}.json")
        with open(file_path, "wb") as file:
            file.write(response.content)

        print(f"✅ Downloaded {data_type}.json for {email}")
    else:
        print(f"❌ Failed to download {data_type} for {email}")

# Get users and download files
cursor.execute("SELECT email FROM users")
users = [row[0] for row in cursor.fetchall()]

DATA_TYPES = ["heart_rate_data", "workout_data", "daily_data"]

for email in users:
    for data_type in DATA_TYPES:
        download_file(email, data_type)

conn.close()
