"""
Manual weekly planner for NA + AA only. Not scheduled.

The Sunday AI_WeeklyPlanner task (trend_scanner.py) plans every channel,
including NA and AA. Until 2026-11-04 this script also ran every Sunday and
gave each of them 14 shorts a week against a quota of 7, so its "PlanNAAA
Weekly" task is now disabled. ai_writer also skips any channel already
planned for next week, so running this by hand can't double a week.

Usage:
    python plan_na_aa.py                # generate + push to Supabase
    python plan_na_aa.py --dry-run      # generate + print, don't push
"""
import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")
sys.path.insert(0, str(Path(__file__).parent))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def _fetch_na_aa_channels() -> list:
    """Fetch ONLY the NA and AA channel rows in the shape
    ai_writer.generate_weekly_plan expects."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/channels?slug=in.(na,aa)"
        f"&select=id,name,slug,settings",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser(description="Weekly planner scoped to NA + AA")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan without pushing to Supabase")
    args = ap.parse_args()

    print("=" * 60)
    print("  NA + AA WEEKLY PLANNER")
    print("=" * 60)

    channels = _fetch_na_aa_channels()
    if len(channels) != 2:
        print(f"  Expected 2 channels (na, aa), got {len(channels)}: "
              f"{[c['slug'] for c in channels]}")
        sys.exit(1)

    for ch in channels:
        s = ch.get("settings") or {}
        print(f"  {ch['slug']:6} | personas={len(s.get('philosophers') or [])} "
              f"| frequency={s.get('frequency')}")

    if args.dry_run:
        # generate_weekly_plan auto-pushes to Supabase. To dry-run we
        # have to monkey-patch its push function before calling.
        import ai_writer
        original_push = ai_writer.push_weekly_plan_to_supabase
        ai_writer.push_weekly_plan_to_supabase = lambda *a, **k: print(
            "  [dry-run] skipping push to Supabase"
        )
        try:
            from ai_writer import generate_weekly_plan
            plan = generate_weekly_plan(channels=channels)
        finally:
            ai_writer.push_weekly_plan_to_supabase = original_push
    else:
        from ai_writer import generate_weekly_plan
        plan = generate_weekly_plan(channels=channels)

    if not plan:
        print("  Plan generation produced nothing — check ai_writer logs above.")
        sys.exit(2)

    print(f"\n  {len(plan)} plan items generated:")
    for item in plan[:25]:
        ch = item.get("channel", "?")
        day = item.get("day", "?")
        phil = item.get("philosopher", "?")
        topic = (item.get("topic") or "?")[:55]
        fmt = item.get("format", "short")
        print(f"    [{ch:3} {day:9} {fmt:6}] {phil:25} | {topic}")

    if len(plan) > 25:
        print(f"    ... and {len(plan) - 25} more")

    print("\n  DONE")


if __name__ == "__main__":
    main()
