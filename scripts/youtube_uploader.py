"""
YouTube Auto-Upload for Wisdom Pipeline
========================================
Uploads approved videos to YouTube using the YouTube Data API v3.

Two channels, each with its own OAuth refresh token stored in Supabase
channels.settings JSONB:
  - Deep Echoes of Wisdom  (UCg2xRMNI-w3u2_Rhm6FHrdA)  — Brand Account token
  - Gibran                 (UCClMnqz-TZoBOmiDZGUqMxQ)  — Brand Account token

Functions:
  upload_to_youtube(content_id)   — upload one video, update Supabase on completion
  publish_approved_content()      — find all approved content and upload each

OAuth tokens are refreshed automatically via youtube_refresh_token in channel settings.
Note: google_refresh_token is the Drive (personal account) token — kept separate.
Videos are downloaded from Google Drive (video_drive_url), uploaded via resumable
upload to the YouTube Data API v3, then the temp file is cleaned up.

Usage:
    python youtube_uploader.py                     # publish all approved content
    python youtube_uploader.py --id <content_id>   # upload a single item
    python youtube_uploader.py --dry-run           # preview without uploading

Python env: /c/Users/ziadf/miniconda3/envs/lora_train/python.exe
"""

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv("C:/AI/.env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]

# YouTube Data API v3 endpoints
YOUTUBE_UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# YouTube category: 27 = Education
YOUTUBE_CATEGORY_ID = "27"

# Chunk size for resumable upload (5 MB)
UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
def _supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _fetch_content(content_id: str) -> dict:
    """
    Fetch a single content row from Supabase, joined with its channel settings.
    Returns the full row dict or raises on error.
    """
    url = (
        f"{SUPABASE_URL}/rest/v1/content"
        f"?id=eq.{content_id}"
        f"&select=*,channels:channel_id(id,name,slug,settings)"
        f"&limit=1"
    )
    resp = requests.get(url, headers=_supabase_headers(), timeout=15)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError(f"Content {content_id} not found in Supabase")
    return rows[0]


def _fetch_approved_content() -> list:
    # Return content rows that should be uploaded to YouTube:
    #   - status=approved
    #   - has a playable video (storage path or drive URL)
    #   - NOT already uploaded (youtube_video_id IS NULL) — idempotency guard
    #   - flag is set (generation_params->youtube_publish_requested = true)
    # The youtube_video_id guard is the fix for a real duplicate-upload bug:
    # two content_poller processes were running in parallel, both saw the
    # same approved row, both spawned uploader subprocesses, both uploaded
    # to YouTube, only one got to write youtube_video_id back to the DB —
    # leaving an orphan YouTube video with no DB mapping.
    url = (
        f"{SUPABASE_URL}/rest/v1/content"
        f"?status=eq.approved"
        f"&or=(video_drive_url.not.is.null,video_storage_path.not.is.null)"
        f"&youtube_video_id=is.null"
        f"&generation_params->youtube_publish_requested=eq.true"
        f"&deleted_at=is.null"
        f"&order=created_at.asc"
        f"&select=*,channels:channel_id(id,name,slug,settings)"
    )
    resp = requests.get(url, headers=_supabase_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def _update_content(content_id: str, updates: dict):
    """PATCH a content row in Supabase. Skips soft-deleted rows so a
    YouTube upload that completes after the user tombstones the row
    can't write youtube_video_id back onto a deleted record (zombie-row
    prevention — see orchestrator.update_supabase)."""
    url = f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}&deleted_at=is.null"
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    resp = requests.patch(url, headers=_supabase_headers(), json=updates, timeout=15)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Google OAuth helpers
# ---------------------------------------------------------------------------
def _refresh_access_token(channel_id: str, refresh_token: str) -> str:
    """
    Exchange a refresh_token for a new access_token via Google OAuth.
    Persists the new access_token (and its expiry) back to channel settings.

    Returns the fresh access_token string.
    """
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Token refresh failed ({resp.status_code}): {resp.text[:300]}"
        )

    data = resp.json()
    access_token = data["access_token"]
    expires_in = data.get("expires_in", 3600)

    # Persist updated token to Supabase channel settings (youtube_* namespace)
    ch_url = f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel_id}&select=settings"
    ch_resp = requests.get(ch_url, headers=_supabase_headers(), timeout=15)
    ch_resp.raise_for_status()
    channels = ch_resp.json()
    if channels:
        settings = channels[0].get("settings", {}) or {}
        settings["youtube_access_token"] = access_token
        expiry_ts = datetime.now(timezone.utc).timestamp() + expires_in
        settings["youtube_token_expiry"] = (
            datetime.fromtimestamp(expiry_ts, tz=timezone.utc).isoformat()
        )
        patch_url = f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel_id}"
        requests.patch(
            patch_url,
            headers=_supabase_headers(),
            json={"settings": settings},
            timeout=15,
        )

    return access_token


def _get_access_token(channel: dict) -> str:
    """
    Return a valid Google access token for YouTube uploads.
    Reads youtube_refresh_token (Brand Account token) from channel settings.
    Raises ValueError if youtube_refresh_token is not configured.
    """
    settings = channel.get("settings", {}) or {}
    refresh_token = settings.get("youtube_refresh_token")
    if not refresh_token:
        raise ValueError(
            f"No youtube_refresh_token for channel '{channel.get('name', '?')}'. "
            "Run: python generate_youtube_token.py --channel <slug>"
        )

    # Check if cached token is still valid (5-min buffer)
    expiry_str = settings.get("youtube_token_expiry", "")
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if (expiry - now).total_seconds() > 300:
                cached = settings.get("youtube_access_token", "")
                if cached:
                    return cached
        except (ValueError, TypeError):
            pass  # Can't parse expiry — refresh anyway

    return _refresh_access_token(channel["id"], refresh_token)


# ---------------------------------------------------------------------------
# Google Drive download helper
# ---------------------------------------------------------------------------
def _extract_drive_file_id(drive_url: str) -> str:
    """
    Extract the Google Drive file ID from various Drive URL formats:
      https://drive.google.com/file/d/{id}/view
      https://drive.google.com/open?id={id}
      https://docs.google.com/...
    """
    # Pattern: /file/d/{id}/
    parts = drive_url.split("/file/d/")
    if len(parts) == 2:
        return parts[1].split("/")[0].split("?")[0]

    # Pattern: ?id={id}
    parsed = urlparse(drive_url)
    qs = parse_qs(parsed.query)
    if "id" in qs:
        return qs["id"][0]

    raise ValueError(f"Cannot extract Drive file ID from URL: {drive_url}")


def _download_from_drive(drive_url: str, access_token: str,
                         dest_path: str) -> str:
    """
    Download a file from Google Drive to dest_path.
    Handles the redirect that Drive sends for large files.
    Returns dest_path.
    """
    file_id = _extract_drive_file_id(drive_url)
    download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

    print(f"  [drive] Downloading file ID: {file_id}")

    with requests.get(
        download_url,
        headers={"Authorization": f"Bearer {access_token}"},
        stream=True,
        timeout=300,
    ) as resp:
        # Drive may return a virus-scan warning page for large files
        if resp.status_code == 200 and "text/html" in resp.headers.get(
            "Content-Type", ""
        ):
            raise RuntimeError(
                "Drive returned HTML instead of video data. "
                "The file may be too large or require manual confirmation."
            )
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

    size_mb = downloaded / (1024 * 1024)
    print(f"  [drive] Downloaded {size_mb:.1f} MB -> {dest_path}")
    return dest_path


# ---------------------------------------------------------------------------
# YouTube upload helpers
# ---------------------------------------------------------------------------
def _is_short(content: dict) -> bool:
    """
    Determine whether a content item should be uploaded as a YouTube Short.
    Shorts are vertical-format videos ≤60 seconds (format='short').
    """
    return content.get("format", "short") == "short"


def _build_video_metadata(content: dict) -> dict:
    """
    Build the YouTube video resource body from content row data.

    Title and description come from the stored title/description columns
    (set by ai_writer.generate_youtube_metadata during orchestration).
    Tags come from generation_params.tags.
    """
    title = content.get("title") or ""
    description = content.get("description") or ""
    philosopher = content.get("philosopher", "")
    topic = content.get("topic", "")

    # Fallback title/description if orchestrator didn't populate them
    if not title:
        title = f"{philosopher}: {topic}" if topic else philosopher
    if not description:
        description = content.get("quote_text", "")

    # Tags: from generation_params JSON, with sensible defaults
    gen_params = content.get("generation_params", {}) or {}
    tags = gen_params.get("tags", [])
    if not tags:
        tags = [philosopher, topic, "philosophy", "wisdom", "quotes"]
    # YouTube allows max 500 chars total for tags; trim defensively
    tags = [str(t)[:100] for t in tags[:20]]

    return {
        "snippet": {
            "title": title[:100],          # YouTube max title length
            "description": description[:5000],  # YouTube max description length
            "tags": tags,
            "categoryId": YOUTUBE_CATEGORY_ID,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "madeForKids": False,
        },
    }


def _youtube_set_thumbnail(access_token: str, video_id: str,
                           thumb_path: str) -> bool:
    """Upload a custom thumbnail to YouTube via the thumbnails.set endpoint.

    Without this, YouTube auto-grabs a frame from the video — which for NA/AA
    MonologueOverlay shorts means dark scrolling-text walls (every thumbnail
    looks identical and unreadable). User flagged 2026-04-30 with screenshot
    of the @OneDayAtATime channel grid.

    Custom thumbnails on Shorts CAN be restricted to verified channels, so
    we wrap this in try/except at the call site and don't fail the publish
    when thumbnail upload fails — the video is up, that's the win.

    YouTube API: POST upload/youtube/v3/thumbnails/set?videoId={id} with
    image bytes. Max 2MB, JPEG or PNG, 16:9 ratio recommended for Standard
    but Shorts are 9:16 — YouTube accepts either and pillars/letterboxes
    as needed.

    Returns True on success.
    """
    file_size = Path(thumb_path).stat().st_size
    print(f"  [yt] Uploading custom thumbnail ({file_size // 1024} KB)...")

    with open(thumb_path, "rb") as f:
        thumb_bytes = f.read()

    url = (f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
           f"?videoId={video_id}&uploadType=media")
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "image/jpeg",
            "Content-Length": str(file_size),
        },
        data=thumb_bytes,
        timeout=60,
    )
    if resp.status_code in (200, 201):
        print(f"  [yt] Thumbnail set successfully.")
        return True
    raise RuntimeError(
        f"thumbnails.set failed ({resp.status_code}): {resp.text[:300]}"
    )


def _youtube_resumable_upload(
    access_token: str,
    video_path: str,
    metadata: dict,
    is_short: bool = False,
) -> str:
    """
    Upload a video to YouTube using the resumable upload protocol.

    Step 1: POST metadata to initiate the session → get upload Location URL.
    Step 2: PUT the video bytes in chunks to the Location URL.

    Returns the YouTube video ID (e.g. 'dQw4w9WgXcQ').
    """
    file_size = Path(video_path).stat().st_size
    print(f"  [yt] Initiating resumable upload ({file_size / 1024 / 1024:.1f} MB)...")

    # --- Step 1: Initiate ---
    init_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(file_size),
    }

    # For Shorts: YouTube detects #Shorts in title/description or via vertical
    # resolution. We append #Shorts to the description as the reliable signal.
    if is_short:
        snippet = metadata["snippet"]
        if "#Shorts" not in snippet.get("description", ""):
            snippet["description"] = (snippet["description"] + "\n\n#Shorts").strip()
        if "#Shorts" not in snippet.get("title", ""):
            # Don't stuff title; just ensure description has it
            pass

    init_resp = requests.post(
        YOUTUBE_UPLOAD_URL,
        headers=init_headers,
        json=metadata,
        timeout=30,
    )
    if init_resp.status_code not in (200, 201):
        raise RuntimeError(
            f"YouTube upload initiation failed ({init_resp.status_code}): "
            f"{init_resp.text[:500]}"
        )

    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube did not return a resumable upload Location URL")

    print(f"  [yt] Upload session started. Uploading in chunks...")

    # --- Step 2: Upload in chunks ---
    uploaded = 0
    video_id = None

    with open(video_path, "rb") as f:
        while uploaded < file_size:
            chunk = f.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break

            chunk_end = uploaded + len(chunk) - 1
            content_range = f"bytes {uploaded}-{chunk_end}/{file_size}"

            for attempt in range(3):
                try:
                    chunk_resp = requests.put(
                        upload_url,
                        headers={
                            "Content-Length": str(len(chunk)),
                            "Content-Range": content_range,
                        },
                        data=chunk,
                        timeout=120,
                    )
                    break
                except requests.exceptions.RequestException as e:
                    if attempt == 2:
                        raise RuntimeError(
                            f"Upload chunk failed after 3 attempts: {e}"
                        )
                    print(f"  [yt] Chunk upload error (attempt {attempt+1}): {e}")
                    time.sleep(5)

            # 308 Resume Incomplete: chunk received, more to go
            if chunk_resp.status_code == 308:
                uploaded += len(chunk)
                pct = uploaded / file_size * 100
                print(f"  [yt] {pct:.0f}% uploaded ({uploaded // 1024 // 1024} MB)...")
                continue

            # 200 or 201: upload complete
            if chunk_resp.status_code in (200, 201):
                result = chunk_resp.json()
                video_id = result.get("id")
                print(f"  [yt] Upload complete. Video ID: {video_id}")
                break

            # Anything else is an error
            raise RuntimeError(
                f"Unexpected YouTube response ({chunk_resp.status_code}): "
                f"{chunk_resp.text[:500]}"
            )

    if not video_id:
        raise RuntimeError("Upload finished but no video ID was returned by YouTube")

    return video_id


# ---------------------------------------------------------------------------
# Core upload function
# ---------------------------------------------------------------------------
def upload_to_youtube(content_id: str, dry_run: bool = False) -> str:
    """
    Upload the video for a specific content item to its YouTube channel.

    Flow:
      1. Fetch content row + channel settings from Supabase
      2. Validate: must have video_drive_url and channel google_refresh_token
      3. Get / refresh Google access token
      4. Download video from Drive to a temp file
      5. Upload to YouTube via resumable upload
      6. Update Supabase: status='published', youtube_video_id=<id>
      7. Clean up temp file

    Returns the YouTube video URL on success.
    Raises on any unrecoverable error (after updating status='failed' in Supabase).
    """
    print(f"\n{'='*60}")
    print(f"  Uploading content: {content_id}")
    print(f"{'='*60}")

    # --- 1. Fetch content ---
    try:
        content = _fetch_content(content_id)
    except Exception as e:
        raise RuntimeError(f"Could not fetch content {content_id}: {e}")

    philosopher = content.get("philosopher", "?")
    topic = content.get("topic", "?")
    channel = content.get("channels", {}) or {}
    channel_name = channel.get("name", "?")
    drive_url = content.get("video_drive_url", "")
    storage_path = content.get("video_storage_path", "")

    print(f"  Philosopher : {philosopher}")
    print(f"  Topic       : {topic}")
    print(f"  Channel     : {channel_name}")
    if storage_path:
        print(f"  Storage Path: {storage_path}")
    print(f"  Drive URL   : {drive_url or '(none)'}")

    # --- 2. Validate ---
    if not drive_url and not storage_path:
        err = "No video source — neither video_storage_path nor video_drive_url is set"
        _update_content(content_id, {"status": "failed",
                                     "rejection_reason": err})
        raise ValueError(err)

    settings = channel.get("settings", {}) or {}
    if not settings.get("youtube_refresh_token"):
        err = (
            f"No youtube_refresh_token for channel '{channel_name}'. "
            "Run: python generate_youtube_token.py --channel <slug>"
        )
        _update_content(content_id, {"status": "failed",
                                     "rejection_reason": err})
        raise ValueError(err)

    if dry_run:
        print("  [dry-run] Would upload. Skipping actual upload.")
        return "(dry-run)"

    # --- 3. Get access tokens ---
    # YouTube token (Brand Account) for uploading to YouTube
    # Drive token (personal account) for downloading from Google Drive
    try:
        yt_access_token = _get_access_token(channel)
        print(f"  [auth] YouTube token obtained for '{channel_name}'")
    except Exception as e:
        _update_content(content_id, {"status": "failed",
                                     "rejection_reason": str(e)})
        raise

    # Drive download uses google_refresh_token (personal account that owns the Drive files)
    drive_refresh = settings.get("google_refresh_token")
    if drive_refresh:
        drive_token_resp = requests.post(GOOGLE_TOKEN_URL, data={
            "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": drive_refresh, "grant_type": "refresh_token"}, timeout=30)
        drive_access_token = drive_token_resp.json().get("access_token", yt_access_token)
        print(f"  [auth] Drive token obtained (personal account)")
    else:
        drive_access_token = yt_access_token  # fallback

    # --- 4. Download video (prefer Supabase Storage, fallback to Drive) ---
    tmp_dir = Path(tempfile.gettempdir()) / "wisdom_yt_upload"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_video = str(tmp_dir / f"{content_id[:8]}.mp4")

    try:
        if storage_path:
            from supabase_storage import download_from_storage
            download_from_storage("wisdom-videos", storage_path, tmp_video)
        else:
            _download_from_drive(drive_url, drive_access_token, tmp_video)
    except Exception as e:
        err = f"Video download failed: {e}"
        _update_content(content_id, {"status": "failed",
                                     "rejection_reason": err})
        raise RuntimeError(err)

    # --- 5. Upload to YouTube ---
    try:
        metadata = _build_video_metadata(content)
        is_short = _is_short(content)
        print(f"  [yt] Format: {'Short' if is_short else 'Standard'}")

        video_id = _youtube_resumable_upload(yt_access_token, tmp_video,
                                             metadata, is_short)
    except Exception as e:
        err = f"YouTube upload failed: {e}"
        _update_content(content_id, {"status": "failed",
                                     "rejection_reason": err})
        # Clean up temp file even on failure
        try:
            Path(tmp_video).unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(err)

    # --- 5b. Upload custom thumbnail (best-effort) ---
    # The orchestrator already generates a PIL thumbnail (scene art + dark
    # bottom gradient + Georgia bold title overlay) and uploads it to the
    # wisdom-thumbnails Supabase bucket. Without sending it to YouTube
    # explicitly, YT auto-generates from a video frame — for NA/AA shorts
    # that means dark scrolling-text walls (every thumbnail looks identical
    # and unscannable in the channel grid).
    #
    # Wrapped in try/except: custom thumbnails on Shorts can be limited by
    # channel verification status, and a thumbnail failure should not
    # invalidate a successful video publish. Logged for follow-up.
    thumb_storage_path = content.get("thumbnail_storage_path")
    if thumb_storage_path:
        try:
            from supabase_storage import download_from_storage as dl_thumb
            tmp_thumb = str(tmp_dir / f"{content_id[:8]}_thumb.jpg")
            dl_thumb("wisdom-thumbnails", thumb_storage_path, tmp_thumb)
            _youtube_set_thumbnail(yt_access_token, video_id, tmp_thumb)
            try:
                Path(tmp_thumb).unlink(missing_ok=True)
            except Exception:
                pass
        except Exception as thumb_e:
            print(f"  [yt] WARN: thumbnail upload failed ({thumb_e}); "
                  f"video published anyway, YT will auto-generate from frame")
    else:
        print(f"  [yt] No thumbnail_storage_path on row; YT will auto-generate")

    # --- 6. Update Supabase ---
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    _update_content(content_id, {
        "status": "published",
        "youtube_video_id": video_id,
        "published_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"  [db] Supabase updated: status=published, video_id={video_id}")

    # --- 7. Cleanup ---
    try:
        Path(tmp_video).unlink(missing_ok=True)
    except Exception:
        pass

    print(f"  DONE: {youtube_url}")
    return youtube_url


# ---------------------------------------------------------------------------
# Batch publish function
# ---------------------------------------------------------------------------
def publish_approved_content(dry_run: bool = False) -> list:
    """
    Find all approved content with a video_drive_url and upload each to YouTube.

    Returns a list of result dicts:
      [{"content_id": ..., "status": "success"|"failed", "url": ..., "error": ...}]
    """
    print(f"\n{'='*60}")
    print("  WISDOM YOUTUBE PUBLISHER")
    print(f"  {'DRY RUN — ' if dry_run else ''}Started: {datetime.now()}")
    print(f"{'='*60}")

    try:
        items = _fetch_approved_content()
    except Exception as e:
        print(f"FATAL: Could not fetch approved content from Supabase: {e}")
        sys.exit(1)

    if not items:
        print("No approved content with video_drive_url found. Nothing to publish.")
        return []

    print(f"Found {len(items)} item(s) to publish:\n")
    for item in items:
        ch = (item.get("channels") or {}).get("name", "?")
        print(
            f"  [{item['id'][:8]}] {item.get('philosopher','?')} | "
            f"{item.get('topic','?')} | channel={ch}"
        )

    results = []

    for content in items:
        cid = content["id"]
        try:
            url = upload_to_youtube(cid, dry_run=dry_run)
            results.append({"content_id": cid, "status": "success", "url": url})
        except Exception as e:
            print(f"\n  [ERROR] {cid[:8]}: {e}")
            traceback.print_exc()
            results.append({"content_id": cid, "status": "failed",
                            "error": str(e)})

    # Summary
    successes = sum(1 for r in results if r["status"] == "success")
    failures = sum(1 for r in results if r["status"] == "failed")
    print(f"\n{'='*60}")
    print(f"  Published: {successes}  Failed: {failures}")
    print(f"{'='*60}")

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Upload approved Wisdom videos to YouTube."
    )
    parser.add_argument(
        "--id",
        dest="content_id",
        default=None,
        help="Upload a single content item by ID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without uploading or updating Supabase",
    )
    args = parser.parse_args()

    if args.content_id:
        try:
            url = upload_to_youtube(args.content_id, dry_run=args.dry_run)
            print(f"\nResult: {url}")
        except Exception as e:
            print(f"\nFailed: {e}")
            sys.exit(1)
    else:
        publish_approved_content(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
