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
# Public API
# ---------------------------------------------------------------------------

def generate_weekly_plan(trending_topics: list, channels: list) -> list:
    """
    Use Claude Haiku to create 7 daily topic suggestions from trends.

    Args:
        trending_topics: List of dicts with keys like 'topic', 'source', 'score'.
        channels: List of channel names (e.g., ['Wisdom', 'Gibran Wisdom']).

    Returns:
        List of 7 dicts, one per day, each with:
        - day: str (Monday-Sunday)
        - channel: str
        - philosopher: str
        - topic: str
        - format: str (short | story | midform | longform)
        - hook: str (opening line / angle)
        - reasoning: str
    """
    system = (
        "You are a content strategist for philosophical YouTube channels. "
        "You create weekly content plans that balance trending relevance with "
        "timeless philosophical wisdom. Output valid JSON only."
    )
    user = f"""Create a 7-day content plan based on these trending topics and channels.

Trending topics:
{json.dumps(trending_topics, indent=2)}

Channels: {', '.join(channels)}

For each day (Monday through Sunday), suggest one video. Return a JSON array of 7 objects:
[
  {{
    "day": "Monday",
    "channel": "channel name",
    "philosopher": "philosopher name",
    "topic": "specific topic title",
    "format": "short | story | midform | longform",
    "hook": "compelling opening angle",
    "reasoning": "why this topic now"
  }}
]

Formats:
- short: 30-60s single quote with art (daily)
- story: 3-5 min original FICTION that embeds philosophy through narrative, not lectures (2-3x/week)
- midform: 3-5 min multi-quote direct philosophical exploration (2x/week)
- longform: 15-25 min deep essay (weekends)

Mix formats across the week. Include at least 2 stories per week.
Match philosophers to topics naturally. Ensure variety across philosophers and channels."""

    response = _call_anthropic(HAIKU_MODEL, system, user, max_tokens=2048)
    result = _parse_json_response(response)
    if isinstance(result, list):
        return result
    return result.get("plan", result.get("raw", []))


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

    system = """You are an extraordinary fiction writer who weaves philosophy into stories so naturally that the viewer never feels preached to.

Your stories have:
- Real characters with names, faces, and flaws
- Specific sensory details — what the air smells like, what the walls look like
- Conflict, tension, turning points — not just atmosphere
- Dialogue that reveals character, not lectures
- The philosophical teaching EMBEDDED in action and consequence, never stated directly
- An ending that lands emotionally — the viewer feels the wisdom, they don't hear it

You write like Hemingway meets Khalil Gibran — spare, vivid, poetic where it counts, never flowery for the sake of it.

Output valid JSON only."""

    user = f"""Write an original short fiction story (500-800 words, 3-5 minutes when narrated) that carries the philosophical spirit of {philosopher} on the theme of "{theme}".

{setting_line}
{era_line}
{mood_line}
{notes_line}

CRITICAL RULES:
- This is FICTION. Original characters, original plot.
- NEVER quote the philosopher directly in the story
- NEVER mention the philosopher's name in the story — not even as a character name
- NEVER name a character after ANY philosopher (no Marcus, no Seneca, no Rumi, etc.)
- NEVER use phrases like "as the ancients said" or "wisdom teaches us"
- The philosophy must be FELT through what happens, not TOLD
- The philosopher's name only appears in the closing attribution
- Use simple ASCII punctuation only — no em dashes, curly quotes, or special Unicode

CHARACTER CONSISTENCY (CRITICAL FOR AI ART):
You must define ONE main character with a FIXED appearance that stays the same across all scenes.
Include a "character" field in your JSON that describes the protagonist precisely:
gender, approximate age, ethnicity, hair, clothing, and one distinguishing feature.
Example: "A woman in her early 30s, East Asian, shoulder-length black hair, wearing a dark grey wool coat, small scar above her left eyebrow"

EVERY scene's art_prompt MUST include this exact character description so AI-generated images stay consistent.

ART STYLE CONSISTENCY (CRITICAL):
Include a "visual_style" field that defines the consistent look for ALL scenes.
Example: "muted cinematic realism, desaturated warm tones, soft natural lighting, painterly brushwork, film grain texture"
EVERY scene's art_prompt MUST begin with this visual_style prefix.

Return JSON:
{{
  "title": "YouTube title (compelling, under 70 chars, does NOT mention the philosopher)",
  "description": "YouTube description (3-4 lines, mention philosopher + theme, hashtags)",
  "tags": ["tag1", "tag2", "..."],
  "character": "Precise physical description of the protagonist — gender, age, ethnicity, hair, clothing, distinguishing feature",
  "visual_style": "Consistent art direction for all scenes — color palette, rendering style, lighting, texture",
  "story_script": "The complete narration script — the full story as it will be read aloud. 500-800 words.",
  "scenes": [
    {{
      "scene_number": 1,
      "narration": "The portion of narration for this scene",
      "art_prompt": "[visual_style], [character description], [scene-specific details — location, action, composition, lighting]",
      "mood": "emotional tone of this scene"
    }}
  ],
  "closing_attribution": "A single line like: Inspired by the philosophy of [philosopher name]",
  "music_mood": "overall mood for background music",
  "suno_prompt": "Suno AI music prompt (under 80 words, describe instruments, tempo, feel)"
}}

Break the story into 4-6 scenes. Each scene is a visual beat — a new location, a shift in tension, a revelation.

THE STORY ARC:
1. Ground us — where are we, who is this person, what do they want
2. Tension — something goes wrong, a choice must be made, pressure builds
3. The turn — the moment where the philosophical insight manifests through ACTION
4. Resolution — not a happy ending necessarily, but a true one"""

    response = _call_anthropic(SONNET_MODEL, system, user, max_tokens=4000,
                               temperature=0.85)
    result = _parse_json_response(response)
    result["philosopher"] = philosopher
    result["theme"] = theme
    result["format"] = "story"
    return result


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
