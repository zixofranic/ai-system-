"""
Meta (Facebook + Instagram) Video Uploader for Wisdom/Fellows Pipeline
======================================================================
Publishes approved SHORT videos to a Facebook Page + its linked Instagram
Business account using the Graph API.

Auth
----
Reads long-lived Page Access Token from channel.settings, populated by the
`/api/auth/meta/callback` OAuth flow in the dashboard. The page token is
also good for the Instagram Graph endpoints via the linked IG business
account.

Flow
----
  1. Query Supabase for content where:
       - status in (approved, published)
       - format in ('short', 'midform')
         · short  (9:16) → publishes to BOTH FB + IG Reels
         · midform (16:9) → FB only (IG Reels reject 16:9)
       - generation_params->meta_publish_requested == true
       - meta_fb_post_id and meta_ig_post_id not yet set
       - video_storage_path or video_drive_url is not null
  2. Resolve channel's meta_page_access_token / meta_page_id / meta_ig_user_id
  3. Build a public URL for the video (Supabase Storage public bucket)
  4. POST /{page-id}/videos with file_url  -> FB post
  5. POST /{ig-user-id}/media  (media_type=REELS) -> container
     Poll GET /{container-id}?fields=status_code until FINISHED
     POST /{ig-user-id}/media_publish with creation_id -> IG post
  6. Update content row with generation_params.meta_fb_post_id /
     meta_ig_post_id / meta_published_at

Usage
-----
    python meta_uploader.py                     # publish all flagged items
    python meta_uploader.py --id <content_id>   # upload a single item
    python meta_uploader.py --dry-run           # preview without uploading
    python meta_uploader.py --fb-only           # skip IG (FB only)
    python meta_uploader.py --ig-only           # skip FB (IG only)
"""

import argparse
import json
import os
import sys
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

GRAPH_VERSION = os.environ.get("META_GRAPH_API_VERSION", "v21.0").strip()
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

# Supabase Storage public URL builder (mirrors supabase_storage.get_public_url)
FELLOWS_URL = os.environ.get(
    "FELLOWS_SUPABASE_URL", "https://cujwhqoezvehwhhigxmr.supabase.co"
)
STORAGE_BUCKET = "wisdom-videos"  # same bucket for all channels
THUMBNAIL_BUCKET = "wisdom-thumbnails"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# Per-channel caption / hashtag config. Keep copy compliant with the memory
# rules: no NA/AA endorsement claims, no trademarked logos, no identifiable
# member names. Recovery-community language, not medical.
CHANNEL_HASHTAGS = {
    "wisdom": ["#Philosophy", "#Wisdom", "#DeepEchoesOfWisdom"],
    "gibran": ["#Gibran", "#KhalilGibran", "#TheProphet"],
    "na": ["#Recovery", "#OneDayAtATime", "#SoberLife", "#RecoveryCommunity"],
    "aa": ["#Recovery", "#EasyDoesIt", "#SoberLife", "#RecoveryCommunity"],
}


# --- Supabase helpers -----------------------------------------------------


def get_content(content_id):
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}&select=*",
        headers=HEADERS, timeout=10,
    )
    items = resp.json() if resp.status_code == 200 else []
    return items[0] if items else None


def get_channel(channel_id):
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel_id}&select=*",
        headers=HEADERS, timeout=10,
    )
    channels = resp.json() if resp.status_code == 200 else []
    return channels[0] if channels else None


def update_content_meta(content_id, fb_post_id=None, ig_post_id=None,
                        fb_error=None, ig_error=None):
    """Merge Meta post IDs, errors, and timestamps into generation_params.

    Persists even when both sides failed so `meta_last_error` is visible
    in the dashboard. `meta_publish_requested` is only cleared once BOTH
    meta_fb_post_id and meta_ig_post_id are set — a partial success
    (e.g. IG ok, FB failed) keeps the flag so the next poll retries."""
    current = get_content(content_id)
    if not current:
        return
    params = current.get("generation_params") or {}
    if isinstance(params, str):
        params = json.loads(params)

    if fb_post_id:
        params["meta_fb_post_id"] = fb_post_id
    if ig_post_id:
        params["meta_ig_post_id"] = ig_post_id

    if fb_error or ig_error:
        params["meta_last_error"] = {"fb": fb_error, "ig": ig_error}
        params["meta_error_at"] = datetime.now(timezone.utc).isoformat()
    elif fb_post_id or ig_post_id:
        params.pop("meta_last_error", None)
        params.pop("meta_error_at", None)

    if fb_post_id or ig_post_id:
        params["meta_published_at"] = datetime.now(timezone.utc).isoformat()

    if params.get("meta_fb_post_id") and params.get("meta_ig_post_id"):
        params.pop("meta_publish_requested", None)

    # Soft-delete guard — don't write meta_fb_post_id / meta_ig_post_id
    # back onto a row that was tombstoned between flag-set and upload.
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}&deleted_at=is.null",
        headers=HEADERS,
        json={
            "generation_params": params,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=10,
    )


# --- Video URL resolution -------------------------------------------------


def resolve_public_video_url(content):
    """Meta needs a public HTTPS URL. Prefer Supabase Storage public URL."""
    storage_path = content.get("video_storage_path")
    if storage_path:
        return f"{FELLOWS_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{storage_path}"
    drive_url = content.get("video_drive_url")
    if drive_url:
        # Drive "view" links aren't directly fetchable by Meta. Convert to
        # the direct-download form (works for public files).
        if "/file/d/" in drive_url:
            file_id = drive_url.split("/file/d/")[1].split("/")[0]
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        return drive_url
    raise ValueError("No video URL available (no storage_path or drive_url)")


def resolve_public_thumbnail_url(content):
    """Public URL for the pre-rendered thumbnail (JPEG). Used as the IG Reel
    cover_url and the FB video thumbnail. Without this, both platforms grab
    the first video frame — which is black for our fade-in Remotion renders,
    leaving Reels visually blank in the grid."""
    storage_path = content.get("thumbnail_storage_path")
    if storage_path:
        return f"{FELLOWS_URL}/storage/v1/object/public/{THUMBNAIL_BUCKET}/{storage_path}"
    return None


# --- Caption builder ------------------------------------------------------


def build_caption(content, channel_slug):
    title = content.get("title", "")
    description = content.get("description", "") or ""
    params = content.get("generation_params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    tags = params.get("tags", []) or []

    caption = title
    if description:
        first_line = description.split("\n")[0][:200]
        caption = f"{title}\n\n{first_line}"

    hashtags = []
    for tag in tags[:8]:
        ht = "#" + tag.replace(" ", "").replace("-", "").replace("'", "")
        if ht and ht not in hashtags:
            hashtags.append(ht)
    for ht in CHANNEL_HASHTAGS.get(channel_slug, []):
        if ht not in hashtags:
            hashtags.append(ht)

    if hashtags:
        caption += "\n\n" + " ".join(hashtags)
    return caption[:2200]


# --- Facebook Page video upload ------------------------------------------


def publish_to_facebook_page(page_id, page_token, video_url, caption, thumb_url=None):
    """POST the video to the FB Page via file_url (Meta fetches it).

    NOTE: `thumb_url` is intentionally IGNORED here. The FB Graph
    /{page-id}/videos endpoint's `thumb` field requires a multipart-uploaded
    file (`source=@file.jpg`), NOT a URL. Passing a URL fails with:
        (#100) Invalid image format. It should be an image file data.
    For IG Reels the analogous `cover_url` field DOES accept a URL — so the
    parameter still flows in for caller symmetry; FB just won't use it.
    Without thumb, FB auto-picks a representative frame from the video.
    Re-add a multipart `source` upload later if a custom cover is needed.
    """
    url = f"{GRAPH_BASE}/{page_id}/videos"
    payload = {
        "file_url": video_url,
        "description": caption,
        "access_token": page_token,
    }
    resp = requests.post(url, data=payload, timeout=600)
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"FB Page video upload failed: {json.dumps(data)[:400]}")
    return data["id"]


# --- Instagram Reels upload ----------------------------------------------


def publish_to_instagram_reel(ig_user_id, page_token, video_url, caption, cover_url=None):
    """Two-step IG content publishing: create container -> poll -> publish."""
    # Step 1 — create the media container (REELS)
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": page_token,
        "share_to_feed": "true",
    }
    # cover_url overrides the first-frame thumbnail that IG picks by default.
    # Our renders fade in from black so the default leaves a black tile in
    # the Reels grid. Passing our pre-rendered thumbnail gives a proper
    # cover image.
    if cover_url:
        payload["cover_url"] = cover_url
    container_resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media",
        data=payload,
        timeout=120,
    )
    container_data = container_resp.json()
    container_id = container_data.get("id")
    if not container_id:
        raise RuntimeError(
            f"IG container creation failed: {json.dumps(container_data)[:400]}"
        )

    # Step 2 — poll until the container finishes processing.
    # IG can take 30s-a few min to fetch + transcode. Cap at 10 min.
    deadline = time.time() + 600
    while time.time() < deadline:
        status_resp = requests.get(
            f"{GRAPH_BASE}/{container_id}",
            params={
                "fields": "status_code,status",
                "access_token": page_token,
            },
            timeout=30,
        )
        sd = status_resp.json()
        code = sd.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(
                f"IG container failed: {json.dumps(sd)[:400]}"
            )
        print(f"    IG container status: {code} - waiting...")
        time.sleep(8)
    else:
        raise RuntimeError("IG container timed out after 10 minutes")

    # Step 3 — publish
    publish_resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media_publish",
        data={
            "creation_id": container_id,
            "access_token": page_token,
        },
        timeout=60,
    )
    pd = publish_resp.json()
    media_id = pd.get("id")
    if not media_id:
        raise RuntimeError(f"IG media_publish failed: {json.dumps(pd)[:400]}")
    return media_id


# --- Main per-item processor ---------------------------------------------


def process_content(content, dry_run=False, fb_only=False, ig_only=False):
    content_id = content["id"]
    title = content.get("title", "")
    print(f"\n  [{content_id[:8]}] {title[:60]}")

    content_format = content.get("format") or ""
    # Shorts → FB + IG Reels. Midform → FB only (IG Reels require 9:16,
    # 16:9 landscape won't pass IG's validation). Anything else skipped.
    midform_fb_only = content_format == "midform"
    if content_format not in ("short", "midform"):
        print(f"    Skipping — format='{content_format}' not eligible for Meta "
              f"(shorts + midform only; midform posts to FB only).")
        return False

    channel = get_channel(content["channel_id"])
    if not channel:
        print("    Channel not found")
        return False

    settings = channel.get("settings") or {}
    if not settings.get("meta_connected"):
        print("    Meta not connected for this channel")
        return False

    page_id = settings.get("meta_page_id")
    page_token = settings.get("meta_page_access_token")
    ig_user_id = settings.get("meta_ig_user_id")

    if not page_id or not page_token:
        print("    Missing meta_page_id or meta_page_access_token")
        return False

    try:
        video_url = resolve_public_video_url(content)
    except ValueError as e:
        print(f"    {e}")
        return False

    thumb_url = resolve_public_thumbnail_url(content)
    if not thumb_url:
        print("    Warning: no thumbnail_storage_path — IG will show black cover")

    channel_slug = (channel.get("slug") or "wisdom").lower()
    caption = build_caption(content, channel_slug)

    if dry_run:
        print(f"    [dry-run] Would publish to page={page_id} ig={ig_user_id}")
        print(f"    [dry-run] video_url={video_url}")
        print(f"    [dry-run] thumb_url={thumb_url}")
        print(f"    [dry-run] caption={caption[:200]}...")
        return True

    fb_post_id = None
    ig_post_id = None
    fb_error = None
    ig_error = None

    existing_params = content.get("generation_params") or {}
    if isinstance(existing_params, str):
        existing_params = json.loads(existing_params)
    existing_fb_id = existing_params.get("meta_fb_post_id")
    existing_ig_id = existing_params.get("meta_ig_post_id")

    # --- Facebook Page ---
    if not ig_only:
        if existing_fb_id:
            print(f"    FB already published: {existing_fb_id} — skipping")
        else:
            try:
                print(f"    Publishing to FB Page {page_id}...")
                fb_post_id = publish_to_facebook_page(
                    page_id, page_token, video_url, caption, thumb_url=thumb_url
                )
                print(f"    FB published: {fb_post_id}")
            except Exception as e:
                fb_error = str(e)[:500]
                print(f"    FB error: {e}")

    # --- Instagram Reel ---
    if not fb_only and not midform_fb_only:
        if not ig_user_id:
            print("    No IG account linked — skipping IG")
        elif existing_ig_id:
            print(f"    IG already published: {existing_ig_id} — skipping")
        else:
            try:
                print(f"    Publishing to IG {ig_user_id}...")
                ig_post_id = publish_to_instagram_reel(
                    ig_user_id, page_token, video_url, caption, cover_url=thumb_url
                )
                print(f"    IG Reel published: {ig_post_id}")
            except Exception as e:
                ig_error = str(e)[:500]
                print(f"    IG error: {e}")

    update_content_meta(
        content_id,
        fb_post_id=fb_post_id, ig_post_id=ig_post_id,
        fb_error=fb_error, ig_error=ig_error,
    )
    return bool(fb_post_id or ig_post_id)


# --- Entrypoint ---------------------------------------------------------


def fetch_items():
    # Filter meta_publish_requested=true in the PostgREST query, NOT in
    # Python. The previous version fetched the oldest 20 approved shorts
    # and filtered client-side, which silently dropped newly-flagged rows
    # once the backlog of older shorts grew past 20.
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/content",
        headers=HEADERS,
        params={
            "select": "id,title,description,philosopher,channel_id,format,"
                      "video_drive_url,video_storage_path,thumbnail_storage_path,"
                      "generation_params",
            "status": "in.(approved,published)",
            "format": "eq.short",
            "or": "(video_drive_url.not.is.null,video_storage_path.not.is.null)",
            "generation_params->meta_publish_requested": "eq.true",
            "deleted_at": "is.null",
            "order": "created_at.asc",
            "limit": "20",
        },
        timeout=10,
    )
    items = resp.json() if resp.status_code == 200 else []
    # Only exclude rows where BOTH ids are set (fully published). Rows with
    # a partial result — e.g. IG ok, FB failed — stay eligible so the next
    # tick retries the missing side. process_content short-circuits the
    # already-posted side so IG won't be re-posted.
    return [
        i for i in items
        if not (
            (i.get("generation_params") or {}).get("meta_fb_post_id")
            and (i.get("generation_params") or {}).get("meta_ig_post_id")
        )
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", help="Upload specific content ID")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fb-only", action="store_true", help="Skip Instagram")
    parser.add_argument("--ig-only", action="store_true", help="Skip Facebook")
    args = parser.parse_args()

    print("=" * 60)
    print("  META UPLOADER (Facebook + Instagram)")
    print(f"  {datetime.now()}")
    print("=" * 60)

    if args.id:
        item = get_content(args.id)
        items = [item] if item else []
    else:
        items = fetch_items()

    print(f"  Found {len(items)} item(s) to upload")

    success = 0
    for item in items:
        if process_content(
            item,
            dry_run=args.dry_run,
            fb_only=args.fb_only,
            ig_only=args.ig_only,
        ):
            success += 1

    print(f"\n{'=' * 60}")
    print(f"  Done: {success}/{len(items)} uploaded")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
