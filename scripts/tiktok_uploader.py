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
TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")

# Upload mode — controls which TikTok endpoint we use:
#   - "inbox"  → /v2/post/publish/inbox/video/init/. Video lands in the
#                user's TikTok app inbox/drafts. User manually publishes
#                from the app (caption typed there). Works for ALL apps
#                regardless of audit status. Required while Direct Post
#                audit is pending.
#   - "direct" → /v2/post/publish/video/init/. Video publishes straight
#                to the public feed with our pre-built caption. Requires
#                the Direct Post audit at developers.tiktok.com →
#                Content Posting API → "Apply for audit". Without this
#                audit, public-account users get
#                `unaudited_client_can_only_post_to_private_accounts`
#                regardless of post privacy_level.
# 2026-05-10: Direct Post audit submitted; using inbox in the meantime.
# Flip to "direct" via env var when the audit clears.
TIKTOK_UPLOAD_MODE = os.environ.get("TIKTOK_UPLOAD_MODE", "inbox")
TIKTOK_PRIVACY_LEVEL = os.environ.get("TIKTOK_PRIVACY_LEVEL", "PUBLIC_TO_EVERYONE")

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


def _get_content(content_id):
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}&select=*",
        headers=HEADERS, timeout=10,
    )
    items = resp.json() if resp.status_code == 200 else []
    return items[0] if items else None


def update_tiktok_state(content_id, tiktok_id=None, error=None,
                        halt_auto_retry=False):
    """Merge TikTok upload result into the row. Parallel to update_content_meta
    in meta_uploader.

    On success: set `tiktok_video_id` top-level column, clear
    `tiktok_publish_requested` + `tiktok_last_error` in generation_params.

    On failure: write `tiktok_last_error` + `tiktok_error_at` to
    generation_params, KEEP `tiktok_publish_requested=true` so the next
    poll tick retries. Previously errors were printed to stdout and lost.

    On `halt_auto_retry=True`: clear `tiktok_publish_requested` AND set
    `tiktok_retry_halted` so the dashboard can surface the row. Used when
    the channel is unconfigured (no point retrying every tick — observed
    2026-05-09: row 62610c66 reappeared every 5 min for hours).
    """
    current = _get_content(content_id)
    if not current:
        return
    params = current.get("generation_params") or {}
    if isinstance(params, str):
        params = json.loads(params)

    if error:
        params["tiktok_last_error"] = str(error)[:500]
        params["tiktok_error_at"] = datetime.now(timezone.utc).isoformat()
    elif tiktok_id:
        params.pop("tiktok_last_error", None)
        params.pop("tiktok_error_at", None)
        params.pop("tiktok_publish_requested", None)
        if str(tiktok_id).startswith("v_inbox"):
            # Human-readable audit trail; expiry logic itself decodes the
            # snowflake timestamp inside the publish_id (analytics_fetcher
            # / tiktok_daily_batch), so this stamp is informational.
            params["tiktok_inbox_uploaded_at"] = datetime.now(timezone.utc).isoformat()

    if halt_auto_retry:
        params.pop("tiktok_publish_requested", None)
        params["tiktok_retry_halted"] = True
        params["tiktok_retry_halted_at"] = datetime.now(timezone.utc).isoformat()

    patch = {
        "generation_params": params,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if tiktok_id:
        patch["tiktok_video_id"] = tiktok_id

    # Soft-delete guard — don't resurrect tombstoned rows.
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}&deleted_at=is.null",
        headers=HEADERS, json=patch, timeout=10,
    )


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

    # The dashboard OAuth callback stores expiry as an ISO-8601 string;
    # this uploader's refresh path stores epoch seconds. Accept both —
    # otherwise a dashboard-linked channel force-refreshes on every call.
    if isinstance(expiry, str):
        try:
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00")).timestamp()
        except ValueError:
            expiry = 0

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


class TikTokPostInitError(Exception):
    """Raised when TikTok upload fails AFTER the init step succeeded.

    Once init returns a publish_id, TikTok has committed to that post.
    Any subsequent error (file PUT failure, status fetch crash, transient
    network blip) MAY have left the video on TikTok with no way for us
    to know. Retrying on the next poll tick would re-init and re-upload,
    creating a duplicate post. Surface this to the dashboard via
    halt_auto_retry instead.
    """
    def __init__(self, original_error: Exception, publish_id: str):
        self.original_error = original_error
        self.publish_id = publish_id
        super().__init__(f"after init (publish_id={publish_id}): {original_error}")


# TikTok init-time error codes that won't fix themselves on a 5-min retry.
# Retrying just spams the log and the dashboard. Halt the flag and let
# Ziad surface the row in /review with the error attached.
PERMANENT_INIT_ERROR_CODES = {
    # Content Posting API audit not yet approved. App can sign in (OAuth
    # works) but can't publish PUBLIC posts. Ziad must complete the audit
    # at developers.tiktok.com — typically 1-3 weeks. Until then the only
    # alternative is privacy_level=SELF_ONLY (drafts/private). 2026-05-10.
    "unaudited_client_can_only_post_to_private_accounts",
    # Wrong client_key, app not approved at all, or app deleted.
    "app_not_found",
    # User-facing scope is missing — re-auth needed.
    "scope_not_authorized",
    # Hard auth failure — refresh token dead, user must re-link.
    "access_token_invalid",
}


class TikTokPermanentInitError(Exception):
    """Raised when TikTok init fails with a code that won't change on retry.

    Caller should halt auto-retry and surface the error to the dashboard
    so the user can take action (complete audit, re-auth, etc).
    """
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


def upload_to_tiktok(video_path, title, token, open_id):
    """Upload video to TikTok using Content Posting API v2."""
    file_size = os.path.getsize(video_path)

    # Init endpoint depends on TIKTOK_UPLOAD_MODE.
    if TIKTOK_UPLOAD_MODE == "direct":
        # Direct Post — publishes to feed with our caption. Requires the
        # Direct Post audit (Content Posting API → "Apply for audit").
        init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
        init_body = {
            "post_info": {
                "title": title[:2200],
                "privacy_level": TIKTOK_PRIVACY_LEVEL,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "is_aigc": True,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": file_size,
                "total_chunk_count": 1,
            },
        }
    else:
        # Inbox — drops the video into the user's TikTok inbox/drafts.
        # User completes the post from the TikTok app (adds caption,
        # taps publish). Works without Direct Post audit.
        init_url = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
        init_body = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": file_size,
                "total_chunk_count": 1,
            },
        }

    init_resp = requests.post(
        init_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=init_body,
        timeout=30,
    )
    init_data = init_resp.json()

    if "data" not in init_data:
        # Detect permanent init failures so the caller can halt auto-retry
        # instead of spamming the same failed call every 5 min.
        err = (init_data.get("error") or {}) if isinstance(init_data, dict) else {}
        code = err.get("code", "")
        if code in PERMANENT_INIT_ERROR_CODES:
            raise TikTokPermanentInitError(code, err.get("message", ""))
        raise ValueError(f"TikTok init failed: {json.dumps(init_data)[:300]}")

    publish_id = init_data["data"]["publish_id"]
    upload_url = init_data["data"]["upload_url"]

    print(f"  Upload URL obtained, publish_id: {publish_id}")

    # From here on, any failure must be wrapped in TikTokPostInitError so
    # the caller can halt auto-retry — we cannot tell whether the post
    # ended up on TikTok server-side, and a retry would post a duplicate.
    try:
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

            # Inbox mode: SEND_TO_USER_INBOX is the TERMINAL success state —
            # PUBLISH_COMPLETE only ever arrives after the user manually
            # posts the draft, which can be hours later. Waiting the full
            # 150s here (as the code did before 2026-08-12) just slowed
            # every batch 5x and widened the concurrent-upload race window.
            if TIKTOK_UPLOAD_MODE != "direct" and status == "SEND_TO_USER_INBOX":
                print(f"  Delivered to TikTok inbox: {publish_id}")
                return publish_id

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
    except Exception as e:
        # Wrap in TikTokPostInitError so process_content knows to halt
        # auto-retry rather than risk a duplicate post.
        if isinstance(e, TikTokPostInitError):
            raise
        raise TikTokPostInitError(e, publish_id) from e


CHANNEL_HASHTAGS = {
    "wisdom": ["#Philosophy", "#Wisdom", "#Shorts", "#DeepEchoesOfWisdom"],
    "gibran": ["#Gibran", "#KhalilGibran", "#TheProphet", "#Shorts"],
    # Recovery channels share the same TikTok account. Copy must stay
    # compliant: no NA/AA endorsement claims, recovery-community language.
    # Mirrors meta_uploader.CHANNEL_HASHTAGS.
    "na": ["#Recovery", "#OneDayAtATime", "#SoberLife", "#RecoveryCommunity"],
    "aa": ["#Recovery", "#EasyDoesIt", "#SoberLife", "#RecoveryCommunity"],
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

    # Add hashtags from AI-generated tags. Same cleaning rule as
    # meta_uploader: hashtags only render when alphanumeric (underscore
    # allowed) — strip `/`, `.`, `'`, dashes, spaces, etc.
    # Dedup is case-insensitive: hashtags are case-insensitive on TikTok,
    # so "#Recovery" and "#recovery" are the same tag shown twice.
    hashtags = []
    seen = set()

    def _add(ht, front=False):
        if ht.lower() in seen:
            return
        seen.add(ht.lower())
        hashtags.insert(0, ht) if front else hashtags.append(ht)

    if tags:
        for tag in tags[:8]:
            cleaned = "".join(ch for ch in tag if ch.isalnum() or ch == "_")
            if cleaned:
                _add("#" + cleaned)

    # Add channel-specific hashtags. No wisdom fallback — a channel
    # missing from the map gets no channel tags rather than another
    # channel's (never leak #Philosophy onto the recovery account).
    for ht in CHANNEL_HASHTAGS.get(channel_slug, []):
        _add(ht)

    philosopher = content.get("philosopher", "")
    if philosopher:
        # Same alnum cleaning — NA/AA personas contain dashes
        # ("The Old-Timer") which break hashtag rendering.
        cleaned = "".join(ch for ch in philosopher if ch.isalnum() or ch == "_")
        if cleaned:
            _add("#" + cleaned, front=True)

    caption += "\n\n" + " ".join(hashtags)
    return caption[:2200]


def process_content(content, dry_run=False):
    """Process a single content item for TikTok upload."""
    content_id = content["id"]
    title = content.get("title", "")
    print(f"\n  [{content_id[:8]}] {title[:50]}")

    # Idempotency recheck (2026-08-12): re-fetch the row RIGHT before
    # uploading and skip if another process already sent it. Root cause of
    # today's duplicate inbox drafts: a killed poller's uploader child
    # survived as an orphan and worked the same batch as the new poller's
    # uploader — both had fetched the flag list minutes earlier. The
    # stale list is unavoidable; acting on it without rechecking isn't.
    fresh = _get_content(content_id)
    if not fresh:
        print("    Row vanished — skipping")
        return False
    fresh_params = fresh.get("generation_params") or {}
    if isinstance(fresh_params, str):
        fresh_params = json.loads(fresh_params)
    if fresh.get("tiktok_video_id"):
        print(f"    Already sent ({str(fresh['tiktok_video_id'])[:30]}) — skipping")
        return False
    if not fresh_params.get("tiktok_publish_requested"):
        print("    Flag cleared by another process — skipping")
        return False
    content = fresh

    channel = get_channel(content["channel_id"])
    if not channel:
        print("    Channel not found")
        return False

    settings = channel.get("settings", {}) or {}
    if not settings.get("tiktok_connected"):
        # Channel isn't connected — clearing the flag is required, otherwise
        # the row matches the poller's query every 5 min FOREVER (observed
        # 2026-05-09: row 62610c66 reappeared every tick for hours). Park
        # the flag with a halted marker so the dashboard surfaces it.
        print("    TikTok not connected for this channel — halting flag")
        update_tiktok_state(
            content_id, halt_auto_retry=True,
            error="TikTok not connected for this channel "
                  "(connect TikTok on /settings, then re-flag publish).",
        )
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

        update_tiktok_state(content_id, tiktok_id=tiktok_id)
        print(f"    TikTok published: {tiktok_id}")

        # Clean up temp file
        os.unlink(video_path)
        return True

    except TikTokPermanentInitError as e:
        # Init failed with a code that won't change on retry. Halt the
        # flag and surface the error to /review so Ziad can take action
        # (complete the Content Posting audit, re-link OAuth, etc).
        if e.error_code == "unaudited_client_can_only_post_to_private_accounts":
            hint = ("TikTok app is OAuth-approved but Content Posting audit is pending. "
                    "Submit at developers.tiktok.com (your app -> add scope/audit). "
                    "Until approved, public posts are blocked.")
        else:
            hint = f"TikTok rejected init: {e.message}"
        print(f"    Permanent init error ({e.error_code}) — halting flag")
        update_tiktok_state(
            content_id, halt_auto_retry=True,
            error=hint,
        )
        return False

    except TikTokPostInitError as e:
        # Init succeeded; later step blew up. The post MAY exist on
        # TikTok server-side. Halting auto-retry prevents a duplicate
        # publish on the next 5-min poll tick. We still record the
        # publish_id under tiktok_video_id so the row exits the queue
        # filter (`tiktok_video_id IS NULL`) — verify on TikTok manually
        # and clear `tiktok_retry_halted` from generation_params if you
        # actually want to repost.
        print(f"    Error after init: {e} — halting auto-retry to avoid duplicate")
        update_tiktok_state(
            content_id,
            tiktok_id=e.publish_id,
            error=e.original_error,
            halt_auto_retry=True,
        )
        return False

    except Exception as e:
        update_tiktok_state(content_id, error=e)
        print(f"    Error: {e}")
        return False


# Single-instance lock (2026-08-12). Two uploaders running concurrently
# double-send every row in the batch (seen today: orphaned child of a
# killed poller + the new poller's child → duplicate inbox drafts).
# Staleness is age-based, NOT pid-probe based: os.kill(pid, 0) on Windows
# calls TerminateProcess and would KILL the other uploader.
LOCK_PATH = Path("C:/AI/system/scripts/.tiktok_uploader.lock")
LOCK_STALE_SECONDS = 30 * 60


def acquire_lock():
    if LOCK_PATH.exists():
        age = time.time() - LOCK_PATH.stat().st_mtime
        if age < LOCK_STALE_SECONDS:
            print(f"  Another uploader holds the lock ({age:.0f}s old) — exiting. "
                  f"Delete {LOCK_PATH} if this is wrong.")
            return False
        print(f"  Stale lock ({age:.0f}s old) — taking over.")
    LOCK_PATH.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}")
    return True


def release_lock():
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", help="Upload specific content ID")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("  TIKTOK UPLOADER")
    print(f"  {datetime.now()}")
    print("=" * 60)

    if not acquire_lock():
        return

    if args.id:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/content?id=eq.{args.id}&select=*",
            headers=HEADERS, timeout=10,
        )
        items = resp.json()
    else:
        # Flag filter is server-side. Prior version fetched the oldest 10
        # approved/published rows and then filtered tiktok_publish_requested
        # client-side — newly-flagged rows paginated out and the uploader
        # silently reported "Found 0". Same class of bug as meta_uploader
        # had before its 2026-04-16 fix. format=eq.short enforces the
        # 9:16-only constraint TikTok requires.
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/content",
            headers=HEADERS,
            params={
                "select": "id,title,description,philosopher,channel_id,video_drive_url,video_storage_path,generation_params",
                "status": "in.(approved,published)",
                "format": "eq.short",
                "or": "(video_drive_url.not.is.null,video_storage_path.not.is.null)",
                "generation_params->tiktok_publish_requested": "eq.true",
                "tiktok_video_id": "is.null",
                "deleted_at": "is.null",
                "order": "created_at.asc",
                "limit": "20",
            },
            timeout=10,
        )
        items = resp.json() if resp.status_code == 200 else []

    try:
        print(f"  Found {len(items)} item(s) to upload")

        success = 0
        for item in items:
            if process_content(item, args.dry_run):
                success += 1

        print(f"\n{'='*60}")
        print(f"  Done: {success}/{len(items)} uploaded")
        print(f"{'='*60}")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
