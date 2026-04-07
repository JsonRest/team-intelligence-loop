#!/usr/bin/env python3
"""
Run this once locally to get your Google OAuth refresh token.
The token is stored permanently and used for the Cloud Run deployment.

Usage:
    pip install google-auth-oauthlib
    python get_refresh_token.py

Then copy the printed GOOGLE_WORKSPACE_REFRESH_TOKEN value into
your .env and into Cloud Run Secret Manager.
"""
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Reads from client_secret.json in the same directory.
# Download this from GCP Console → Credentials → your Desktop App OAuth client.
flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)

print("\nOpening browser for OAuth consent...")
print("Grant access to Calendar and Gmail when prompted.\n")

creds = flow.run_local_server(port=0)

print("\n" + "="*60)
print("SUCCESS — copy these values into your .env and Secret Manager:")
print("="*60)
print(f"\nGOOGLE_WORKSPACE_CLIENT_ID={creds.client_id}")
print(f"GOOGLE_WORKSPACE_CLIENT_SECRET={creds.client_secret}")
print(f"GOOGLE_WORKSPACE_REFRESH_TOKEN={creds.refresh_token}")
print("\nThis refresh token is long-lived. Store it securely.")
print("="*60 + "\n")

# Also write to a file for reference
output = {
    "client_id": creds.client_id,
    "client_secret": creds.client_secret,
    "refresh_token": creds.refresh_token,
}
with open("workspace_credentials.json", "w") as f:
    json.dump(output, f, indent=2)

print("Also saved to workspace_credentials.json — DO NOT commit this file.\n")
