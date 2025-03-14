import os
from dotenv import load_dotenv
import requests
import sqlite3
import csv
from flask import Flask, request, jsonify
from datetime import datetime, timedelta

app = Flask(__name__)

# Load environment variables from the .env file
load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = "https://oura-oauth-server.onrender.com/callback" 

# Database setup (stores user tokens)
conn = sqlite3.connect("oura_tokens.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT, access_token TEXT)")
conn.commit()

@app.route("/")
def home():
    return "✅ Oura OAuth Server is running!"

@app.route("/callback")
def get_token():
    """
    Handles Oura's OAuth callback, exchanges the authorization code for an access token,
    and stores it for future API use.
    """
    auth_code = request.args.get("code")
    if not auth_code:
        return "❌ Error: No authorization code received."

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
    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get("access_token")

        # Fetch user email to associate with the token
        email = get_oura_email(access_token)

        # Save token to database
        cursor.execute("INSERT INTO users (email, access_token) VALUES (?, ?)", (email, access_token))
        conn.commit()

        return f"✅ Access granted! Data for {email} has been stored."
    
    return "❌ Error: We were unable to retrieve the token. Please try again later."

def get_oura_email(access_token):
    """
    Fetches the user's email from Oura to associate with their token.
    """
    url = "https://api.ouraring.com/v2/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json().get("email", "unknown_user")
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
        "personal_info": "https://api.ouraring.com/v2/usercollection/personal_info",
        "daily_data": "https://api.ouraring.com/v2/usercollection/daily",
        "heart_rate_data": "https://api.ouraring.com/v2/usercollection/heartrate",
        "workout_data": "https://api.ouraring.com/v2/usercollection/workout",
        "tags_data": "https://api.ouraring.com/v2/usercollection/tags",
        "spo2_data": "https://api.ouraring.com/v2/usercollection/spo2_daily"
    }
    
    for key, url in endpoints.items():
        try:
            params = {'start_date': start_date_str, 'end_date': end_date_str}
            response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
            data[key] = {
                "status_code": response.status_code,
                "data": response.json() if response.status_code == 200 else None
            }
        except Exception as e:
            data[key] = {"status_code": 500, "error": f"Error fetching data: {e}"}

    # Check if there is any valid data and save the available data
    if any(entry.get("data") for entry in data.values()):
        save_combined_csv(email, data)
        return jsonify(data)
    
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
    
    # Make sure the folder exists
    os.makedirs(folder_path, exist_ok=True)
    
    with open(filename, mode='w', newline='') as file:
        fieldnames = ["data_type", "status_code", "data"]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for data_type, data_content in data.items():
            if data_content and "data" in data_content:
                for entry in data_content["data"]:
                    writer.writerow({"data_type": data_type, "status_code": data_content.get("status_code", "N/A"), "data": entry})
            else:
                writer.writerow({"data_type": data_type, "status_code": data_content.get("status_code", "N/A"), "data": data_content})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
