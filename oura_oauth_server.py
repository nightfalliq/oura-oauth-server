import os
import sys
import requests
import sqlite3
import csv
import logging
from flask import Flask, request, jsonify
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

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
    logging.debug("Authorization Code Received:", auth_code)

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

def get_oura_email(access_token):
    """
    Fetches the user's email from Oura to associate with their token.
    """
    url = "https://api.ouraring.com/v2/usercollection/personal_info"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    logging.debug("🔹 Oura User Info Response:", response.status_code, response.text)  # Debugging

    if response.status_code == 200:
        return response.json().get("email", "unknown_user")

    logging.warning(f"❌ Failed to retrieve user email: {response.status_code}")
    return "unknown_user"


@app.route("/fetch_oura_data/<email>")
def fetch_oura_data(email):
    """
    Uses the stored access token to fetch Oura data for a specific user.
    Fetches the most recent year of data or less if they have less data.
    """
    cursor.execute("SELECT access_token FROM users WHERE email=?", (email,))
    row = cursor.fetchone()

    if not row:
        logging.warning(f"User {email} not found in database.")
        return jsonify({"error": "User not found"}), 404

    access_token = row[0]

    # Calculate the date range for the last year
    end_date = datetime.today().date()
    start_date = end_date - timedelta(days=365)  # 365 days ago

    # Convert to string format (YYYY-MM-DD)
    start_date_str = start_date.strftime('%Y-%m-%d')
    end_date_str = end_date.strftime('%Y-%m-%d')

    data = {}
    endpoints = {
        "email": "https://api.ouraring.com/v2/usercollection/email",
        "personal_info": "https://api.ouraring.com/v2/usercollection/personal_info",
        "daily_data": "https://api.ouraring.com/v2/usercollection/daily",
        "heart_rate_data": "https://api.ouraring.com/v2/usercollection/heartrate",
        "workout_data": "https://api.ouraring.com/v2/usercollection/workout",
        "tags_data": "https://api.ouraring.com/v2/usercollection/tags",
    }

    for key, url in endpoints.items():
        print(f"🔹 Fetching data from endpoint: {key} ({url})")  # Debugging

        try:
            params = {'start_date': start_date_str, 'end_date': end_date_str}
            logging.debug(f"Fetching data from endpoint: {url}")
            response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
            data[key] = {
                "status_code": response.status_code,
                "data": response.json() if response.status_code == 200 else None
            }
            logging.debug(f"Data for {key}: {response.status_code}")
        except Exception as e:
            data[key] = {"status_code": 500, "error": f"Error fetching data: {e}"}
            logging.error(f"Error fetching {key}: {e}")

    # Check if there is any valid data and save the available data
    if any(entry.get("data") for entry in data.values()):
        logging.info(f"🔹 Data available for {email}. Saving to CSV...")
        save_combined_csv(email, data)
        return jsonify(data)

    logging.warning(f"Failed to fetch valid data for {email}.")
    return jsonify({
        "error": "Failed to fetch Oura data",
        "details": data
    })

def save_combined_csv(email, data):
    """
    Saves all available data into a single CSV file per client.
    """
    folder_path = r"C:\Users\chels\Documents\NIQ_data"
    filename = os.path.join(folder_path, f"{email}_oura_data_{datetime.now().strftime('%Y-%m-%d')}.csv")

    logging.debug(f"📂 [DEBUG] Attempting to save data for: {email}")
    logging.debug(f"📁 [DEBUG] Target folder: {folder_path}")
    logging.debug(f"📄 [DEBUG] Target file: {filename}")


    # Make sure the folder exists
    os.makedirs(folder_path, exist_ok=True)

    try:
        # Open the CSV file (will create it if it doesn't exist)
        with open(filename, mode='w', newline='') as file:
            fieldnames = ["data_type", "status_code", "data"]
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()

            # Flatten data and write it into the CSV
            for data_type, data_content in data.items():
                if data_content:
                    # Debug: print what data is being written for each type
                    logging.info(f"🔹 Writing data for {data_type}: {data_content}")
                    # If data has 'data' key (usually a list of entries)
                    if isinstance(data_content, dict) and "data" in data_content:
                        for entry in data_content["data"]:
                            writer.writerow(
                                {"data_type": data_type, "status_code": data_content.get("status_code", "N/A"),
                                 "data": entry})
                    else:  # In case it's not a list, store it directly
                        writer.writerow({"data_type": data_type, "status_code": data_content.get("status_code", "N/A"),
                                         "data": data_content})
            logging.info(f"📄 Data written to {filename} successfully.")
    except Exception as e:
        logging.error(f"❌ Failed to save CSV file: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
