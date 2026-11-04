"""
TikTok daily batch — feeds the Recovery Fellows inbox pipeline.
================================================================
Runs once a day via Task Scheduler (AI_TikTokDailyBatch). Flags up to 5
NA/AA shorts for TikTok upload (content_poller picks the flags up within
5 minutes and tiktok_uploader sends them to the account inbox):

  - 1 slot for the NEWEST fresh video (created in the last 7 days) so
    new content reaches TikTok the same day it's published
  - remaining slots (normally 4) for the OLDEST backlog videos
    (created before 2026-08-01 — the June/July catch-up)

Why 5: TikTok allows at most 5 pending inbox shares per account per
24h, and unposted drafts expire after ~24h. NA and AA share one TikTok
account ("Recovery Fellows"), so they share one budget of 5.

Slot accounting: drafts sent in the last 24h that Ziad hasn't posted yet
(tiktok_video_id still v_inbox_*) hold a slot, as do rows already
flagged but not yet uploaded. We only flag as many as remain free —
otherwise TikTok rejects with spam_risk_too_many_pending_share.
analytics_fetcher.reconcile_inbox_share frees slots automatically:
posted drafts get their real video id; expired drafts get cleared for
re-sending.

Usage:
    python tiktok_daily_batch.py            # flag today's batch
    python tiktok_daily_batch.py --dry-run  # show selection, flag nothing
"""

import argparse
import json
import os
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

MAX_PENDING = 5          # TikTok cap: pending inbox shares / account / 24h
RESERVED_FOR_TODAY = 2   # NA + AA daily shorts Ziad publishes after this runs
FRESH_SLOTS = 1          # reserved for the newest recent video
BACKLOG_BEFORE = "2026-08-01"   # June/July catch-up pool boundary
FRESH_WINDOW_DAYS = 7


def recovery_channel_ids():
    rows = requests.get(
        f"{SUPABASE_URL}/rest/v1/channels?select=id,slug&slug=in.(na,aa)",
        headers=HEADERS, timeout=15,
    ).json()
    return {r["id"]: r["slug"] for r in rows}


def fetch_candidates(chan_ids):
    """All NA/AA shorts relevant to slot accounting + selection."""
    rows = requests.get(
        f"{SUPABASE_URL}/rest/v1/content",
        headers=HEADERS,
        params={
            "select": ("id,title,status,channel_id,created_at,updated_at,"
                       "tiktok_video_id,generation_params,"
                       "video_storage_path,video_drive_url"),
            "channel_id": f"in.({','.join(chan_ids)})",
            "format": "eq.short",
            "deleted_at": "is.null",
            "order": "created_at.asc",
            "limit": "500",
        },
        timeout=30,
    ).json()
    for r in rows:
        p = r.get("generation_params") or {}
        if isinstance(p, str):
            p = json.loads(p)
        r["_params"] = p
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    print(f"=== TikTok daily batch {now.isoformat()} ===")

    chans = recovery_channel_ids()
    rows = fetch_candidates(list(chans))

    # --- Slot accounting ---------------------------------------------------
    # A v_inbox draft holds a slot until it's INBOX_DRAFT_MAX_AGE_H old.
    # Age comes from the snowflake timestamp inside the publish_id itself
    # (id >> 32 = unix seconds) — NOT updated_at, which the analytics
    # fetcher bumps every 30 min, making every draft look forever-fresh
    # (that bug kept 8/12 drafts holding slots through 8/14). Past the age
    # cap, reconcile_inbox_share clears the row for re-flagging; mirror
    # its 48h threshold here so both scripts agree on what holds a slot.
    INBOX_DRAFT_MAX_AGE_H = 48

    def _inbox_age_hours(publish_id):
        try:
            ts = int(str(publish_id).rsplit(".", 1)[-1]) >> 32
        except (ValueError, TypeError):
            return None
        if not (1.5e9 < ts < 4e9):
            return None
        return (now.timestamp() - ts) / 3600.0

    holding = []      # drafts likely still pending in the TikTok inbox
    flagged = []      # flagged, uploader hasn't sent yet
    for r in rows:
        tk = str(r.get("tiktok_video_id") or "")
        if tk.startswith("v_inbox"):
            age_h = _inbox_age_hours(tk)
            if age_h is None or age_h <= INBOX_DRAFT_MAX_AGE_H:
                holding.append(r)
        elif not tk and r["_params"].get("tiktok_publish_requested"):
            flagged.append(r)

    # Leave room for today's new shorts. Filling all 5 at 9 AM would make
    # TikTok reject the videos Ziad publishes later (too many pending shares).
    free = MAX_PENDING - RESERVED_FOR_TODAY - len(holding) - len(flagged)
    print(f"  slots: {MAX_PENDING} cap - {RESERVED_FOR_TODAY} reserved for today "
          f"- {len(holding)} pending in inbox "
          f"- {len(flagged)} already flagged = {free} free")
    if free <= 0:
        print("  No free slots today — yesterday's drafts not posted yet?")
        return

    # --- Eligibility -------------------------------------------------------
    def eligible(r):
        return (
            not r.get("tiktok_video_id")
            and r["status"] in ("approved", "published")
            and (r.get("video_storage_path") or r.get("video_drive_url"))
            and not r["_params"].get("tiktok_publish_requested")
            and not r["_params"].get("tiktok_retry_halted")
        )

    pool = [r for r in rows if eligible(r)]
    fresh_cut = (now - timedelta(days=FRESH_WINDOW_DAYS)).isoformat()
    fresh = sorted([r for r in pool if r["created_at"] >= fresh_cut],
                   key=lambda r: r["created_at"], reverse=True)
    backlog = [r for r in pool if r["created_at"] < BACKLOG_BEFORE]

    picks = fresh[:min(FRESH_SLOTS, free)]
    picks += backlog[:free - len(picks)]
    # Fresh pool empty and backlog short? Top up with anything eligible
    # between the two pools (early-August rows) so slots aren't wasted.
    if len(picks) < free:
        rest = [r for r in pool if r not in picks]
        picks += rest[:free - len(picks)]

    if not picks:
        print("  Nothing eligible to flag — backlog complete?")
        return

    # --- Flag --------------------------------------------------------------
    for r in picks:
        slug = chans.get(r["channel_id"], "?")
        kind = "FRESH" if r in fresh else "backlog"
        print(f"  -> [{slug}] {kind:<7} {r['created_at'][:10]} {r['title'][:55]}")
        if args.dry_run:
            continue
        p = dict(r["_params"])
        p["tiktok_publish_requested"] = True
        p["tiktok_publish_requested_by"] = "tiktok_daily_batch"
        p["tiktok_publish_requested_at"] = now.isoformat()
        p.pop("tiktok_last_error", None)
        p.pop("tiktok_error_at", None)
        resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/content?id=eq.{r['id']}&deleted_at=is.null",
            headers=HEADERS, json={"generation_params": p}, timeout=15,
        )
        if resp.status_code not in (200, 204):
            print(f"     FLAG FAILED: {resp.status_code} {resp.text[:150]}")

    print(f"  {'[dry-run] would flag' if args.dry_run else 'Flagged'} "
          f"{len(picks)} video(s). Poller sends them within ~5 min.")


if __name__ == "__main__":
    main()
