"""Queue today's short for each channel, so videos are made without being picked.

Runs daily from Task Scheduler (AI_DailyAutoGen). content_poller then generates
the queued rows on this machine, and they wait at status=ready for Ziad to
press publish. This job never approves or publishes anything.

Rules:
  - One short per channel per day, from the CURRENT week's plan only.
    Today's topic comes first, then any earlier or later topic this week.
    Older planned rows are the backlog and are left alone.
  - Safe to re-run: a channel already queued today is skipped.
  - A channel with MAX_UNREVIEWED auto-made videos waiting stops generating,
    so a week away from the dashboard doesn't keep spending voice credits.

Usage:
    python daily_autogen.py            # queue today's shorts
    python daily_autogen.py --dry-run  # show the picks, change nothing
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
MAX_UNREVIEWED = 7  # about a week of unpublished shorts per channel


def _headers(prefer: str = None) -> dict:
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
               "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    return headers


def week_bounds(today: str) -> tuple:
    """(Monday, next Monday) of the week containing `today`, as ISO dates."""
    day = date.fromisoformat(today)
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat(), (monday + timedelta(days=7)).isoformat()


def pick_short_for_today(rows: list, today: str):
    """Today's first planned row; otherwise the earliest other row this week.
    Returns None when nothing is planned this week, which protects the backlog."""
    start, end = week_bounds(today)
    this_week = [r for r in rows
                 if start <= str(r.get("scheduled_at") or "")[:10] < end]
    for row in this_week:
        if str(row["scheduled_at"])[:10] == today:
            return row
    return min(this_week, key=lambda r: str(r["scheduled_at"])[:10], default=None)


def _get(path: str, params) -> list:
    response = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=_headers(),
                            params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def _channels_with_daily_shorts() -> list:
    from ai_writer import _parse_frequency
    channels = _get("channels", {"select": "id,slug,settings", "order": "slug.asc"})
    return [c for c in channels
            if _parse_frequency(((c.get("settings") or {}).get("frequency") or {}).get("short")) > 0]


def queue_channel(channel: dict, today: str, dry_run: bool) -> bool:
    """Queue this channel's short. Returns False if a request failed."""
    slug, channel_id = channel["slug"], channel["id"]
    base = [("channel_id", f"eq.{channel_id}"), ("deleted_at", "is.null"), ("format", "eq.short")]

    already = _get("content", base + [("select", "id"),
                                      ("generation_params->>auto_queued_on", f"eq.{today}"),
                                      ("limit", "1")])
    if already:
        print(f"  [{slug}] already queued today, skipping")
        return True

    waiting = _get("content", base + [("select", "id"), ("status", "eq.ready"),
                                      ("generation_params->>auto_queued_on", "not.is.null"),
                                      ("limit", str(MAX_UNREVIEWED))])
    if len(waiting) >= MAX_UNREVIEWED:
        print(f"  [{slug}] {len(waiting)} videos waiting for review, not making more")
        return True

    start, end = week_bounds(today)
    rows = _get("content", base + [("select", "id,title,scheduled_at,generation_params"),
                                   ("status", "eq.planned"),
                                   ("scheduled_at", f"gte.{start}"), ("scheduled_at", f"lt.{end}"),
                                   ("order", "scheduled_at.asc,created_at.asc")])
    pick = pick_short_for_today(rows, today)
    if not pick:
        print(f"  [{slug}] WARNING: nothing planned this week, no video today")
        return True

    title = (pick.get("title") or "")[:80]
    if dry_run:
        print(f"  [{slug}] would queue {pick['id'][:8]} ({str(pick['scheduled_at'])[:10]}): {title}")
        return True

    params = pick.get("generation_params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    params = {**params, "auto_queued_on": today,
              "auto_queued_at": datetime.now(timezone.utc).isoformat()}
    # The status filter means a row someone just changed by hand is left alone.
    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/content",
        headers=_headers("return=minimal"),
        params={"id": f"eq.{pick['id']}", "status": "eq.planned"},
        json={"status": "queued", "generation_params": params},
        timeout=30,
    )
    if response.status_code not in (200, 204):
        print(f"  [{slug}] FAILED to queue: {response.status_code} {response.text[:200]}")
        return False
    print(f"  [{slug}] queued {pick['id'][:8]}: {title}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = date.today().isoformat()  # this machine runs on Louisville time
    print(f"=== Daily auto-generation {today}{' (dry run)' if args.dry_run else ''} ===")
    ok = True
    for channel in _channels_with_daily_shorts():
        try:
            ok = queue_channel(channel, today, args.dry_run) and ok
        except requests.RequestException as e:
            print(f"  [{channel['slug']}] FAILED: {e}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
