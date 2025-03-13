import requests
import sqlite3
from flask import Flask, request, redirect, jsonify

app = Flask(__name__)

# Replace these with your Oura API credentials
CLIENT_ID = "5WKQJB355KD6ELTL"
CLIENT_SECRET = "BX5X4ENYHMEPQJOMERBYY4L24NR2R7UX"
REDIRECT_URI = "https://oura-oauth-server.onrender.com/callback"  # Update after deploying

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

    else:
        return f"❌ Error retrieving token: {response.text}"

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
    """
    cursor.execute("SELECT access_token FROM users WHERE email=?", (email,))
    row = cursor.fetchone()

    if not row:
        return jsonify({"error": "User not found"}), 404

    access_token = row[0]

    # Fetch Sleep Data as an example
    sleep_url = "https://api.ouraring.com/v2/usercollection/sleep"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(sleep_url, headers=headers)

    if response.status_code == 200:
        return jsonify(response.json())

    return jsonify({"error": "Failed to fetch Oura data", "details": response.text})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
