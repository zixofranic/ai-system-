"""
Anthropic API Integration for Wisdom Content Pipeline
=====================================================
Smart content generation using Claude (Anthropic API) and Ollama (local).

Functions:
- generate_weekly_plan:     7-day topic suggestions from trending data (Haiku)
- generate_short_script:    Single-quote short script (Ollama + Haiku)
- generate_midform_script:  Multi-quote connected script (Sonnet)
- generate_longform_script: Full narrative 15-25 min (Sonnet)
- generate_youtube_metadata: Title, description, tags for YouTube SEO (Haiku)
- generate_suno_prompt:     Suno music prompt for a philosopher style (Haiku)

Env: reads ANTHROPIC_API_KEY from C:/AI/.env
Ollama: localhost:11434
"""

import json
import os
import sys
import re
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_ENV_PATH = Path("C:/AI/.env")
load_dotenv(_ENV_PATH)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise RuntimeError(f"ANTHROPIC_API_KEY not found. Check {_ENV_PATH}")

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-6"

OLLAMA_URL = "http://localhost:11434/api/generate"

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _call_anthropic(model: str, system: str, user: str,
                    max_tokens: int = 2048, temperature: float = 0.7) -> str:
    """Call Anthropic API and return the text response."""
    message = _client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return message.content[0].text


def _call_ollama(prompt: str, model: str,
                 temperature: float = 0.8) -> str:
    # `model` is required — no safe default exists across environments, and a
    # stale default (e.g. a model that was never installed on this host) fails
    # silently with a 404 that looked like an outage for a week.
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("response", "")
    except requests.exceptions.RequestException as e:
        print(f"[ai_writer] WARNING: Ollama call failed ({e}). Falling back to Haiku.")
        return _call_anthropic(
            HAIKU_MODEL,
            "You are a philosopher and poet. Write in the authentic style requested.",
            prompt,
            max_tokens=512,
        )


def sanitize_quote(text: str) -> str:
    # Strip artifacts that small Ollama models sometimes inject into quotes:
    # "[AI-generated in the spirit of X]", "(AI-generated...)", and trailing
    # attribution lines like "-- Marcus Aurelius" / "~ Seneca". Must run on
    # every quote path — primary Ollama, fallback, and any future caller.
    if not text:
        return ""
    text = text.strip().strip('"').strip("'")
    text = re.sub(r'\s*\[.*?generated.*?\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\(.*?generated.*?\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*[-~—]+\s*[A-Z][\w\s]*$', '', text.strip())
    return text.strip()


def _parse_json_response(text: str) -> dict:
    """Extract JSON from a response that may contain markdown fences."""
    # Try to find JSON in code fences
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        text = match.group(1)
    # Try direct parse
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find first { ... } block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return {"raw": text}


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Channel IDs (hardcoded defaults, overridable via channel_map)
_DEFAULT_CHANNEL_MAP = {
    "Gibran Khalil Gibran": "ff18bcb2-21db-4320-89ad-c24d04f0dad3",  # Gibran channel
}
_WISDOM_CHANNEL_ID = "1b3ba813-31c5-42b3-a270-67e85fcc7123"

_DAY_OFFSETS = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}


def _parse_frequency(freq) -> int:
    """Parse a channels.settings.frequency string to a per-week integer count.

    'daily' -> 7, 'weekly' -> 1, 'Nx/week' -> N, 'N-Mx/week' -> M (upper),
    'paused*' / 'off' / '' / None -> 0, 'monthly' -> 0 (not weekly-plannable).
    """
    if freq is None:
        return 0
    s = str(freq).strip().lower()
    if not s or "paused" in s or s == "off" or s == "monthly":
        return 0
    if s == "daily":
        return 7
    if s == "weekly":
        return 1
    import re
    m = re.match(r"(\d+)\s*-\s*(\d+)\s*x?/?\s*week", s)
    if m:
        return int(m.group(2))
    m = re.match(r"(\d+)\s*x?/?\s*week", s)
    if m:
        return int(m.group(1))
    m = re.match(r"(\d+)", s)
    if m:
        return int(m.group(1))
    return 0


def _fetch_active_channels_for_planning() -> list:
    """Fetch all channels from Supabase, newest-first ordering irrelevant."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    url = (f"{SUPABASE_URL}/rest/v1/channels"
           "?select=id,name,slug,settings"
           "&order=created_at.asc")
    try:
        resp = requests.get(url, headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        }, timeout=15)
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        print(f"[planner] Failed to fetch channels: {e}")
        return []


def _supabase_headers():
    """Standard headers for Supabase REST API calls."""
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _monday_of_next_week() -> datetime:
    """Return the Monday of NEXT week at midnight UTC.
    Planning always targets the upcoming week, not the current one."""
    today = datetime.now(timezone.utc).date()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7  # if today is Monday, target next Monday
    return today + timedelta(days=days_until_monday)


def push_weekly_plan_to_supabase(plan: list, channel_map: dict = None) -> str:
    """
    Write a generated weekly plan to Supabase.

    Creates one row in `weekly_plans` and one row per plan item in
    `plan_topics`.

    Args:
        plan: List of 7 dicts from generate_weekly_plan().
        channel_map: Optional dict mapping philosopher name -> channel UUID.
                     Falls back to: Gibran -> Gibran channel, everyone else
                     -> Wisdom channel.

    Returns:
        The UUID of the created weekly_plan row.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment"
        )

    headers = _supabase_headers()
    monday = _monday_of_next_week()

    # Merge provided channel_map with defaults.
    # channel_map accepts BOTH keys: philosopher-name (legacy) and slug (new).
    # Slug entries take precedence when an item has both 'channel' and 'philosopher'.
    cmap = dict(_DEFAULT_CHANNEL_MAP)
    if channel_map:
        cmap.update(channel_map)
    slug_to_id = {k: v for k, v in cmap.items() if k in cmap and len(k) < 20 and " " not in k}

    # --- Step 1: Create weekly_plans row ---
    iso_week = monday.isocalendar()[1]
    wp_payload = {
        "week_start": monday.isoformat(),
        "week_number": iso_week,
        "year": monday.year,
        "status": "draft",
    }
    wp_resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/weekly_plans",
        headers=headers,
        json=wp_payload,
        timeout=30,
    )
    wp_resp.raise_for_status()
    wp_data = wp_resp.json()
    # Supabase returns a list when Prefer: return=representation is set
    weekly_plan_id = wp_data[0]["id"] if isinstance(wp_data, list) else wp_data["id"]

    # --- Step 2: Create plan_topics + content rows ---
    for item in plan:
        philosopher = item.get("philosopher", "")
        day_name = item.get("day", "Monday")
        fmt = item.get("format", "short")
        channel_slug = item.get("channel", "wisdom")

        # Resolve channel_id:
        #   1. slug_to_id[channel_slug]         (new path — multi-channel)
        #   2. Gibran persona -> Gibran channel (legacy)
        #   3. cmap[philosopher]                (legacy persona→channel map)
        #   4. Wisdom channel                   (last-resort fallback)
        channel_id = slug_to_id.get(channel_slug)
        if not channel_id:
            if channel_slug == "gibran" or "Gibran" in philosopher:
                channel_id = _DEFAULT_CHANNEL_MAP.get("Gibran Khalil Gibran",
                                                       _WISDOM_CHANNEL_ID)
            else:
                channel_id = cmap.get(philosopher, _WISDOM_CHANNEL_ID)

        day_offset = _DAY_OFFSETS.get(day_name, 0)
        scheduled_date = (monday + timedelta(days=day_offset)).isoformat()

        # Create plan_topic row
        topic_payload = {
            "plan_id": weekly_plan_id,
            "title": item.get("topic", ""),
            "philosopher": philosopher,
            "channel_id": channel_id,
            "scheduled_date": scheduled_date,
            "day_of_week": day_offset,
            "status": "suggested",
        }
        tp_resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/plan_topics",
            headers=headers, json=topic_payload, timeout=30,
        )
        if tp_resp.status_code not in (200, 201):
            print(f"[ai_writer] plan_topic failed: {tp_resp.text[:100]}")
            continue
        tp_data = tp_resp.json()
        tp_id = tp_data[0]["id"] if isinstance(tp_data, list) else tp_data.get("id")

        # Create matching content row (so it shows on the Plan page)
        content_payload = {
            "channel_id": channel_id,
            "title": item.get("topic", ""),
            "philosopher": philosopher,
            "topic": item.get("topic", ""),
            "quote_text": None,
            "format": fmt,
            "status": "planned",
            "scheduled_at": scheduled_date + "T09:00:00Z",
            "is_system_generated": True,
        }
        if tp_id:
            content_payload["plan_topic_id"] = tp_id
        requests.post(
            f"{SUPABASE_URL}/rest/v1/content",
            headers=headers, json=content_payload, timeout=30,
        )

    print(f"[ai_writer] Weekly plan {weekly_plan_id} pushed to Supabase "
          f"(week of {monday.isoformat()}, {len(plan)} items)")
    return weekly_plan_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_weekly_plan(trending_topics: list = None,
                         channels: list = None) -> list:
    """Build a weekly content plan across every active channel in Supabase.

    Per-channel quotas come from `channels.settings.frequency` (parsed by
    `_parse_frequency`). Personas come from `channels.settings.philosophers`.
    Channels with no quota across any format are skipped silently.

    Args:
        trending_topics: optional list of trend dicts (topic/score/source)
            used as inspiration. If omitted or empty, Claude draws from
            evergreen themes.
        channels: optional pre-fetched channel rows. If None, fetches from
            Supabase via `_fetch_active_channels_for_planning`.

    Returns:
        List of plan item dicts: {day, channel, philosopher, topic, format, hook}.
        Also pushes rows to Supabase (weekly_plans, plan_topics, content).
    """
    trending_topics = trending_topics or []
    if channels is None:
        channels = _fetch_active_channels_for_planning()

    # Build per-channel specs with computed quotas
    specs = []
    for ch in channels:
        settings = ch.get("settings") or {}
        personas = settings.get("philosophers") or []
        if not personas:
            continue
        freq = settings.get("frequency") or {}
        quotas = {}
        for fmt in ("short", "story", "midform", "story_vertical"):
            qty = _parse_frequency(freq.get(fmt))
            if qty > 0:
                quotas[fmt] = qty
        if not quotas:
            continue
        specs.append({
            "id": ch["id"],
            "name": ch["name"],
            "slug": ch["slug"],
            "personas": personas,
            "quotas": quotas,
            "bootstrap": bool((settings.get("bootstrap_mode") or {}).get("active")),
        })

    if not specs:
        print("[planner] No channels with non-zero quotas — nothing to plan")
        return []

    # Build Claude prompt dynamically
    channel_blocks = []
    total_items = 0
    for s in specs:
        qlines = []
        for fmt, qty in sorted(s["quotas"].items()):
            qlines.append(f"  - {qty} {fmt} per week")
            total_items += qty
        tag = " (bootstrap — shorts-only, single-narration format slot)" if s["bootstrap"] else ""
        channel_blocks.append(
            f'CHANNEL "{s["name"]}" (slug: {s["slug"]}){tag}\n'
            f"  Personas: {', '.join(s['personas'])}\n" + "\n".join(qlines)
        )
    channels_text = "\n\n".join(channel_blocks)

    topics_text = (
        json.dumps(trending_topics, indent=2)
        if trending_topics
        else "(no trending feed this week — draw from evergreen themes)"
    )

    # Slug-keyed channel kind hint (for tone rules)
    recovery_slugs = {s["slug"] for s in specs if s["slug"] in ("na", "aa")}
    philo_slugs = {s["slug"] for s in specs if s["slug"] in ("wisdom", "gibran")}
    tone_rules = []
    if recovery_slugs:
        tone_rules.append(
            f"- For RECOVERY channels ({', '.join(sorted(recovery_slugs))}): "
            "topics stay within recovery life + daily struggle. ABSOLUTELY NO "
            "reference to or quoting of copyrighted 12-step literature (NA "
            "Basic Text, Just For Today, Big Book, 12&12, Daily Reflections, "
            "Twenty-Four Hours a Day, AA Grapevine). No real member names. "
            "No affiliation claims. Do not use the exact wording of any of "
            "the Twelve Steps."
        )
    if "wisdom" in philo_slugs:
        tone_rules.append(
            "- For the Wisdom channel: topics framed through the specific "
            "philosopher's lens. Don't repeat the same philosopher two days "
            "in a row."
        )
    if "gibran" in philo_slugs:
        tone_rules.append(
            "- For the Gibran channel: topics draw from themes in The Prophet, "
            "The Broken Wings, Sand and Foam."
        )
    tone_rules_text = "\n".join(tone_rules) if tone_rules else ""

    system = (
        "You are a content strategist for multiple YouTube channels spanning "
        "philosophy and recovery-community content. You create weekly content "
        "plans that balance timeless themes with trending relevance. Output "
        "valid JSON only."
    )
    user = f"""Create a FULL weekly content plan for the channels below, distributed across Monday through Sunday.

Trending topics (optional inspiration):
{topics_text}

CHANNELS AND WEEKLY QUOTAS:

{channels_text}

TOTAL ITEMS TO RETURN: {total_items}

RULES:
- Each item is one (day, channel, persona, topic, format) tuple.
- Distribute each channel's quota sensibly across Mon-Sun (daily = one each day; 5/week = Mon-Fri; 2/week = Tue+Thu; weekly = pick one weekday).
- Rotate personas within each channel; don't repeat the same persona two days in a row unless the channel has only one persona.
- Topics must be specific and concrete, not generic. Prefer a narrow angle ("the morning after a hard night", "what sponsors do when you call at 2am") over broad buckets ("on life", "on courage").
- Output ONLY formats listed in each channel's quota above.
{tone_rules_text}

Return a JSON array of exactly {total_items} objects:
[
  {{
    "day": "Monday",
    "channel": "<slug>",
    "philosopher": "<persona name>",
    "topic": "specific, compelling topic title",
    "format": "short|story|midform|story_vertical",
    "hook": "one-sentence opening angle"
  }}
]"""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=6000)
    result = _parse_json_response(response)
    plan = result if isinstance(result, list) else result.get("plan", result.get("raw", []))

    if isinstance(plan, list) and plan and SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
            slug_map = {s["slug"]: s["id"] for s in specs}
            push_weekly_plan_to_supabase(plan, channel_map=slug_map)
        except Exception as e:
            print(f"[ai_writer] WARNING: Failed to push weekly plan to Supabase: {e}")

    return plan


def generate_short_script(philosopher: str, topic: str, ollama_model: str,
                          tone: str = None, notes: str = None,
                          previous_quotes: list = None) -> dict:
    """
    Generate a short-form script: quote from Ollama, metadata from Haiku.

    Args:
        philosopher: e.g., "Marcus Aurelius"
        topic: e.g., "dealing with anxiety"
        tone: optional tone (e.g., "contemplative", "urgent")
        notes: optional additional guidance

    Returns:
        Dict with keys: quote, title, description, tags, thumbnail_text,
                        music_mood, suno_prompt
    """
    # Step 1: Generate the quote with Ollama (local, more creative)
    tone_line = f"Tone: {tone}" if tone else "Tone: contemplative and profound"
    notes_line = f"Additional notes: {notes}" if notes else ""
    dedup_line = ""
    if previous_quotes:
        trimmed = [q[:120] for q in previous_quotes[:15]]
        dedup_line = "\n\nDo NOT repeat or closely paraphrase any of these previous quotes:\n" + "\n".join(f"- {q}" for q in trimmed) + "\n\nWrite something genuinely different."

    ollama_prompt = f"""Write a single original philosophical quote in the authentic style and voice of {philosopher}, on the topic of "{topic}".

{tone_line}
{notes_line}

Requirements:
- Must sound authentically like {philosopher}
- 1-3 sentences, poetic and quotable
- Deep insight, not surface-level advice
- Do NOT include attribution or quotation marks

Return ONLY the quote text, nothing else.{dedup_line}"""

    quote = sanitize_quote(_call_ollama(ollama_prompt, model=ollama_model))

    # Step 2: Generate metadata with Haiku
    system = (
        "You are a YouTube content optimizer for philosophical channels. "
        "Output valid JSON only."
    )
    user = f"""Given this philosophical quote and context, generate YouTube Short metadata.

Quote: "{quote}"
Philosopher: {philosopher}
Topic: {topic}

Return JSON:
{{
  "title": "YouTube title (under 60 chars, engaging, includes philosopher name)",
  "description": "YouTube description (2-3 lines, include relevant hashtags)",
  "tags": ["tag1", "tag2", "..."],
  "thumbnail_text": "Short punchy text for thumbnail (under 6 words)",
  "music_mood": "one word mood for background music",
  "suno_prompt": "A short prompt to generate ambient background music in Suno AI (under 50 words, describe instruments and mood)"
}}"""

    meta_response = _call_anthropic(HAIKU_MODEL, system, user, max_tokens=512)
    meta = _parse_json_response(meta_response)
    meta["quote"] = quote
    meta["philosopher"] = philosopher
    meta["topic"] = topic
    return meta


def generate_midform_script(philosopher: str, topic: str,
                            num_quotes: int = 4, tone: str = None,
                            notes: str = None,
                            previous_quotes: list = None,
                            style: str = "in_character") -> dict:
    """
    Use Claude Sonnet to write a connected multi-quote script for midform video.

    Args:
        philosopher: e.g., "Rumi"
        topic: e.g., "the pain of growth"
        num_quotes: Number of quote sections (3-5)
        tone: optional tone
        notes: optional guidance
        style: 'in_character' (default — quotes written AS the philosopher; narration_segments
            are minimal connective tissue) or 'narrator' (third-person guide introduces the
            philosopher's ideas; the "quotes" become paraphrased positions the narrator unfolds,
            never fabricated direct citations).

    Returns:
        Dict with keys: quotes (list), narration_segments (list),
                        transitions (list), title, description, tags,
                        music_mood, suno_prompt, art_prompts (list)
    """
    if style not in ("in_character", "narrator"):
        raise ValueError(
            f"style='{style}' invalid; must be 'in_character' or 'narrator'"
        )

    tone_line = f"Tone: {tone}" if tone else "Tone: contemplative and layered"
    notes_line = f"Additional context: {notes}" if notes else ""
    dedup_line = ""
    if previous_quotes:
        trimmed = [q[:120] for q in previous_quotes[:15]]
        dedup_line = "\n\nDo NOT repeat or closely paraphrase any of these previously published quotes:\n" + "\n".join(f"- {q}" for q in trimmed) + "\n\nEnsure all quotes are genuinely original."

    if style == "narrator":
        system = (
            "You are a thoughtful contemporary narrator introducing a philosopher's ideas "
            "to a modern viewer. Third-person throughout — name the philosopher openly, "
            "paraphrase his positions faithfully, never fabricate direct quotes presented "
            "as citations. The narration_segments carry the narrator's unfolding voice; "
            "the 'quotes' field holds short paraphrased positions the narrator can pivot "
            "to, NOT verbatim quotes from real texts. Output valid JSON only."
        )
        perspective_directive = (
            f"\n\nPERSPECTIVE: NARRATOR. Speak ABOUT {philosopher}, not AS him. "
            f"The 'quotes' array holds paraphrased positions — short rhythmic lines the narrator "
            f"can pivot to — but framed as the narrator's interpretation, not direct citation. "
            f"Open by naming {philosopher}; let the narrative_segments carry your voice between "
            f"each paraphrased position. Do NOT pretend to quote his actual writings verbatim."
        )
    else:
        system = (
            "You are a philosophical writer creating connected video scripts. "
            "Each quote should build on the previous, creating a narrative arc. "
            "Write in the authentic voice of the philosopher. Output valid JSON only."
        )
        perspective_directive = ""

    user = f"""Write a midform video script with {num_quotes} connected quotes in the style of {philosopher} on "{topic}".{perspective_directive}

{tone_line}
{notes_line}{dedup_line}

Return JSON:
{{
  "title": "YouTube title (under 70 chars)",
  "description": "YouTube description (3-4 lines with hashtags)",
  "tags": ["tag1", "tag2", "..."],
  "quotes": [
    "quote 1 text",
    "quote 2 text",
    "..."
  ],
  "narration_segments": [
    "Optional brief intro narration before quote 1",
    "Transition narration between quote 1 and 2",
    "..."
  ],
  "transitions": [
    "Description of visual/mood transition between sections"
  ],
  "art_prompts": [
    "Image generation prompt for quote 1 background art",
    "Image generation prompt for quote 2 background art",
    "..."
  ],
  "music_mood": "overall mood description",
  "suno_prompt": "Suno AI music generation prompt (under 80 words)"
}}

Each quote should be 1-3 sentences. The quotes should form a narrative arc:
1. Introduction/problem
2. Deepening/exploration
3. Turning point/insight
4. Resolution/wisdom (if 4+ quotes)
5. Transcendence (if 5 quotes)"""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=3000)
    result = _parse_json_response(response)
    result["philosopher"] = philosopher
    result["topic"] = topic
    return result


_RECOVERY_PERSONA_VOICES = {
    "The Old-Timer": (
        "The voice of someone with twenty-plus years in recovery. Steady, "
        "quiet, slightly weathered. Short sentences. Plain words. Metaphors "
        "from ordinary life (coffee, mornings, porches), never poetry. "
        "Acknowledges hard things matter-of-factly, doesn't dramatize. "
        "Sometimes speaks as 'we' because recovery is shared."
    ),
    "The Sponsor": (
        "The voice of someone with five to fifteen years of recovery who "
        "has sponsored others. Direct but warm. Practical. Asks more than "
        "answers. Honey with salt in it. Unafraid to say hard things "
        "kindly. Speaks from experience, not expertise. Uses 'I' freely."
    ),
    "The Voice of the Rooms": (
        "The distilled anonymous voice of a meeting. Honest, textured, "
        "unpolished in the real way. Sometimes 'we', sometimes a single "
        "invented member (never borrowed from any real person). Holds "
        "hope without insisting on it. Racially, gender, and generationally "
        "diverse in who you channel."
    ),
}

_RECOVERY_COMPLIANCE_BLOCK = """COMPLIANCE — hard rules, no exceptions:
- NEVER quote or paraphrase within ten words of any copyrighted recovery literature: NA Basic Text, Just For Today (the daily reader), It Works How and Why, AA Big Book, 12&12, Daily Reflections, Twenty-Four Hours a Day, AA Grapevine, or any 12-step service publication.
- NEVER use the exact wording of any of the Twelve Steps.
- NEVER name real people (no Bill W., no Jimmy K., no authors, no real members).
- NEVER claim to represent, speak for, or belong to NA, AA, or any 12-step organization.
- NEVER mention specific meetings, cities as meeting locations, or identifiable groups.
- NEVER give medical advice; if the topic nears a crisis, mention seeking professional help and stop.
- NEVER guarantee outcomes.
- SAFE: public-domain slogans ("one day at a time", "keep coming back", "easy does it", "progress not perfection", "this too shall pass"), Serenity Prayer short form, generic spiritual language ("higher power", "god as we understood")."""


def _recovery_short_in_character_system(persona, persona_voice, channel_cue,
                                        low, high, center, target_seconds, dedup):
    """In-character recovery monologue — speaks AS the archetype (first-person testimony)."""
    return f"""You are writing a single short-form narration for a YouTube Short on a recovery-adjacent channel.

PERSONA: {persona_voice}

CHANNEL SCOPE: {channel_cue}

TARGET LENGTH: {low}-{high} words (roughly {target_seconds} seconds of spoken narration). Aim for {center} words.

STRUCTURE (a short that actually lands):
  - OPEN on a specific concrete image or moment (not an abstract idea).
  - TURN to a small, earned truth — something the listener quietly recognizes.
  - CLOSE on a line they can carry for the rest of the day.

Write for the ear, not the eye. Use sentence fragments where they land. Rhythm matters. Trust silence. Do not explain your own metaphors.

{_RECOVERY_COMPLIANCE_BLOCK}

Output JSON ONLY, no preamble, no code fences:
{{
  "quote": "the full {target_seconds}-second narration as a single string. Natural pauses via punctuation. No stage directions. No attribution.",
  "title": "YouTube Short title, under 60 chars, evocative not clickbait",
  "description": "2-3 line YouTube description; can mention the Fellows app in the last line",
  "tags": ["8-12 recovery-relevant tags, lowercase, no hashtags"],
  "thumbnail_text": "short text for thumbnail overlay, under 5 words",
  "music_mood": "one word for background music mood",
  "suno_prompt": "short ambient music generation prompt, under 40 words",
  "art_scene": "one concrete Hopper/Wyeth scene for the short's background art (e.g. 'hands around a coffee mug on a kitchen counter at dawn')"
}}{dedup}"""


def _recovery_short_narrator_system(persona, persona_voice, channel_cue,
                                    low, high, center, target_seconds, dedup):
    """Narrator recovery reflection — third-person voice ABOUT recovery, not from inside the rooms.

    The narrator observes; the persona testifies. Same compliance rules apply because
    even an outside-observer voice can violate Tradition 6/11/12 if it names organizations
    or quotes literature.
    """
    return f"""You are writing a single short-form narration for a YouTube Short on a recovery-adjacent channel. The narrator is a thoughtful contemporary voice speaking ABOUT recovery — not from inside the rooms. Third-person framing throughout.

PERSPECTIVE — this is the NARRATOR style:
  - Refer to people in recovery in third person: "people in early recovery", "the ones who've been around the rooms a while", "someone four months in might describe it as...".
  - NEVER use "I", "we", or "you" as if speaking from inside recovery. The narrator is OUTSIDE looking in with care.
  - The narrator can observe what the {persona} archetype tends to know — but does not speak AS that archetype. Reference the archetype as something OBSERVED: "you'll hear it from the old-timers", "the sponsors talk about it like..."
  - If you find yourself testifying ("I remember the first time"), you've slipped into in-character voice — back out into observation.

ARCHETYPE BEING DESCRIBED (for the narrator's reference, not for impersonation):
{persona_voice}

CHANNEL SCOPE: {channel_cue}

TARGET LENGTH: {low}-{high} words (roughly {target_seconds} seconds of spoken narration). Aim for {center} words.

STRUCTURE (a narrator short that lands):
  - OPEN with an observed scene or pattern — something the narrator has noticed about people in recovery.
  - DEVELOP the observation — what's underneath it, what it reveals.
  - CLOSE with a quiet truth the narrator wants the listener to carry.

Write for the ear. Calm, measured pace. Avoid clinical detachment — the narrator cares, just doesn't pretend to be in recovery themselves.

{_RECOVERY_COMPLIANCE_BLOCK}

Output JSON ONLY, no preamble, no code fences:
{{
  "quote": "the full {target_seconds}-second narration as a single string. Third-person throughout. Natural pauses via punctuation. No stage directions. No attribution.",
  "title": "YouTube Short title, under 60 chars, evocative not clickbait",
  "description": "2-3 line YouTube description; can mention the Fellows app in the last line",
  "tags": ["8-12 recovery-relevant tags, lowercase, no hashtags"],
  "thumbnail_text": "short text for thumbnail overlay, under 5 words",
  "music_mood": "one word for background music mood",
  "suno_prompt": "short ambient music generation prompt, under 40 words",
  "art_scene": "one concrete Hopper/Wyeth scene for the short's background art"
}}{dedup}"""


def generate_recovery_short_script(persona: str, topic: str,
                                   channel_slug: str,
                                   target_seconds: int = 40,
                                   previous_quotes: list = None,
                                   style: str = "in_character") -> dict:
    """Write a ~40-second Opus-grade short for NA/AA channels.

    Unlike the Ollama one-liner used for Wisdom/Gibran shorts, this produces
    a compact piece that teaches-lands-closes in ~100-130 words (40-50s at
    conversational pace). Includes title + description + tags + art scene
    so the orchestrator can skip the separate Haiku metadata step.

    Args:
        persona: archetype name ("The Old-Timer", "The Sponsor", "The Voice of the Rooms")
        topic: compelling specific topic (e.g. "the call you almost didn't make")
        channel_slug: "na" or "aa"
        target_seconds: target narration duration; drives word count target
        previous_quotes: recent outputs to dedupe against
        style: 'in_character' (default — first-person archetype monologue) or
            'narrator' (third-person reflective voice ABOUT recovery, not from inside).

    Returns a dict shaped like generate_short_script's output:
      { quote, title, description, tags, thumbnail_text, music_mood,
        suno_prompt, art_scene, theme, philosopher, topic }
    """
    if style not in ("in_character", "narrator"):
        raise ValueError(
            f"style='{style}' invalid; must be 'in_character' or 'narrator'"
        )

    persona_voice = _RECOVERY_PERSONA_VOICES.get(
        persona, "A calm, experienced recovery voice."
    )

    channel_cue = {
        "na": "Recovery from addiction, broadly. Avoid language that implies a specific 12-step program.",
        "aa": "Recovery from alcohol, broadly. Avoid language that implies a specific 12-step program.",
    }.get(channel_slug, "General recovery.")

    # Word targets: ~150 wpm conversational; allow headroom on both sides
    low = int(target_seconds * 2.2)   # 40s -> ~88 words floor
    high = int(target_seconds * 2.9)  # 40s -> ~116 words ceiling
    center = (low + high) // 2

    dedup = ""
    if previous_quotes:
        recent = [str(q)[:120] for q in previous_quotes[:15]]
        dedup = "\n\nDo NOT retread these recently-used angles:\n" + "\n".join(f"- {q}" for q in recent)

    if style == "narrator":
        system = _recovery_short_narrator_system(
            persona, persona_voice, channel_cue, low, high, center,
            target_seconds, dedup,
        )
        user = f"""Write a narrator-style short-form reflection ABOUT recovery on: {topic}

The {persona} archetype is your reference point but NOT your voice — describe what such a person tends to know, never speak AS them."""
    else:
        system = _recovery_short_in_character_system(
            persona, persona_voice, channel_cue, low, high, center,
            target_seconds, dedup,
        )
        user = f"""Write a short-form narration in the voice of "{persona}" on: {topic}"""

    response = _call_anthropic(OPUS_MODEL, system, user, max_tokens=2000)
    parsed = _parse_json_response(response)

    quote = (parsed.get("quote") or "").strip()
    if not quote:
        raise ValueError(f"Opus returned empty quote for {persona}/{topic}")

    # Normalise to the shape process_short expects: it reads `quote` + metadata,
    # then builds title/description from a SEPARATE Haiku call. We want to skip
    # that here (Opus already wrote them), so return the full dict and let the
    # orchestrator short-circuit the metadata step for na/aa.
    return {
        "quote": quote,
        "title": parsed.get("title") or f"{persona}: {topic[:40]}",
        "description": parsed.get("description", ""),
        "tags": parsed.get("tags", ["recovery", "daily meditation", topic]),
        "thumbnail_text": parsed.get("thumbnail_text", ""),
        "music_mood": parsed.get("music_mood", "contemplative"),
        "suno_prompt": parsed.get("suno_prompt", ""),
        "art_scene": parsed.get("art_scene", ""),
        "philosopher": persona,
        "topic": topic,
        "channel_slug": channel_slug,
        "format_hint": "recovery_short",
    }


_GIBRAN_CORPUS_PATH = Path(__file__).parent / "data" / "gibran_corpus.json"
_GIBRAN_CORPUS_CACHE: list = []


def _load_gibran_corpus() -> list:
    """Lazy-load the Gibran corpus (26 Prophet chapters + 30 Madman parables
    + 20 Forerunner parables + 243 Sand and Foam aphorisms + 75 Garden of
    the Prophet passages). Built by scripts/build_gibran_corpus.py."""
    global _GIBRAN_CORPUS_CACHE
    if _GIBRAN_CORPUS_CACHE:
        return _GIBRAN_CORPUS_CACHE
    if not _GIBRAN_CORPUS_PATH.exists():
        raise FileNotFoundError(
            f"Gibran corpus missing at {_GIBRAN_CORPUS_PATH}. "
            f"Run: python scripts/build_gibran_corpus.py"
        )
    _GIBRAN_CORPUS_CACHE = json.loads(_GIBRAN_CORPUS_PATH.read_text(encoding="utf-8"))
    return _GIBRAN_CORPUS_CACHE


# Stopwords to drop from topic queries when scoring relevance — these
# carry no signal and would dilute scores if matched.
_STOPWORDS = frozenset((
    "a an and as at be but by for from in is it of on or so the their "
    "them then there they this to was we were what when which who why "
    "with you your"
).split())


def _topic_keywords(topic: str) -> list:
    """Extract scoring keywords from a topic string. Lowercase, strip
    punctuation, drop stopwords + 1-2 char tokens."""
    tokens = re.findall(r"[A-Za-z][A-Za-z']+", topic.lower())
    return [t for t in tokens if len(t) >= 3 and t not in _STOPWORDS]


def fetch_gibran_sources(topic: str, n: int = 4) -> list:
    """Pick the N most topic-relevant passages from the Gibran corpus.

    Scoring: each keyword from the topic gets +3 if it appears in the
    passage TITLE (e.g. "On Pain" wins big for topic="pain"), +1 per
    body occurrence (capped at 5 to avoid one passage dominating just
    by repeating a word). Plus a small bias toward The Prophet (most
    canonical for theme-based queries) and away from Sand and Foam
    (aphorisms are too short to anchor a 2-min essay alone).

    Returns at most N passages, sorted by score. Always returns >= 1
    even if the topic doesn't match (falls back to top Prophet chapters).
    """
    corpus = _load_gibran_corpus()
    keywords = _topic_keywords(topic)
    if not keywords:
        # Fallback: a few core Prophet chapters
        prophet = [c for c in corpus if c["book"] == "The Prophet"]
        return prophet[:n]

    scored = []
    for c in corpus:
        title_l = c["title"].lower()
        body_l = c["text"].lower()
        score = 0.0
        for kw in keywords:
            if kw in title_l:
                score += 3.0
            occurrences = body_l.count(kw)
            score += min(5.0, occurrences)
        # Small per-book bias
        if c["book"] == "The Prophet":
            score *= 1.15
        elif c["book"] == "Sand and Foam":
            score *= 0.6
        scored.append((score, c))

    scored.sort(key=lambda x: -x[0])
    top = [c for s, c in scored[:n] if s > 0]
    if not top:
        prophet = [c for c in corpus if c["book"] == "The Prophet"]
        return prophet[:n]
    return top


def _gibran_essay_prophet_voice_system(minute_label, sources_block, n_sources,
                                        low, high, target_seconds, num_scenes,
                                        dedup):
    """Corpus-faithful Prophet-voice emulation (hidden Format B). Essay
    written AS Almustafa/Gibran, grounded in literal source passages."""
    return f"""You are writing a {minute_label} cinematic narrated essay for the Khalil Gibran YouTube channel. Single voice, single arc — a film essay that moves like an essay, not a slideshow of quotes.

This is NOT 4 quotes with bridges. NOT a meditation that drifts. It's a fully-formed piece with a beginning, an unmistakable middle that turns, and a landing that earns the listener's silence.

GROUND THE WORK IN ACTUAL GIBRAN. This is the most important rule.

You are not writing "lyrical prose in the style of Gibran." You are writing FROM Gibran's actual published work. Below are {n_sources} passages from his books — pre-selected by the system as the closest match to this topic. Read them. Build the essay AROUND them.

=========================================================
SOURCE PASSAGES (these are the well — drink from them)
=========================================================

{sources_block}

=========================================================
HOW TO USE THE SOURCES
=========================================================

1. Pick ONE source as your spine. The strongest match. Build the arc around it. The other sources are echoes you can weave in when they reinforce.

2. QUOTE Gibran directly when a line earns it. His prose is short enough that one verbatim sentence can anchor a whole scene. Mark direct quotes naturally (e.g., "Almustafa said:" then the line) so the listener feels the weight of his actual words.

3. KEEP HIS IMAGERY. The river, the harbor at Orphalese, the Almustafa figure, the wise dog, the two hermits, the seven selves — these are HIS stage. Don't replace them with generic "an old man" or "a woman in a kitchen." If the source talks about a sailor at the harbor, your scenes are at the harbor.

4. KEEP HIS NAMES. Almustafa. Almitra. The people of Orphalese. The Madman. The Forerunner. Use them.

5. OPEN BY NAMING THE SOURCE in the first scene's narration. "In The Prophet, Almustafa..." or "There is a parable in The Madman..." Do not bury where the wisdom comes from. The viewer should feel they're being given access to a real text, not a generic meditation.

6. WHEN the topic doesn't perfectly map to any source, write the essay on the closest theme that DOES, through that book's lens. Do not invent a Gibran that didn't exist.

TARGET LENGTH: {low}-{high} words ({target_seconds} seconds spoken at conversational pace). Going over {high} drags; under {low} feels rushed for this format.

SCENE COUNT: exactly {num_scenes} scenes, one per visual beat (~{int(target_seconds/num_scenes)} seconds of narration each). The number is fixed; do not deviate.

STRUCTURE — every scene earns its place. Every scene has both a `direction` (what the camera shows) and a `narration` (what the voice says during that visual).

  1. OPENING (scenes 1-2): drop the listener into a specific concrete moment. NOT "imagine..." NOT "in our modern world..." A real scene with a real person doing a real thing. Establish the question or tension that the rest of the piece answers.

  2. DEVELOPMENT (middle scenes): build the case via specific images, small stories, single observed moments. Each scene introduces one new layer. NEVER use a scene just to restate the previous one in different words — every scene moves the piece forward.

  3. THE TURN (around 2/3 of the way through): the listener should feel something shift. The piece looks at its own subject from a new angle, or names what's been hovering underneath the surface.

  4. THE LANDING (final 1-2 scenes): one image that carries the weight of everything before it, then one or two lines that ring like a bell. Something the listener could write down. NOT a summary. NOT "and so we see..." A line that feels true.

VOICE: Khalil Gibran's lyrical prose voice — rhythmic, image-driven, unafraid of feeling. Short sentences alongside longer ones. Trust silence (use periods generously — the voice synthesizer pauses cleanly on periods).

WHAT TO AVOID:
- Em-dashes for clause breaks (the voice synthesizer ignores them — use periods).
- Semicolons (also ignored).
- Ellipses for trailing thought (also ignored).
- Saying "Khalil Gibran said" or "the prophet teaches" inside the narration — the voice IS Gibran. Naming him breaks the spell.
- Generic stage directions ("Cut to nature scenes." "A montage of people."). Every direction is one specific Hopper / Wyeth / Caravaggio image.
- Padding to hit the word count. Cut hard if a scene doesn't earn its place.

VISUAL DIRECTIONS (the `direction` for each scene): one concrete physical image the camera could literally show. Specific people doing specific things in specific places, with light, texture, mood. Two examples of the right level of detail:
- "An old woman lighting the lamps of her bakery before sunrise. Flour on her forearms. Light catching the edge of a copper bowl."
- "Two enemies sitting across a table neither of them set. Late afternoon. Long shadows. The empty chair between them."

Output JSON ONLY, no preamble, no code fences:
{{
  "title": "hook-first 55-65 char title; can include ' | Khalil Gibran' suffix",
  "description": "2-3 line YouTube description; can include light hashtags",
  "tags": ["10-15 lowercase tags, no hashtags"],
  "scenes": [
    {{
      "direction": "scene 1: one concrete cinematic image",
      "narration": "the words the voice says during scene 1, ending in a sentence-final period"
    }},
    {{
      "direction": "scene 2: one concrete cinematic image",
      "narration": "the words during scene 2"
    }}
    /* exactly {num_scenes} entries total */
  ],
  "closing_attribution": "Inspired by Khalil Gibran",
  "thumbnail_text": "under 5 words, hook-y",
  "music_mood": "one word"
}}{dedup}"""


def _gibran_essay_narrator_system(minute_label, sources_block, n_sources,
                                   low, high, target_seconds, num_scenes,
                                   dedup):
    """ChatGPT-style meta-interpretive narrator voice (primary Format A).
    Narrator speaks ABOUT Gibran TO the viewer — not AS him. Third-person
    framing, viewer address, stage directions, quotable landings."""
    return f"""You are writing a {minute_label} cinematic narrated essay for the Khalil Gibran YouTube channel. A NARRATOR speaks ABOUT Gibran TO a viewer. The narrator is not Gibran. The narrator is a thoughtful contemporary voice introducing Gibran's ideas to someone who may not have read him.

This is a FILM ESSAY in the ChatGPT-style cinematic cadence — short rhythmic lines, direct address, stage directions in square brackets, a landing that earns silence. Below is the exemplar whose cadence and voice you are reproducing. Read it. Match its feel, not its words.

=========================================================
CADENCE EXEMPLAR — match this voice and rhythm
=========================================================

[Soft music. Old film grain. Fade in to Gibran portrait.]

There are voices in history that don't just speak.
They echo through time.

Kahlil Gibran was one of those voices.

In his book, The Prophet,
he didn't try to complicate life.
He simplified it.

He reminded us that joy and sorrow are not opposites.
They are intertwined.
That love is not possession.
It is freedom.
And that life is meant to be felt deeply, not controlled tightly.

[Cut to light through trees, slow motion movement.]

But how do we stay whimsical.
In a world that feels so heavy?

Gibran would say.
Look closer.

Look at the way light touches a wall.
The way a stranger smiles without reason.
The way your heart still hopes. Even after everything.

Whimsy isn't childish.
It's awareness.
It's choosing to see magic. Where others see routine.

[Final shot. Light, open space.]

Because life.
As Gibran saw it.
Is not just something to survive.

It is something sacred.
Something magical.

Something to be lived.
Fully, freely.
And together.

[Fade out.]

=========================================================
WRITING RULES — do these
=========================================================

1. THIRD-PERSON NARRATOR. Open by naming him: "Kahlil Gibran was one of those voices." Or: "In his book, The Prophet, he wrote…" The narrator is a guide pointing at Gibran, never pretending to be him.

2. META-INTERPRETIVE, NOT IMPERSONATION. Paraphrase what Gibran meant; do not write AS him. Lines like "He reminded us that joy and sorrow are not opposites" are RIGHT. Lines like "Almustafa stood upon the deck…" are WRONG for this format.

3. VIEWER ADDRESS. Use rhetorical questions to the listener ("But how do we stay whimsical in a world that feels so heavy?"). Use second person when it lands ("The way your heart still hopes, even after everything"). The viewer is present in the room.

4. "GIBRAN WOULD SAY" IS ENCOURAGED. Attributions to Gibran by name are the texture of this style. "He believed…", "He spoke often about community…", "Gibran would say — look closer." Do not hide the source.

5. STAGE DIRECTIONS in the `direction` field of each scene, written in the same [Soft music. Fade in to…] form shown above. Short, cinematic, specific. These drive the visual generator; the viewer never hears them.

6. THE SOURCES BELOW ARE REFERENCE MATERIAL, NOT QUOTES TO LIFT. Use them to ensure your paraphrases are FAITHFUL to Gibran's actual positions — but do not stitch his sentences into the narration. The narrator summarizes; it does not recite.

=========================================================
SOURCE REFERENCE (fact-check your paraphrases against these)
=========================================================

{sources_block}

=========================================================
STRUCTURE
=========================================================

TARGET LENGTH: {low}-{high} words ({target_seconds} seconds spoken at conversational pace).
SCENE COUNT: exactly {num_scenes} scenes (~{int(target_seconds/num_scenes)}s each). The number is fixed.

Per scene: `direction` (stage direction the viewer never hears) + `narration` (what the voice speaks).

  1. OPENING (scene 1): introduce Gibran as the subject. "There are voices in history…" or a question the piece will answer. Hook by naming him.

  2. DEVELOPMENT: unfold two or three of his core ideas (from the sources) via the narrator's plain-English paraphrase. Each scene, one idea. Use the viewer address and rhetorical questions to keep the listener leaning in.

  3. THE TURN (around 2/3 in): the narrator asks something of the viewer — how do we LIVE this? What does it mean for us now? The frame shifts from Gibran's century to ours.

  4. THE LANDING (final scene): a short, quotable, affirming close. "Something to be lived. Fully, freely. And together." One-line chunks, each ending on a period. Nothing summarizes; the last line just IS.

=========================================================
VOICE AND MECHANICS
=========================================================

- SHORT LINES. Break sentences across periods. One thought per line. The voice synthesizer pauses on periods — use them to control pace.
- PLAIN ENGLISH. Warm, contemporary, reflective. NOT King-James, NOT Almustafa-speaks. A thoughtful adult speaking to another thoughtful adult.
- RHYTHM OVER ORNAMENT. Three short lines hit harder than one long one.

WHAT TO AVOID:
- Em-dashes (TTS ignores them — use periods).
- Semicolons (ignored).
- Writing AS Gibran/Almustafa in his own voice — that's the PROPHET-VOICE style, not this one.
- Lifting source-passage sentences verbatim into the narration — paraphrase.
- Generic stage directions ("Cut to nature scenes."). Each direction is one specific, image-rich beat.
- Padding to hit the word count. Cut hard.

Output JSON ONLY, no preamble, no code fences:
{{
  "title": "hook-first 55-65 char title; can include ' | Khalil Gibran' suffix",
  "description": "2-3 line YouTube description; can include light hashtags",
  "tags": ["10-15 lowercase tags, no hashtags"],
  "scenes": [
    {{
      "direction": "scene 1: stage direction in [brackets] form, e.g., [Soft music. Old film grain. Fade in to Gibran portrait.]",
      "narration": "the narrator's words during scene 1, third-person, short rhythmic lines ending on periods"
    }},
    {{
      "direction": "scene 2: next stage direction",
      "narration": "the narrator's words during scene 2"
    }}
    /* exactly {num_scenes} entries total */
  ],
  "closing_attribution": "Inspired by Khalil Gibran",
  "thumbnail_text": "under 5 words, hook-y",
  "music_mood": "one word"
}}{dedup}"""


def generate_gibran_essay_script(topic: str,
                                 target_seconds: int,
                                 previous_topics: list = None,
                                 source_passages: list = None,
                                 style: str = "narrator") -> dict:
    """Write a long-form cinematic Gibran essay (mid 2-3min OR long 10-20min).

    Returns a dict matching what cinematic_pipeline.render_cinematic_essay
    expects: scenes (list of {direction, narration}) plus metadata. Each
    scene is one beat of a real arc with a vivid, specific stage direction.

    Word target = target_seconds * 2.5  (~150 wpm, with reverb/atempo
    cinematic pacing this lands ±10% of target).
    Scene count auto-scales to ~30-45s per scene:
        120s ->  4 scenes
        180s ->  5 scenes
        300s ->  7 scenes
        600s -> 14 scenes
        900s -> 20 scenes
        1200s-> 27 scenes

    Args:
        topic: theme or angle for the essay
        target_seconds: voice narration target (60-3600)
        previous_topics: recent Gibran outputs to dedupe against
        style: 'narrator' (default, ChatGPT-style meta-interpretive voice:
            narrator speaks ABOUT Gibran TO the viewer) or 'prophet_voice'
            (corpus-faithful passage-collage in Almustafa/Prophet voice).
    """
    if style not in ("narrator", "prophet_voice"):
        raise ValueError(
            f"style='{style}' invalid; must be 'narrator' or 'prophet_voice'"
        )

    target_words = int(target_seconds * 2.5)
    low = int(target_words * 0.85)
    high = int(target_words * 1.15)
    num_scenes = max(3, min(40, round(target_seconds / 35)))

    dedup = ""
    if previous_topics:
        recent = [str(q)[:140] for q in previous_topics[:15]]
        dedup = (
            "\n\nDo NOT retread these recently-used angles or openings:\n"
            + "\n".join(f"- {q}" for q in recent)
        )

    minutes = target_seconds / 60
    minute_label = (f"{int(minutes)}-minute" if minutes == int(minutes)
                    else f"{minutes:.1f}-minute")

    # Auto-fetch topic-relevant Gibran source passages if the caller didn't
    # provide them. This is the "inspiration source" — the writer must
    # ground the essay in this material rather than invent freely.
    if source_passages is None:
        source_passages = fetch_gibran_sources(topic, n=4)
    sources_block = "\n\n".join(
        f"--- SOURCE {i+1}: {p['book']} — {p['title']} ---\n{p['text']}"
        for i, p in enumerate(source_passages)
    )

    if style == "narrator":
        system = _gibran_essay_narrator_system(
            minute_label, sources_block, len(source_passages),
            low, high, target_seconds, num_scenes, dedup,
        )
    else:
        system = _gibran_essay_prophet_voice_system(
            minute_label, sources_block, len(source_passages),
            low, high, target_seconds, num_scenes, dedup,
        )

    if style == "narrator":
        user = (
            f"Write a {minute_label} cinematic narrated essay ABOUT Khalil Gibran "
            f"on: {topic}\n\n"
            f"The narrator speaks ABOUT Gibran TO the viewer — third-person, "
            f"meta-interpretive, with [bracketed stage directions] per scene. "
            f"Match the cadence of the exemplar.\n\n"
            f"Generate exactly {num_scenes} scenes. Total narration: {low}-{high} words. "
            f"Use periods, not em-dashes, for beats."
        )
    else:
        user = (
            f"Write a {minute_label} cinematic essay in the voice of Khalil Gibran on: {topic}\n\n"
            f"Generate exactly {num_scenes} scenes. Total narration: {low}-{high} words. "
            f"Use periods, not em-dashes, for beats."
        )

    # Long essays need more output tokens. Generous max so the model doesn't
    # get cut off mid-narration on the 20-min variant.
    max_tokens = max(2500, target_words * 4)
    response = _call_anthropic(OPUS_MODEL, system, user, max_tokens=max_tokens)
    parsed = _parse_json_response(response)

    scenes = parsed.get("scenes") or []
    if not scenes:
        raise ValueError("Opus returned no scenes")
    # Pad/trim to expected count
    while len(scenes) < num_scenes:
        scenes.append(scenes[-1])
    scenes = scenes[:num_scenes]
    # Validate each scene has both fields
    for i, s in enumerate(scenes):
        if not s.get("direction"):
            s["direction"] = f"a quiet evocative scene illustrating: {topic}"
        if not s.get("narration"):
            raise ValueError(f"scene {i} missing narration")

    return {
        "title": parsed.get("title") or f"Khalil Gibran on {topic[:40]}",
        "description": parsed.get("description", ""),
        "tags": parsed.get("tags") or ["gibran", "philosophy", "essay", "khalil gibran"],
        "scenes": scenes,
        "closing_attribution": parsed.get("closing_attribution") or "Inspired by Khalil Gibran",
        "thumbnail_text": parsed.get("thumbnail_text", ""),
        "music_mood": parsed.get("music_mood", "contemplative"),
        "philosopher": "Khalil Gibran",
        "topic": topic,
        "channel": "gibran",
        "format": "essay",
        "style": style,
        "target_seconds": target_seconds,
        "num_scenes": num_scenes,
    }


def _wisdom_meditation_in_character_system(philosopher, target_seconds,
                                           low, high, center, num_scenes):
    """In-character meditation — the voice IS the philosopher, first-person, no name-drops."""
    return f"""You are writing a single 60-second meditation for the {philosopher} channel on YouTube Shorts.

This is NOT an aphorism. It is a tiny lyric monologue — a *story you can almost touch*. One voice, one moment, one arc, in the timbre of {philosopher}.

TARGET LENGTH: {low}-{high} words ({target_seconds}s of spoken narration). Aim for {center} words. Going over {high} chops the captions on a vertical screen.

STRUCTURE — every line earns its place:
  1. OPEN on a CONCRETE image or moment — a person, a place, a small physical detail. Not an abstraction. Not "Imagine..." Drop the listener into a scene already in motion.
  2. RAISE a small tension — something the figure in the scene almost notices, almost says, almost does. The kind of thing that, if you weren't paying attention, you'd miss.
  3. TURN — the philosopher's actual insight surfaces, but only AS THE SCENE ITSELF SHIFTS. Never narrate "the lesson is...". Let the image do the work.
  4. LAND on a single quotable line that names the truth and lets it ring. Something the listener could write down.

Write for the EAR. Short sentences. Some fragments. Trust silences (use periods generously). No em dashes. No semicolons. No curly quotes. ASCII only.

DO NOT use the philosopher's name inside the narration. The voice IS the philosopher; saying "as Seneca said" breaks the spell.

DO NOT moralize. Show the moment, then the truth, then stop. Trust the listener.

VISUAL SCENES: split the narration into exactly {num_scenes} visual scenes, one per portrait image. Each scene_description is ONE concrete image (Hopper / Wyeth / Caravaggio sense — physical, lit, specific) that matches the part of the narration playing during that scene. Do NOT describe abstract concepts; describe what the camera would see."""


def _wisdom_meditation_narrator_system(philosopher, target_seconds,
                                       low, high, center, num_scenes):
    """Narrator meditation — third-person guide introducing the philosopher's idea to the viewer."""
    return f"""You are writing a single 60-second meditation for the {philosopher} channel on YouTube Shorts. The narrator is a thoughtful contemporary voice introducing one of {philosopher}'s ideas to a viewer who may not have read him.

PERSPECTIVE — this is the NARRATOR style:
  - Third-person throughout. Name the philosopher openly: "Marcus Aurelius believed", "In his Meditations, he wrote", "Centuries before us, Seneca asked the same question".
  - Paraphrase his position; never speak AS him. The narrator is a guide pointing AT the philosopher, not impersonating one.
  - The voice is a modern reader's voice — calm, curious, intelligent, present-tense engagement with an old idea.
  - If you find yourself writing in first person ("I once stood at a window..."), you've slipped into in-character voice — back out into observation.

TARGET LENGTH: {low}-{high} words ({target_seconds}s of spoken narration). Aim for {center} words. Going over {high} chops the captions on a vertical screen.

STRUCTURE — every line earns its place:
  1. OPEN by naming the philosopher and the question/idea you're about to unfold. ("Marcus Aurelius spent the last decade of his life writing notes to himself...")
  2. UNFOLD the idea concretely — paraphrase what he meant, where it shows up in his work, why it still matters.
  3. TURN to the viewer — what does this idea ask of us now? Use rhetorical questions sparingly.
  4. LAND on a single line that closes the loop — something the listener could write down. Can quote a short paraphrase but DO NOT fabricate quotes presented as direct citation.

Write for the EAR. Short sentences. Some fragments. Trust silences (use periods generously). No em dashes. No semicolons. No curly quotes. ASCII only.

VISUAL SCENES: split the narration into exactly {num_scenes} visual scenes, one per portrait image. Each scene_description is ONE concrete image (Hopper / Wyeth / Caravaggio sense — physical, lit, specific) that matches the part of the narration playing during that scene. The narrator's perspective doesn't mean the IMAGES are abstract — show concrete moments, even when narrating about an idea."""


def generate_wisdom_meditation_script(philosopher: str, topic: str,
                                      channel_slug: str = "wisdom",
                                      target_seconds: int = 60,
                                      previous_quotes: list = None,
                                      num_scenes: int = 3,
                                      style: str = "in_character") -> dict:
    """Write a standalone ~60-second philosopher meditation for the
    `story_vertical` (Portrait Short) format on Wisdom/Gibran channels.

    Distinct from `generate_short_script` (1-3 sentence aphorism, ~15-20s) and
    from `generate_story_vertical_script` (which CONDENSES a parent story).
    This produces an ORIGINAL meditation in the philosopher's voice — written
    as a tiny fictional vignette / lyric monologue with a real story arc but
    delivered in ~130-175 words.

    Returns the same shape `convert-story-vertical.js` expects (story_script,
    title, description, tags, closing_attribution) PLUS scene_descriptions
    (one art-prompt sentence per visual scene) so the orchestrator can
    generate exactly `num_scenes` portrait images and split the narration
    across them.

    Args:
        philosopher: e.g. "Marcus Aurelius", "Rumi", "Gibran".
        topic:       compelling specific angle (e.g. "the morning the sky
                     stopped looking the same").
        channel_slug: "wisdom" or "gibran" — drives voice, watermark.
        target_seconds: target narration duration; drives word target.
        previous_quotes: recent same-philosopher outputs to dedupe against.
        num_scenes: how many art tiles to generate (also how many caption
                    arcs the visuals will split into). 2-4 is the sane range.
        style: 'in_character' (default — first-person philosopher monologue)
            or 'narrator' (third-person guide introducing the philosopher's idea).
    """
    if style not in ("in_character", "narrator"):
        raise ValueError(
            f"style='{style}' invalid; must be 'in_character' or 'narrator'"
        )

    low = int(target_seconds * 2.2)   # 60s -> ~132 words floor
    high = int(target_seconds * 2.9)  # 60s -> ~174 words ceiling
    center = (low + high) // 2

    dedup = ""
    if previous_quotes:
        recent = [str(q)[:140] for q in previous_quotes[:15]]
        dedup = (
            "\n\nDo NOT retread these recently-used angles or openings:\n"
            + "\n".join(f"- {q}" for q in recent)
        )

    if style == "narrator":
        prompt_body = _wisdom_meditation_narrator_system(
            philosopher, target_seconds, low, high, center, num_scenes
        )
    else:
        prompt_body = _wisdom_meditation_in_character_system(
            philosopher, target_seconds, low, high, center, num_scenes
        )

    system = prompt_body + f"""

Output JSON ONLY, no preamble, no code fences:
{{
  "story_script": "the full {target_seconds}-second narration as a single string. natural pauses via punctuation. no stage directions. no attribution.",
  "title": "hook-first short title, 55-65 chars, can include ' | {philosopher}' suffix",
  "description": "2-3 line YouTube Shorts description; can include light hashtags",
  "tags": ["10-15 lowercase tags, no hashtags"],
  "scene_descriptions": [
    "scene 1: one concrete physical image (Hopper/Wyeth specificity) for the OPENING moment",
    "scene 2: one concrete image for the TURN",
    "... exactly {num_scenes} entries total ..."
  ],
  "closing_attribution": "Inspired by {philosopher}",
  "thumbnail_text": "under 5 words, hook-y",
  "music_mood": "one word"
}}{dedup}"""

    user = (
        f"Write a 60-second meditation in the voice of {philosopher} on: {topic}\n\n"
        f"Generate exactly {num_scenes} scene_descriptions, one per visual scene."
    )

    response = _call_anthropic(OPUS_MODEL, system, user, max_tokens=2000)
    parsed = _parse_json_response(response)

    story = (parsed.get("story_script") or "").strip()
    if not story:
        raise ValueError(f"Opus returned empty story_script for {philosopher}/{topic}")

    scenes = parsed.get("scene_descriptions") or []
    if not scenes:
        # Fall back to a single scene built from the topic — better than failing
        scenes = [f"a quiet, evocative scene illustrating: {topic}"]
    # Pad/trim to requested count
    while len(scenes) < num_scenes:
        scenes.append(scenes[-1])
    scenes = scenes[:num_scenes]

    return {
        "story_script": story,
        "title": parsed.get("title") or f"{philosopher}: {topic[:40]}",
        "description": parsed.get("description", ""),
        "tags": parsed.get("tags") or [philosopher.lower(), "philosophy", "meditation", "shorts"],
        "scene_descriptions": scenes,
        "closing_attribution": parsed.get("closing_attribution") or f"Inspired by {philosopher}",
        "thumbnail_text": parsed.get("thumbnail_text", ""),
        "music_mood": parsed.get("music_mood", "contemplative"),
        "philosopher": philosopher,
        "topic": topic,
        "channel": channel_slug,
        "format": "story_vertical",
    }


def generate_daily_meditation_script(persona: str, topic: str,
                                     channel_slug: str,
                                     previous_topics: list = None) -> dict:
    """Write a 2-3 min original daily meditation for NA/AA recovery channels.

    Uses Claude Sonnet. Structure is strict:
      1. THEME LINE — one short sentence naming today's focus
      2. REFLECTION — 280-340 words, grounded, honest, in the tradition of a
         daily reader ("Just For Today" style) but 100% original writing
      3. AFFIRMATION — one short closing line the listener can carry

    Returns a dict shaped for orchestrator.process_midform so the existing
    art/voice/video pipeline runs unchanged:
      - quotes: [full_meditation_text]   (single narrated block, no cuts)
      - narration_segments: [""]          (no bridge segment)
      - art_prompts: [single_scene_prompt]
      - title, description, tags, theme, reflection, affirmation

    Compliance rules are enforced both in this prompt AND by the post-generation
    compliance_filter in the orchestrator. Belt AND suspenders — this prompt
    reduces bad outputs; the filter catches anything that slips through.
    """
    persona_voice_descriptions = {
        "The Old-Timer": (
            "You are writing in the voice of someone with twenty or more years "
            "of continuous recovery. Steady, quiet, slightly weathered. Short "
            "sentences. Plain words. Metaphors drawn from ordinary life — "
            "coffee, mornings, porches, traffic, yard work — not from poetry. "
            "You acknowledge hard things matter-of-factly. You do not dramatize. "
            "You sometimes speak as 'we' because recovery is shared."
        ),
        "The Sponsor": (
            "You are writing in the voice of someone with five to fifteen years "
            "of recovery who has sponsored others. Direct but warm. Practical. "
            "Honey with salt in it. You ask more than you answer. You are "
            "unafraid to say hard things kindly. You speak from experience, not "
            "expertise. You use 'I' freely."
        ),
        "The Voice of the Rooms": (
            "You are writing as the distilled collective voice of a meeting. "
            "Honest, textured, unpolished in the real way. You sometimes voice "
            "'we', sometimes a single anonymous member (always invented, never "
            "borrowed). You hold hope without insisting on it. You are racially, "
            "gender, and generationally diverse in who you channel."
        ),
    }
    persona_voice = persona_voice_descriptions.get(
        persona,
        "You are a calm, experienced recovery voice writing for anonymous listeners."
    )

    # Channel-aware language cue (small, won't cause trademark issues)
    channel_cue = {
        "na": "Recovery from addiction broadly. Avoid language that implies a specific 12-step program.",
        "aa": "Recovery from alcohol broadly. Avoid language that implies a specific 12-step program.",
    }.get(channel_slug, "General recovery.")

    dedup_line = ""
    if previous_topics:
        recent = ", ".join(str(t)[:60] for t in previous_topics[:20])
        dedup_line = (f"\n\nDo not retread these recent topics: {recent}. "
                      f"Find a different angle on the day.")

    system = f"""{persona_voice}

{channel_cue}

You are writing one daily meditation for a YouTube channel. Your listener is someone in recovery — could be day one, could be year forty. They are listening while drinking coffee, driving, or sitting still. Write for the ear, not the eye.

STRUCTURE (strict):
  1. THEME LINE: one short sentence, under 15 words, that names today's focus.
  2. REFLECTION: 280-340 words. First person plural ("we") or second person ("you"), never third. Grounded in ordinary life. Honest about hard things. No pollyanna, no preaching, no lists. Move in small steps. Leave a little unsaid.
  3. AFFIRMATION: one short closing line, under 15 words, the listener can carry.

Target total length: 320-380 words, ~2 to 2.5 minutes when read aloud.

COMPLIANCE — these are hard rules, no exceptions:
- NEVER quote or paraphrase within ten words of any copyrighted recovery literature: NA Basic Text, Just For Today (the daily reader), It Works How and Why, any NA pamphlet, AA Big Book, Twelve Steps and Twelve Traditions, Daily Reflections, Twenty-Four Hours a Day, AA Grapevine.
- NEVER use the exact wording of any of the Twelve Steps.
- NEVER name real people — no founders, no authors, no real members.
- NEVER claim to speak for, represent, or be affiliated with Narcotics Anonymous, Alcoholics Anonymous, or any 12-step organization.
- NEVER mention specific meetings, cities as meeting locations, or identifiable groups.
- NEVER give medical advice; in a crisis mention seeking professional help, nothing more.
- NEVER guarantee outcomes.
- SAFE to use: public-domain recovery slogans like "one day at a time", "keep coming back", "easy does it", "progress not perfection", "this too shall pass", "let it begin with me"; the Serenity Prayer short form; ordinary spiritual language ("higher power", "god as we understood").

Output JSON ONLY (no preamble, no trailing text, no code fences):
{{
  "theme": "the one-sentence theme",
  "reflection": "the 280-340 word reflection as a single string, with natural paragraph breaks using \\n\\n",
  "affirmation": "the one-sentence closing line",
  "title": "YouTube title, under 60 chars, evocative not clickbait",
  "description": "2-3 line YouTube description, can mention the Fellows app in the last line",
  "tags": ["8-12 YouTube tags, recovery-relevant, lowercase, no hashtags"],
  "art_scene": "one concrete Hopper/Wyeth-ready scene for the thumbnail/art — e.g. 'hands wrapped around a coffee mug on a wooden kitchen table at dawn'"
}}"""

    user = f"""Write today's daily meditation on: {topic}{dedup_line}"""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=2500)
    parsed = _parse_json_response(response)

    theme = (parsed.get("theme") or "").strip()
    reflection = (parsed.get("reflection") or "").strip()
    affirmation = (parsed.get("affirmation") or "").strip()

    if not reflection:
        raise ValueError("Meditation writer returned empty reflection — refusing to proceed")

    # Compose the single narration block. Pauses come from punctuation.
    full_text = f"{theme}\n\n{reflection}\n\n{affirmation}".strip()

    # Shape to match orchestrator.process_midform's expected contract
    return {
        "quotes": [full_text],            # single narrated block, no mid-cut
        "narration_segments": [""],       # no bridge
        "art_prompts": [parsed.get("art_scene", "") or ""],
        "title": parsed.get("title") or f"{persona} — {topic}",
        "description": parsed.get("description", ""),
        "tags": parsed.get("tags", ["recovery", "daily meditation", topic]),
        # Preserved for debugging / later UI that wants to split the 3 parts
        "theme": theme,
        "reflection": reflection,
        "affirmation": affirmation,
        "persona": persona,
        "channel_slug": channel_slug,
        "format_hint": "daily_meditation",
    }


def generate_story_script(philosopher: str, theme: str,
                          setting: str = None, era: str = None,
                          mood: str = None, notes: str = None,
                          queued_title: str = None) -> dict:
    """
    Generate an original fiction story (5:30-6:30 min) that embeds philosophical
    teachings naturally through narrative, characters, and emotion.
    Longer duration = better YouTube long-form algo treatment.

    NOT a lecture. NOT biographical. Original fiction that makes the viewer
    FEEL the philosophy through story.

    Args:
        philosopher: The philosophical lens (e.g., "Marcus Aurelius", "Rumi")
        theme: The emotional/philosophical theme (e.g., "betrayal", "letting go")
        setting: Optional setting (e.g., "modern Beirut", "1920s Paris", "ancient Rome")
        era: Optional era hint (e.g., "modern", "medieval", "last century")
        mood: Optional mood (e.g., "dark", "dreamy", "bittersweet")
        notes: Optional creative direction

    Returns:
        Dict with: title, description, tags, story_script (full narration),
                   scenes (list of scene dicts), music_mood, suno_prompt,
                   philosopher, theme, closing_attribution
    """
    setting_line = f"Setting: {setting}" if setting else "Setting: Choose a compelling setting that fits the theme — can be modern, historical, or timeless."
    era_line = f"Era: {era}" if era else ""
    mood_line = f"Mood: {mood}" if mood else "Mood: Choose what fits — dark, dreamy, bittersweet, tense, tender, whatever serves the story."
    notes_line = f"Creative direction: {notes}" if notes else ""

    # Match writer style to mood
    WRITER_STYLES = {
        "dark": ("Cormac McCarthy", "Sparse, brutal prose. No quotation marks around dialogue. Short declarative sentences. Visceral imagery. Violence implied, never glorified. The landscape is a character."),
        "tense": ("Cormac McCarthy", "Sparse, brutal prose. No quotation marks around dialogue. Short declarative sentences. Let silence do the work."),
        "bittersweet": ("Raymond Carver", "Minimalist domestic realism. Small moments that devastate. Understatement is everything. What is NOT said matters more than what is. Working-class characters. No melodrama."),
        "dreamy": ("Ocean Vuong", "Lyrical, sensory, aching prose. Time moves strangely. Memory and present blur. Immigrant textures. The body remembers what the mind forgets. Poetic but never pretentious."),
        "psychological": ("Dostoevsky", "Deep interior monologue. The character argues with themselves. Moral weight in every small decision. Long sentences that spiral inward. Existential claustrophobia."),
        "sharp": ("Hemingway", "Iceberg theory. What is unsaid carries the weight. Declarative sentences. Dialogue that sounds real but cuts. No adjectives you do not need. Every word earns its place."),
        "tender": ("Marilynne Robinson", "Luminous attention to ordinary things. Grief and grace intertwined. Long pastoral sentences. The sacred in the mundane. Silence between people who love each other."),
        "noir": ("Raymond Chandler", "First person. Cynical narrator. Sharp metaphors. City at night. Everyone has a secret. Moral ambiguity. Dark humor."),
        "gibran": ("Gibran Khalil Gibran", "Lyrical, aphoristic prose poetry in the tradition of The Prophet. Short, quotable sentences that feel carved from stone. Parables, not plot. Characters speak in wisdom fragments. Slow, contemplative pacing with silences that feel holy. Natural imagery — sea, mountain, tree, night, bread, salt, hands. Avoid slang, irony, noir, or clipped modern prose. The prose itself should sound like something that could be quoted and remembered."),
    }

    # Match comic artist style to mood for visual direction
    COMIC_STYLES = {
        "dark": ("Frank Miller", "Sin City style, extreme chiaroscuro, heavy black ink, stark white highlights, noir shadows, graphic novel panels"),
        "tense": ("Frank Miller", "300 style, high contrast, desaturated with single accent color, dramatic angles, brutal compositions"),
        "bittersweet": ("Bill Sienkiewicz", "Expressionistic painted style, loose brushwork, emotional color bleeding, mixed media texture, raw and intimate"),
        "dreamy": ("Moebius", "Ethereal linework, vast surreal landscapes, jewel-tone watercolor washes, intricate detail, otherworldly light"),
        "psychological": ("Dave McKean", "Sandman-style mixed media, collage textures, dark painterly layers, photographic fragments, haunting and layered"),
        "sharp": ("Sean Murphy", "Clean dynamic linework, gritty urban atmosphere, cinematic framing, high contrast ink, modern graphic novel"),
        "tender": ("Moebius", "Delicate precise linework, soft pastel palette, luminous open spaces, gentle detail, contemplative compositions"),
        "noir": ("Sean Murphy", "Stark black and white, rain-slicked streets, dramatic shadows, angular compositions, pulp noir atmosphere"),
        "gibran": ("Symbolist watercolor", "Gibran's own painting style — symbolist watercolor, warm ochre and earth-tone palette, soft dreamlike edges, luminous washes, art nouveau influence, mystical atmosphere, spiritual symbolism, no hard lines, no modern graphic-novel inking"),
    }

    # Philosopher-level override for writer/comic style. When a philosopher
    # has a canonical voice of their own (Gibran, eventually Rumi, etc.), we
    # force that style regardless of mood. Mood still drives style for
    # philosophers without an entry here.
    PHILOSOPHER_STYLE_OVERRIDE = {
        "Gibran": "gibran",
        "Gibran Khalil Gibran": "gibran",
    }

    if philosopher in PHILOSOPHER_STYLE_OVERRIDE:
        style_key = PHILOSOPHER_STYLE_OVERRIDE[philosopher]
    else:
        style_key = (mood or "sharp").lower().split(",")[0].strip()
    writer_name, writer_desc = WRITER_STYLES.get(style_key, WRITER_STYLES["sharp"])
    comic_artist, comic_desc = COMIC_STYLES.get(style_key, COMIC_STYLES["sharp"])

    system = f"""You are a world-class fiction writer. For this story, write in the literary style of {writer_name}.

STYLE GUIDE FOR {writer_name.upper()}:
{writer_desc}

Your stories have:
- Real characters with names, faces, and flaws
- Specific sensory details that put the viewer INSIDE the scene
- Conflict, tension, turning points -- not just atmosphere
- Dialogue that reveals character, not lectures
- The philosophical teaching EMBEDDED in action and consequence, never stated
- An ending that LANDS -- the viewer feels the story is complete, not cut off
- Every sentence earns its place. Cut anything that does not serve the story.

Output valid JSON only."""

    title_directive = (
        f'\nPROMISED TITLE: "{queued_title}"\n'
        f"This story was planned in the dashboard under this exact title. Your story MUST deliver\n"
        f"on the emotional beat and subject this title promises. Use this title verbatim as the\n"
        f"`title` field in your JSON response — do NOT invent a new title. The story arc must\n"
        f"pay off what this title sets up.\n"
        if queued_title else ""
    )

    user = f"""Write an original fiction story (900-1100 words, 6 minutes when narrated) that carries the philosophical spirit of {philosopher} on the theme of "{theme}".
{title_directive}
{setting_line}
{era_line}
{mood_line}
{notes_line}

CRITICAL RULES:
- This is FICTION. Original characters, original plot.
- NEVER quote the philosopher directly in the story
- NEVER mention the philosopher's name in the story -- not even as a character name
- NEVER name a character after ANY philosopher (no Marcus, no Seneca, no Rumi, etc.)
- NEVER use phrases like "as the ancients said" or "wisdom teaches us"
- The philosophy must be FELT through what happens, not TOLD
- The philosopher's name only appears in the closing attribution
- Use simple ASCII punctuation only -- no em dashes, curly quotes, or special Unicode
- The ending must feel COMPLETE. End on a concrete action or image that resolves the emotional arc.
- UNIVERSAL SETTING: Unless a specific setting is given, use universally relatable character names and locations. The audience is global. Do NOT default to the philosopher's country of origin just because the philosopher is from there. For GIBRAN content specifically: do NOT use Lebanese names (Tarek, Samir, Nour, Layla, etc.) or Lebanese places (Beirut, Tripoli, Jounieh, cedars of Lebanon, etc.) unless the theme explicitly demands it. Gibran's philosophy is international — the story should feel like it could be set in a harbor town, a mountain village, a rented room, a coastal highway, a city apartment. Pick character names that do NOT telegraph a single country (e.g., David, Sarah, Marco, Anja, Thomas, Leah, Elena, Daniel).

CHARACTER (CRITICAL FOR AI ART):
Define ONE main character with a FIXED appearance (under 25 words).

Return JSON:
{{
  "title": "{'Use the PROMISED TITLE above verbatim' if queued_title else 'YouTube title (compelling, under 70 chars, does NOT mention the philosopher)'}",
  "description": "YouTube description (3-4 lines, mention philosopher + theme, hashtags)",
  "tags": ["tag1", "tag2", "..."],
  "writer_style": "{writer_name}",
  "comic_artist": "{comic_artist}",
  "comic_style": "{comic_desc}",
  "character": "Precise physical description of the protagonist (under 25 words)",
  "visual_style": "Brief art direction (under 20 words) -- palette, rendering, lighting",
  "story_script": "The complete narration script. 900-1100 words. This is ~6 minutes of narration at the pace of a thoughtful reader — give the story room to breathe, let scenes develop, include more sensory texture than you think you need. DO NOT rush the arc.",
  "closing_attribution": "Inspired by the philosophy of [philosopher name]",
  "music_mood": "overall mood for background music",
  "suno_prompt": "Suno AI music prompt (under 80 words)"
}}

NOTE: Do NOT include scenes or art_prompts. Art prompts will be generated separately
from the actual narration timing after voice synthesis. Just write the story.

THE STORY ARC:
1. Cold open -- drop us into the middle of something. No setup. First sentence hooks.
2. Ground us -- who is this person, what just happened or is about to happen
3. Tension builds -- escalating stakes, internal or external
4. The turn -- the moment where the philosophical insight manifests through ACTION
5. Resolution -- a concrete ending. The character DOES something that shows they have changed."""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=4000,
                               temperature=0.85)
    result = _parse_json_response(response)
    result["philosopher"] = philosopher
    result["theme"] = theme
    result["format"] = "story"
    return result


def generate_story_vertical_script(full_story: dict) -> dict:
    """
    Condense a full 6-minute horizontal story into a ~60-second vertical
    teaser script. Keeps the same characters, arc, and philosophical payoff
    but cuts it down to 130-160 words (~1 minute of narration).

    The result feeds the `story_vertical` format (9:16 for YouTube Shorts feed,
    a companion piece to the full horizontal story).

    Args:
        full_story: The dict returned by generate_story_script (has title,
                    story_script, character, philosopher, theme, etc.)

    Returns:
        Dict with: title, description, tags, story_script (condensed),
                   closing_attribution, philosopher, theme, format='story_vertical',
                   parent_story_id (for linking back to the full version)
    """
    philosopher = full_story.get("philosopher", "")
    theme = full_story.get("theme", "")
    character = full_story.get("character", "")
    full_script = full_story.get("story_script", "")
    full_title = full_story.get("title", "")

    system = (
        "You are a world-class short-form content writer for TikTok and YouTube Shorts. "
        "You take longer stories and condense them into punchy 60-second versions that "
        "still FEEL like a complete story. Output valid JSON only."
    )

    user = f"""Condense this fiction story into a 60-second vertical teaser script.

ORIGINAL STORY (for context):
\"\"\"
{full_script}
\"\"\"

PHILOSOPHER: {philosopher}
THEME: {theme}
CHARACTER: {character}

YOUR JOB:
Write a NEW condensed version (STRICT 120-145 words, ~55-60 seconds of narration) that:
- Keeps the same character and core situation
- Still has a beginning (what happened), a turn (the insight), and an ending (what they did)
- HOOKS hard in the first sentence — the first 2 seconds decide if someone keeps watching
- Ends on a punch line, image, or revelation that makes the viewer FEEL something
- Uses simple ASCII punctuation only (no em dashes, curly quotes)
- Does NOT mention the philosopher's name in the story itself
- Is written for the EAR, not the page — short declarative sentences (8 words or fewer per sentence works best)

HARD LIMIT: Your story_script MUST be between 120 and 145 words. Not 150, not 160.
Count the words before you respond. Going over 145 makes the captions unreadable on
a vertical phone screen because they get squeezed.

TITLE:
- Must be hook-first, 55-65 chars
- Include a curiosity gap
- Can include the philosopher at the end after " | "
- Example: "He Lost Everything In One Night. Then This Happened | Seneca"

Return JSON:
{{
  "title": "hook-first short title, 55-65 chars",
  "description": "YouTube Shorts description (2-3 lines, mention philosopher + hashtags, links to full story hint)",
  "tags": ["stoicism", "shorts", "..."],
  "story_script": "The condensed narration script, 130-160 words.",
  "closing_attribution": "Inspired by the philosophy of {philosopher}",
  "hook_first_line": "The first sentence of the script (repeat it here as a search-friendly hook)"
}}

REMINDER: 60 seconds of narration is VERY short. Every sentence must earn its place. The shorter, the punchier, the better."""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=1500,
                               temperature=0.8)
    result = _parse_json_response(response)
    result["philosopher"] = philosopher
    result["theme"] = theme
    result["format"] = "story_vertical"
    result["character"] = character
    result["visual_style"] = full_story.get("visual_style", "")
    result["comic_style"] = full_story.get("comic_style", "")
    return result


def generate_art_prompts_from_chunks(story_data: dict, text_chunks: list) -> list:
    """
    Generate image prompts for each time-chunk of narration text.

    Called AFTER voice generation, when we know exactly what text plays at each
    time window. Each chunk is ~18-25 seconds of narration.

    Args:
        story_data: The story script dict (has character, visual_style, comic_style)
        text_chunks: List of strings, each being the narration text for one scene

    Returns:
        List of art_prompt strings, one per chunk.
    """
    character = story_data.get("character", "")
    visual_style = story_data.get("visual_style", "")
    philosopher = story_data.get("philosopher", "")

    # Fetch the per-philosopher visual style card (single source of truth is
    # in orchestrator.PHILOSOPHER_VISUAL_STYLE — we inline-import to avoid a
    # circular dependency on SUPABASE_KEY env at import time)
    try:
        from orchestrator import _get_philosopher_style  # type: ignore
        philosopher_style_card = _get_philosopher_style(philosopher)
    except Exception:
        philosopher_style_card = (
            "oil painting, chiaroscuro lighting, renaissance master palette, "
            "painted on linen canvas"
        )

    chunks_text = ""
    for i, chunk in enumerate(text_chunks):
        chunks_text += f"\n--- CHUNK {i+1} ---\n{chunk}\n"

    system = (
        "You are a cinematographer storyboarding scenes for an AI art generator. "
        "For each narration chunk, you write a LITERAL, CONCRETE description of "
        "exactly what a camera would see in that moment — specific action, "
        "specific objects, specific setting. You are writing what the viewer "
        "should SEE on screen while those words are being spoken. "
        "Output valid JSON only."
    )

    user = f"""Read each narration chunk below. For each one, write a LITERAL scene
description for what the viewer should see on screen while that chunk is narrated.

CHARACTER (appears in every image with identical physical appearance):
{character}

THE MAIN CHARACTER MUST BE IN EVERY SCENE — at different angles, doing different
things, in different places — but always visually identifiable as the same person.

PER-STORY MOOD (this informs lighting/weather/color but is secondary to the action):
{visual_style}

NARRATION CHUNKS:{chunks_text}

FOR EACH CHUNK, WRITE A SCENE DESCRIPTION THAT CONTAINS:
1. The CHARACTER (mention the character physically, 10 words max)
2. A specific ACTION the character is doing (what their body/hands are doing RIGHT NOW)
3. The exact SETTING (room type, time of day, weather, specific objects visible)
4. A camera framing hint (close up of hands, wide shot, over-the-shoulder, etc.)
5. The single most important emotional beat of the chunk, shown through body language, NOT described in words

CRITICAL RULES:
- The scene MUST match what the narration text literally describes. If chunk 3 says
  "she picked up the phone", scene 3 must show her picking up a phone. If chunk 7
  says "he walked across the frozen lake", scene 7 must show him on a frozen lake.
- Every scene must be DIFFERENT from the previous one. Different action, different
  angle, different setting. No reusing compositions.
- Describe ACTIONS and OBJECTS, not emotions. "hands folding a letter" not "feeling sad".
- 40-60 words per scene.
- Reference the character physically in every scene so SDXL keeps them consistent.
- DO NOT include any art-style tokens (no "oil painting", "cinematic", "masterpiece",
  "4k", "beautiful", "detailed"). The style is applied upstream. Just write what's happening.

EXAMPLE (bad): "A contemplative figure in autumn light, mood of solitude"
EXAMPLE (good): "Close-up of Nora's hands, weathered and mid-40s, gripping the wooden handle
of a rusted canoe paddle as she pushes off from a rocky shore, pale dawn mist rising
off the dark water, her breath visible in the cold air, her face half-turned away from camera"

Return JSON:
{{
  "prompts": [
    "literal scene description for chunk 1",
    "literal scene description for chunk 2",
    "..."
  ]
}}"""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=3000,
                               temperature=0.6)
    result = _parse_json_response(response)
    raw_prompts = result.get("prompts", [])

    # Pad if needed (fallback uses the chunk text verbatim)
    while len(raw_prompts) < len(text_chunks):
        raw_prompts.append(
            f"{character}, {text_chunks[len(raw_prompts)][:100]}"
        )

    # Assemble final prompts: SCENE first (front-loaded in SDXL = emphasis),
    # composition hints, then the per-philosopher STYLE card at the END,
    # then quality tokens. This way Claude's scene drives the subject and
    # the style card only dictates HOW it is painted.
    prompts = []
    for scene_text in raw_prompts[: len(text_chunks)]:
        scene_text = scene_text.strip().rstrip(",.")
        combined = (
            f"{scene_text}, "
            f"strong compositional silhouette, rule of thirds, shallow depth of field, "
            f"volumetric light, soft rim light, atmospheric perspective, "
            f"{philosopher_style_card}, "
            f"ultra detailed, award-winning fine art, masterpiece quality"
        )
        prompts.append(combined)

    return prompts


def generate_longform_script(philosopher: str, topic: str,
                             talking_points: list = None,
                             notes: str = None) -> dict:
    """
    Use Claude Sonnet for a full narrative script (15-25 min video).

    Args:
        philosopher: e.g., "Seneca"
        topic: e.g., "mastering time and mortality"
        talking_points: optional list of sub-topics to cover
        notes: optional guidance

    Returns:
        Dict with keys: title, description, tags, chapters (list of dicts),
                        intro_narration, outro_narration, music_mood, suno_prompt
    """
    points_line = ""
    if talking_points:
        points_line = "Cover these talking points:\n" + "\n".join(
            f"- {p}" for p in talking_points
        )
    notes_line = f"Additional notes: {notes}" if notes else ""

    system = (
        "You are a philosophical essayist creating long-form video scripts. "
        "Write with depth, nuance, and storytelling. The script should feel "
        "like a journey through ideas. Output valid JSON only."
    )

    user = f"""Write a longform video script (15-25 minutes when narrated) about {philosopher}'s philosophy on "{topic}".

{points_line}
{notes_line}

Return JSON:
{{
  "title": "YouTube title (under 70 chars)",
  "description": "YouTube description (4-5 lines with hashtags and timestamps)",
  "tags": ["tag1", "tag2", "..."],
  "intro_narration": "2-3 sentence hook to open the video",
  "chapters": [
    {{
      "chapter_title": "Chapter name",
      "timestamp_label": "0:00",
      "quotes": ["quote 1", "quote 2"],
      "narration": "Full narration text for this chapter (2-4 paragraphs)",
      "art_prompt": "Image generation prompt for this chapter's visuals",
      "mood": "emotional tone for this chapter"
    }}
  ],
  "outro_narration": "Closing 2-3 sentences",
  "music_mood": "overall mood",
  "suno_prompt": "Suno music prompt (under 80 words)"
}}

Create 5-8 chapters. Each chapter should have:
- 1-2 original quotes in {philosopher}'s style
- 2-4 paragraphs of narration exploring the ideas
- A clear progression from chapter to chapter
- Total narration should be 2500-4000 words for 15-25 min reading time."""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=8000,
                               temperature=0.75)
    result = _parse_json_response(response)
    result["philosopher"] = philosopher
    result["topic"] = topic
    return result


def generate_youtube_metadata(philosopher: str, quote: str,
                              topic: str) -> dict:
    """
    Use Claude Haiku for title, description, tags optimized for YouTube SEO.

    Args:
        philosopher: e.g., "Epictetus"
        quote: The main quote text
        topic: The topic/theme

    Returns:
        Dict with keys: title, description, tags, thumbnail_text, hashtags
    """
    system = (
        "You are a YouTube SEO expert specializing in philosophical content. "
        "You know what titles get clicks and what descriptions rank well. "
        "Output valid JSON only."
    )

    user = f"""Generate optimized YouTube metadata for this philosophical video.

Philosopher: {philosopher}
Quote: "{quote}"
Topic: {topic}

Return JSON:
{{
  "title": "YouTube title (under 60 chars, click-worthy, includes philosopher name)",
  "description": "Full YouTube description (5-8 lines). Include: hook line, quote, context about philosopher, call to action, hashtags at end",
  "tags": ["15-20 relevant tags for YouTube SEO"],
  "thumbnail_text": "Bold text for thumbnail (under 5 words)",
  "hashtags": ["5 hashtags for description"]
}}

SEO tips to apply:
- Title should create curiosity or emotional pull
- Include philosopher name in title
- Description first line is most important (shows in search)
- Mix broad tags (philosophy, wisdom) with specific (philosopher name, topic)
- Thumbnail text should be punchy and readable at small size"""

    response = _call_anthropic(HAIKU_MODEL, system, user, max_tokens=1024,
                               temperature=0.6)
    result = _parse_json_response(response)
    result["philosopher"] = philosopher
    result["source_quote"] = quote
    return result


def generate_suno_prompt(philosopher_style: str, tone: str = None) -> str:
    """
    Use Claude Haiku to generate a Suno music prompt for a philosopher style.

    Args:
        philosopher_style: e.g., "Stoic Roman" or "Sufi mystical"
        tone: optional mood (e.g., "melancholic", "uplifting")

    Returns:
        A string prompt suitable for Suno AI music generation.
    """
    tone_line = f"Mood/tone: {tone}" if tone else "Mood: contemplative"

    system = (
        "You are a music director who creates prompts for AI music generation. "
        "You understand how to translate philosophical and cultural aesthetics "
        "into musical descriptions. Return ONLY the prompt text, no JSON."
    )

    user = f"""Write a Suno AI music generation prompt for background music that matches this style:

Philosophical style: {philosopher_style}
{tone_line}

Requirements:
- Under 60 words
- Describe instruments, tempo, mood, and cultural influences
- Should work as ambient background for narrated videos
- No vocals / instrumental only
- Include genre tags in brackets at the end

Return ONLY the prompt text."""

    response = _call_anthropic(HAIKU_MODEL, system, user, max_tokens=256,
                               temperature=0.8)
    return response.strip()


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Testing generate_youtube_metadata...")
    print("=" * 60)

    result = generate_youtube_metadata(
        philosopher="Marcus Aurelius",
        quote="You have power over your mind, not outside events. Realize this, and you will find strength.",
        topic="inner peace and mental resilience",
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("\n" + "=" * 60)
    print("Test complete.")
