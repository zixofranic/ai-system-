"""
TikTok Video Uploader for Wisdom Pipeline
==========================================
Uploads approved videos to TikTok using the Content Posting API v2.

Flow:
  1. Query Supabase for content with tiktok_publish_requested=true
  2. Download video from Google Drive
  3. Upload to TikTok via Content Posting API
  4. Update Supabase with tiktok_video_id

Usage:
    python tiktok_uploader.py                     # publish all flagged content
    python tiktok_uploader.py --id <content_id>   # upload a single item
    python tiktok_uploader.py --dry-run           # preview without uploading
"""

import argparse
import json
import os
import sys
import tempfile
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
# Use sandbox keys if available (sandbox app issued the tokens), fall back to production
TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_SANDBOX_CLIENT_KEY") or os.environ.get("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_SANDBOX_CLIENT_SECRET") or os.environ.get("TIKTOK_CLIENT_SECRET", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def get_channel(channel_id):
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel_id}&select=*",
        headers=HEADERS, timeout=10,
    )
    channels = resp.json()
    return channels[0] if channels else None


def refresh_tiktok_token(channel):
    """Refresh TikTok access token using stored refresh token."""
    settings = channel.get("settings", {}) or {}
    refresh_token = settings.get("tiktok_refresh_token")
    if not refresh_token:
        raise ValueError("No TikTok refresh token for this channel")

    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    data = resp.json()
    if "access_token" not in data:
        raise ValueError(f"Token refresh failed: {data}")

    # Update stored tokens
    settings["tiktok_access_token"] = data["access_token"]
    settings["tiktok_refresh_token"] = data.get("refresh_token", refresh_token)
    settings["tiktok_token_expiry"] = (
        datetime.now(timezone.utc).timestamp() + data.get("expires_in", 86400)
    )
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel['id']}",
        headers=HEADERS,
        json={"settings": settings},
        timeout=10,
    )
    return data["access_token"]


def get_tiktok_token(channel):
    """Get valid TikTok access token, refreshing if needed."""
    settings = channel.get("settings", {}) or {}
    token = settings.get("tiktok_access_token")
    expiry = settings.get("tiktok_token_expiry", 0)

    if token and (isinstance(expiry, (int, float)) and expiry > time.time() + 300):
        return token

    return refresh_tiktok_token(channel)


def download_from_drive(video_drive_url, channel):
    """Download video from Google Drive to a temp file."""
    # Extract file ID from Drive URL
    file_id = None
    if "/file/d/" in video_drive_url:
        file_id = video_drive_url.split("/file/d/")[1].split("/")[0]
    elif "id=" in video_drive_url:
        file_id = video_drive_url.split("id=")[1].split("&")[0]

    if not file_id:
        raise ValueError(f"Cannot extract file ID from: {video_drive_url}")

    # Get Google access token
    settings = channel.get("settings", {}) or {}
    google_refresh = settings.get("google_drive_refresh_token") or settings.get("google_refresh_token")
    if not google_refresh:
        raise ValueError("No Google refresh token")

    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "refresh_token": google_refresh,
        "grant_type": "refresh_token",
    }, timeout=30)
    google_token = token_resp.json().get("access_token")

    # Download
    dl_resp = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
        headers={"Authorization": f"Bearer {google_token}"},
        stream=True, timeout=300,
    )
    dl_resp.raise_for_status()

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    for chunk in dl_resp.iter_content(chunk_size=8192):
        tmp.write(chunk)
    tmp.close()
    size = os.path.getsize(tmp.name)
    print(f"  Downloaded: {size / 1024 / 1024:.1f} MB")
    return tmp.name


def upload_to_tiktok(video_path, title, token, open_id):
    """Upload video to TikTok using Content Posting API v2."""
    file_size = os.path.getsize(video_path)

    # Step 1: Initialize upload
    init_resp = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": file_size,
                "total_chunk_count": 1,
            },
        },
        timeout=30,
    )

    init_data = init_resp.json()
    if init_data.get("error", {}).get("code") != "ok":
        # Try direct publish instead of inbox
        init_resp = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "post_info": {
                    "title": title[:150],
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": file_size,
                    "total_chunk_count": 1,
                },
            },
            timeout=30,
        )
        init_data = init_resp.json()

    if "data" not in init_data:
        raise ValueError(f"TikTok init failed: {json.dumps(init_data)[:300]}")

    publish_id = init_data["data"]["publish_id"]
    upload_url = init_data["data"]["upload_url"]

    print(f"  Upload URL obtained, publish_id: {publish_id}")

    # Step 2: Upload video file
    with open(video_path, "rb") as f:
        upload_resp = requests.put(
            upload_url,
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
            },
            data=f,
            timeout=600,
        )

    if upload_resp.status_code not in (200, 201):
        raise ValueError(f"Upload failed: {upload_resp.status_code} {upload_resp.text[:200]}")

    print(f"  Video uploaded successfully")

    # Step 3: Check publish status
    for attempt in range(30):
        time.sleep(5)
        status_resp = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"publish_id": publish_id},
            timeout=30,
        )
        status_data = status_resp.json()
        status = status_data.get("data", {}).get("status")

        if status == "PUBLISH_COMPLETE":
            video_id = status_data["data"].get("publicaly_available_post_id", [None])
            if isinstance(video_id, list):
                video_id = video_id[0] if video_id else publish_id
            print(f"  Published! Video ID: {video_id}")
            return video_id or publish_id

        if status in ("FAILED", "PUBLISH_FAILED"):
            fail_reason = status_data.get("data", {}).get("fail_reason", "unknown")
            raise ValueError(f"Publish failed: {fail_reason}")

        print(f"  Status: {status} (attempt {attempt + 1}/30)")

    return publish_id  # Return publish_id even if status check times out


CHANNEL_HASHTAGS = {
    "wisdom": ["#Philosophy", "#Wisdom", "#Shorts", "#DeepEchoesOfWisdom"],
    "gibran": ["#Gibran", "#KahlilGibran", "#TheProphet", "#Shorts"],
}


def build_tiktok_caption(content, channel_slug="wisdom"):
    """Build TikTok caption from title, description, and tags."""
    title = content.get("title", "")
    description = content.get("description", "")
    params = content.get("generation_params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    tags = params.get("tags", [])

    # Build caption: title + key hashtags (TikTok limit ~2200 chars)
    caption = title
    if description:
        # Take first 1-2 sentences of description
        first_line = description.split("\n")[0][:200]
        caption = f"{title}\n\n{first_line}"

    # Add hashtags from AI-generated tags
    hashtags = []
    if tags:
        for tag in tags[:8]:
            ht = "#" + tag.replace(" ", "").replace("-", "")
            hashtags.append(ht)

    # Add channel-specific hashtags
    for ht in CHANNEL_HASHTAGS.get(channel_slug, CHANNEL_HASHTAGS["wisdom"]):
        if ht not in hashtags:
            hashtags.append(ht)

    philosopher = content.get("philosopher", "")
    if philosopher:
        phil_tag = "#" + philosopher.replace(" ", "")
        if phil_tag not in hashtags:
            hashtags.insert(0, phil_tag)

    caption += "\n\n" + " ".join(hashtags)
    return caption[:2200]


def process_content(content, dry_run=False):
    """Process a single content item for TikTok upload."""
    content_id = content["id"]
    title = content.get("title", "")
    print(f"\n  [{content_id[:8]}] {title[:50]}")

    channel = get_channel(content["channel_id"])
    if not channel:
        print("    Channel not found")
        return False

    settings = channel.get("settings", {}) or {}
    if not settings.get("tiktok_connected"):
        print("    TikTok not connected for this channel")
        return False

    if dry_run:
        print("    [dry-run] Would upload to TikTok")
        return True

    try:
        # Get TikTok token
        token = get_tiktok_token(channel)
        open_id = settings.get("tiktok_open_id", "")

        # Download video (prefer Supabase Storage, fallback to Drive)
        storage_path = content.get("video_storage_path")
        if storage_path:
            from supabase_storage import download_from_storage
            import tempfile as _tmpfile
            tmp = _tmpfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            video_path = download_from_storage("wisdom-videos", storage_path, tmp.name)
        else:
            video_path = download_from_drive(content["video_drive_url"], channel)

        # Upload with full caption
        channel_slug = channel.get("slug", "wisdom")
        caption = build_tiktok_caption(content, channel_slug=channel_slug)
        tiktok_id = upload_to_tiktok(video_path, caption, token, open_id)

        # Update Supabase
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}",
            headers=HEADERS,
            json={
                "tiktok_video_id": tiktok_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=10,
        )
        print(f"    TikTok published: {tiktok_id}")

        # Clean up temp file
        os.unlink(video_path)
        return True

    except Exception as e:
        print(f"    Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", help="Upload specific content ID")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("  TIKTOK UPLOADER")
    print(f"  {datetime.now()}")
    print("=" * 60)

    if args.id:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/content?id=eq.{args.id}&select=*",
            headers=HEADERS, timeout=10,
        )
        items = resp.json()
    else:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/content",
            headers=HEADERS,
            params={
                "select": "id,title,description,philosopher,channel_id,video_drive_url,video_storage_path,generation_params",
                "status": "eq.approved",
                "or": "(video_drive_url.not.is.null,video_storage_path.not.is.null)",
                "tiktok_video_id": "is.null",
                "deleted_at": "is.null",
                "order": "created_at.asc",
                "limit": "10",
            },
            timeout=10,
        )
        items = resp.json() if resp.status_code == 200 else []
        # Filter for tiktok_publish_requested
        items = [
            i for i in items
            if (i.get("generation_params") or {}).get("tiktok_publish_requested")
        ]

    print(f"  Found {len(items)} item(s) to upload")

    success = 0
    for item in items:
        if process_content(item, args.dry_run):
            success += 1

    print(f"\n{'='*60}")
    print(f"  Done: {success}/{len(items)} uploaded")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
