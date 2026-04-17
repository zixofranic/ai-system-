"""
Analytics fetcher — pulls view/like/comment/share counts from YouTube,
TikTok, and Meta (Facebook + Instagram) for every published row and writes
a time-series row into Supabase `content_analytics`.

Run pattern
-----------
  python analytics_fetcher.py             # fetch all published rows
  python analytics_fetcher.py --id <uid>  # single content row
  python analytics_fetcher.py --dry-run   # print, don't write

Invoked by content_poller.py on a 30-minute gate (every 6th 5-min tick).

Storage
-------
We store stats inside the `content.generation_params` JSONB as:

  platform_stats: {
    youtube:   { views, likes, comments, shares, fetched_at },
    tiktok:    { ... },
    facebook:  { ... },
    instagram: { ... }
  },
  platform_stats_fetched_at: ISO8601  # last-run timestamp

This sidesteps a CHECK constraint on public.content_analytics.platform that
currently only permits 'youtube'. If we later need a time-series history,
swap to content_analytics after extending the check constraint.

Per-platform notes
------------------
YouTube Data API v3 — `GET /videos?id=...&part=statistics`
  Auth: `channel.settings.youtube_access_token` (refreshed per channel).
TikTok Display API — `POST /v2/video/query/?fields=view_count,like_count,comment_count,share_count`
  Auth: `channel.settings.tiktok_access_token` (refreshed via tiktok_uploader.refresh_tiktok_token).
Facebook Page video — `GET /{video-id}?fields=views,likes.summary(true),comments.summary(true)`
  Auth: `channel.settings.meta_page_access_token`.
Instagram Reel — `GET /{media-id}/insights?metric=plays,likes,comments,shares`
  Auth: same page token (IG business API goes through the linked page token).
"""

import argparse
import json
import os
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GRAPH_VERSION = os.environ.get("META_GRAPH_API_VERSION", "v21.0").strip()
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_SANDBOX_CLIENT_KEY") or os.environ.get("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_SANDBOX_CLIENT_SECRET") or os.environ.get("TIKTOK_CLIENT_SECRET", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


# --- Supabase helpers -----------------------------------------------------


def get_channel(channel_id):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel_id}&select=*",
        headers=HEADERS, timeout=10,
    ).json()
    return r[0] if r else None


def fetch_rows_with_published_content(single_id=None):
    """Rows that have at least one platform ID set."""
    if single_id:
        return requests.get(
            f"{SUPABASE_URL}/rest/v1/content?id=eq.{single_id}&select=*",
            headers=HEADERS, timeout=10,
        ).json()
    # All rows where any of the 3 platform IDs is present
    or_clause = (
        "(youtube_video_id.not.is.null,"
        "tiktok_video_id.not.is.null,"
        "generation_params->meta_fb_post_id.not.is.null,"
        "generation_params->meta_ig_post_id.not.is.null)"
    )
    return requests.get(
        f"{SUPABASE_URL}/rest/v1/content",
        headers=HEADERS,
        params={
            "select": "id,title,channel_id,youtube_video_id,tiktok_video_id,generation_params",
            "or": or_clause,
            "deleted_at": "is.null",
            "limit": "500",
        },
        timeout=30,
    ).json()


def persist_stats_bundle(content_id, stats_by_platform, dry_run=False):
    """Merge platform_stats into content.generation_params in a single PATCH."""
    now_iso = datetime.now(timezone.utc).isoformat()
    normalized = {}
    for platform, stats in stats_by_platform.items():
        if stats is None:
            continue
        normalized[platform] = {
            "views": int(stats.get("views", 0) or 0),
            "likes": int(stats.get("likes", 0) or 0),
            "comments": int(stats.get("comments", 0) or 0),
            "shares": int(stats.get("shares", 0) or 0),
            "fetched_at": now_iso,
        }
    if not normalized:
        return
    if dry_run:
        print(f"    [dry-run] would patch stats for {list(normalized)}")
        return
    # Read existing generation_params, merge, patch back.
    cur = requests.get(
        f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}&select=generation_params",
        headers=HEADERS, timeout=10,
    ).json()
    if not cur:
        return
    params = cur[0].get("generation_params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    existing = params.get("platform_stats") or {}
    existing.update(normalized)
    params["platform_stats"] = existing
    params["platform_stats_fetched_at"] = now_iso
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}",
        headers=HEADERS, json={"generation_params": params}, timeout=15,
    )
    if r.status_code not in (200, 204):
        print(f"    [db] patch failed: {r.status_code} {r.text[:200]}")


# --- YouTube --------------------------------------------------------------


def _google_access_token(channel):
    """Refresh YouTube/Google access token for this channel."""
    settings = channel.get("settings") or {}
    refresh = (
        settings.get("youtube_refresh_token")
        or settings.get("google_refresh_token")
        or settings.get("google_drive_refresh_token")
    )
    if not refresh:
        return None
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=30,
    ).json()
    return r.get("access_token")


def fetch_youtube_stats(channel, video_id):
    token = _google_access_token(channel)
    if not token:
        return None
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"id": video_id, "part": "statistics"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    ).json()
    items = r.get("items") or []
    if not items:
        return None
    s = items[0].get("statistics") or {}
    return {
        "views": int(s.get("viewCount", 0) or 0),
        "likes": int(s.get("likeCount", 0) or 0),
        "comments": int(s.get("commentCount", 0) or 0),
        "shares": 0,  # YouTube doesn't expose shares
    }


# --- TikTok ---------------------------------------------------------------


def _tiktok_access_token(channel):
    """Refresh TikTok access token for this channel (mirrors tiktok_uploader)."""
    settings = channel.get("settings") or {}
    access = settings.get("tiktok_access_token")
    expiry = settings.get("tiktok_token_expiry", 0)
    if access and isinstance(expiry, (int, float)) and expiry > time.time() + 300:
        return access

    refresh = settings.get("tiktok_refresh_token")
    if not refresh or not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
        return None
    r = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        },
        timeout=30,
    ).json()
    tok = r.get("access_token")
    if not tok:
        return None
    # Persist refreshed token
    settings["tiktok_access_token"] = tok
    settings["tiktok_refresh_token"] = r.get("refresh_token", refresh)
    settings["tiktok_token_expiry"] = time.time() + r.get("expires_in", 86400)
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel['id']}",
        headers=HEADERS, json={"settings": settings}, timeout=10,
    )
    return tok


def fetch_tiktok_stats(channel, video_id):
    token = _tiktok_access_token(channel)
    if not token:
        return None
    r = requests.post(
        "https://open.tiktokapis.com/v2/video/query/",
        params={"fields": "id,view_count,like_count,comment_count,share_count"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"filters": {"video_ids": [video_id]}},
        timeout=30,
    ).json()
    videos = (r.get("data") or {}).get("videos") or []
    if not videos:
        return None
    v = videos[0]
    return {
        "views": int(v.get("view_count", 0) or 0),
        "likes": int(v.get("like_count", 0) or 0),
        "comments": int(v.get("comment_count", 0) or 0),
        "shares": int(v.get("share_count", 0) or 0),
    }


# --- Facebook Page video --------------------------------------------------


def fetch_facebook_stats(channel, video_id):
    settings = channel.get("settings") or {}
    token = settings.get("meta_page_access_token")
    if not token:
        return None
    r = requests.get(
        f"{GRAPH_BASE}/{video_id}",
        params={
            "fields": "views,likes.summary(true).limit(0),comments.summary(true).limit(0)",
            "access_token": token,
        },
        timeout=15,
    ).json()
    if "error" in r:
        print(f"    [fb] error: {r['error'].get('message','')[:120]}")
        return None
    likes = (r.get("likes") or {}).get("summary", {}).get("total_count", 0)
    comments = (r.get("comments") or {}).get("summary", {}).get("total_count", 0)
    return {
        "views": int(r.get("views", 0) or 0),
        "likes": int(likes or 0),
        "comments": int(comments or 0),
        "shares": 0,
    }


# --- Instagram Reel -------------------------------------------------------


def fetch_instagram_stats(channel, media_id):
    settings = channel.get("settings") or {}
    token = settings.get("meta_page_access_token")
    if not token:
        return None

    # Try full insights first (needs instagram_manage_insights scope).
    r = requests.get(
        f"{GRAPH_BASE}/{media_id}/insights",
        params={
            "metric": "plays,likes,comments,shares",
            "access_token": token,
        },
        timeout=15,
    ).json()

    if "error" not in r:
        by_name = {m["name"]: m for m in (r.get("data") or [])}

        def v(name):
            m = by_name.get(name) or {}
            vals = m.get("values") or [{}]
            return int(vals[0].get("value", 0) or 0)

        return {
            "views": v("plays"),
            "likes": v("likes"),
            "comments": v("comments"),
            "shares": v("shares"),
        }

    # Fallback: basic media fields — works without instagram_manage_insights.
    # Gives likes + comments but no view count. Better than nothing.
    err = r.get("error", {}).get("message", "")[:100]
    print(f"    [ig] insights blocked ({err}), falling back to basic fields")
    r2 = requests.get(
        f"{GRAPH_BASE}/{media_id}",
        params={
            "fields": "like_count,comments_count",
            "access_token": token,
        },
        timeout=15,
    ).json()
    if "error" in r2:
        print(f"    [ig] fallback error: {r2['error'].get('message','')[:100]}")
        return None
    return {
        "views": 0,
        "likes": int(r2.get("like_count", 0) or 0),
        "comments": int(r2.get("comments_count", 0) or 0),
        "shares": 0,
    }


# --- Driver --------------------------------------------------------------


def process_row(row, dry_run=False):
    cid = row["id"]
    title = (row.get("title") or "").strip()[:50]
    print(f"\n  [{cid[:8]}] {title}")

    channel = get_channel(row["channel_id"]) or {}
    params = row.get("generation_params") or {}
    if isinstance(params, str):
        params = json.loads(params)

    tasks = []
    if row.get("youtube_video_id"):
        tasks.append(("youtube", row["youtube_video_id"], fetch_youtube_stats))
    if row.get("tiktok_video_id"):
        tasks.append(("tiktok", row["tiktok_video_id"], fetch_tiktok_stats))
    fb = params.get("meta_fb_post_id")
    if fb:
        tasks.append(("facebook", fb, fetch_facebook_stats))
    ig = params.get("meta_ig_post_id")
    if ig:
        tasks.append(("instagram", ig, fetch_instagram_stats))

    collected = {}
    for platform, platform_id, fn in tasks:
        try:
            stats = fn(channel, platform_id)
            if stats is None:
                print(f"    [{platform}] no data (token missing or API error)")
                continue
            print(f"    [{platform}] views={stats['views']:>5} likes={stats['likes']:>3} "
                  f"comments={stats['comments']:>3} shares={stats['shares']:>3}")
            collected[platform] = stats
        except Exception as e:
            print(f"    [{platform}] EXCEPTION: {e}")
    persist_stats_bundle(cid, collected, dry_run=dry_run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="Single content id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print(f"  ANALYTICS FETCHER  {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    rows = fetch_rows_with_published_content(single_id=args.id)
    print(f"  {len(rows)} row(s) with platform ID")

    for row in rows:
        try:
            process_row(row, dry_run=args.dry_run)
        except Exception as e:
            print(f"  [{row.get('id','?')[:8]}] FAILED: {e}")

    print("\n" + "=" * 60)
    print("  Done")
    print("=" * 60)


if __name__ == "__main__":
    main()
