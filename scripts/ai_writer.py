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


def _call_ollama(prompt: str, model: str = "qwen3:32b",
                 temperature: float = 0.8) -> str:
    """Call local Ollama and return the generated text."""
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
    except requests.exceptions.ConnectionError:
        print("[ai_writer] WARNING: Ollama not running. Falling back to Haiku.")
        return _call_anthropic(
            HAIKU_MODEL,
            "You are a philosopher and poet. Write in the authentic style requested.",
            prompt,
            max_tokens=512,
        )


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

    # Merge provided channel_map with defaults
    cmap = dict(_DEFAULT_CHANNEL_MAP)
    if channel_map:
        cmap.update(channel_map)

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

        # Resolve channel_id from slug or philosopher
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
            "quote_text": "Pending generation",
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

def generate_weekly_plan(trending_topics: list, channels: list) -> list:
    """
    Use Claude to create a full weekly content plan with the correct quotas.

    Weekly quota per channel:
      - 7 shorts (1 per day)
      - 3 stories (Mon/Wed/Fri)
      - 2 midform (Tue/Thu)
      - 1 longform (Saturday or Sunday)
      = 13 items per channel

    Wisdom channel philosophers: Marcus Aurelius, Seneca, Epictetus, Rumi,
      Lao Tzu, Nietzsche, Emerson, Thoreau, Dostoevsky, Wilde, etc.
    Gibran channel: Gibran Khalil Gibran only.

    Returns:
        List of dicts with: day, channel, philosopher, topic, format, hook
    """
    system = (
        "You are a content strategist for philosophical YouTube channels. "
        "You create weekly content plans that balance trending relevance with "
        "timeless philosophical wisdom. Output valid JSON only."
    )
    user = f"""Create a FULL weekly content plan for TWO channels based on trending topics.

Trending topics:
{json.dumps(trending_topics, indent=2)}

CHANNEL 1: "Deep Echoes of Wisdom" (slug: wisdom)
  Philosophers: Marcus Aurelius, Seneca, Epictetus, Rumi, Lao Tzu, Nietzsche, Emerson, Thoreau, Dostoevsky, Wilde, Musashi, Confucius

CHANNEL 2: "Gibran Khalil Gibran" (slug: gibran)
  Philosopher: Gibran Khalil Gibran ONLY

WEEKLY QUOTA PER CHANNEL:
  - 7 shorts (one per day, Mon-Sun)
  - 3 stories (Mon, Wed, Fri)
  - 2 midform (Tue, Thu)
  - 1 longform (Sat or Sun)
  Total: 13 items per channel, 26 items total

Return a JSON array of 26 objects:
[
  {{
    "day": "Monday",
    "channel": "wisdom" or "gibran",
    "philosopher": "philosopher name",
    "topic": "specific compelling topic title",
    "format": "short | story | midform | longform",
    "hook": "compelling opening angle (1 sentence)"
  }}
]

RULES:
- Every day MUST have 1 short for wisdom AND 1 short for gibran
- Stories on Mon/Wed/Fri for each channel
- Midform on Tue/Thu for each channel
- Longform on Sat or Sun for each channel
- Vary philosophers across the week (no same philosopher 2 days in a row for wisdom)
- Topics should be inspired by trending data but framed through the philosopher's lens
- Each topic title should be compelling and specific, not generic
- Gibran topics should draw from themes in The Prophet, The Broken Wings, Sand and Foam"""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=4000)
    result = _parse_json_response(response)
    if isinstance(result, list):
        plan = result
    else:
        plan = result.get("plan", result.get("raw", []))

    # Push plan to Supabase if credentials are available
    if isinstance(plan, list) and plan and SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
            push_weekly_plan_to_supabase(plan)
        except Exception as e:
            print(f"[ai_writer] WARNING: Failed to push weekly plan to Supabase: {e}")

    return plan


def generate_short_script(philosopher: str, topic: str,
                          tone: str = None, notes: str = None) -> dict:
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

    ollama_prompt = f"""Write a single original philosophical quote in the authentic style and voice of {philosopher}, on the topic of "{topic}".

{tone_line}
{notes_line}

Requirements:
- Must sound authentically like {philosopher}
- 1-3 sentences, poetic and quotable
- Deep insight, not surface-level advice
- Do NOT include attribution or quotation marks

Return ONLY the quote text, nothing else."""

    quote = _call_ollama(ollama_prompt).strip().strip('"').strip("'")

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
                            notes: str = None) -> dict:
    """
    Use Claude Sonnet to write a connected multi-quote script for midform video.

    Args:
        philosopher: e.g., "Rumi"
        topic: e.g., "the pain of growth"
        num_quotes: Number of quote sections (3-5)
        tone: optional tone
        notes: optional guidance

    Returns:
        Dict with keys: quotes (list), narration_segments (list),
                        transitions (list), title, description, tags,
                        music_mood, suno_prompt, art_prompts (list)
    """
    tone_line = f"Tone: {tone}" if tone else "Tone: contemplative and layered"
    notes_line = f"Additional context: {notes}" if notes else ""

    system = (
        "You are a philosophical writer creating connected video scripts. "
        "Each quote should build on the previous, creating a narrative arc. "
        "Write in the authentic voice of the philosopher. Output valid JSON only."
    )

    user = f"""Write a midform video script with {num_quotes} connected quotes in the style of {philosopher} on "{topic}".

{tone_line}
{notes_line}

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


def generate_story_script(philosopher: str, theme: str,
                          setting: str = None, era: str = None,
                          mood: str = None, notes: str = None) -> dict:
    """
    Generate an original fiction story (3-5 min) that embeds philosophical
    teachings naturally through narrative, characters, and emotion.

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
    }

    mood_key = (mood or "sharp").lower().split(",")[0].strip()
    writer_name, writer_desc = WRITER_STYLES.get(mood_key, WRITER_STYLES["sharp"])
    comic_artist, comic_desc = COMIC_STYLES.get(mood_key, COMIC_STYLES["sharp"])

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

    user = f"""Write an original short fiction story (500-700 words, 3 minutes when narrated) that carries the philosophical spirit of {philosopher} on the theme of "{theme}".

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

CHARACTER (CRITICAL FOR AI ART):
Define ONE main character with a FIXED appearance (under 25 words).

Return JSON:
{{
  "title": "YouTube title (compelling, under 70 chars, does NOT mention the philosopher)",
  "description": "YouTube description (3-4 lines, mention philosopher + theme, hashtags)",
  "tags": ["tag1", "tag2", "..."],
  "writer_style": "{writer_name}",
  "comic_artist": "{comic_artist}",
  "comic_style": "{comic_desc}",
  "character": "Precise physical description of the protagonist (under 25 words)",
  "visual_style": "Brief art direction (under 20 words) -- palette, rendering, lighting",
  "story_script": "The complete narration script. 500-700 words.",
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
    comic_artist = story_data.get("comic_artist", "")
    comic_style = story_data.get("comic_style", "")

    chunks_text = ""
    for i, chunk in enumerate(text_chunks):
        chunks_text += f"\n--- CHUNK {i+1} ---\n{chunk}\n"

    system = (
        "You are a visual director creating image prompts for an AI art generator (SDXL). "
        "Each prompt must describe a UNIQUE scene that matches the narration text. "
        "Output valid JSON only."
    )

    user = f"""Generate one image prompt for each narration chunk below. Each image will be shown
while that chunk is being narrated aloud, so the image MUST depict what is being described.

CHARACTER (same person in every image): {character}
VISUAL STYLE: {visual_style}
COMIC ARTIST INFLUENCE: {comic_artist} -- {comic_style}

NARRATION CHUNKS:{chunks_text}

RULES:
- Each prompt starts with the SCENE ACTION/SETTING (what is happening, where, objects visible)
- Then brief character reference (10 words max)
- Then brief style note including "{comic_artist} style" (10 words max)
- Total prompt under 50 words
- Every image must show a DIFFERENT composition, angle, or setting
- Match the emotion and content of the narration text exactly
- If the text describes hands folding paper, show hands folding paper
- If the text describes walking across a parking lot, show that
- Do NOT repeat backgrounds or compositions

Return JSON:
{{
  "prompts": [
    "prompt for chunk 1",
    "prompt for chunk 2",
    "..."
  ]
}}"""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=2000,
                               temperature=0.7)
    result = _parse_json_response(response)
    prompts = result.get("prompts", [])

    # Pad if needed
    while len(prompts) < len(text_chunks):
        prompts.append(f"{text_chunks[len(prompts)][:30]}, {character}, {comic_artist} style {visual_style}")

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
