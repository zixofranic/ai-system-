"""
YouTube OAuth Token Generator for Brand Accounts
==================================================
Generates a refresh token for uploading to a Brand Account's YouTube channel.

THE KEY INSIGHT:
When a Google account (e.g., ziadfeg@gmail.com) owns a Brand Account,
the OAuth consent flow presents TWO screens:
  1. "Choose an account" — select your Google account (ziadfeg@gmail.com)
  2. "Choose a channel"  — select which YouTube channel to authorize
     (personal channel OR Brand Account channel)

If you select the Brand Account channel ("Deep Echoes of Wisdom") in step 2,
the resulting refresh token is BOUND to that Brand Account channel.
All API calls using that token will operate on the Brand Account channel.

If you select your personal channel ("ziad feghali") in step 2,
the token is bound to your personal channel instead.

IMPORTANT REQUIREMENTS:
  1. Your OAuth app must be set to "External" (not Internal) in Google Cloud Console
  2. Your email (ziadfeg@gmail.com) must be listed as a Test User in the consent screen
  3. The Brand Account email/identity is implicitly a test user if the managing
     personal account is listed as a test user
  4. If the app is in "Testing" mode, refresh tokens expire after 7 days.
     To get permanent tokens, publish the app to "Production" (requires verification
     for sensitive scopes like youtube.upload — but you can still use Testing mode
     and re-run this script every 7 days if verification is pending)

Usage:
    python generate_youtube_token.py
    python generate_youtube_token.py --channel wisdom   # hint for which channel
    python generate_youtube_token.py --channel gibran

After running, copy the refresh token and store it in Supabase channel settings
as google_refresh_token.

Python env: /c/Users/ziadf/miniconda3/envs/lora_train/python.exe
"""

import argparse
import json
import os
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv("C:/AI/.env")

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]

# Google OAuth endpoints
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Loopback redirect for desktop OAuth flow
REDIRECT_PORT = 8976
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"

# Scopes needed for YouTube upload + Drive storage
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
]

# Channel hints
CHANNEL_HINTS = {
    "wisdom": {
        "name": "Deep Echoes of Wisdom",
        "id": "UCg2xRMNl-w3u2_Rhm6FHrdA",
        "account": "ziadfeg@gmail.com",
    },
    "gibran": {
        "name": "Gibran",
        "id": "UCClMnqz-TZoBOmiDZGUqMxQ",
        "account": None,  # Elias's account
    },
}


# ---------------------------------------------------------------------------
# Local HTTP server to capture the OAuth callback
# ---------------------------------------------------------------------------
class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect callback on localhost."""

    auth_code = None
    error = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
            <h1>Authorization successful!</h1>
            <p>You can close this window and return to the terminal.</p>
            </body></html>
            """)
        elif "error" in params:
            OAuthCallbackHandler.error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            error_msg = params.get("error_description", [params["error"][0]])[0]
            self.wfile.write(f"""
            <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
            <h1>Authorization failed</h1>
            <p style="color:red;">{error_msg}</p>
            </body></html>
            """.encode())
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default HTTP log noise
        pass


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------
def build_auth_url(login_hint: str = None) -> str:
    """
    Build the Google OAuth authorization URL.

    Key parameters:
      - prompt=consent         : Force showing the consent screen every time
                                 (ensures we get a refresh_token)
      - access_type=offline    : Request a refresh_token (for background use)
      - login_hint             : Pre-fill the email for convenience
      - include_granted_scopes : Incremental authorization

    The user will see:
      1. Account picker (select Google account)
      2. Channel picker (select personal channel OR Brand Account channel)
      3. Consent screen (approve scopes)

    CRITICAL: In step 2, SELECT THE BRAND ACCOUNT CHANNEL to get a token
    that uploads to the Brand Account, not the personal channel.
    """
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "select_account consent",  # Force account picker + consent
        "include_granted_scopes": "true",
    }

    # NOTE: Do NOT set login_hint — it can cause Google to skip showing
    # Brand Account options in the account picker.

    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(auth_code: str) -> dict:
    """Exchange the authorization code for access + refresh tokens."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": auth_code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"\nERROR: Token exchange failed ({resp.status_code})")
        print(resp.text)
        sys.exit(1)

    return resp.json()


def verify_token_channel(access_token: str) -> dict:
    """
    Call the YouTube channels.list API with mine=true to verify
    which channel this token is actually associated with.
    """
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet,id", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )

    if resp.status_code != 200:
        print(f"\nWARNING: Could not verify channel ({resp.status_code})")
        print(resp.text[:300])
        return {}

    data = resp.json()
    items = data.get("items", [])
    if items:
        ch = items[0]
        return {
            "channel_id": ch["id"],
            "channel_title": ch["snippet"]["title"],
        }
    return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Generate YouTube OAuth refresh token for Brand Account uploads."
    )
    parser.add_argument(
        "--channel",
        choices=["wisdom", "gibran"],
        default=None,
        help="Channel hint (pre-fills login email)",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Use manual code entry instead of localhost redirect",
    )
    args = parser.parse_args()

    # Determine login hint
    login_hint = None
    if args.channel and args.channel in CHANNEL_HINTS:
        hint = CHANNEL_HINTS[args.channel]
        login_hint = hint.get("account")
        print(f"\nTarget channel: {hint['name']} ({hint['id']})")
        print(f"Login hint: {login_hint or '(none)'}")

    print("\n" + "=" * 60)
    print("  YOUTUBE OAUTH TOKEN GENERATOR")
    print("=" * 60)
    print()
    print("IMPORTANT: When the browser opens, you will see up to 3 screens:")
    print()
    print("  1. CHOOSE AN ACCOUNT — Select your Google account")
    print("     (e.g., ziadfeg@gmail.com)")
    print()
    print("  2. CHOOSE A CHANNEL — This is the CRITICAL step!")
    print("     Select the BRAND ACCOUNT channel, NOT your personal channel.")
    print('     For Wisdom: select "Deep Echoes of Wisdom"')
    print('     For Gibran: select "Gibran"')
    print()
    print("  3. GRANT PERMISSIONS — Approve the requested YouTube scopes.")
    print()
    print("  The resulting token will be bound to whichever channel you")
    print("  select in step 2.")
    print()

    if args.manual:
        # Manual flow: user copies code from browser
        auth_url = build_auth_url(login_hint)
        # For manual flow, use the OOB-like redirect (but Google deprecated OOB,
        # so we use loopback but tell the user to copy from the URL bar)
        print(f"Open this URL in your browser:\n\n{auth_url}\n")
        print("After authorizing, you will be redirected to localhost.")
        print("Copy the 'code' parameter from the URL bar.\n")
        auth_code = input("Paste the authorization code here: ").strip()
    else:
        # Automatic flow: start local server and open browser
        auth_url = build_auth_url(login_hint)

        print(f"Opening browser for authorization...\n")
        print(f"(If browser doesn't open, visit this URL manually):\n{auth_url}\n")

        # Start local server
        server = HTTPServer(("localhost", REDIRECT_PORT), OAuthCallbackHandler)
        webbrowser.open(auth_url)

        print("Waiting for authorization callback...")
        server.handle_request()  # Handle one request (the OAuth callback)
        server.server_close()

        if OAuthCallbackHandler.error:
            print(f"\nERROR: Authorization failed: {OAuthCallbackHandler.error}")
            sys.exit(1)

        auth_code = OAuthCallbackHandler.auth_code
        if not auth_code:
            print("\nERROR: No authorization code received.")
            sys.exit(1)

    print("\nAuthorization code received. Exchanging for tokens...")

    # Exchange code for tokens
    tokens = exchange_code_for_tokens(auth_code)

    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    expires_in = tokens.get("expires_in", 0)

    if not refresh_token:
        print("\nWARNING: No refresh_token received!")
        print("This can happen if:")
        print("  - You didn't use prompt=consent (already handled)")
        print("  - The app previously authorized without revoking first")
        print("\nTry revoking access at https://myaccount.google.com/permissions")
        print("then run this script again.\n")

    # Verify which channel the token is associated with
    print("\nVerifying token channel association...")
    channel_info = verify_token_channel(access_token)

    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)

    if channel_info:
        print(f"\n  Channel ID    : {channel_info.get('channel_id', '?')}")
        print(f"  Channel Title : {channel_info.get('channel_title', '?')}")

        # Check if this is the expected channel
        expected_id = None
        if args.channel and args.channel in CHANNEL_HINTS:
            expected_id = CHANNEL_HINTS[args.channel]["id"]

        if expected_id and channel_info.get("channel_id") == expected_id:
            print("\n  TOKEN IS FOR THE CORRECT CHANNEL!")
        elif expected_id and channel_info.get("channel_id") != expected_id:
            print(f"\n  WARNING: Token is for a DIFFERENT channel than expected!")
            print(f"  Expected: {expected_id}")
            print(f"  Got:      {channel_info.get('channel_id')}")
            print(f"\n  You likely selected the wrong channel in the consent screen.")
            print(f"  Revoke access and try again, selecting the Brand Account channel.")

    print(f"\n  Access Token  : {access_token[:30]}...")
    print(f"  Expires In    : {expires_in} seconds")
    print(f"\n  Refresh Token : {refresh_token}")

    print(f"\n{'='*60}")
    print("  NEXT STEPS")
    print("=" * 60)
    print()
    print("  1. Copy the refresh token above")
    print("  2. Store it in Supabase channel settings as 'google_refresh_token':")
    print()
    print("     UPDATE channels SET settings = settings || ")
    print(f"       '{{\"google_refresh_token\": \"{refresh_token}\"}}'::jsonb")
    print(f"     WHERE id = '<channel-uuid>';")
    print()
    print("  3. The youtube_uploader.py will automatically use this token")
    print("     to upload videos to the Brand Account channel.")
    print()

    if not refresh_token:
        print("  NOTE: No refresh token was returned. You need to:")
        print("  - Go to https://myaccount.google.com/permissions")
        print("  - Remove your app's access")
        print("  - Run this script again")
        print()


if __name__ == "__main__":
    main()
