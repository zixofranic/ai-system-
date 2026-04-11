"""
YouTube OAuth Token Generator — Device Code Flow
===================================================
Uses the device code flow which shows a different Google UI
that may better support Brand Account channel selection.

Usage:
    python generate_youtube_token_device.py
"""

import json
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/drive.file",
]

DEVICE_AUTH_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def main():
    print("\n" + "=" * 60)
    print("  YOUTUBE TOKEN GENERATOR (Device Code Flow)")
    print("=" * 60)

    # Step 1: Request device code
    resp = requests.post(DEVICE_AUTH_URL, data={
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPES),
    })

    if resp.status_code != 200:
        print(f"ERROR: {resp.status_code} - {resp.text}")
        return

    data = resp.json()
    user_code = data["user_code"]
    device_code = data["device_code"]
    verification_url = data["verification_url"]
    interval = data.get("interval", 5)

    print(f"\n  1. Go to: {verification_url}")
    print(f"  2. Enter code: {user_code}")
    print(f"\n  IMPORTANT: After entering the code, Google will ask you to")
    print(f"  choose an account. Select ziadfeg@gmail.com, then when it")
    print(f"  asks to choose a channel, select 'Deep Echoes of Wisdom'.")
    print(f"\n  Waiting for authorization...")

    # Step 2: Poll for token
    while True:
        time.sleep(interval)
        token_resp = requests.post(TOKEN_URL, data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })

        token_data = token_resp.json()

        if "access_token" in token_data:
            break
        elif token_data.get("error") == "authorization_pending":
            continue
        elif token_data.get("error") == "slow_down":
            interval += 2
            continue
        else:
            print(f"\nERROR: {token_data}")
            return

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")

    # Verify channel
    ch_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet,id", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)

    if ch_resp.status_code == 200:
        items = ch_resp.json().get("items", [])
        if items:
            ch = items[0]
            print(f"\n  Channel ID    : {ch['id']}")
            print(f"  Channel Title : {ch['snippet']['title']}")

    print(f"\n  Refresh Token : {refresh_token}")
    print(f"\n  Access Token  : {access_token[:40]}...")

    if not refresh_token:
        print("\n  WARNING: No refresh token received!")

    print()


if __name__ == "__main__":
    main()
