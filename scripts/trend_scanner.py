"""
Trend Scanner — Scans Google Trends, Reddit, and news for trending topics
related to philosophy, wisdom, self-improvement, and motivation.

Maps trends to philosophers and generates weekly content suggestions.

Usage:
    python trend_scanner.py                    # scan + generate plan + push to Supabase
    python trend_scanner.py --dry-run          # scan + print plan, no Supabase push
    python trend_scanner.py --scan-only        # just scan trends, no plan

Can be called by:
  - n8n weekly_plan_generator workflow (Saturdays 22:00 UTC)
  - Manual CLI run
  - content_poller.py (future: weekly trigger)
"""

import os
import sys
import json
import re
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")
sys.path.insert(0, str(Path(__file__).parent))

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# ---------------------------------------------------------------------------
# Philosopher-topic mapping
# ---------------------------------------------------------------------------
TOPIC_PHILOSOPHER_MAP = {
    # Emotions & mental health
    "anxiety": ["Seneca", "Marcus Aurelius", "Epictetus"],
    "stress": ["Marcus Aurelius", "Seneca"],
    "depression": ["Seneca", "Epictetus"],
    "grief": ["Rumi", "Seneca", "Marcus Aurelius"],
    "anger": ["Seneca", "Marcus Aurelius"],
    "fear": ["Epictetus", "Seneca", "Marcus Aurelius"],
    "loneliness": ["Rumi", "Seneca"],
    "burnout": ["Seneca", "Marcus Aurelius", "Lao Tzu"],

    # Life situations
    "betrayal": ["Seneca", "Epictetus", "Marcus Aurelius"],
    "divorce": ["Seneca", "Rumi"],
    "job loss": ["Epictetus", "Seneca"],
    "failure": ["Seneca", "Nietzsche", "Marcus Aurelius"],
    "starting over": ["Seneca", "Lao Tzu"],
    "career change": ["Epictetus", "Seneca"],
    "toxic relationship": ["Epictetus", "Seneca", "Rumi"],
    "death": ["Seneca", "Marcus Aurelius", "Epictetus"],
    "money": ["Seneca", "Epictetus"],
    "success": ["Marcus Aurelius", "Nietzsche"],

    # Personal growth
    "discipline": ["Marcus Aurelius", "Epictetus", "Nietzsche"],
    "self-improvement": ["Marcus Aurelius", "Nietzsche", "Epictetus"],
    "purpose": ["Nietzsche", "Marcus Aurelius", "Emerson"],
    "motivation": ["Marcus Aurelius", "Nietzsche"],
    "resilience": ["Seneca", "Epictetus", "Nietzsche"],
    "letting go": ["Lao Tzu", "Rumi"],
    "patience": ["Lao Tzu", "Marcus Aurelius"],
    "forgiveness": ["Rumi", "Seneca"],
    "gratitude": ["Marcus Aurelius", "Seneca"],
    "solitude": ["Nietzsche", "Emerson", "Lao Tzu"],
    "simplicity": ["Lao Tzu", "Emerson"],
    "love": ["Rumi", "Gibran"],
    "wisdom": ["Lao Tzu", "Marcus Aurelius", "Gibran"],
    "truth": ["Nietzsche", "Epictetus"],
    "freedom": ["Epictetus", "Nietzsche", "Lao Tzu"],
    "suffering": ["Nietzsche", "Rumi", "Gibran"],
    "peace": ["Lao Tzu", "Marcus Aurelius", "Rumi"],
    "courage": ["Nietzsche", "Marcus Aurelius", "Seneca"],
    "change": ["Marcus Aurelius", "Lao Tzu", "Seneca"],
}

# Midform killed 2026-04-09, longform parked. Mon-Fri stories + daily shorts.
# Sat/Sun are rest days in the rotation.
FORMAT_ROTATION = ["short", "story", "short", "story", "short", "story", "short"]


# ---------------------------------------------------------------------------
# Trend sources
# ---------------------------------------------------------------------------
def scan_google_trends():
    """Fetch trending searches from Google Trends RSS."""
    print("  Scanning Google Trends...")
    trends = []
    try:
        resp = requests.get(
            "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code == 200:
            # Simple XML parsing for titles
            titles = re.findall(r"<title>(.+?)</title>", resp.text)
            for t in titles[1:21]:  # skip RSS title, take top 20
                trends.append({"topic": t, "source": "google_trends"})
            print(f"    Found {len(trends)} Google trends")
    except Exception as e:
        print(f"    Google Trends error: {e}")
    return trends


def scan_reddit():
    """Fetch top posts from philosophy and self-improvement subreddits."""
    print("  Scanning Reddit...")
    trends = []
    subreddits = [
        "philosophy", "Stoicism", "selfimprovement", "getdisciplined",
        "DecidingToBeBetter", "ZenHabits", "Meditation",
    ]
    for sub in subreddits:
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/hot.json?limit=5",
                timeout=10,
                headers={"User-Agent": "WisdomBot/1.0"},
            )
            if resp.status_code == 200:
                posts = resp.json().get("data", {}).get("children", [])
                for post in posts:
                    title = post["data"]["title"]
                    score = post["data"]["score"]
                    if score > 50:
                        trends.append({
                            "topic": title,
                            "source": f"reddit/r/{sub}",
                            "score": score,
                        })
        except Exception as e:
            pass  # Reddit rate limits are common
    print(f"    Found {len(trends)} Reddit topics")
    return trends


def scan_news():
    """Fetch philosophy/self-help related news headlines."""
    print("  Scanning news...")
    trends = []
    # Use a free news API or Google News RSS
    try:
        for query in ["philosophy", "self improvement", "mental health", "motivation"]:
            resp = requests.get(
                f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                titles = re.findall(r"<title>(.+?)</title>", resp.text)
                for t in titles[1:6]:
                    trends.append({"topic": t, "source": "google_news"})
    except Exception as e:
        print(f"    News error: {e}")
    print(f"    Found {len(trends)} news topics")
    return trends


# ---------------------------------------------------------------------------
# Topic extraction & philosopher matching
# ---------------------------------------------------------------------------
def extract_themes(trends):
    """Extract philosophical themes from raw trend topics."""
    themes = {}
    for trend in trends:
        topic_lower = trend["topic"].lower()
        for keyword, philosophers in TOPIC_PHILOSOPHER_MAP.items():
            if keyword in topic_lower:
                if keyword not in themes:
                    themes[keyword] = {
                        "keyword": keyword,
                        "philosophers": philosophers,
                        "sources": [],
                        "score": 0,
                    }
                themes[keyword]["sources"].append(trend["source"])
                themes[keyword]["score"] += trend.get("score", 10)

    # Sort by score
    return sorted(themes.values(), key=lambda x: x["score"], reverse=True)


def generate_weekly_plan_from_trends(themes):
    """Use Claude to generate a 7-day plan from extracted themes."""
    print("  Generating weekly plan via Claude...")
    from ai_writer import generate_weekly_plan

    # Build trending topics list for the AI
    trending = []
    for t in themes[:10]:
        trending.append({
            "topic": t["keyword"],
            "score": t["score"],
            "source": ", ".join(set(t["sources"])),
        })

    # If not enough themes from scanning, add evergreen topics
    evergreen = [
        {"topic": "dealing with anxiety", "score": 50, "source": "evergreen"},
        {"topic": "finding inner peace", "score": 40, "source": "evergreen"},
        {"topic": "overcoming failure", "score": 40, "source": "evergreen"},
        {"topic": "the power of discipline", "score": 35, "source": "evergreen"},
        {"topic": "letting go of the past", "score": 35, "source": "evergreen"},
        {"topic": "courage in uncertainty", "score": 30, "source": "evergreen"},
        {"topic": "the art of patience", "score": 30, "source": "evergreen"},
    ]
    while len(trending) < 7:
        trending.append(evergreen[len(trending) % len(evergreen)])

    channels = ["Deep Echoes of Wisdom", "Gibran Khalil Gibran"]
    plan = generate_weekly_plan(trending, channels)
    return plan


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Trend Scanner + Weekly Planner")
    parser.add_argument("--dry-run", action="store_true", help="Don't push to Supabase")
    parser.add_argument("--scan-only", action="store_true", help="Just scan, no plan")
    args = parser.parse_args()

    print("=" * 60)
    print("  TREND SCANNER")
    print(f"  {datetime.now()}")
    print("=" * 60)

    # Step 1: Scan all sources
    all_trends = []
    all_trends.extend(scan_google_trends())
    all_trends.extend(scan_reddit())
    all_trends.extend(scan_news())
    print(f"\n  Total raw trends: {len(all_trends)}")

    # Step 2: Extract themes
    themes = extract_themes(all_trends)
    print(f"  Matched themes: {len(themes)}")
    for t in themes[:10]:
        print(f"    {t['keyword']} (score: {t['score']}) -> {t['philosophers'][0]}")

    if args.scan_only:
        print("\n  --scan-only: stopping here")
        return

    # Step 3: Generate weekly plan
    plan = generate_weekly_plan_from_trends(themes)

    if not plan:
        print("  Plan generation failed")
        return

    print(f"\n  Weekly Plan ({len(plan)} days):")
    for item in plan:
        day = item.get("day", "?")
        phil = item.get("philosopher", "?")
        topic = item.get("topic", "?")
        fmt = item.get("format", "short")
        print(f"    {day}: [{fmt}] {phil} - {topic}")

    if args.dry_run:
        print("\n  --dry-run: not pushing to Supabase")
        # Save locally
        out = Path("C:/AI/system/output/weekly_plan.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump({"trends": [t for t in themes[:10]], "plan": plan}, f, indent=2)
        print(f"  Saved: {out}")
        return

    # Step 4: Push to Supabase (generate_weekly_plan already does this via ai_writer)
    print("\n  Plan pushed to Supabase via ai_writer.push_weekly_plan_to_supabase()")

    print(f"\n{'='*60}")
    print("  DONE")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
