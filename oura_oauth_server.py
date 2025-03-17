import os
import sys
import requests
import sqlite3
import json
import logging
from flask import Flask, request, jsonify
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

# Base directory where data will be saved
BASE_FOLDER = os.path.normpath("C:/temp/oura_data")

# Ensure the base folder exists before saving files
if not os.path.exists(BASE_FOLDER):
    os.makedirs(BASE_FOLDER, exist_ok=True)
    print(f"📁 Created missing directory: {BASE_FOLDER}")


# Read CLIENT_ID and CLIENT_SECRET from Render secret files if they exist
def read_secret(secret_name):
    try:
        with open(f"/etc/secrets/{secret_name}", "r") as file:
            return file.read().strip()
    except FileNotFoundError:
        return None


CLIENT_ID = read_secret("CLIENT_ID")
CLIENT_SECRET = read_secret("CLIENT_SECRET")
REDIRECT_URI = "https://oura-oauth-server.onrender.com/callback"

print("🔹 CLIENT_ID:", "Loaded" if CLIENT_ID else "MISSING")
print("🔹 CLIENT_SECRET:", "HIDDEN" if CLIENT_SECRET else "MISSING")
print("🔹 REDIRECT_URI:", REDIRECT_URI)

# Database setup (stores user tokens)
conn = sqlite3.connect("oura_tokens.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT, access_token TEXT)")
conn.commit()


def get_oura_email(access_token):
    """
    Fetches the user's email from Oura to associate with their token.
    """
    url = "https://api.ouraring.com/v2/usercollection/personal_info"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    logging.debug(f"🔹 Oura User Info Response: {response.status_code}, {response.text}")  # Debugging

    if response.status_code == 200:
        return response.json().get("email", "unknown_user")

    logging.warning(f"❌ Failed to retrieve user email: {response.status_code}")
    return "unknown_user"


def save_json(folder, email, data_type, data):
    """
    Saves each data type as a separate JSON file inside the user's folder.
    """
    logging.info(f"📁 Attempting to save {data_type} data for {email}")

    if not data:
        logging.warning(f"⚠️ No data found for {data_type}, skipping JSON creation")
        return

    folder = os.path.normpath(folder)  # Normalize folder path
    os.makedirs(folder, exist_ok=True)  # Ensure directory exists

    filename = os.path.join(folder, f"{data_type}_{datetime.now().strftime('%Y-%m-%d')}.json")
    filename = os.path.normpath(filename)  # Normalize full path

    logging.info(f"📁 Saving {data_type} data to {filename}")

    try:
        with open(filename, mode='w', encoding='utf-8') as file:
            json.dump(data, file, indent=4)  # ✅ Save data as formatted JSON

        logging.info(f"✅ Successfully saved {data_type} data for {email}")

        # Check if the file actually exists
        if os.path.exists(filename):
            logging.info(f"✅ Confirmed file exists: {filename}")
        else:
            logging.error(f"❌ File does NOT exist after writing: {filename}")

    except Exception as e:
        logging.error(f"❌ Error saving {data_type} data for {email}: {e}")

@app.route("/")
def home():
    logging.info("Flask app is running.")
    return "✅ Oura OAuth Server is running!"


@app.route("/callback")
def get_token():
    """
    Handles Oura's OAuth callback, exchanges the authorization code for an access token,
    and stores it for future API use.
    """
    auth_code = request.args.get("code")
    if not auth_code:
        logging.error("❌ Error: No authorization code received.")
        return "❌ Error: No authorization code received."

    # Debugging: Print auth code before exchange
    logging.debug(f"Authorization Code Received: {auth_code}")

    # Exchange the authorization code for an access token
    token_url = "https://cloud.ouraring.com/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI
    }

    response = requests.post(token_url, data=payload)

    # 🔹 Log full response for debugging
    print("Oura Token Exchange Response:", response.status_code, response.text)

    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get("access_token")
        logging.info(f"Access token retrieved successfully.")

        # Fetch user email to associate with the token
        email = get_oura_email(access_token)
        logging.info(f"User email retrieved: {email}")

        # Save token to database
        cursor.execute("INSERT INTO users (email, access_token) VALUES (?, ?)", (email, access_token))
        conn.commit()

        logging.info(f"Data saved for {email}")
        return f"✅ Access granted! Data for {email} has been stored."

    else:
        # 🔹 Return Oura's actual error message
        logging.error(f"❌ Error retrieving token: {response.status_code}")
        return f"❌ Error retrieving token: {response.status_code} - {response.text}"


@app.route("/test_save")
def test_save():
    folder = BASE_FOLDER
    test_file = os.path.join(folder, "render_test.txt")

    try:
        with open(test_file, "w") as f:
            f.write("Render test successful")
        return jsonify({"status": "✅ File write successful", "file_path": test_file})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/test_save_json")
def test_save_json():
    """
    Tests the save_json function by writing a test JSON file.
    """
    folder = os.path.normpath("C:/temp/oura_data/test_user")
    os.makedirs(folder, exist_ok=True)

    test_data = [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": 25}
    ]

    test_filename = "test_data"
    try:
        save_json(folder, "test_user", test_filename, test_data)

        # ✅ Verify the file exists after writing
        file_path = os.path.join(folder, f"{test_filename}_{datetime.now().strftime('%Y-%m-%d')}.json")
        if os.path.exists(file_path):
            return jsonify({"status": "✅ `save_json` function worked!", "file_path": file_path})
        else:
            return jsonify({"status": "❌ `save_json` function did NOT create a file!"})

    except Exception as e:
        return jsonify({"error": f"❌ Error in `save_json`: {e}"})


@app.route("/fetch_oura_data/<email>")
def fetch_oura_data(email):
    """
    Fetches Oura data for a specific user and saves each endpoint's data as a separate JSON file.
    """
    cursor.execute("SELECT access_token FROM users WHERE email=?", (email,))
    row = cursor.fetchone()

    if not row:
        logging.warning(f"User {email} not found in database.")
        return jsonify({"error": "User not found"}), 404

    access_token = row[0]

    # Define endpoints
    endpoints = {
        "email": "https://api.ouraring.com/v2/usercollection/email",
        "personal_info": "https://api.ouraring.com/v2/usercollection/personal_info",
        "daily_data": "https://api.ouraring.com/v2/usercollection/daily",
        "heart_rate_data": "https://api.ouraring.com/v2/usercollection/heartrate",
        "workout_data": "https://api.ouraring.com/v2/usercollection/workout",
        "tags_data": "https://api.ouraring.com/v2/usercollection/tags",
    }

    # Set the date range for the last year
    end_date = datetime.today().date()
    start_date = end_date - timedelta(days=365)  # 365 days ago
    params = {'start_date': start_date.strftime('%Y-%m-%d'), 'end_date': end_date.strftime('%Y-%m-%d')}

    # Define client folder
    client_folder = os.path.join(BASE_FOLDER, email)
    os.makedirs(client_folder, exist_ok=True)

    saved_files = []

    for key, url in endpoints.items():
        logging.debug(f"🔹 Fetching {key} data from {url}")

    for key, url in endpoints.items():
        try:
            response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
            response.raise_for_status()  # ✅ Ensure a failed request does not stop processing

            data = response.json().get("data", [])

            if data:
                logging.info(f"✅ Retrieved {len(data)} records from {key}, saving json.")
                save_json(client_folder, email, key, data)
                saved_files.append(f"{key}.json")
            else:
                logging.warning(f"⚠️ No data found for {key}, skipping.")

        except requests.exceptions.RequestException as e:
            logging.error(f"❌ Error fetching {key} data for {email}: {e}")
            continue  # ✅ Skip the failed endpoint and move to the next one


        # ✅ Move return statement outside loop to ensure all endpoints are processed
    logging.info(f"✅ Data retrieval complete for {email}. Saved files: {saved_files}")
    return jsonify({"status": "Data retrieval and saving complete", "saved_files": saved_files})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
