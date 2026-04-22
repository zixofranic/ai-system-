"""
Content Generation Orchestrator for Wisdom Pipeline
====================================================
Main pipeline script that connects all individual tools into an automated
video production workflow.

Reads queued content from Supabase, then for each item:
  1. Generate quote via Ollama (fallback: Claude Haiku)
  2. Generate metadata via Claude Haiku (ai_writer.py)
  3. Generate art via ComfyUI API
  4. Generate voice via Chatterbox TTS API
  5. Pick background music from library
  6. Assemble video (assemble_video.py)
  7. Upload to Google Drive
  8. Update Supabase with Drive URL + status

VRAM management: art generation is batched first, then voice generation,
so ComfyUI and Chatterbox never compete for GPU memory.

Usage:
    python orchestrator.py                 # process full queue
    python orchestrator.py --limit 3       # process at most 3 items
    python orchestrator.py --dry-run       # preview queue without processing
"""

import sys
import os
import json
import time
import uuid
import random
import argparse
import traceback
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
import calendar

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv("C:/AI/.env")

# Our pipeline modules
sys.path.insert(0, "C:/AI/system/scripts")
from ai_writer import generate_short_script, generate_youtube_metadata, sanitize_quote
from render_remotion import render_remotion_video

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

OLLAMA_URL = "http://localhost:11434"
COMFYUI_URL = "http://localhost:8188"
CHATTERBOX_URL = "http://localhost:8004"  # kept for future Chatterbox option
MUSIC_ROOT = Path("C:/AI/system/music")
WORK_DIR = Path("C:/AI/system/pipeline_work")

# ---------------------------------------------------------------------------
# Philosopher -> Style mappings
# ---------------------------------------------------------------------------
PERSONA_TO_LORA = {
    "Marcus Aurelius": "stoic_classical_v1",
    "Seneca": "stoic_classical_v1",
    "Epictetus": "stoic_classical_v1",
    "Gibran Khalil Gibran": "gibran_style_v1",
    "Gibran": "gibran_style_v1",
    "Rumi": "persian_miniature_v1",
    "Lao Tzu": "eastern_ink_v1",
    "Sun Tzu": "eastern_ink_v1",
    "Confucius": "eastern_ink_v1",
    "Musashi": "eastern_ink_v1",
    "Emerson": "romantic_landscape_v1",
    "Thoreau": "romantic_landscape_v1",
    "Nietzsche": "dark_expressionist_v1",
    "Dostoevsky": "dark_expressionist_v1",
    "Wilde": "aesthetic_gilded_v1",
    "Franklin": "aesthetic_gilded_v1",
    "Da Vinci": "renaissance_genius_v1",
    "Tesla": "renaissance_genius_v1",
    "Vivekananda": "vedic_sacred_v1",
}

PERSONA_TO_MUSIC_STYLE = {
    "Marcus Aurelius": "stoic_classical",
    "Seneca": "stoic_classical",
    "Epictetus": "stoic_classical",
    "Gibran Khalil Gibran": "gibran",
    "Gibran": "gibran",
    "Rumi": "persian_miniature",
    "Lao Tzu": "eastern_ink",
    "Sun Tzu": "eastern_ink",
    "Confucius": "eastern_ink",
    "Musashi": "eastern_ink",
    "Emerson": "romantic_landscape",
    "Thoreau": "romantic_landscape",
    "Nietzsche": "dark_expressionist",
    "Dostoevsky": "dark_expressionist",
    "Wilde": "aesthetic_gilded",
    "Franklin": "aesthetic_gilded",
    "Da Vinci": "renaissance_genius",
    "Tesla": "renaissance_genius",
    "Vivekananda": "vedic_sacred",
}

# Explicit mapping from philosopher display name to Ollama model name.
# Use this whenever the lowercase-with-underscores form doesn't match.
PERSONA_TO_OLLAMA_MODEL = {
    "Marcus Aurelius": "marcus_aurelius",
    "Seneca": "seneca",
    "Epictetus": "epictetus",
    "Gibran Khalil Gibran": "gibran",
    "Gibran": "gibran",
    "Rumi": "rumi",
    "Lao Tzu": "lao_tzu",
    "Sun Tzu": "sun_tzu",
    "Confucius": "confucius",
    "Musashi": "musashi",
    "Emerson": "emerson",
    "Thoreau": "thoreau",
    "Nietzsche": "nietzsche",
    "Dostoevsky": "dostoevsky",
    "Wilde": "wilde",
    "Franklin": "franklin",
    "Da Vinci": "da_vinci",
    "Tesla": "tesla",
    "Vivekananda": "vivekananda",
    # NA archetypes — custom Ollama models with persona + compliance baked into SYSTEM prompt.
    # Modelfiles at C:\AI\na\ollama\Modelfile.{the_old_timer,the_sponsor,the_voice_of_the_rooms}
    "The Old-Timer": "the_old_timer",
    "The Sponsor": "the_sponsor",
    "The Voice of the Rooms": "the_voice_of_the_rooms",
}


PERSONA_TO_VOICE_SETTINGS = {
    "Marcus Aurelius": {"exaggeration": 0.3},
    "Seneca": {"exaggeration": 0.5},
    "Epictetus": {"exaggeration": 0.6},
    "Gibran Khalil Gibran": {"exaggeration": 0.4},
    "Rumi": {"exaggeration": 0.5},
    "Lao Tzu": {"exaggeration": 0.3},
    "Sun Tzu": {"exaggeration": 0.4},
    "Confucius": {"exaggeration": 0.3},
    "Musashi": {"exaggeration": 0.4},
    "Emerson": {"exaggeration": 0.4},
    "Thoreau": {"exaggeration": 0.4},
    "Nietzsche": {"exaggeration": 0.6},
    "Dostoevsky": {"exaggeration": 0.5},
    "Wilde": {"exaggeration": 0.5},
    "Franklin": {"exaggeration": 0.4},
    "Da Vinci": {"exaggeration": 0.4},
    "Tesla": {"exaggeration": 0.5},
    "Vivekananda": {"exaggeration": 0.5},
    # NA archetypes
    "The Old-Timer": {"exaggeration": 0.3},        # calm, steady narrator
    "The Sponsor": {"exaggeration": 0.5},          # warm, direct
    "The Voice of the Rooms": {"exaggeration": 0.4},  # honest, gentle
}

# ---------------------------------------------------------------------------
# Voice ID routing — philosopher-specific wins, channel default otherwise
# ---------------------------------------------------------------------------
# ElevenLabs voice IDs per archetype (NA)
PERSONA_TO_VOICE_ID = {
    "The Old-Timer": "EkK5I93UQWFDigLMpZcX",
    "The Sponsor": "VAnZB441uRGQ8uoZunqz",
    "The Voice of the Rooms": "h2sm0NbeIZXHBzJOMYcQ",
}

# NOTE: persona-level Chatterbox refs were considered (PERSONA_TO_CHATTERBOX_REF)
# but never populated — Chatterbox is currently CHANNEL-scoped, not
# persona-scoped (see CHANNEL_CHATTERBOX_REF below). Add a persona-level
# map only when an archetype actually needs a different reference clip
# from the channel default.

# --- Per-channel voice configuration ---
#
# One nested map keyed by channel slug. Replaces the four parallel dicts
# (CHANNEL_TTS_PROVIDER + CHANNEL_CHATTERBOX_REF/ATEMPO/REVERB) that
# could fall out of sync — adding a channel with `chatterbox.ref` set
# but `chatterbox.atempo` missing would have rendered without time-
# stretch and quietly produced a too-fast voice. Now it's structurally
# impossible to half-configure a channel.
#
# Resolver: `_resolve_voice_config(channel_slug)` returns the merged
# config (env-overridden provider on top of the static defaults below).
#
# Status as of 2026-04-19:
#   - Wisdom / NA / AA → ElevenLabs only (Chatterbox declined for these
#     channels per feedback_chatterbox_declined memory)
#   - Gibran → ElevenLabs by default, flipped to Chatterbox via
#     switch_gibran_tts.py (writes GIBRAN_TTS_PROVIDER env var to .env)
CHANNEL_VOICE = {
    "wisdom": {
        "provider_default": "elevenlabs",
    },
    "gibran": {
        "provider_default": "chatterbox",  # 2026-04-21: Gibran moved off ElevenLabs entirely
        # Chatterbox profile — now the default. Env var GIBRAN_TTS_PROVIDER
        # was already set to chatterbox; this just makes the code agree.
        # ref:    Built 2026-04-19 from 12.7 min of clean EL Gibran output;
        #         A_v2_slow recipe (long_ref) won the A/B/C blind test.
        # atempo: CB native cadence runs ~20% faster than EL — this
        #         time-stretches without pitch artifacts. 0.80 is the
        #         limit before single-pass artifacts become audible.
        # reverb: ffmpeg `aecho=in_gain:out_gain:delays(ms):decays`,
        #         multi-tap via `|`. Two short taps with low decay add
        #         a tiny room-sound to CB's dry zero-shot output.
        "chatterbox": {
            "ref":    "gibran_long_ref.wav",
            "atempo": 0.80,
            "reverb": "aecho=0.85:0.85:40|60:0.18|0.10",
            # Soft landing — terminal falling intonation on the last 1.5s.
            # Cinematic essays especially benefit since they end on a
            # quotable line that should "settle" rather than just stop.
            "tail_fade": {"duration": 1.5, "pitch_factor": 0.94, "volume_target": 0.7},
        },
    },
    # NA / AA share the same three archetype voices (per CLAUDE.md). The
    # CB profile picks a persona-specific reference; absence falls back
    # to the channel default ref. Re-evaluation 2026-04-20: previous
    # 2026-04-16 attempt declined CB because pacing felt rushed and the
    # pause cues weren't honored. Since then we shipped the
    # _chatterbox_pause_hints (em-dash → period rewrite) + atempo +
    # reverb post-process, which should close most of the gap. This
    # re-test runs through the same 3 reference clips that 2026-04-16
    # used so the comparison is apples-to-apples.
    #
    # NO REVERB on recovery channels — the rooms-of-recovery aesthetic is
    # close-mic intimate, not cinematic. Reverb would push it toward
    # podcast-y / fake. Tune later if needed.
    "na": {
        "provider_default": "elevenlabs",
        "chatterbox": {
            "atempo": 0.85,           # less aggressive than Gibran (0.80)
            "reverb": None,
            # Smaller pitch drop (0.96 ~ -0.7 semitones) than Gibran — the
            # recovery voice is already conversational; too much drop reads
            # as melodramatic. Volume tapers slightly to 0.75.
            "tail_fade": {"duration": 1.2, "pitch_factor": 0.96, "volume_target": 0.75},
            "ref": "na_old_timer_5min.wav",   # fallback when no persona match
            "persona_refs": {
                "The Old-Timer":          "na_old_timer_5min.wav",
                "The Sponsor":            "na_sponsor_5min.wav",
                "The Voice of the Rooms": "na_rooms_5min.wav",
            },
        },
    },
    "aa": {
        "provider_default": "elevenlabs",
        "chatterbox": {
            "atempo": 0.85,
            "reverb": None,
            "tail_fade": {"duration": 1.2, "pitch_factor": 0.96, "volume_target": 0.75},
            "ref": "na_old_timer_5min.wav",
            "persona_refs": {  # AA shares NA's voice clones
                "The Old-Timer":          "na_old_timer_5min.wav",
                "The Sponsor":            "na_sponsor_5min.wav",
                "The Voice of the Rooms": "na_rooms_5min.wav",
            },
        },
    },
}


def _resolve_voice_config(channel_slug: str, philosopher: str = None) -> dict:
    """Return the resolved voice config for a channel + (optional) persona.

    Returns dict with keys: provider, chatterbox (or None).
    `provider` is post-env-override; `chatterbox` is None when CB isn't
    qualified for this channel even if the env asks for it.

    For NA/AA, the chatterbox profile carries `persona_refs` keyed by
    archetype (The Old-Timer / The Sponsor / The Voice of the Rooms).
    The resolver picks the right ref for `philosopher` and writes it
    back into the returned cb dict's `ref` key. Falls back to the
    channel-level `ref` when no persona match exists.
    """
    cfg = CHANNEL_VOICE.get(channel_slug, {"provider_default": "elevenlabs"})
    provider = _resolve_tts_provider(channel_slug)
    cb = cfg.get("chatterbox") if provider == "chatterbox" else None
    if cb and philosopher:
        # Copy so we don't mutate the static config dict
        cb = dict(cb)
        persona_refs = cb.get("persona_refs") or {}
        if philosopher in persona_refs:
            cb["ref"] = persona_refs[philosopher]
        # else: keep cb["ref"] as the channel default
    return {"provider": provider, "chatterbox": cb}


def _resolve_tts_provider(channel_slug: str) -> str:
    """Resolve the active TTS provider for a channel.

    Order of precedence:
      1. Channel-specific env var ({CHANNEL}_TTS_PROVIDER=chatterbox)
         — set/cleared via switch_gibran_tts.py and analogous tools
      2. CHANNEL_VOICE[channel].provider_default
      3. "elevenlabs" hard fallback (never silently break a render)

    Env values are normalized: "cb" / "chatterbox" / "elevenlabs" / "el".

    A previous global PIPELINE_TTS_PROVIDER override was removed because
    a single env var that flips ALL channels at once is footgun-shaped —
    we always want per-channel control.
    """
    def _norm(v):
        if not v:
            return None
        v = v.strip().lower()
        if v in ("cb", "chatterbox"):
            return "chatterbox"
        if v in ("el", "elevenlabs", "11labs"):
            return "elevenlabs"
        return v

    n = _norm(os.environ.get(f"{channel_slug.upper()}_TTS_PROVIDER"))
    if n in ("chatterbox", "elevenlabs"):
        return n
    return (CHANNEL_VOICE.get(channel_slug, {}).get("provider_default")
            or "elevenlabs")


def _chatterbox_pause_hints(text: str) -> str:
    """Pre-process text for Chatterbox to maximize honored pauses.

    Empirical findings (2026-04-19, anchor-sentence test, see
    cb_punctuation_test.py):
      - PERIOD is the only punctuation CB honors strongly (~840ms gap)
      - Comma / colon get a modest pause (~440ms)
      - Em-dash, ellipsis, semicolon, question, exclamation all
        produce gaps within ±100ms of baseline (effectively ignored)
      - Extra whitespace around punctuation does NOTHING — `. ` vs
        `.  ` vs `.   ` all produced identical 840ms gaps
      - Newline behaves like a period

    So the right preprocessing is: convert weak/ignored marks into
    PERIODS where the writer intended a real beat, and leave commas /
    colons alone (they're already getting their share). Don't bother
    padding whitespace.
    """
    # Em-dashes — Gibran loves them, CB ignores them. Convert to period
    # for a real ~840ms beat. Both spaced (` — `) and tight (`—`).
    text = text.replace(" — ", ". ").replace("—", ". ")
    # Semicolons — also ignored by CB. Treat the same way.
    text = text.replace("; ", ". ")
    # Ellipsis — surprised us; CB barely pauses on `...`. If the writer
    # wanted a trailing beat, give them a real period.
    text = text.replace("... ", ". ").replace("…", ".")
    return text

# Channel music pools — multi-style pool drawn from when picking a track.
# Falls through to CHANNEL_DEFAULT_MUSIC_STYLE if the channel has no pool entry.
CHANNEL_MUSIC_POOL = {
    "na": ["stoic_classical", "gibran"],   # soft mixed pool per user direction 2026-04-16
    "aa": ["stoic_classical", "gibran"],   # same soft pool — AA tone maps to NA aesthetic
}

EQUALIZER_COLORS = {
    "stoic_classical": "#8B7355",
    "gibran": "#D4AF37",
    "persian_miniature": "#C19A6B",
    "eastern_ink": "#708090",
    "romantic_landscape": "#DAA520",
    "dark_expressionist": "#8B0000",
    "aesthetic_gilded": "#FFD700",
    "renaissance_genius": "#CD853F",
    "vedic_sacred": "#FF8C00",
    # NA uses the warm-gold sun color from the Fellows brand family
    "recovery_soft": "#E8B868",
}


# --- Gibran-only long-form format gate ----------------------------------
#
# Gibran non-short content (anything where format != 'short') must have
# both `gibran_long_form_style` and `gibran_target_seconds` set on the
# content row before generation. The columns are added by the migration
# at scripts/migrations/2026_04_19_gibran_long_form_format_fields.sql.
# Set via the dashboard modal OR scripts/set_gibran_format.py.
#
# Existing rows already rendered (status published/ready) never re-enter
# the queue, so they're naturally grandfathered. New planned rows have
# NULLs and get refused — orchestrator marks them rejected with a clear
# rejection_reason so they show up in the dashboard with the right cue.

GIBRAN_VALID_STYLES = ("essay", "anthology")
GIBRAN_VALID_SECONDS_RANGE = (60, 3600)


def _resolve_gibran_choice(content: dict) -> tuple:
    """Validate the gibran_long_form_style + gibran_target_seconds fields
    on a content row. Returns (style, seconds, error_or_None).

    Tolerates the columns being absent entirely (migration not yet applied)
    by treating them as None and surfacing a single "migration missing"
    error instead of crashing.
    """
    style = content.get("gibran_long_form_style")
    seconds = content.get("gibran_target_seconds")
    if style is None and seconds is None:
        return None, None, (
            "Gibran non-short row missing gibran_long_form_style + "
            "gibran_target_seconds — set via dashboard modal or "
            "scripts/set_gibran_format.py"
        )
    if style not in GIBRAN_VALID_STYLES:
        return style, seconds, (
            f"gibran_long_form_style='{style}' invalid; "
            f"must be one of {GIBRAN_VALID_STYLES}"
        )
    lo, hi = GIBRAN_VALID_SECONDS_RANGE
    if seconds is None or not isinstance(seconds, int) or seconds < lo or seconds > hi:
        return style, seconds, (
            f"gibran_target_seconds={seconds!r} invalid; "
            f"must be int in [{lo}, {hi}]"
        )
    return style, seconds, None


def _apply_gibran_format_gate(queued: list) -> list:
    """Filter the queue: any Gibran non-short row missing the new format
    fields gets rejected immediately with a clear rejection_reason.
    Returns the queue minus those rejected rows.

    Rationale: forcing the choice at queue-pickup time (rather than at
    queue-creation time) means the dashboard can rely on the orchestrator
    as the source-of-truth gate even if a row was queued via the legacy
    path or directly via SQL.
    """
    surviving = []
    for content in queued:
        slug = (content.get("channels") or {}).get("slug")
        fmt = content.get("format", "short")
        if slug != "gibran" or fmt == "short":
            surviving.append(content)
            continue
        style, seconds, err = _resolve_gibran_choice(content)
        if err:
            cid = content["id"]
            print(f"  REJECTED gibran-{fmt} {cid[:8]}: {err}")
            # generation_log.step has a CHECK constraint — "publish" with
            # step_order=0 is the existing convention for queue-rejection
            # entries (see midform/story_vertical reject paths).
            log_step(cid, "publish", 0, "failed", err)
            try:
                update_supabase(cid, {
                    "status": "rejected",
                    "rejection_reason": err,
                })
            except Exception as e:
                print(f"    [WARN] couldn't mark row rejected: {e}")
            continue
        surviving.append(content)
    return surviving


# Default ComfyUI SDXL + LoRA workflow template
# Placeholder values are filled at runtime via _build_comfyui_workflow()
#
# Quality tuning (2026-04-09):
#   - sampler dpmpp_2m_sde_gpu + scheduler karras → richer detail on skin/fabric
#   - cfg 7.0 → 6.0  less "burned" colors, more naturalistic
#   - steps 30 → 40  sharper fine detail (+33% GPU time is fine on 5060 Ti)
#   - stronger negative to kill the plastic/waxy "AI look"
_COMFYUI_WORKFLOW_TEMPLATE = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 40,
            "cfg": 6.0,
            "sampler_name": "dpmpp_2m_sde_gpu",
            "scheduler": "karras",
            "denoise": 1.0,
            "model": ["10", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "width": 832,
            "height": 1216,
            "batch_size": 1,
        },
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "",
            "clip": ["10", 1],
        },
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": (
                "text, watermark, logo, blurry, low quality, deformed, ugly, "
                "plastic skin, waxy, doll face, dead eyes, airbrushed, "
                "oversaturated, cgi render, 3d render, deviantart, trending on artstation, "
                "disfigured, extra limbs, bad anatomy, fused fingers, mutated hands, "
                "lowres, jpeg artifacts, grainy noise, over-smoothed"
            ),
            "clip": ["10", 1],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["3", 0],
            "vae": ["10", 2],
        },
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "wisdom_gen",
            "images": ["8", 0],
        },
    },
    "10": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": "sd_xl_base_1.0.safetensors",
        },
    },
    "11": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "",
            "strength_model": 0.85,
            "strength_clip": 0.85,
            "model": ["10", 0],
            "clip": ["10", 1],
        },
    },
}


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
def _supabase_headers():
    """Standard headers for Supabase REST API calls."""
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def fetch_queued_content():
    """Get all content rows with status='queued' from Supabase, oldest first."""
    url = (
        f"{SUPABASE_URL}/rest/v1/content"
        f"?status=eq.queued&deleted_at=is.null"
        f"&order=created_at.asc"
        f"&select=*,channels:channel_id(id,name,slug,google_drive_folder_id,settings)"
    )
    resp = requests.get(url, headers=_supabase_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _ensure_channel_data(content: dict) -> dict:
    """
    Guarantee that content['channels'] is populated. If missing (e.g., direct call
    to process_short without the Supabase join), fetch it from channel_id.
    Raises loudly if neither is available — never silently defaults to 'wisdom'.
    """
    if content.get("channels"):
        return content
    channel_id = content.get("channel_id")
    if not channel_id:
        raise ValueError(
            f"Content {content.get('id', '?')} has no channel_id and no channels join. "
            "Cannot determine target channel. Refusing to default to 'wisdom'."
        )
    url = (
        f"{SUPABASE_URL}/rest/v1/channels"
        f"?id=eq.{channel_id}"
        f"&select=id,name,slug,google_drive_folder_id,settings"
    )
    resp = requests.get(url, headers=_supabase_headers(), timeout=15)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError(
            f"Channel {channel_id} not found in Supabase for content {content.get('id', '?')}. "
            "Refusing to default to 'wisdom'."
        )
    content["channels"] = rows[0]
    return content


# ---------------------------------------------------------------------------
# Compliance screen — for recovery channels (NA, AA) only.
#
# Screens generated script fields against a corpus of forbidden phrases
# (NA Basic Text, Just For Today, Big Book, 12&12, etc.) BEFORE any TTS/art
# spend. For channels not in the corpus (wisdom, gibran) this is a no-op.
# ---------------------------------------------------------------------------
def _compliance_screen_or_raise(script_fields: dict, channel_slug: str) -> None:
    """Screen a dict of generated text fields against the compliance filter.

    On failure, logs the hits and raises RuntimeError so the content row is
    marked failed before any downstream spend. Caller is expected to let the
    exception propagate to the outer process_* handler.
    """
    from compliance_filter import check_all as _check_all
    ok, reason, details = _check_all(script_fields, channel_slug)
    if ok:
        return
    print(f"  [compliance] REJECTED on channel '{channel_slug}': {reason}")
    for field, hits in (details.get("hits_by_field") or {}).items():
        for hit in hits:
            forbid = str(hit.get("forbidden", ""))[:80]
            print(f"    - {field} [{hit.get('kind')}]: {forbid}")
    raise RuntimeError(
        f"Compliance filter rejected {channel_slug} content before TTS: {reason}"
    )


# Channel-level defaults for when philosopher mapping doesn't match.
# Used by pick_music / LoRA selection so Gibran content never falls back to Stoic.
CHANNEL_DEFAULT_MUSIC_STYLE = {
    "wisdom": "stoic_classical",
    "gibran": "gibran",
    # NA/AA use a meta-style for equalizer color only; actual track selection
    # draws from CHANNEL_MUSIC_POOL[slug] (stoic_classical + gibran folders).
    "na": "recovery_soft",
    "aa": "recovery_soft",
}
CHANNEL_DEFAULT_LORA = {
    "wisdom": "stoic_classical_v1",
    "gibran": "gibran_style_v1",
    # NA/AA run raw SDXL (no LoRA) until recovery_grounded_v1 is trained.
    "na": None,
    "aa": None,
}

# Top-of-video watermark text per channel. Matches the Remotion JS resolver
# (video-engine/scripts/lib/channel-meta.js) so both the standalone playground
# and the pipeline use the same branding.
CHANNEL_WATERMARK = {
    "wisdom": "Deep Echoes of Wisdom",
    "gibran": "Gibran Khalil Gibran",
    "na": "One Day At A Time",
    "aa": "Easy Does It",
}


def watermark_for_channel(channel_slug: str) -> str:
    """Resolve the top-of-video watermark for a channel. Unknown channels
    get the slug uppercased as a last-resort label so NA/AA can't silently
    fall through to the Wisdom watermark."""
    if not channel_slug:
        return CHANNEL_WATERMARK["wisdom"]
    slug = channel_slug.lower()
    if slug in CHANNEL_WATERMARK:
        return CHANNEL_WATERMARK[slug]
    return slug.upper()


def update_supabase(content_id: str, updates: dict, *,
                    allow_deleted: bool = False):
    """PATCH a content row in Supabase.

    By default the PATCH is gated on `deleted_at IS NULL` so that a row
    soft-deleted by the dashboard MID-GENERATION cannot be silently
    resurrected by the pipeline's status-promotion writes. Without this,
    a row deleted at T+30s during a 10-minute essay render would still
    flip to status=ready at T+10m and the dashboard would hide it (real
    incident 2026-04-20: River-of-Change essay rendered + uploaded onto
    a tombstoned row, invisible until manually un-deleted).

    Pass `allow_deleted=True` only for explicit "resurrect" operations.

    Returns the PATCHed rows (Prefer: return=representation). When the
    row was tombstoned mid-flight the array is empty — we log a warning
    so the failure is visible in the poller log instead of silent.
    """
    url = f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}"
    if not allow_deleted:
        url += "&deleted_at=is.null"
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    headers = _supabase_headers()
    headers["Prefer"] = "return=representation"
    resp = requests.patch(url, headers=headers, json=updates, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if not allow_deleted and isinstance(body, list) and len(body) == 0:
        # PATCH matched zero rows — the deleted_at filter blocked it.
        # The row was soft-deleted while the pipeline was running. Don't
        # raise (pipeline can't recover), but log loudly so it shows up
        # in poller.log.
        print(f"  [{content_id[:8]}] WARN: update_supabase no-op — "
              f"row was soft-deleted mid-generation (deleted_at != NULL). "
              f"Skipping update: {list(updates.keys())}")
    return body


def _is_deleted(content_id: str) -> bool:
    """Cheap pre-flight check: returns True iff the row is soft-deleted.
    Pipeline entry points call this before kicking off expensive work
    (Opus + SDXL + voice + Remotion) so they can bail early instead of
    rendering ~10 min of video onto a tombstoned row.
    """
    url = (f"{SUPABASE_URL}/rest/v1/content"
           f"?id=eq.{content_id}&select=deleted_at")
    try:
        resp = requests.get(url, headers=_supabase_headers(), timeout=10)
        resp.raise_for_status()
        rows = resp.json()
        return bool(rows and rows[0].get("deleted_at"))
    except Exception as e:
        # If the check itself fails, don't block generation — let the
        # downstream update_supabase guard catch it after the fact.
        print(f"  [{content_id[:8]}] WARN: _is_deleted check failed ({e}); "
              f"proceeding with generation")
        return False


def _bail_if_deleted(content_id: str, where: str) -> bool:
    """Convenience: print a clear message and return True if the row is
    deleted. Caller should `return` immediately when this returns True."""
    if _is_deleted(content_id):
        print(f"  [{content_id[:8]}] BAIL ({where}): row is soft-deleted; "
              f"refusing to generate. Un-delete via dashboard if intentional.")
        return True
    return False


def mark_failed(content_id: str, reason) -> None:
    # Write status=failed AND rejection_reason so the dashboard can surface
    # the real error instead of a bare "Retry" button.
    msg = str(reason).strip()[:500] or "unknown error"
    try:
        update_supabase(content_id, {"status": "failed", "rejection_reason": msg})
    except Exception as e:
        print(f"  [{content_id[:8]}] Failed to mark failed: {e}")


def log_step(content_id: str, step: str, step_order: int, status: str,
             error: str = None, gpu_stats: dict = None):
    """Insert or update a generation_log row for a pipeline step."""
    url = f"{SUPABASE_URL}/rest/v1/generation_log"
    payload = {
        "content_id": content_id,
        "step": step,
        "step_order": step_order,
        "status": status,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if status in ("success", "failed"):
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    if error:
        payload["error_message"] = error
    if gpu_stats:
        payload["gpu_stats"] = gpu_stats

    # Upsert: if a row for this content_id + step already exists, update it.
    # We use POST with Prefer: resolution=merge-duplicates when possible.
    # Since generation_log has no unique constraint on (content_id, step),
    # we just insert a new row for each status change.
    resp = requests.post(url, headers=_supabase_headers(),
                         json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Quote deduplication — fetch recent quotes to avoid repetition
# ---------------------------------------------------------------------------
def _fetch_recent_quotes(philosopher: str, limit: int = 20) -> list:
    """Fetch recent quote_text for a philosopher from Supabase."""
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/content"
            f"?philosopher=eq.{philosopher}"
            f"&quote_text=not.is.null"
            f"&deleted_at=is.null"
            f"&select=quote_text"
            f"&order=created_at.desc"
            f"&limit={limit}"
        )
        resp = requests.get(url, headers=_supabase_headers(), timeout=10)
        if resp.status_code == 200:
            return [r["quote_text"] for r in resp.json() if r.get("quote_text")]
    except Exception:
        pass
    return []


def _build_dedup_context(previous_quotes: list) -> str:
    """Build a prompt section listing previous quotes to avoid."""
    if not previous_quotes:
        return ""
    trimmed = [q[:120] for q in previous_quotes[:15]]
    lines = "\n".join(f"- {q}" for q in trimmed)
    return (
        f"\n\nIMPORTANT — Do NOT repeat or closely paraphrase any of these "
        f"previous quotes:\n{lines}\n\nWrite something genuinely different.\n"
    )


# ---------------------------------------------------------------------------
# Quote generation (Ollama with Claude fallback)
# ---------------------------------------------------------------------------
def generate_quote(philosopher: str, topic: str) -> str:
    """
    Generate a single philosophical quote via Ollama (local).
    Falls back to Claude Haiku if Ollama is unavailable.
    Passes recent quotes for deduplication.
    """
    previous = _fetch_recent_quotes(philosopher)
    dedup = _build_dedup_context(previous)

    prompt = (
        f"Write a single original philosophical quote in the authentic style "
        f"and voice of {philosopher}, on the topic of \"{topic}\".\n\n"
        f"Requirements:\n"
        f"- Must sound authentically like {philosopher}\n"
        f"- 1-3 sentences, poetic and quotable\n"
        f"- Deep insight, not surface-level advice\n"
        f"- Do NOT include attribution or quotation marks\n\n"
        f"Return ONLY the quote text, nothing else."
        f"{dedup}"
    )

    ollama_model = PERSONA_TO_OLLAMA_MODEL.get(
        philosopher, philosopher.lower().replace(" ", "_")
    )
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.8},
            },
            timeout=120,
        )
        resp.raise_for_status()
        quote = sanitize_quote(resp.json().get("response", ""))
        if quote:
            return quote
    except Exception as e:
        print(f"  [quote] Ollama failed ({e}), falling back to Haiku")

    result = generate_short_script(philosopher, topic, ollama_model=ollama_model)
    return sanitize_quote(result.get("quote", ""))


# ---------------------------------------------------------------------------
# Art generation via ComfyUI
# ---------------------------------------------------------------------------
def _build_comfyui_workflow(prompt: str, lora_name,
                            width: int, height: int,
                            filename_prefix: str) -> dict:
    """Build a ComfyUI workflow JSON with optional LoRA for SDXL generation.

    lora_name may be None or empty string — in that case we drop node 11 and
    run raw SDXL (template defaults already wire KSampler + CLIPs to the
    checkpoint at node 10).
    """
    import copy
    workflow = copy.deepcopy(_COMFYUI_WORKFLOW_TEMPLATE)

    # Seed
    workflow["3"]["inputs"]["seed"] = random.randint(0, 2**32 - 1)

    # Dimensions
    workflow["5"]["inputs"]["width"] = width
    workflow["5"]["inputs"]["height"] = height

    # Positive prompt
    workflow["6"]["inputs"]["text"] = prompt

    # Output filename
    workflow["9"]["inputs"]["filename_prefix"] = filename_prefix

    # LoRA — optional. When set, insert the LoraLoader into the graph.
    # When None/empty, drop node 11 and let KSampler/CLIPs pull directly from
    # the checkpoint (node 10) — which is the template default.
    if lora_name:
        workflow["11"]["inputs"]["lora_name"] = f"{lora_name}.safetensors"
        workflow["3"]["inputs"]["model"] = ["11", 0]
        workflow["6"]["inputs"]["clip"] = ["11", 1]
        workflow["7"]["inputs"]["clip"] = ["11", 1]
    else:
        workflow.pop("11", None)

    return workflow


def generate_art(prompt: str, lora_name, width: int, height: int,
                 output_path: str) -> str:
    """
    Call ComfyUI API to generate an image using SDXL (with optional LoRA).
    Polls for completion and downloads the result.

    lora_name may be None/empty — the workflow builder will run raw SDXL.
    Returns the local file path of the saved image.
    """
    output_path = str(output_path)
    filename_prefix = Path(output_path).stem

    workflow = _build_comfyui_workflow(prompt, lora_name, width, height,
                                       filename_prefix)

    # Queue the prompt
    payload = {"prompt": workflow}
    resp = requests.post(f"{COMFYUI_URL}/prompt", json=payload, timeout=30)
    resp.raise_for_status()
    prompt_id = resp.json()["prompt_id"]
    print(f"  [art] ComfyUI prompt queued: {prompt_id}")

    # Poll for completion
    max_wait = 300  # 5 minutes
    poll_interval = 3
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        hist_resp = requests.get(
            f"{COMFYUI_URL}/history/{prompt_id}", timeout=15
        )
        hist_resp.raise_for_status()
        history = hist_resp.json()

        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            # Find the SaveImage node output
            for node_id, node_output in outputs.items():
                images = node_output.get("images", [])
                if images:
                    img_info = images[0]
                    img_filename = img_info["filename"]
                    subfolder = img_info.get("subfolder", "")

                    # Download the image
                    params = {
                        "filename": img_filename,
                        "subfolder": subfolder,
                        "type": "output",
                    }
                    img_resp = requests.get(
                        f"{COMFYUI_URL}/view", params=params, timeout=30
                    )
                    img_resp.raise_for_status()

                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(img_resp.content)

                    print(f"  [art] Saved: {output_path}")
                    return output_path

    raise TimeoutError(
        f"ComfyUI did not complete prompt {prompt_id} within {max_wait}s"
    )


# ---------------------------------------------------------------------------
# Voice generation via ElevenLabs
# ---------------------------------------------------------------------------
ELEVENLABS_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_WISDOM = os.environ.get("ELEVENLABS_VOICE_WISDOM", "0ABJJI7ZYmWZBiUBMHUW")
ELEVENLABS_VOICE_GIBRAN = os.environ.get("ELEVENLABS_VOICE_GIBRAN", "R68HwD2GzEdWfqYZP9FQ")

# Channel-level default voice IDs for channels without per-philosopher voices.
# PERSONA_TO_VOICE_ID takes precedence over this map.
CHANNEL_DEFAULT_VOICE_ID = {
    "wisdom": ELEVENLABS_VOICE_WISDOM,
    "gibran": ELEVENLABS_VOICE_GIBRAN,
}


# ---------------------------------------------------------------------------
# ElevenLabs credit-floor gate
#
# Before each EL call, check account usage. If the projected remaining credit
# after the call would drop below the configured floor, refuse. This prevents
# accidental monthly-quota blowouts when running at scale across multiple
# channels. Disable by setting ELEVENLABS_CREDIT_FLOOR=0.
# ---------------------------------------------------------------------------
class ElevenLabsCreditFloorExceeded(RuntimeError):
    """Raised when an EL call would push remaining credits below the floor."""
    pass


_EL_SUBSCRIPTION_CACHE = {"ts": 0.0, "data": None}


def _el_subscription(force_refresh: bool = False) -> dict:
    """Fetch /v1/user/subscription with a 60s in-process cache.

    Returns the parsed JSON dict on success, or None on any failure.
    Requires the ELEVENLABS_API_KEY to have the user_read permission.
    """
    import time
    now = time.time()
    cached = _EL_SUBSCRIPTION_CACHE.get("data")
    if not force_refresh and cached and (now - _EL_SUBSCRIPTION_CACHE["ts"]) < 60:
        return cached
    try:
        resp = requests.get(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": ELEVENLABS_KEY},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            _EL_SUBSCRIPTION_CACHE["ts"] = now
            _EL_SUBSCRIPTION_CACHE["data"] = data
            return data
        print(f"  [voice] WARNING: EL subscription fetch HTTP {resp.status_code}: "
              f"{resp.text[:120] if resp.text else ''}")
    except Exception as e:
        print(f"  [voice] WARNING: EL subscription fetch failed: {e}")
    return cached  # return stale cache if we have one, else None


def _el_credit_gate(estimated_chars: int = 0) -> None:
    """Raise ElevenLabsCreditFloorExceeded if projected remaining after the
    planned call would fall below `floor * character_limit`.

    Floor is controlled by ELEVENLABS_CREDIT_FLOOR env var (fraction 0-1).
    Default 0.05 (5%). Set to 0 to disable the gate.

    Fails open if subscription data can't be fetched — do not block pipeline
    because of a transient EL outage.
    """
    try:
        floor = float(os.environ.get("ELEVENLABS_CREDIT_FLOOR", "0.05"))
    except ValueError:
        floor = 0.05
    if floor <= 0:
        return

    sub = _el_subscription()
    if not sub:
        print("  [voice] WARNING: EL credit gate bypassed — no subscription data")
        return

    limit = int(sub.get("character_limit") or 0)
    used = int(sub.get("character_count") or 0)
    if limit <= 0:
        return

    remaining = limit - used
    projected_remaining = remaining - estimated_chars
    min_required = int(limit * floor)
    if projected_remaining < min_required:
        pct_used = used / limit * 100
        raise ElevenLabsCreditFloorExceeded(
            f"ElevenLabs credit floor breached: "
            f"{used}/{limit} used ({pct_used:.1f}%), "
            f"{remaining} remaining, estimated {estimated_chars} more needed, "
            f"floor = {min_required} ({floor*100:.0f}% of limit). "
            f"Upgrade plan or lower ELEVENLABS_CREDIT_FLOOR."
        )


def _apply_tail_fade(in_path: str, out_path: str,
                     duration_s: float = 1.5,
                     pitch_factor: float = 0.94,
                     volume_target: float = 0.7) -> bool:
    """Add a "soft landing" to the END of an audio file.

    Splits the clip at `duration_s` from the end, pitch-shifts the tail
    by `pitch_factor` (0.94 = drop ~1 semitone) using librubberband
    (time-preserving — no time-stretch artifact), and tapers the volume
    from 1.0 to `volume_target` across the tail using an exponential-sine
    curve.

    Real natural human voices DO drop pitch at the end of declarative
    sentences (linguistic term: "terminal falling intonation"). Chatterbox
    sometimes produces a flat end where this drop is missing — this
    post-process restores the natural feel.

    Returns True on success, False if ffmpeg failed (caller keeps the
    pre-fade file rather than crashing the pipeline).
    """
    import subprocess
    # Get total duration via ffprobe
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", in_path],
            capture_output=True, text=True, check=True,
        )
        total = float(probe.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        print(f"  [voice] WARN: tail_fade ffprobe failed ({e}); skipping post-process")
        return False

    # If clip is too short for a meaningful tail, skip
    if total < duration_s + 0.5:
        print(f"  [voice] tail_fade skipped — clip {total:.2f}s shorter than tail+headroom")
        return False

    head_end = total - duration_s  # split point
    # Filter graph:
    #   asplit -> two copies
    #   head: trim to [0, head_end]
    #   tail: trim to [head_end, end], reset PTS, pitch-shift, fade to target
    #   concat both back
    filter_graph = (
        f"[0:a]asplit=2[a][b];"
        f"[a]atrim=end={head_end:.4f},asetpts=PTS-STARTPTS[head];"
        f"[b]atrim=start={head_end:.4f},asetpts=PTS-STARTPTS,"
        f"rubberband=pitch={pitch_factor},"
        f"volume=eval=frame:volume='1-(1-{volume_target})*(t/{duration_s})'[tail];"
        f"[head][tail]concat=n=2:v=0:a=1[out]"
    )
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", in_path, "-filter_complex", filter_graph,
             "-map", "[out]", out_path],
            check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [voice] WARN: tail_fade ffmpeg failed "
              f"({e.stderr[:200] if e.stderr else e}); keeping pre-fade output")
        return False


def _generate_voice_chatterbox(text: str, output_path: str,
                               channel_slug: str,
                               cb_cfg: dict,
                               temperature: float = 0.75,
                               exaggeration: float = 0.5,
                               cfg_weight: float = 0.5,
                               chunk_size: int = 240,
                               seed: int = 4242) -> str:
    """Synthesize via Chatterbox using the channel's CB profile.

    cb_cfg comes from CHANNEL_VOICE[channel].chatterbox — atomic config:
        ref:    reference_audio filename in voice/recordings/
        atempo: ffmpeg time-stretch factor (1.0 = no change)
        reverb: optional ffmpeg `aecho` expression baked into output

    Recipe (from the Gibran A_v2_slow A/B/C blind test on 2026-04-19):
      1. Pre-process text via _chatterbox_pause_hints — converts em-dash,
         semicolon, ellipsis -> period (the only mark CB strongly honors).
      2. POST /tts with voice_mode=clone + the channel's registered
         reference. speed_factor stays at 1.0 (CB's native speed_factor
         pitch-shifts; we time-stretch with ffmpeg atempo instead).
      3. Single ffmpeg pass: atempo + optional reverb chained.

    Returns the final WAV path.
    """
    import subprocess

    ref_filename = cb_cfg["ref"]
    atempo = cb_cfg.get("atempo", 1.0)
    reverb = cb_cfg.get("reverb")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "text": _chatterbox_pause_hints(text),
        "voice_mode": "clone",
        "reference_audio_filename": ref_filename,
        "output_format": "wav",
        "split_text": True,
        "chunk_size": chunk_size,
        "temperature": temperature,
        "exaggeration": exaggeration,
        "cfg_weight": cfg_weight,
        "speed_factor": 1.0,
        "seed": seed,
        "language": "en",
    }
    raw_path = output_path + ".cb_raw.wav"
    print(f"  [voice] Chatterbox synth ({channel_slug}, ref={ref_filename}, "
          f"text={len(text)}ch, atempo={atempo}"
          + (f", reverb={reverb}" if reverb else "")
          + ")...")
    resp = requests.post(f"{CHATTERBOX_URL}/tts", json=payload, timeout=1800)
    resp.raise_for_status()
    with open(raw_path, "wb") as f:
        f.write(resp.content)

    # Build a single ffmpeg filter chain: atempo + optional silence-cap
    # + optional reverb. Doing them in one pass keeps things simple and
    # avoids extra disk I/O.
    #
    # silence-cap: Chatterbox occasionally inserts a runaway 5-10s gap
    # at a sentence/chunk boundary (real bug observed 2026-04-20: the
    # NA "Voice of the Rooms" 90-day-clean short had a 9.1s silent stretch
    # mid-audio). silenceremove with stop_periods=-1 finds every silence
    # longer than `silence_cap_trigger` seconds and trims it down to
    # `silence_cap_keep` seconds — natural ~0.5-1.5s sentence pauses are
    # left intact, only the pathological gaps get squashed.
    silence_cap_trigger = cb_cfg.get("silence_cap_trigger", 2.0)
    silence_cap_keep    = cb_cfg.get("silence_cap_keep", 1.0)
    silence_cap_threshold = cb_cfg.get("silence_cap_threshold", "-30dB")
    filter_parts = []
    if atempo and atempo != 1.0:
        filter_parts.append(f"atempo={atempo}")
    if silence_cap_trigger and silence_cap_keep:
        filter_parts.append(
            f"silenceremove=stop_periods=-1:"
            f"stop_duration={silence_cap_trigger}:"
            f"stop_threshold={silence_cap_threshold}:"
            f"stop_silence={silence_cap_keep}"
        )
    if reverb:
        filter_parts.append(reverb)

    if filter_parts:
        chain = ",".join(filter_parts)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", raw_path, "-filter:a", chain,
                 output_path],
                check=True, capture_output=True,
            )
            os.remove(raw_path)
        except subprocess.CalledProcessError as e:
            # Roll back to raw if the chain fails — better dry-but-coherent
            # than no audio at all.
            print(f"  [voice] WARN: ffmpeg chain '{chain}' failed "
                  f"({e.stderr[:120] if e.stderr else e}); keeping raw output")
            os.replace(raw_path, output_path)
    else:
        os.replace(raw_path, output_path)

    # Optional tail-fade — adds the missing "terminal falling intonation"
    # that natural voices have at the end of a declarative sentence. CB
    # sometimes ends flat; this gives the perceptual feel of the voice
    # softly landing instead of just stopping. Configurable per channel.
    tail_cfg = cb_cfg.get("tail_fade")
    tail_status = "off"
    if tail_cfg:
        tmp_path = output_path + ".pre_tailfade.wav"
        os.replace(output_path, tmp_path)
        ok = _apply_tail_fade(
            tmp_path, output_path,
            duration_s=tail_cfg.get("duration", 1.5),
            pitch_factor=tail_cfg.get("pitch_factor", 0.94),
            volume_target=tail_cfg.get("volume_target", 0.7),
        )
        if ok:
            os.remove(tmp_path)
            tail_status = (
                f"on(d={tail_cfg.get('duration',1.5)}s,"
                f"pitch={tail_cfg.get('pitch_factor',0.94)},"
                f"vol={tail_cfg.get('volume_target',0.7)})"
            )
        else:
            os.replace(tmp_path, output_path)

    print(f"  [voice] Saved (Chatterbox, atempo={atempo}, "
          f"reverb={'on' if reverb else 'off'}, tail_fade={tail_status}): {output_path}")
    return output_path


def generate_voice(text: str, output_path: str,
                   channel_slug: str,
                   philosopher: str = None,
                   tts_provider: str = None,
                   exaggeration: float = 0.5,
                   cfg_weight: float = 0.5,
                   slow_factor: float = 1.0) -> str:
    """
    Generate voice via ElevenLabs OR Chatterbox per CHANNEL_VOICE config.

    channel_slug is REQUIRED.
    philosopher: when provided, PERSONA_TO_VOICE_ID wins over channel default.
    tts_provider: forces "elevenlabs" or "chatterbox". When None (default),
      _resolve_voice_config(channel_slug) reads env vars and CHANNEL_VOICE
      — the recommended path because it lets switch_gibran_tts.py flip
      without touching any caller.
    slow_factor: EL-only scalar; <1.0 slows playback via ffmpeg atempo
      while preserving pitch. CB ignores it (uses its own per-channel
      atempo from CHANNEL_VOICE[channel].chatterbox.atempo).
    Returns the local file path of the saved audio.
    """
    from elevenlabs import ElevenLabs, VoiceSettings

    if not channel_slug:
        raise ValueError("generate_voice requires a channel_slug — refusing to default to wisdom voice")

    output_path = str(output_path)

    # Resolve provider + CB config in one go. Pass philosopher so NA/AA
    # archetype-aware references (The Old-Timer / The Sponsor / The Voice
    # of the Rooms) get picked correctly per-row.
    voice_cfg = _resolve_voice_config(channel_slug, philosopher=philosopher)
    if tts_provider is None:
        tts_provider = voice_cfg["provider"]
        ref_for_log = (voice_cfg.get("chatterbox") or {}).get("ref", "-")
        print(f"  [voice] resolved tts_provider={tts_provider} "
              f"channel={channel_slug} persona={philosopher!r} ref={ref_for_log}")

    # Chatterbox path — only enabled when the channel has a CB profile in
    # CHANNEL_VOICE. If CB is requested but the channel isn't qualified,
    # fall back to EL loudly rather than silently producing garbage.
    if tts_provider == "chatterbox":
        cb_cfg = voice_cfg["chatterbox"]
        if not cb_cfg or not cb_cfg.get("ref"):
            print(
                f"  [voice] WARNING: tts_provider=chatterbox requested for "
                f"channel='{channel_slug}' but no Chatterbox profile is "
                f"registered in CHANNEL_VOICE — falling back to ElevenLabs."
            )
        else:
            return _generate_voice_chatterbox(
                text=text,
                output_path=output_path,
                channel_slug=channel_slug,
                cb_cfg=cb_cfg,
            )

    # ElevenLabs path (default).
    # Priority: philosopher-specific voice > channel default voice.
    voice_id = (
        PERSONA_TO_VOICE_ID.get(philosopher)
        or CHANNEL_DEFAULT_VOICE_ID.get(channel_slug)
    )
    if not voice_id:
        raise ValueError(
            f"No ElevenLabs voice ID for channel='{channel_slug}' philosopher='{philosopher}'. "
            "Add to PERSONA_TO_VOICE_ID or CHANNEL_DEFAULT_VOICE_ID."
        )

    # Gibran voice is 10% faster — the default ElevenLabs cadence sounds too slow
    if channel_slug == "gibran":
        slow_factor *= 1.1

    # Credit-floor gate — refuse if this call would push us below the floor
    _el_credit_gate(estimated_chars=len(text))

    client = ElevenLabs(api_key=ELEVENLABS_KEY)
    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
        voice_settings=VoiceSettings(
            stability=0.70,
            similarity_boost=0.85,
            style=0.25,
            use_speaker_boost=True,
        ),
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)

    # Apply tempo slowdown (pitch-preserving) if requested
    if slow_factor != 1.0:
        import subprocess
        tmp_path = output_path + ".fast.wav"
        os.replace(output_path, tmp_path)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_path, "-af",
                 f"atempo={slow_factor}", output_path],
                check=True, capture_output=True,
            )
            os.remove(tmp_path)
            print(f"  [voice] Saved (ElevenLabs, atempo={slow_factor}): {output_path}")
        except subprocess.CalledProcessError as e:
            # Roll back — keep original if atempo fails
            os.replace(tmp_path, output_path)
            print(f"  [voice] WARNING: atempo slowdown failed ({e.stderr[:120] if e.stderr else e}), kept original")
        return output_path

    print(f"  [voice] Saved (ElevenLabs): {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Music selection
# ---------------------------------------------------------------------------
def _collect_tracks(styles) -> list:
    """Collect all mp3/wav tracks from one or more music style folders."""
    if isinstance(styles, str):
        styles = [styles]
    tracks = []
    for s in styles:
        d = MUSIC_ROOT / s
        if d.exists():
            tracks.extend(d.glob("*.mp3"))
            tracks.extend(d.glob("*.wav"))
    return tracks


def pick_music(philosopher: str, channel_slug: str = None) -> str:
    """
    Pick a random music track for this content.

    Resolution order:
      1. PERSONA_TO_MUSIC_STYLE[philosopher]  (single style)
      2. CHANNEL_MUSIC_POOL[channel_slug]         (multi-style pool, e.g. NA)
      3. CHANNEL_DEFAULT_MUSIC_STYLE[channel_slug] (single-style fallback)
      4. any style folder with tracks              (last resort)
    """
    # 1. Philosopher-specific single style
    style = PERSONA_TO_MUSIC_STYLE.get(philosopher)
    if style:
        tracks = _collect_tracks(style)
        if tracks:
            chosen = random.choice(tracks)
            print(f"  [music] Selected: {chosen.name} (style: {style})")
            return str(chosen)

    # 2. Channel multi-style pool (e.g. NA pulls from stoic_classical + gibran)
    if channel_slug:
        pool = CHANNEL_MUSIC_POOL.get(channel_slug)
        if pool:
            tracks = _collect_tracks(pool)
            if tracks:
                chosen = random.choice(tracks)
                print(f"  [music] Selected: {chosen.name} (pool: {pool})")
                return str(chosen)

    # 3. Channel default single style
    if channel_slug:
        style = CHANNEL_DEFAULT_MUSIC_STYLE.get(channel_slug)
        if style:
            tracks = _collect_tracks(style)
            if tracks:
                chosen = random.choice(tracks)
                print(f"  [music] Selected: {chosen.name} (channel default: {style})")
                return str(chosen)

    # 4. Last-ditch fallback: any style folder with tracks
    for fallback_dir in MUSIC_ROOT.iterdir():
        if fallback_dir.is_dir():
            tracks = _collect_tracks(fallback_dir.name)
            if tracks:
                chosen = random.choice(tracks)
                print(f"  [music] Fallback: {chosen.name} (from {fallback_dir.name})")
                return str(chosen)

    raise FileNotFoundError(f"No music tracks found in {MUSIC_ROOT}")


# ---------------------------------------------------------------------------
# Google Drive upload
# ---------------------------------------------------------------------------
def _refresh_google_token(channel_id: str, refresh_token: str) -> str:
    """Refresh Google access token using the stored refresh token."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    new_token = data["access_token"]
    expires_in = data.get("expires_in", 3600)

    # Update stored token in Supabase channel settings
    ch_url = f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel_id}&select=settings"
    ch_resp = requests.get(ch_url, headers=_supabase_headers(), timeout=15)
    ch_resp.raise_for_status()
    channels = ch_resp.json()
    if channels:
        existing_settings = channels[0].get("settings", {}) or {}
        existing_settings["google_access_token"] = new_token
        expiry = datetime.now(timezone.utc).timestamp() + expires_in
        existing_settings["google_token_expiry"] = (
            datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat()
        )
        update_url = f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel_id}"
        requests.patch(
            update_url,
            headers=_supabase_headers(),
            json={"settings": existing_settings},
            timeout=15,
        )

    return new_token


def _get_google_access_token(channel: dict) -> str:
    """Get a valid Google access token for a channel, refreshing if needed."""
    settings = channel.get("settings", {}) or {}
    refresh_token = settings.get("google_refresh_token")
    if not refresh_token:
        raise ValueError(
            f"Google not connected for channel '{channel.get('name', '?')}'"
        )

    token_expiry_str = settings.get("google_token_expiry", "")
    if token_expiry_str:
        try:
            expiry = datetime.fromisoformat(token_expiry_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            # Refresh if expiring within 5 minutes
            if (expiry - now).total_seconds() > 300:
                existing_token = settings.get("google_access_token", "")
                if existing_token:
                    return existing_token
        except (ValueError, TypeError):
            pass  # Token expiry unparseable, refresh anyway

    return _refresh_google_token(channel["id"], refresh_token)


def _week_folder_name(target_date: datetime = None) -> str:
    """
    Build the weekly folder name: ``Month-W#-MonDD-MonDD``

    Examples:
        ``March-W1-Feb23-Mar1``   (week spans two months)
        ``March-W4-Mar22-Mar28``  (week within one month)

    The week number is relative to the month: W1 contains the first Monday
    that falls on or after the 1st, and so on.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()
    elif hasattr(target_date, "date"):
        target_date = target_date.date()

    # Monday of the target date's week
    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)

    # Month name comes from the target date (the date we are scheduling for)
    month_name = calendar.month_name[target_date.month]

    # Week number within the month: how many Mondays from the 1st to this Monday
    first_of_month = target_date.replace(day=1)
    # Find the first Monday on or after the 1st
    days_until_monday = (7 - first_of_month.weekday()) % 7
    first_monday = first_of_month + timedelta(days=days_until_monday)
    if monday < first_monday:
        week_num = 1
    else:
        week_num = ((monday - first_monday).days // 7) + 1
        if first_of_month.weekday() != 0:
            week_num += 1  # account for partial first week

    # Short month abbreviations for range
    mon_start = f"{calendar.month_abbr[monday.month]}{monday.day}"
    mon_end = f"{calendar.month_abbr[sunday.month]}{sunday.day}"

    return f"{month_name}-W{week_num}-{mon_start}-{mon_end}"


def _find_drive_subfolder(access_token: str, parent_id: str,
                          folder_name: str) -> str | None:
    """Search for an existing subfolder by name inside a parent Drive folder."""
    query = (
        f"'{parent_id}' in parents "
        f"and name = '{folder_name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    resp = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"q": query, "fields": "files(id,name)", "pageSize": 1},
        timeout=30,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])
    return files[0]["id"] if files else None


def _create_drive_subfolder(access_token: str, parent_id: str,
                            folder_name: str) -> str:
    """Create a new subfolder inside a parent Drive folder. Returns the new folder id."""
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    resp = requests.post(
        "https://www.googleapis.com/drive/v3/files",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=metadata,
        params={"fields": "id"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _get_or_create_week_folder(access_token: str, parent_folder_id: str) -> str:
    """
    Ensure a weekly subfolder exists under the channel's root Drive folder.
    Returns the subfolder id to upload into.
    """
    folder_name = _week_folder_name()
    existing_id = _find_drive_subfolder(access_token, parent_folder_id, folder_name)
    if existing_id:
        print(f"  [drive] Using existing week folder: {folder_name}")
        return existing_id

    new_id = _create_drive_subfolder(access_token, parent_folder_id, folder_name)
    print(f"  [drive] Created week folder: {folder_name}")
    return new_id


def _make_file_public(access_token: str, file_id: str) -> None:
    """Share a Drive file as 'anyone with the link can view'."""
    try:
        resp = requests.post(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"role": "reader", "type": "anyone"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  [drive] WARNING: Could not make file public: {e}")


def upload_to_drive(file_path: str, channel: dict) -> str:
    """
    Upload a video file to the channel's Google Drive folder inside a
    weekly subfolder (``Month-W#-MonDD-MonDD``).
    Returns the Drive web view URL.
    """
    access_token = _get_google_access_token(channel)
    folder_id = channel.get("google_drive_folder_id")
    if not folder_id:
        raise ValueError(
            f"No Google Drive folder configured for channel '{channel.get('name', '?')}'"
        )

    # Resolve (or create) the weekly subfolder
    week_folder_id = _get_or_create_week_folder(access_token, folder_id)

    filename = Path(file_path).name
    file_size = Path(file_path).stat().st_size

    # Use multipart upload for files under 5MB, resumable for larger
    if file_size < 5 * 1024 * 1024:
        drive_url = _upload_multipart(access_token, week_folder_id, file_path, filename)
    else:
        drive_url = _upload_resumable(access_token, week_folder_id, file_path, filename)

    # Make the file publicly viewable so dashboard thumbnails work
    file_id = drive_url.split("/d/")[1].split("/")[0] if "/d/" in drive_url else None
    if file_id:
        _make_file_public(access_token, file_id)

    return drive_url


def _upload_multipart(access_token: str, folder_id: str,
                      file_path: str, filename: str) -> str:
    """Multipart upload for smaller files."""
    metadata = json.dumps({"name": filename, "parents": [folder_id]})
    boundary = "-------wisdom_upload_boundary"

    with open(file_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--".encode("utf-8")

    resp = requests.post(
        "https://www.googleapis.com/upload/drive/v3/files"
        "?uploadType=multipart&fields=id,webViewLink",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        data=body,
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("webViewLink",
                      f"https://drive.google.com/file/d/{result['id']}/view")


def _upload_resumable(access_token: str, folder_id: str,
                      file_path: str, filename: str) -> str:
    """Resumable upload for larger files (>5MB)."""
    metadata = json.dumps({"name": filename, "parents": [folder_id]})

    # Initiate resumable session
    init_resp = requests.post(
        "https://www.googleapis.com/upload/drive/v3/files"
        "?uploadType=resumable&fields=id,webViewLink",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
        },
        data=metadata,
        timeout=30,
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    # Upload file content
    with open(file_path, "rb") as f:
        file_data = f.read()

    resp = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=file_data,
        timeout=300,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("webViewLink",
                      f"https://drive.google.com/file/d/{result['id']}/view")


# ---------------------------------------------------------------------------
# Art prompt builder
# ---------------------------------------------------------------------------
# Per-philosopher visual STYLE cards (2026-04-09, refined).
#
# IMPORTANT: These describe HOW the image is painted (medium, lighting,
# palette, brushwork, art-historical influence) — NOT WHAT is in it.
# The SUBJECT of each image comes from Claude's per-scene description.
# An earlier version of these cards baked specific scenes into the style
# (e.g. "Walden Pond in autumn, solitary figure in contemplation") which
# caused SDXL to paint the SAME scene every time regardless of narration.
# Rule: no subject nouns, no specific locations, no "figure doing X".
PHILOSOPHER_VISUAL_STYLE = {
    # --- Stoics: Caravaggio oil painting with chiaroscuro ---
    "Marcus Aurelius": (
        "oil painting, chiaroscuro lighting, renaissance master palette, "
        "earth tones and warm ochre, deep umber shadows, single warm light source, "
        "painterly brushwork on linen canvas, museum-quality fine art, "
        "Caravaggio influence, tenebrism"
    ),
    "Seneca": (
        "oil painting, Caravaggio tenebrism style, dramatic single-source lighting, "
        "rich earth tones of umber and sienna, deep shadows and warm highlights, "
        "painterly brushwork on linen canvas, 17th century master fine art"
    ),
    "Epictetus": (
        "oil painting, severe tenebrism, cold light cutting through darkness, "
        "muted earth palette, rough linen textures, restrained Caravaggio style, "
        "painterly realism, museum-quality fine art"
    ),
    # --- Rumi & Gibran: illuminated manuscript / watercolor ---
    "Rumi": (
        "Persian miniature painting style, gold leaf accents, illuminated manuscript "
        "aesthetic, intricate decorative borders, jewel-tone palette of lapis and "
        "turquoise and saffron, ornate detail work, medieval Islamic art tradition"
    ),
    "Gibran": (
        "symbolist watercolor painting, warm ochre and earth-tone palette, "
        "soft dreamlike edges, mystical atmosphere, art nouveau influence, "
        "luminous washes, spiritual symbolism, Khalil Gibran's own painting style"
    ),
    "Gibran Khalil Gibran": (
        "symbolist watercolor painting, warm ochre and earth-tone palette, "
        "soft dreamlike edges, mystical atmosphere, art nouveau influence, "
        "luminous washes, spiritual symbolism, Khalil Gibran's own painting style"
    ),
    # --- Eastern philosophy: ink wash ---
    "Lao Tzu": (
        "Chinese sumi-e ink wash painting, minimalist composition with negative space, "
        "black ink on rice paper aesthetic, single confident brushstrokes, "
        "zen brushwork, subtle grey wash tones, contemplative restraint"
    ),
    "Confucius": (
        "classical Chinese scholar painting, ink and light color wash on silk, "
        "restrained palette, calligraphic brushwork, Song dynasty fine art, "
        "refined atmosphere, painted scroll aesthetic"
    ),
    "Sun Tzu": (
        "ancient Chinese painting on silk, ink and gold leaf, dynamic composition, "
        "Tang dynasty aesthetic, heroic mood, dramatic brushwork, "
        "imperial scroll art, painted with confident ink lines"
    ),
    "Musashi": (
        "Japanese ukiyo-e woodblock print style, Hokusai influence, bold outlines, "
        "flat jewel-tone palette, sumi-e ink accents, Edo period aesthetic, "
        "compositional clarity, traditional Japanese print"
    ),
    # --- Western Romantics: Hudson River School ---
    "Emerson": (
        "Hudson River School oil painting style, luminous romantic realism, "
        "warm golden-hour light, atmospheric perspective, painterly brushwork, "
        "Thomas Cole influence, 19th century American landscape tradition"
    ),
    "Thoreau": (
        "Hudson River School oil painting style, painterly romantic realism, "
        "warm amber autumn light, atmospheric perspective, visible brushwork, "
        "luminous palette, Asher Brown Durand influence, 19th century landscape tradition"
    ),
    # --- Nietzsche & Dostoevsky: dark expressionism ---
    "Nietzsche": (
        "German expressionist oil painting, Caspar David Friedrich influence, "
        "dark romantic sublime palette, heavy impasto brushwork, moody atmospheric lighting, "
        "dramatic chiaroscuro, 19th century painterly tradition"
    ),
    "Dostoevsky": (
        "Russian realist oil painting, Ilya Repin influence, candlelit interior lighting, "
        "painterly psychological realism, heavy shadow and warm lamplight, "
        "19th century academic oil technique, muted earth palette"
    ),
    # --- Victorian / Renaissance / Scientific ---
    "Wilde": (
        "Victorian aesthetic movement oil painting, John Singer Sargent influence, "
        "jewel-tone palette, opulent textures, decadent lighting, fin-de-siecle atmosphere, "
        "rich velvet and gilded details in the palette, 19th century portraiture style"
    ),
    "Da Vinci": (
        "high renaissance oil painting, Leonardo da Vinci sfumato technique, "
        "atmospheric perspective, warm earth tones, chiaroscuro modeling, "
        "soft gradient light, anatomical precision, painted on poplar panel"
    ),
    "Tesla": (
        "early 20th century oil painting, Ashcan school realism, tungsten and "
        "electric arc color temperature contrast, moody industrial palette, "
        "painterly brushwork, period scientific atmosphere"
    ),
    "Franklin": (
        "colonial American oil painting, John Singleton Copley influence, "
        "candlelit warmth, period portraiture lighting, painted on linen, "
        "18th century academic oil technique, warm wood and parchment palette"
    ),
    "Vivekananda": (
        "Indian miniature painting tradition, saffron and deep red palette, "
        "gold leaf accents, Rajput court art aesthetic, luminous devotional mood, "
        "ornate detail work, spiritual radiance in the light"
    ),
}


def _get_philosopher_style(philosopher: str) -> str:
    """Return the visual style card for a philosopher, with a sensible default."""
    # Try exact match first, then common variations
    if philosopher in PHILOSOPHER_VISUAL_STYLE:
        return PHILOSOPHER_VISUAL_STYLE[philosopher]
    # Case-insensitive lookup
    for k, v in PHILOSOPHER_VISUAL_STYLE.items():
        if k.lower() == philosopher.lower():
            return v
    # Default: cinematic oil painting
    return (
        "cinematic oil painting, masterful chiaroscuro lighting, "
        "renaissance master palette, earth tones and warm candlelight, "
        "painted on linen canvas, museum-quality fine art"
    )


# ---------------------------------------------------------------------------
# NA / AA recovery-aesthetic art — Hopper/Wyeth American realist quiet.
# No classical architecture, no historical costume, no posed faces.
# ---------------------------------------------------------------------------
_NA_SCENE_POOL = [
    "hands wrapped around a worn ceramic coffee mug on a simple kitchen counter at dawn",
    "an empty wooden porch chair at sunrise, dew on the railing, long golden light",
    "a silhouette from behind at a kitchen window, morning light spilling in, face not visible",
    "a screen door half open onto a porch, warm dawn light pooling on the wooden floor",
    "a pair of worn work boots on a wooden floor beside a closed door, soft morning light",
    "an empty diner booth at dawn, steam rising from a single white coffee cup",
    "a single lit window in an old apartment building against a pre-dawn blue sky",
    "a paperback book and a half-full coffee mug on a small wooden kitchen table",
    "a cell phone face-down on a kitchen counter next to a small notebook and pen",
    "a worn leather armchair by a window, morning light through half-closed blinds",
    "a hand reaching for a mug on a nightstand, a single warm bedside lamp glowing",
    "two hands folded on a kitchen table in morning light, one older one younger",
    "a mug of coffee steaming on a windowsill, quiet street visible through the glass",
    "a folded jacket over the back of a kitchen chair, sunrise through the window",
    "a silhouette sitting on a front step at dawn, a mug held in both hands",
]

_NA_STYLE = (
    "oil painting in the american realist tradition of edward hopper and "
    "andrew wyeth, intimate quiet composition, warm golden dawn light, "
    "muted earth tones with charcoal shadows, single directional light source, "
    "grounded and anonymous atmosphere, soft painterly brushwork"
)


def _build_art_prompt_na(quote: str, topic: str,
                         scene_hint: str = None) -> str:
    """NA/AA recovery aesthetic — Hopper/Wyeth quiet, grounded, anonymous.

    If scene_hint is provided (e.g. from Opus's art_scene field), it wins over
    the fallback scene pool — gives content-specific imagery.
    """
    if scene_hint and scene_hint.strip():
        scene = scene_hint.strip()
    else:
        import hashlib
        seed_src = (quote + "|" + topic).encode("utf-8")
        h = int(hashlib.sha256(seed_src).hexdigest()[:8], 16)
        scene = _NA_SCENE_POOL[h % len(_NA_SCENE_POOL)]
    return (
        f"{scene}, "
        f"rule of thirds composition, shallow depth of field, intimate framing, "
        f"{_NA_STYLE}, "
        f"ultra detailed brushwork, cinematic quiet, masterpiece oil painting"
    )


def _build_art_prompt(philosopher: str, quote: str, topic: str,
                      channel_slug: str = None,
                      scene_hint: str = None) -> str:
    """Build a ComfyUI-friendly image generation prompt for a quote.

    Channel-aware:
      - na, aa: Hopper/Wyeth recovery aesthetic (no classical, no costume)
      - everything else: philosopher-flavored classical painting aesthetic

    Structure: SUBJECT first (front-loaded in SDXL for emphasis),
    then composition tokens, then style card, then quality tokens.
    """
    if channel_slug in ("na", "aa"):
        return _build_art_prompt_na(quote, topic, scene_hint=scene_hint)

    style = _get_philosopher_style(philosopher)

    # For shorts we don't have per-chunk narration — use the quote + topic
    # as the scene subject. Keep it concrete, not abstract.
    prompt = (
        f"a single contemplative human figure depicting {topic}, "
        f"period-accurate dress, expressive pose, "
        f"strong compositional silhouette, rule of thirds, shallow depth of field, "
        f"volumetric light, soft rim light, atmospheric perspective, "
        f"{style}, "
        f"ultra detailed, award-winning fine art, masterpiece quality"
    )
    return prompt


# ---------------------------------------------------------------------------
# Working directory management
# ---------------------------------------------------------------------------
def _content_work_dir(content_id: str) -> Path:
    """Create and return a working directory for a content item."""
    work = WORK_DIR / content_id
    work.mkdir(parents=True, exist_ok=True)
    return work


def _slugify_title(title: str, max_len: int = 60) -> str:
    """Convert a title to a filesystem-safe slug."""
    import re
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s[:max_len] or "untitled"


def _final_video_path(channel_slug: str, format_name: str,
                      title: str, content_id: str) -> Path:
    """
    Return a human-readable output path for the final video:
      C:/AI/{channel_slug}/videos/{format}/{format}_{YYYY-MM-DD}_{title-slug}.mp4

    Format is normalized: 'short' -> 'short', 'midform' -> 'midform',
    'longform' -> 'longform', 'story' -> 'story'.
    """
    fmt = format_name if format_name in (
        "short", "midform", "longform", "story", "story_vertical"
    ) else "short"
    out_dir = Path(f"C:/AI/{channel_slug}/videos/{fmt}")
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify_title(title)
    return out_dir / f"{fmt}_{date_str}_{slug}.mp4"


# ---------------------------------------------------------------------------
# Short-form pipeline
# ---------------------------------------------------------------------------
def process_short(content: dict):
    """
    Full pipeline for a single Short video.

    VRAM note: this function runs art generation first, then voice,
    assuming ComfyUI is shut down or idle before Chatterbox starts.
    In the batched flow (main), all art is done before all voice.
    For single-item processing, we still do art-then-voice sequentially.
    """
    content_id = content["id"]
    if _bail_if_deleted(content_id, "process_short"):
        return
    philosopher = content["philosopher"]
    topic = content.get("topic", "life and wisdom")
    content = _ensure_channel_data(content)
    channel = content["channels"]
    channel_name = channel["name"]
    channel_slug = channel["slug"]

    work = _content_work_dir(content_id)
    print(f"\n  Processing short: {philosopher} / {topic} [channel={channel_slug}]")

    # --- Step 1: Quote (+ metadata for recovery channels) ---
    # Recovery channels (NA/AA) use Opus to produce a longer ~40s short with
    # baked-in title/description/tags + a specific art scene. Saves the
    # separate Haiku metadata roundtrip and raises craft quality.
    # Wisdom/Gibran keep the Ollama quote -> Haiku metadata pipeline.
    log_step(content_id, "quote", 1, "running")
    na_art_scene_hint = None
    try:
        if channel_slug in ("na", "aa"):
            from ai_writer import generate_recovery_short_script
            previous = _fetch_recent_quotes(philosopher)
            # 60s target -> ~130-175 words. The ShortVideo QuoteOverlay
            # scrolls long text, so we lean into a monologue-length piece
            # that gives the scroll + fade animation something to do.
            script = generate_recovery_short_script(
                philosopher, topic, channel_slug,
                target_seconds=60, previous_quotes=previous,
            )
            quote = sanitize_quote(script.get("quote", ""))
            title = script.get("title") or f"{philosopher}: {topic[:40]}"
            description = script.get("description", "")
            tags = script.get("tags", [])
            na_art_scene_hint = script.get("art_scene") or None
            print(f"  [quote] Opus {channel_slug} short: {len(quote.split())} words")
        else:
            quote = sanitize_quote(content.get("quote_text") or "")
            if not quote or quote.lower() in ("pending generation", "pending"):
                quote = generate_quote(philosopher, topic)
            try:
                meta = generate_youtube_metadata(philosopher, quote, topic)
                title = meta.get("title", f"{philosopher} on {topic}")
                description = meta.get("description", "")
                tags = meta.get("tags", [])
            except Exception as meta_err:
                print(f"  [meta] Warning: metadata generation failed ({meta_err}), using defaults")
                title = f"{philosopher} on {topic}"
                description = quote
                tags = [philosopher, topic, "philosophy", "wisdom"]
        log_step(content_id, "quote", 1, "success")
        print(f"  [quote] {quote[:80]}...")
    except Exception as e:
        log_step(content_id, "quote", 1, "failed", str(e))
        raise

    # --- Compliance screen — fail fast before TTS/art spend (NA/AA only) ---
    _compliance_screen_or_raise(
        {"quote": quote, "title": title, "description": description},
        channel_slug,
    )

    # --- Step 3: Art ---
    log_step(content_id, "image", 2, "running")
    try:
        art_prompt = _build_art_prompt(philosopher, quote, topic,
                                       channel_slug=channel_slug,
                                       scene_hint=na_art_scene_hint)
        # None/empty lora => raw SDXL (no LoRA) path in _build_comfyui_workflow.
        lora = PERSONA_TO_LORA.get(philosopher) or CHANNEL_DEFAULT_LORA.get(channel_slug)
        art_path = str(work / "art.png")
        generate_art(art_prompt, lora, 832, 1216, art_path)
        log_step(content_id, "image", 2, "success")
    except Exception as e:
        log_step(content_id, "image", 2, "failed", str(e))
        raise

    # --- Step 4: Voice ---
    # slow_factor=0.88 = 12% slowdown via ffmpeg atempo. Tuned for the
    # Wisdom James Burton voice; the NA/AA recovery archetypes already
    # speak conversationally, and stretching their EL output produces
    # ~1.8s pauses on commas/periods (esp. on scripts with single-word
    # fragments like "Joy. Surprise."). Gate to wisdom only.
    short_slow_factor = 0.88 if channel_slug == "wisdom" else 1.0
    log_step(content_id, "voice", 3, "running")
    try:
        voice_path = str(work / "voice.wav")
        generate_voice(quote, voice_path, channel_slug=channel_slug,
                       philosopher=philosopher, slow_factor=short_slow_factor)
        log_step(content_id, "voice", 3, "success")
    except Exception as e:
        log_step(content_id, "voice", 3, "failed", str(e))
        raise

    # --- Step 5: Music ---
    music_path = pick_music(philosopher, channel_slug=channel_slug)
    music_style = PERSONA_TO_MUSIC_STYLE.get(philosopher) or CHANNEL_DEFAULT_MUSIC_STYLE.get(channel_slug, "stoic_classical")
    eq_color = EQUALIZER_COLORS.get(music_style, "#D4AF37")

    # --- Step 6: Assemble video ---
    log_step(content_id, "video", 4, "running")
    try:
        video_path = str(_final_video_path(channel_slug, "short", title, content_id))
        render_remotion_video(
            quotes=[quote],
            philosopher=philosopher,
            art_paths=[art_path],
            voice_paths=[voice_path],
            music_path=music_path,
            output_path=video_path,
            format="short",
            channel_slug=channel_slug,
            title=title,
            equalizer_color=eq_color,
            watermark=watermark_for_channel(channel_slug),
        )
        log_step(content_id, "video", 4, "success")
    except Exception as e:
        log_step(content_id, "video", 4, "failed", str(e))
        raise

    # --- Step 7: Upload to Supabase Storage (primary) + Google Drive (fallback) ---
    drive_url = None
    video_storage_path = None
    log_step(content_id, "upload", 5, "running")
    try:
        from supabase_storage import upload_to_storage
        video_storage_path = upload_to_storage(video_path, "wisdom-videos", channel_slug, "short")
        print(f"  [upload] Storage path: {video_storage_path}")
        log_step(content_id, "upload", 5, "success")
    except Exception as e:
        print(f"  [upload] Storage failed, trying Drive: {e}")
        try:
            if channel.get("google_drive_folder_id"):
                drive_url = upload_to_drive(video_path, channel)
                print(f"  [upload] Drive URL: {drive_url}")
            log_step(content_id, "upload", 5, "success")
        except Exception as e2:
            log_step(content_id, "upload", 5, "failed", str(e2))
            print(f"  [upload] WARNING: Both uploads failed: {e2}")

    # --- Step 7b: Generate thumbnail ---
    thumb_drive_url = None
    thumb_storage_path = None
    try:
        from thumbnail_generator import generate_thumbnail, generate_thumbnail_from_video
        thumb_path = video_path.replace(".mp4", "_thumb.jpg")
        if art_path:
            generate_thumbnail(art_path, title, thumb_path, 1080, 1920)  # portrait for shorts
        else:
            generate_thumbnail_from_video(video_path, title, thumb_path, 1080, 1920)
        try:
            from supabase_storage import upload_to_storage as upload_thumb
            thumb_storage_path = upload_thumb(thumb_path, "wisdom-thumbnails", channel_slug, "short")
        except Exception as ts_e:
            print(f"  [thumb] Storage upload failed: {ts_e}")
            if channel.get("google_drive_folder_id") and drive_url:
                thumb_drive_url = upload_to_drive(thumb_path, channel)
        print(f"  [thumb] {thumb_path}")
    except Exception as e:
        print(f"  [thumb] WARNING: {e}")

    # --- Step 8: Update Supabase ---
    # status='ready' means generation is complete; human approves via dashboard
    # to advance to 'approved', which triggers youtube_uploader.py.
    updates = {
        "status": "ready",
        "quote_text": quote,
        "title": title,
        "description": description,
        "local_machine_path": video_path,
        "generation_params": {
            "lora": lora,
            "art_prompt": art_prompt,
            "voice_settings": {"provider": "elevenlabs"},
            "music_track": Path(music_path).name,
            "renderer": "remotion",
            "tags": tags,
        },
    }
    if video_storage_path:
        updates["video_storage_path"] = video_storage_path
    if thumb_storage_path:
        updates["thumbnail_storage_path"] = thumb_storage_path
    if drive_url:
        updates["video_drive_url"] = drive_url
    if thumb_drive_url:
        updates["thumbnail_drive_url"] = thumb_drive_url
    update_supabase(content_id, updates)

    print(f"  DONE: {content_id} -> {video_path}")
    return video_path


# ---------------------------------------------------------------------------
# Mid-form pipeline
# ---------------------------------------------------------------------------
def process_midform(content: dict):
    """
    Full pipeline for a midform (multi-quote, landscape) video.
    Similar to short but generates multiple quotes + art pieces.
    """
    content_id = content["id"]
    if _bail_if_deleted(content_id, "process_midform"):
        return
    philosopher = content["philosopher"]
    topic = content.get("topic", "life and wisdom")
    content = _ensure_channel_data(content)
    channel = content["channels"]
    channel_name = channel["name"]
    channel_slug = channel["slug"]

    work = _content_work_dir(content_id)
    num_quotes = 4
    print(f"\n  Processing midform: {philosopher} / {topic} ({num_quotes} quotes) [channel={channel_slug}]")

    # --- Step 1: Quotes (with dedup) ---
    log_step(content_id, "quote", 1, "running")
    try:
        previous = _fetch_recent_quotes(philosopher)
        # Recovery channels (NA/AA) repurpose the midform slot as a single-
        # narration "daily meditation" instead of 4-quote bridges.
        if channel_slug in ("na", "aa"):
            from ai_writer import generate_daily_meditation_script
            script = generate_daily_meditation_script(
                philosopher, topic, channel_slug, previous_topics=previous
            )
        else:
            from ai_writer import generate_midform_script
            script = generate_midform_script(philosopher, topic, num_quotes=num_quotes,
                                             previous_quotes=previous)
        quotes = script.get("quotes", [])
        narration_segments = script.get("narration_segments", [])
        if not quotes:
            raise ValueError("Midform script returned no quotes")
        log_step(content_id, "quote", 1, "success")
        for i, q in enumerate(quotes):
            print(f"  [quote {i+1}] {q[:60]}...")
    except Exception as e:
        log_step(content_id, "quote", 1, "failed", str(e))
        raise

    # --- Compliance screen — fail fast before TTS/art spend (NA/AA only) ---
    _compliance_fields = {f"quote_{i}": q for i, q in enumerate(quotes)}
    _compliance_fields.update({f"narration_{i}": n for i, n in enumerate(narration_segments) if n})
    _compliance_screen_or_raise(_compliance_fields, channel_slug)

    # --- Step 2: Art (one per quote) ---
    log_step(content_id, "image", 2, "running")
    try:
        # None/empty lora => raw SDXL (no LoRA) path in _build_comfyui_workflow.
        lora = PERSONA_TO_LORA.get(philosopher) or CHANNEL_DEFAULT_LORA.get(channel_slug)
        art_paths = []
        art_prompts = script.get("art_prompts", [])
        for i, quote in enumerate(quotes):
            # Use script-provided art prompt if available, else build one
            if i < len(art_prompts) and art_prompts[i]:
                art_prompt = art_prompts[i]
            else:
                art_prompt = _build_art_prompt(philosopher, quote, topic, channel_slug=channel_slug)
            art_path = str(work / f"art_{i}.png")
            generate_art(art_prompt, lora, 1216, 832, art_path)  # landscape
            art_paths.append(art_path)
        log_step(content_id, "image", 2, "success")
    except Exception as e:
        log_step(content_id, "image", 2, "failed", str(e))
        raise

    # --- Step 3: Voice (narration + quote per section) ---
    log_step(content_id, "voice", 3, "running")
    try:
        voice_paths = []
        for i, quote in enumerate(quotes):
            # Combine narration bridge + quote into one spoken section
            narration = ""
            if i < len(narration_segments) and narration_segments[i]:
                narration = narration_segments[i].strip() + " "
            full_text = narration + quote
            voice_path = str(work / f"voice_{i}.wav")
            generate_voice(full_text, voice_path, channel_slug=channel_slug,
                           philosopher=philosopher)
            voice_paths.append(voice_path)
            if narration:
                print(f"  [voice {i}] narration ({len(narration)}ch) + quote ({len(quote)}ch)")
        log_step(content_id, "voice", 3, "success")
    except Exception as e:
        log_step(content_id, "voice", 3, "failed", str(e))
        raise

    # --- Step 4: Music ---
    music_path = pick_music(philosopher, channel_slug=channel_slug)
    music_style = PERSONA_TO_MUSIC_STYLE.get(philosopher) or CHANNEL_DEFAULT_MUSIC_STYLE.get(channel_slug, "stoic_classical")
    eq_color = EQUALIZER_COLORS.get(music_style, "#D4AF37")

    # --- Step 5: Assemble ---
    log_step(content_id, "video", 4, "running")
    try:
        midform_title = script.get("title", f"{philosopher} on {topic}")
        video_path = str(_final_video_path(channel_slug, "midform", midform_title, content_id))
        render_remotion_video(
            quotes=quotes,
            philosopher=philosopher,
            art_paths=art_paths,
            voice_paths=voice_paths,
            music_path=music_path,
            output_path=video_path,
            format="midform",
            channel_slug=channel_slug,
            title=midform_title,
            narration_segments=narration_segments,
            equalizer_color=eq_color,
            watermark=watermark_for_channel(channel_slug),
        )
        log_step(content_id, "video", 4, "success")
    except Exception as e:
        log_step(content_id, "video", 4, "failed", str(e))
        raise

    # --- Step 6: Upload to Supabase Storage (primary) + Drive (fallback) ---
    drive_url = None
    video_storage_path = None
    log_step(content_id, "upload", 5, "running")
    try:
        from supabase_storage import upload_to_storage
        video_storage_path = upload_to_storage(video_path, "wisdom-videos", channel_slug, "midform")
        print(f"  [upload] Storage path: {video_storage_path}")
        log_step(content_id, "upload", 5, "success")
    except Exception as e:
        print(f"  [upload] Storage failed, trying Drive: {e}")
        try:
            if channel.get("google_drive_folder_id"):
                drive_url = upload_to_drive(video_path, channel)
                print(f"  [upload] Drive URL: {drive_url}")
            log_step(content_id, "upload", 5, "success")
        except Exception as e2:
            log_step(content_id, "upload", 5, "failed", str(e2))
            print(f"  [upload] WARNING: Both uploads failed: {e2}")

    # --- Step 7: Update Supabase ---
    # Generate/refresh YouTube metadata from ai_writer for best SEO
    try:
        yt_meta = generate_youtube_metadata(philosopher, quotes[0], topic)
        title = yt_meta.get("title") or script.get("title", f"{philosopher} on {topic}")
        description = yt_meta.get("description") or script.get("description", "")
        tags = yt_meta.get("tags") or script.get("tags", [])
    except Exception as _yt_meta_err:
        print(f"  [meta] Warning: YouTube metadata gen failed ({_yt_meta_err}), using script values")
        title = script.get("title", f"{philosopher} on {topic}")
        description = script.get("description", "")
        tags = script.get("tags", [])

    # status='ready' — awaiting human approval in dashboard before YouTube upload
    updates = {
        "status": "ready",
        "quote_text": " | ".join(quotes),
        "title": title,
        "description": description,
        "local_machine_path": video_path,
        "generation_params": {
            "lora": lora,
            "quotes": quotes,
            "voice_settings": {"provider": "elevenlabs"},
            "music_track": Path(music_path).name,
            "renderer": "remotion",
            "tags": tags,
        },
    }
    if video_storage_path:
        updates["video_storage_path"] = video_storage_path
    if drive_url:
        updates["video_drive_url"] = drive_url
    update_supabase(content_id, updates)

    print(f"  DONE: {content_id} -> {video_path}")
    return video_path


# ---------------------------------------------------------------------------
# Batched processing (VRAM-aware)
# ---------------------------------------------------------------------------
def _batch_process(items: list):
    """
    Process a list of content items with VRAM-aware batching:
    1. Generate all art first (ComfyUI uses GPU)
    2. Generate all voice next (Chatterbox uses GPU)
    3. Assemble + upload (CPU-only)

    This avoids running ComfyUI and Chatterbox simultaneously.
    """
    if not items:
        return

    results = {}  # content_id -> {art_paths, voice_paths, quote, ...}

    # ---------------------------------------------------------------
    # Phase 1: Quotes + Metadata (CPU/network, no GPU)
    # ---------------------------------------------------------------
    print("\n=== PHASE 1: Quote & Metadata Generation ===")
    for content in items:
        cid = content["id"]
        philosopher = content["philosopher"]
        topic = content.get("topic", "life and wisdom")
        content_type = content.get("format", "short")
        work = _content_work_dir(cid)

        try:
            content = _ensure_channel_data(content)
            log_step(cid, "quote", 1, "running")

            previous = _fetch_recent_quotes(philosopher)

            if content_type == "short":
                _slug = content["channels"]["slug"]
                recovery_script = None
                if _slug in ("na", "aa"):
                    # NA/AA shorts use the Opus-grade recovery writer aiming at
                    # ~60s of narration (130-175 words). This is the path the
                    # content_poller actually hits — the identical block in
                    # process_short() is only reached when orchestrator is
                    # invoked with CLI args, not via the poller's batch path.
                    from ai_writer import generate_recovery_short_script
                    recovery_script = generate_recovery_short_script(
                        philosopher, topic, _slug,
                        target_seconds=60, previous_quotes=previous,
                    )
                    quote = sanitize_quote(recovery_script.get("quote", ""))
                    print(f"  [{cid[:8]}] [quote] Opus {_slug} short: {len(quote.split())} words")
                else:
                    quote = sanitize_quote(content.get("quote_text") or "")
                    if not quote or quote.lower() in ("pending generation", "pending"):
                        quote = generate_quote(philosopher, topic)
                quotes = [quote]
                narration_segments = []
                art_prompt_base = _build_art_prompt(
                    philosopher, quote, topic,
                    channel_slug=_slug,
                    scene_hint=(recovery_script or {}).get("art_scene"),
                )
                art_prompts = [art_prompt_base]
                # Stash the Opus-provided metadata so the later results dict
                # can forward title/description/tags into the Supabase update.
                if recovery_script:
                    results.setdefault(cid, {})["recovery_script"] = recovery_script
            else:
                # Recovery channels use daily-meditation writer in the midform slot
                _slug = content["channels"]["slug"]
                if _slug in ("na", "aa"):
                    from ai_writer import generate_daily_meditation_script
                    script = generate_daily_meditation_script(
                        philosopher, topic, _slug, previous_topics=previous
                    )
                else:
                    from ai_writer import generate_midform_script
                    script = generate_midform_script(philosopher, topic,
                                                     previous_quotes=previous)
                quotes = script.get("quotes", [])
                narration_segments = script.get("narration_segments", [])
                art_prompts = script.get("art_prompts", [])
                if not quotes:
                    raise ValueError("Script returned no quotes")

            log_step(cid, "quote", 1, "success")

            # Compliance screen — fail fast before TTS/art spend (NA/AA only)
            _comp_fields = {f"quote_{i}": q for i, q in enumerate(quotes)}
            _comp_fields.update({f"narration_{i}": n for i, n in enumerate(narration_segments) if n})
            _compliance_screen_or_raise(_comp_fields, content["channels"]["slug"])

            results[cid] = {
                "quotes": quotes,
                "narration_segments": narration_segments,
                "art_prompts": art_prompts,
                "content": content,
                "work": work,
            }
            print(f"  [{cid[:8]}] {len(quotes)} quote(s) ready")

        except Exception as e:
            log_step(cid, "quote", 1, "failed", str(e))
            mark_failed(cid, f"quote step: {e}")
            print(f"  [{cid[:8]}] FAILED at quote: {e}")

    # ---------------------------------------------------------------
    # Phase 2: Art generation (GPU - ComfyUI)
    # ---------------------------------------------------------------
    print("\n=== PHASE 2: Art Generation (ComfyUI) ===")
    for cid, data in list(results.items()):
        content = data["content"]
        philosopher = content["philosopher"]
        channel_slug = content["channels"]["slug"]
        # None/empty lora => raw SDXL (no LoRA) path in _build_comfyui_workflow.
        lora = PERSONA_TO_LORA.get(philosopher) or CHANNEL_DEFAULT_LORA.get(channel_slug)
        content_type = content.get("format", "short")
        work = data["work"]

        # Determine dimensions based on format
        if content_type == "short":
            art_w, art_h = 832, 1216
        else:
            art_w, art_h = 1216, 832

        try:
            log_step(cid, "image", 2, "running")
            art_paths = []
            for i, quote in enumerate(data["quotes"]):
                if i < len(data["art_prompts"]) and data["art_prompts"][i]:
                    prompt = data["art_prompts"][i]
                else:
                    prompt = _build_art_prompt(
                        philosopher, quote,
                        content.get("topic", "life and wisdom"),
                        channel_slug=content["channels"]["slug"],
                    )
                art_path = str(work / f"art_{i}.png")
                generate_art(prompt, lora, art_w, art_h, art_path)
                art_paths.append(art_path)

            data["art_paths"] = art_paths
            data["lora"] = lora
            log_step(cid, "image", 2, "success")
            print(f"  [{cid[:8]}] {len(art_paths)} image(s) generated")

        except Exception as e:
            log_step(cid, "image", 2, "failed", str(e))
            mark_failed(cid, f"image step: {e}")
            del results[cid]
            print(f"  [{cid[:8]}] FAILED at art: {e}")

    # ---------------------------------------------------------------
    # Phase 3: Voice generation (ElevenLabs)
    # ---------------------------------------------------------------
    print("\n=== PHASE 3: Voice Generation (ElevenLabs) ===")
    for cid, data in list(results.items()):
        content = data["content"]
        channel = content["channels"]
        channel_slug = channel["slug"]
        work = data["work"]
        fmt = content.get("format", "short")
        # Shorts need a 12% slowdown so the narration doesn't feel rushed.
        # Midform/longform handle their own pacing. Wisdom-only — NA/AA
        # archetype voices are already conversational and stretching them
        # produces ~1.8s pauses on commas/periods.
        slow_factor = 0.88 if (fmt == "short" and channel_slug == "wisdom") else 1.0

        try:
            log_step(cid, "voice", 3, "running")
            voice_paths = []
            narration_segments = data.get("narration_segments", [])
            for i, quote in enumerate(data["quotes"]):
                # Combine narration bridge + quote for midform
                narration = ""
                if i < len(narration_segments) and narration_segments[i]:
                    narration = narration_segments[i].strip() + " "
                full_text = narration + quote
                voice_path = str(work / f"voice_{i}.wav")
                generate_voice(full_text, voice_path,
                               channel_slug=channel_slug,
                               philosopher=philosopher,
                               slow_factor=slow_factor)
                voice_paths.append(voice_path)

            data["voice_paths"] = voice_paths
            data["voice_settings"] = {"provider": "elevenlabs"}
            log_step(cid, "voice", 3, "success")
            print(f"  [{cid[:8]}] {len(voice_paths)} voice clip(s) generated")

        except Exception as e:
            log_step(cid, "voice", 3, "failed", str(e))
            mark_failed(cid, f"voice step: {e}")
            del results[cid]
            print(f"  [{cid[:8]}] FAILED at voice: {e}")

    # ---------------------------------------------------------------
    # Phase 4: Assembly + Upload (CPU)
    # ---------------------------------------------------------------
    print("\n=== PHASE 4: Assembly & Upload ===")
    for cid, data in results.items():
        content = data["content"]
        philosopher = content["philosopher"]
        topic = content.get("topic", "life and wisdom")
        content_type = content.get("format", "short")
        channel = content["channels"]
        channel_name = channel["name"]
        channel_slug = channel["slug"]
        work = data["work"]
        quotes = data["quotes"]

        try:
            # Music
            music_path = pick_music(philosopher, channel_slug=channel_slug)
            music_style = PERSONA_TO_MUSIC_STYLE.get(philosopher) or CHANNEL_DEFAULT_MUSIC_STYLE.get(channel_slug, "stoic_classical")
            eq_color = EQUALIZER_COLORS.get(music_style, "#D4AF37")

            # Assembly
            log_step(cid, "video", 4, "running")

            vid_format = "short" if content_type == "short" else "midform"
            batch_title = content.get("title", f"{philosopher} on {topic}")
            video_path = str(_final_video_path(channel_slug, vid_format, batch_title, cid))
            render_remotion_video(
                quotes=quotes,
                philosopher=philosopher,
                art_paths=data["art_paths"],
                voice_paths=data["voice_paths"],
                music_path=music_path,
                output_path=video_path,
                format=vid_format,
                channel_slug=channel_slug,
                title=batch_title,
                narration_segments=data.get("narration_segments"),
                equalizer_color=eq_color,
                watermark=watermark_for_channel(channel_slug),
            )
            log_step(cid, "video", 4, "success")

            # Upload to Supabase Storage (primary) + Drive (fallback).
            #
            # Persist video_storage_path to the content row IMMEDIATELY after
            # a successful upload — not at the end of the batch — so a later
            # crash or SIGTERM can't orphan the upload. Previously the path
            # was only written in the final update_supabase(cid, updates) call
            # ~80 lines below, and any death in that window left ready rows
            # with NULL storage paths (invisible on the review page).
            drive_url = None
            video_storage_path = None
            log_step(cid, "upload", 5, "running")
            try:
                from supabase_storage import upload_to_storage
                video_storage_path = upload_to_storage(video_path, "wisdom-videos", channel_slug, vid_format)
                if not video_storage_path:
                    raise RuntimeError("upload_to_storage returned empty path")
                update_supabase(cid, {
                    "video_storage_path": video_storage_path,
                    "local_machine_path": video_path,
                })
                print(f"  [{cid[:8]}] Storage: {video_storage_path}")
                log_step(cid, "upload", 5, "success")
            except Exception as e:
                log_step(cid, "upload", 5, "failed", str(e))
                print(f"  [{cid[:8]}] Storage failed, trying Drive: {e}")
                try:
                    if channel.get("google_drive_folder_id"):
                        drive_url = upload_to_drive(video_path, channel)
                        update_supabase(cid, {
                            "video_drive_url": drive_url,
                            "local_machine_path": video_path,
                        })
                        print(f"  [{cid[:8]}] Drive: {drive_url}")
                except Exception as e2:
                    print(f"  [{cid[:8]}] Upload warning: {e2}")

            # YouTube metadata — for NA/AA shorts, reuse the title/description/
            # tags baked into the Opus recovery script (no extra Haiku round-
            # trip). For other channels, run generate_youtube_metadata.
            recovery_script = data.get("recovery_script")
            if recovery_script:
                title = recovery_script.get("title") or f"{philosopher} on {topic}"
                description = recovery_script.get("description", "")
                tags = recovery_script.get("tags", []) or []
            else:
                try:
                    meta = generate_youtube_metadata(philosopher, quotes[0], topic)
                    title = meta.get("title") or f"{philosopher} on {topic}"
                    description = meta.get("description", "")
                    tags = meta.get("tags", [])
                except Exception as _meta_e:
                    print(f"  [{cid[:8]}] Warning: metadata gen failed ({_meta_e}), using defaults")
                    title = f"{philosopher} on {topic}"
                    description = quotes[0]
                    tags = [philosopher, topic, "philosophy"]

            # Thumbnail — same persist-immediately pattern as video upload.
            thumb_drive_url = None
            thumb_storage_path = None
            try:
                from thumbnail_generator import generate_thumbnail, generate_thumbnail_from_video
                thumb_path = video_path.replace(".mp4", "_thumb.jpg")
                first_art = data["art_paths"][0] if data.get("art_paths") else None
                if first_art and Path(first_art).exists():
                    tw, th = (1080, 1920) if content_type == "short" else (1920, 1080)
                    generate_thumbnail(first_art, title, thumb_path, tw, th)
                else:
                    generate_thumbnail_from_video(video_path, title, thumb_path, 1080, 1920)
                try:
                    from supabase_storage import upload_to_storage as upload_thumb
                    thumb_storage_path = upload_thumb(thumb_path, "wisdom-thumbnails", channel_slug, vid_format)
                    if thumb_storage_path:
                        update_supabase(cid, {"thumbnail_storage_path": thumb_storage_path})
                except Exception as ts_e:
                    print(f"  [{cid[:8]}] Thumb storage failed: {ts_e}")
                    if channel.get("google_drive_folder_id") and drive_url:
                        thumb_drive_url = upload_to_drive(thumb_path, channel)
                        update_supabase(cid, {"thumbnail_drive_url": thumb_drive_url})
                print(f"  [{cid[:8]}] Thumbnail: {thumb_path}")
            except Exception as thumb_e:
                print(f"  [{cid[:8]}] Thumbnail warning: {thumb_e}")

            # Final metadata update — status='ready' signals generation is
            # done; human approves in dashboard → status becomes 'approved'
            # → content_poller triggers youtube_uploader.py automatically.
            #
            # Invariant: a row cannot flip to 'ready' without a persisted
            # video URL (storage path or drive url). Both fields were
            # already persisted above via their own update_supabase calls,
            # so by this point the URL is either saved or the row should
            # be marked failed rather than ready.
            if not video_storage_path and not drive_url:
                mark_failed(cid, "upload step: no video URL persisted; "
                                  "render completed but no storage/drive fallback succeeded")
                print(f"  [{cid[:8]}] FAILED: no upload URL after render")
                continue

            updates = {
                "status": "ready",
                "quote_text": " | ".join(quotes) if len(quotes) > 1 else quotes[0],
                "title": title,
                "description": description,
                "generation_params": {
                    "lora": data.get("lora", ""),
                    "voice_settings": data.get("voice_settings", {}),
                    "music_track": Path(music_path).name,
                    "renderer": "remotion",
                    "tags": tags,
                },
            }
            update_supabase(cid, updates)
            print(f"  [{cid[:8]}] DONE -> {video_path}")

        except Exception as e:
            log_step(cid, "video", 4, "failed", str(e))
            mark_failed(cid, f"video step: {e}")
            print(f"  [{cid[:8]}] FAILED at assembly: {e}")


# ---------------------------------------------------------------------------
# Story pipeline delegation
# ---------------------------------------------------------------------------
def _run_gibran_essay_pipeline(content: dict):
    """Delegate Gibran cinematic essay format to generate_gibran_essay.py
    as a subprocess (mirrors the story / meditation delegation pattern).
    Long essays can run 10-20 min of voice + 20-40 SDXL renders, so we
    isolate from the poller process and keep a per-run log file."""
    cid = content["id"]
    if _bail_if_deleted(cid, "_run_gibran_essay_pipeline"):
        return
    print(f"\n--- Gibran Essay Pipeline: {content.get('title', '?')[:60]} ---")
    update_supabase(cid, {"status": "generating"})
    log_step(cid, "video", 0, "running")  # step_order=0 marks outer-pipeline wrapper

    cmd = [
        sys.executable,
        str(Path(__file__).parent / "generate_gibran_essay.py"),
        "--content-id", cid,
    ]
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"essay_{cid[:8]}_{ts}.log"
    print(f"  Subprocess log: {log_file}")

    try:
        with open(log_file, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(
                cmd, stdout=lf, stderr=subprocess.STDOUT, text=True,
                cwd=str(Path(__file__).parent),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            try:
                # 45 min cap — covers a 20-min essay with ~30 SDXL renders.
                returncode = proc.wait(timeout=2700)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                raise

        if returncode != 0:
            tail = ""
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as lf:
                    tail = "".join(lf.readlines()[-25:])[-1500:]
            except Exception:
                pass
            raise RuntimeError(
                f"Gibran essay pipeline failed (exit {returncode}). Tail:\n{tail}"
            )
        log_step(cid, "video", 0, "success")
        print(f"  Gibran essay completed for {cid}")
    except subprocess.TimeoutExpired:
        log_step(cid, "video", 0, "failed", f"Timeout (log: {log_file})")
        mark_failed(cid, f"essay pipeline: timeout (log: {log_file})")
        raise
    except Exception as e:
        log_step(cid, "video", 0, "failed", str(e))
        mark_failed(cid, f"essay pipeline: {e}")
        raise


def _run_meditation_pipeline(content: dict):
    """Delegate story_vertical (Portrait Short) format to
    generate_meditation_short.py as a subprocess.

    See generate_meditation_short.py for the full pipeline. We launch it as a
    subprocess for the same reason as the story pipeline: long-running
    Whisper + SDXL + Remotion calls that we want to keep isolated from the
    poller process and easily diagnosable from a per-run log file.
    """
    cid = content["id"]
    if _bail_if_deleted(cid, "_run_meditation_pipeline"):
        return
    philosopher = content.get("philosopher", "Marcus Aurelius")
    topic = content.get("topic", "life")
    content = _ensure_channel_data(content)
    channel_slug = content["channels"]["slug"]

    print(f"\n--- Meditation Pipeline: {philosopher} on {topic} [channel={channel_slug}] ---")
    update_supabase(cid, {"status": "generating"})
    log_step(cid, "video", 0, "running")  # step_order=0 marks outer-pipeline wrapper

    cmd = [
        sys.executable, str(Path(__file__).parent / "generate_meditation_short.py"),
        "--content-id", cid,
        "--philosopher", philosopher,
        "--topic", topic,
        "--channel-slug", channel_slug,
    ]
    queued_title = content.get("title")
    if queued_title:
        cmd.extend(["--queued-title", queued_title])

    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"meditation_{cid[:8]}_{ts}.log"
    print(f"  Subprocess log: {log_file}")

    try:
        with open(log_file, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(
                cmd, stdout=lf, stderr=subprocess.STDOUT, text=True,
                cwd=str(Path(__file__).parent),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            try:
                returncode = proc.wait(timeout=1500)  # 25 min cap
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                raise

        if returncode != 0:
            tail = ""
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as lf:
                    tail = "".join(lf.readlines()[-25:])[-1500:]
            except Exception:
                pass
            raise RuntimeError(
                f"Meditation pipeline failed (exit {returncode}). Tail:\n{tail}"
            )
        log_step(cid, "video", 0, "success")
        print(f"  Meditation pipeline completed for {cid}")
    except subprocess.TimeoutExpired:
        log_step(cid, "video", 0, "failed", f"Timeout (log: {log_file})")
        mark_failed(cid, f"meditation pipeline: timeout (log: {log_file})")
        raise
    except Exception as e:
        log_step(cid, "video", 0, "failed", str(e))
        mark_failed(cid, f"meditation pipeline: {e}")
        raise


def _run_story_pipeline(content: dict):
    """Delegate story format to generate_story_video.py as a subprocess."""
    cid = content["id"]
    if _bail_if_deleted(cid, "_run_story_pipeline"):
        return
    philosopher = content.get("philosopher", "Marcus Aurelius")
    topic = content.get("topic", "life")
    content = _ensure_channel_data(content)
    channel_slug = content["channels"]["slug"]

    print(f"\n--- Story Pipeline: {philosopher} on {topic} [channel={channel_slug}] ---")
    update_supabase(cid, {"status": "generating"})
    log_step(cid, "story", 1, "running")

    # Pass --content-id and --channel-slug so the child updates the correct
    # row and writes to the correct channel directory — no search, no
    # silent default (that's how Gibran content leaked into wisdom).
    # Pass --queued-title so the generated script delivers on the planned
    # title instead of inventing a new one.
    cmd = [
        sys.executable, str(Path(__file__).parent / "generate_story_video.py"),
        "--philosopher", philosopher,
        "--theme", topic,
        "--content-id", cid,
        "--channel-slug", channel_slug,
    ]
    queued_title = content.get("title")
    if queued_title:
        cmd.extend(["--queued-title", queued_title])

    # Stream stdout/stderr to a per-content log file instead of buffering
    # via capture_output=True. Story pipelines take up to 25 minutes; if
    # they hang, capture_output gives us nothing to debug until the timeout
    # fires. With Popen + tee-to-file we can tail the log live and we also
    # get partial output to include in the error message on failure.
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"story_{cid[:8]}_{ts}.log"
    print(f"  Subprocess log: {log_file}")

    try:
        with open(log_file, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(
                cmd, stdout=lf, stderr=subprocess.STDOUT, text=True,
                cwd=str(Path(__file__).parent),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            try:
                returncode = proc.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                raise

        if returncode != 0:
            tail = ""
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as lf:
                    tail = "".join(lf.readlines()[-20:])[-1000:]
            except Exception:
                pass
            raise RuntimeError(f"Story pipeline failed (exit {returncode}): {tail}")
        log_step(cid, "story", 1, "success")
        print(f"  Story pipeline completed for {cid}")
    except subprocess.TimeoutExpired:
        log_step(cid, "story", 1, "failed", f"Timeout after 30 minutes (log: {log_file})")
        mark_failed(cid, f"story pipeline: timeout after 30 minutes (log: {log_file})")
        raise
    except Exception as e:
        log_step(cid, "story", 1, "failed", str(e))
        mark_failed(cid, f"story pipeline: {e}")
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Wisdom pipeline orchestrator. Processes queued content."
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="Max items to process (0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show queue without processing")
    parser.add_argument("--no-batch", action="store_true",
                        help="Process items one-by-one instead of VRAM-aware batching")
    args = parser.parse_args()

    start = datetime.now()
    print(f"{'='*60}")
    print(f"Wisdom Orchestrator")
    print(f"Started: {start.isoformat()}")
    print(f"{'='*60}")

    # Fetch queue
    try:
        queued = fetch_queued_content()
    except Exception as e:
        print(f"FATAL: Could not fetch queue from Supabase: {e}")
        sys.exit(1)

    if args.limit > 0:
        queued = queued[:args.limit]

    # Gibran-only: gate any non-short rows that haven't picked a format
    # style + duration. Mark them rejected with a clear reason so the
    # dashboard surfaces them as "needs format choice" instead of silently
    # holding them in the queue.
    queued = _apply_gibran_format_gate(queued)

    print(f"Found {len(queued)} queued item(s)")

    if not queued:
        print("Nothing to process. Exiting.")
        return

    # Dry run: just print the queue
    if args.dry_run:
        print(f"\n{'ID':<40} {'Type':<10} {'Philosopher':<25} {'Topic'}")
        print("-" * 100)
        for item in queued:
            print(
                f"{item['id']:<40} "
                f"{item.get('format', '?'):<10} "
                f"{item['philosopher']:<25} "
                f"{item.get('topic', '?')}"
            )
        return

    # Process
    if args.no_batch:
        # Sequential, one-by-one (simpler, no VRAM optimization)
        for content in queued:
            try:
                content_type = content.get("format", "short")
                # story_vertical (Portrait Short, 9:16, ~60s) runs through
                # the standalone meditation pipeline — Opus writes a
                # philosopher-voice mini-narrative, SDXL paints scene-per-art,
                # Whisper aligns word timestamps, Remotion's
                # StoryVerticalVideo composition renders the 9:16 output.
                # NO parent story required — works straight from the queue.
                # 2026-04-18 fix.
                if content_type == "story_vertical":
                    _run_meditation_pipeline(content)
                    print(f"  Done: {content['id']}")
                    continue
                # Gibran cinematic essay (post-format-gate) — routes any
                # Gibran non-short row whose gibran_long_form_style='essay'
                # to the cinematic essay pipeline regardless of nominal
                # format ("story" / "midform" / "longform"). The format
                # column is now a length category; style is the renderer.
                slug = (content.get("channels") or {}).get("slug")
                if slug == "gibran" and content.get("gibran_long_form_style") == "essay":
                    _run_gibran_essay_pipeline(content)
                    print(f"  Done: {content['id']}")
                    continue
                if content_type == "story":
                    _run_story_pipeline(content)
                elif content_type == "short":
                    process_short(content)
                elif content_type in ("longform", "compilation", "midform"):
                    process_midform(content)
                else:
                    # Unknown format — refuse rather than guess. Previously
                    # this defaulted to process_short and silently produced
                    # the wrong artifact for any format we hadn't enumerated.
                    msg = f"unknown format '{content_type}' — refusing to guess pipeline"
                    print(f"  REJECTED: {content['id']} ({msg})")
                    log_step(content["id"], "publish", 0, "failed", msg)
                    update_supabase(content["id"], {
                        "status": "rejected",
                        "rejection_reason": msg,
                    })
                    continue
                print(f"  Done: {content['id']}")
            except Exception as e:
                print(f"  FAILED: {content['id']} - {e}")
                traceback.print_exc()
                log_step(content["id"], "publish", 0, "failed", str(e))
                mark_failed(content["id"], e)
    else:
        # Reject story_vertical up front — needs parent story, not queue path
        # (silently rendered as midform before 2026-04-18).
        story_verticals = [c for c in queued if c.get("format") == "story_vertical"]
        for content in story_verticals:
            msg = ("story_vertical can't be generated from the queue — "
                   "use scripts/generate_story_vertical.py with a parent story")
            print(f"  REJECTED story_vertical: {content['id']}")
            log_step(content["id"], "publish", 0, "failed", msg)
            try:
                update_supabase(content["id"], {
                    "status": "rejected",
                    "rejection_reason": msg,
                })
            except Exception:
                pass

        # Gibran cinematic essays — own subprocess pipeline (mirrors story).
        # Pulled out before the batched path because they run 10-20 min of
        # voice + many SDXL renders; batching with shorts wastes the
        # VRAM-aware optimization.
        gibran_essays = [
            c for c in queued
            if (c.get("channels") or {}).get("slug") == "gibran"
            and c.get("gibran_long_form_style") == "essay"
            and c.get("format") != "short"
        ]
        for content in gibran_essays:
            try:
                _run_gibran_essay_pipeline(content)
                print(f"  Done (gibran-essay): {content['id']}")
            except Exception as e:
                print(f"  FAILED (gibran-essay): {content['id']} - {e}")
                traceback.print_exc()
                mark_failed(content["id"], f"gibran essay: {e}")
        gibran_essay_ids = {c["id"] for c in gibran_essays}

        # Midform (treated as longform — up to 20 min). Gibran essays already
        # handled above; anything else routes to process_midform.
        non_essay_midforms = [
            c for c in queued
            if c.get("format") == "midform"
            and c["id"] not in gibran_essay_ids
        ]
        for content in non_essay_midforms:
            try:
                process_midform(content)
                print(f"  Done (midform): {content['id']}")
            except Exception as e:
                print(f"  FAILED (midform): {content['id']} - {e}")
                traceback.print_exc()
                mark_failed(content["id"], f"midform: {e}")

        # Stories run separately (own pipeline), rest go through batch
        stories = [c for c in queued
                   if c.get("format") == "story"
                   and c["id"] not in gibran_essay_ids]
        non_stories = [
            c for c in queued
            if c.get("format") not in ("story", "midform", "story_vertical")
            and c["id"] not in gibran_essay_ids
        ]
        for content in stories:
            try:
                _run_story_pipeline(content)
                print(f"  Done (story): {content['id']}")
            except Exception as e:
                print(f"  FAILED (story): {content['id']} - {e}")
                traceback.print_exc()
                mark_failed(content["id"], f"story: {e}")
        if non_stories:
            _batch_process(non_stories)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n{'='*60}")
    print(f"Orchestrator finished in {elapsed:.1f}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
