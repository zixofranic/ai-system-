"""
Generate a cinematic essay end-to-end — Custom Prompts + Gibran regular.

Two entry paths converge here:

  1. CUSTOM PROMPTS (any channel: gibran, wisdom, na, aa).
     The user pasted an LLM-written script on the dashboard; the adapter
     preserves the prose VERBATIM (no LLM rewrite), splits on blank lines
     into scenes, auto-generates art directions from each scene's opening
     words, and runs it through the cinematic pipeline. Routed here when
     `generation_params.is_custom_script = true`.

  2. GIBRAN REGULAR ESSAY (gibran only).
     The non-custom Gibran flow where Opus writes the essay from corpus
     passages. Routed here when `channels.slug = 'gibran' AND
     gibran_long_form_style = 'essay'` AND NOT `is_custom_script`.

Aspect is chosen by `target_seconds`:
  - <= 180s → PORTRAIT (9:16), output format=story_vertical
  -  > 180s → LANDSCAPE (16:9), output format=story

Invoked by orchestrator's `_run_custom_prompt_pipeline` as a subprocess
(mirrors the existing story / meditation pipeline pattern). Can also be
run directly:

    python generate_custom_prompt_essay.py --content-id <uuid>
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")
sys.path.insert(0, "C:/AI/system/scripts")

from cinematic_pipeline import LANDSCAPE, PORTRAIT, render_cinematic_essay
from orchestrator import (
    EQUALIZER_COLORS,
    PERSONA_TO_MUSIC_STYLE,
    CHANNEL_DEFAULT_MUSIC_STYLE,
    _final_video_path,
    _fetch_recent_quotes,
    _resolve_gibran_choice,
    log_step,
    update_supabase,
    watermark_for_channel,
)
from supabase_storage import upload_to_storage
from thumbnail_generator import generate_thumbnail

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# Channels currently supported end-to-end by this pipeline.
# Gibran has regular-essay + custom-prompt paths; others are custom-only
# today — Wisdom/NA/AA do not yet have channel-specific cinematic writers.
SUPPORTED_CHANNELS = ("gibran", "wisdom", "na", "aa")

# Fallback display names for non-Gibran channels when the row doesn't set
# `philosopher`. Wisdom's `philosopher` is usually set by the dashboard
# (Marcus, Seneca, etc.); for NA/AA the row should carry the archetype.
# This only kicks in if the insert path forgot to set it — safety net.
CHANNEL_DEFAULT_PHILOSOPHER = {
    "gibran": "Gibran",
    "wisdom": "Marcus Aurelius",
    "na":     "The Old-Timer",
    "aa":     "The Old-Timer",
}


def _fetch(content_id: str) -> dict:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}"
        f"&select=*,channels:channel_id(id,name,slug,settings)",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise SystemExit(f"content {content_id} not found")
    return rows[0]


def _build_script_from_custom_prompt(
    custom_text: str, target_seconds: int, title: str = None
) -> dict:
    """Format adapter for the Custom Prompts dashboard tile.

    Takes raw essay text pasted from ChatGPT/another LLM and shapes it into
    the {scenes:[{narration, direction}]} schema cinematic_pipeline expects.
    NO LLM is called — the user's prose is preserved byte-for-byte. Only the
    art directions are auto-generated, anchored to each scene's opening
    words so SDXL paints something topical without any interpreter changing
    the text.
    """
    import re
    text = custom_text.replace("\r\n", "\n").strip()
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        raise ValueError("custom_prompt_source has no readable paragraphs")

    # If the user pasted one big block with no blank lines, slice into ~N
    # scenes by sentence so SDXL has separate stills to work with.
    if len(paragraphs) == 1:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", paragraphs[0]) if s.strip()]
        target_scene_count = max(3, min(20, round(target_seconds / 35)))
        per = max(1, (len(sentences) + target_scene_count - 1) // target_scene_count)
        paragraphs = [" ".join(sentences[i:i + per]) for i in range(0, len(sentences), per)]

    # Cap at 40 scenes — merge adjacent paragraphs if the paste was very dense.
    # Join with "\n\n" so the user's paragraph breaks survive in the merged
    # narration block (single-space join would silently flatten them).
    if len(paragraphs) > 40:
        chunk = (len(paragraphs) + 39) // 40
        paragraphs = [
            "\n\n".join(paragraphs[i:i + chunk])
            for i in range(0, len(paragraphs), chunk)
        ]

    scenes = []
    for narration in paragraphs:
        anchor = " ".join(narration.split()[:12])
        direction = (
            f"Cinematic illustration evoking: {anchor}. "
            f"gibran_style, sepia and warm tones, poetic atmosphere, "
            f"painterly detail, soft golden light."
        )
        scenes.append({"narration": narration, "direction": direction})

    return {
        "title": title or "Khalil Gibran",
        "scenes": scenes,
        "description": "",
        "closing_attribution": "Inspired by Khalil Gibran",
        "tags": ["gibran", "wisdom", "custom-prompt"],
    }


def _resolve_target_seconds(row: dict) -> int:
    """Target duration — `gibran_target_seconds` (Gibran-only column) first,
    then `generation_params.target_seconds` (written by Custom Prompts
    server action for all channels).
    """
    s = row.get("gibran_target_seconds")
    if isinstance(s, int) and s > 0:
        return s
    gp = row.get("generation_params") or {}
    s = gp.get("target_seconds")
    if isinstance(s, int) and s > 0:
        return s
    return None


def _resolve_equalizer_color(philosopher: str, channel_slug: str) -> str:
    """Pick the equalizer color from the same mapping the shorts pipeline
    uses. Falls back to the Gibran terracotta if the channel's music style
    isn't in the EQ table.
    """
    music_style = (PERSONA_TO_MUSIC_STYLE.get(philosopher)
                   or CHANNEL_DEFAULT_MUSIC_STYLE.get(channel_slug)
                   or "stoic_classical")
    # #C2603C is the render_cinematic_essay default (Gibran terracotta).
    return EQUALIZER_COLORS.get(music_style, "#C2603C")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content-id", required=True)
    args = ap.parse_args()

    row = _fetch(args.content_id)
    cid = row["id"]

    slug = (row.get("channels") or {}).get("slug")
    if slug not in SUPPORTED_CHANNELS:
        raise SystemExit(
            f"Refusing — channel='{slug}' not supported by cinematic essay "
            f"pipeline. Supported: {SUPPORTED_CHANNELS}"
        )

    gp = row.get("generation_params") or {}
    is_custom_script = bool(gp.get("is_custom_script")
                             or gp.get("custom_prompt_source"))
    custom_prompt = gp.get("custom_prompt_source")

    # ---- Gate by mode ----
    if is_custom_script:
        # Custom Prompts — any supported channel. Duration comes from the
        # dashboard's preset; aspect is inferred below.
        target_seconds = _resolve_target_seconds(row)
        if not target_seconds:
            raise SystemExit(
                "Custom script row missing target_seconds in both "
                "gibran_target_seconds and generation_params.target_seconds"
            )
        essay_voice = None  # unused — prose is verbatim from the paste
    elif slug == "gibran":
        # Gibran regular essay — re-validate the format gate (orchestrator
        # already checked, but a direct CLI call should fail here too).
        style, target_seconds, err = _resolve_gibran_choice(row)
        if err:
            raise SystemExit(f"Format gate: {err}")
        if style != "essay":
            raise SystemExit(
                f"Refusing — Gibran non-custom path handles 'essay' only, got '{style}'"
            )
        # Writing style (new column) takes precedence; fall back to legacy
        # gibran_essay_voice. Mapping:
        #   writing_style       gibran_essay_voice
        #   ──────────────      ──────────────────
        #   narrator       <->  narrator
        #   in_character   <->  prophet_voice
        # The Gibran writer still takes the legacy vocabulary.
        new_style = row.get("writing_style")
        if new_style == "narrator":
            essay_voice = "narrator"
        elif new_style == "in_character":
            essay_voice = "prophet_voice"
        else:
            essay_voice = row.get("gibran_essay_voice") or "narrator"
        if essay_voice not in ("narrator", "prophet_voice"):
            raise SystemExit(
                f"Invalid essay voice='{essay_voice}'; "
                f"must be 'narrator' or 'prophet_voice'"
            )
    else:
        raise SystemExit(
            f"Refusing — channel='{slug}' requires is_custom_script=true "
            f"or custom_prompt_source (no channel-specific cinematic writer "
            f"exists for non-Gibran non-custom rows yet)."
        )

    # ---- Resolve channel/persona/philosopher ----
    philosopher = (row.get("philosopher")
                   or CHANNEL_DEFAULT_PHILOSOPHER.get(slug, "Gibran"))
    topic = row.get("topic") or row.get("title") or "life"
    queued_title = row.get("title")

    # ---- Aspect ----
    # Default: infer from duration (2-3 min → PORTRAIT, 10-20 min → LANDSCAPE).
    # Override: generation_params.force_aspect ('landscape'|'portrait')
    # is set by the Gibran Generate modal so the user can lock a 2-3 min
    # midform to landscape (cinematic intro + per-scene art look).
    forced = (gp.get("force_aspect") or "").strip().lower()
    if forced == "landscape":
        is_portrait = False
    elif forced == "portrait":
        is_portrait = True
    else:
        is_portrait = target_seconds <= 180
    art_aspect = PORTRAIT if is_portrait else LANDSCAPE
    render_format = "story_vertical" if is_portrait else "story"

    print(f"\n{'='*70}")
    print(f"  Cinematic essay — {slug}/{render_format}")
    print(f"  content_id   : {cid}")
    print(f"  philosopher  : {philosopher}")
    print(f"  topic        : {topic}")
    print(f"  target       : {target_seconds}s ({target_seconds/60:.1f} min)")
    print(f"  aspect       : {'9:16 PORTRAIT' if is_portrait else '16:9 LANDSCAPE'}")
    print(f"  mode         : {'custom_script' if is_custom_script else 'gibran_essay'}")
    if essay_voice:
        print(f"  essay voice  : {essay_voice}")
    if queued_title:
        print(f"  queued title : {queued_title}")
    print(f"{'='*70}\n")

    update_supabase(cid, {"status": "generating"})
    # generation_log.step has a CHECK constraint allowing only specific
    # values (essay, image, meditation, publish, quote, story, upload,
    # video, voice). The writer step uses "quote" — same as every other
    # pipeline's writer step.
    log_step(cid, "quote", 1, "running")

    if is_custom_script:
        # Python-side compliance backstop for NA/AA — the dashboard server
        # action runs a substring check too, but that mirror only covers
        # the obvious cases. compliance_filter has the full corpus + fuzzy
        # matching, so re-run here before any pipeline spend on a row that
        # somehow slipped past the dashboard guard.
        if slug in ("na", "aa"):
            try:
                from compliance_filter import check as compliance_check
                ok, reason, details = compliance_check(custom_prompt, channel_slug=slug)
                if not ok:
                    log_step(cid, "quote", 1, "failed", f"compliance: {reason}")
                    raise SystemExit(
                        f"Compliance gate: pasted text rejected for {slug} — {reason}. "
                        f"Details: {details}"
                    )
            except ImportError:
                print("  [compliance] WARN: compliance_filter not importable; skipping check")

        print(f"  [custom-prompt] bypassing LLM — {len(custom_prompt)} chars verbatim")
        try:
            script = _build_script_from_custom_prompt(
                custom_text=custom_prompt,
                target_seconds=target_seconds,
                title=queued_title,
            )
            script["_source_passages"] = []
            log_step(cid, "quote", 1, "success")
        except Exception as e:
            log_step(cid, "quote", 1, "failed", str(e))
            raise
    else:
        # Gibran regular essay — Opus writes from corpus passages
        try:
            from ai_writer import (
                fetch_gibran_sources,
                generate_gibran_essay_script,
            )
            previous = _fetch_recent_quotes("Gibran")
            sources = fetch_gibran_sources(topic, n=4)
            print(f"  [sources] grounding in {len(sources)} passage(s):")
            for s in sources:
                print(f"    - {s['book']} — {s['title']}")
            script = generate_gibran_essay_script(
                topic=topic,
                target_seconds=target_seconds,
                previous_topics=previous,
                source_passages=sources,
                style=essay_voice,
            )
            script["_source_passages"] = [
                {"book": s["book"], "title": s["title"]} for s in sources
            ]
            log_step(cid, "quote", 1, "success")
        except Exception as e:
            log_step(cid, "quote", 1, "failed", str(e))
            raise

    title = queued_title or script.get("title") or f"{philosopher}: {topic[:40]}"
    print(f"  [script] {len(script['scenes'])} scenes, "
          f"{sum(len(s['narration'].split()) for s in script['scenes'])} words")

    # 2) Cinematic pipeline (art + voice + whisper + render)
    log_step(cid, "video", 4, "running")
    try:
        work = Path("C:/AI/system/pipeline_work") / cid / "essay"
        output_path = _final_video_path(slug, render_format, title, cid)
        out = render_cinematic_essay(
            title=title,
            philosopher=philosopher,
            channel_slug=slug,
            scenes=script["scenes"],
            output_path=str(output_path),
            work_dir=work,
            reuse=False,
            art_aspect=art_aspect,
            closing_attribution=script.get("closing_attribution"),
            equalizer_color=_resolve_equalizer_color(philosopher, slug),
        )
        log_step(cid, "video", 4, "success")
    except Exception as e:
        log_step(cid, "video", 4, "failed", str(e))
        raise

    # 3) Thumbnail — aspect-matched so the dashboard review card isn't letterboxed
    thumb_path = work / "thumbnail.jpg"
    thumb_w, thumb_h = (1080, 1920) if is_portrait else (1920, 1080)
    try:
        generate_thumbnail(
            image_path=out["art_paths"][0],
            title=title,
            output_path=str(thumb_path),
            width=thumb_w, height=thumb_h,
        )
    except Exception as e:
        print(f"  [thumb] WARN: thumbnail generation failed ({e})")
        thumb_path = None

    # 4) Upload to Supabase Storage (per-channel folder)
    log_step(cid, "upload", 5, "running")
    try:
        video_storage_path = upload_to_storage(
            str(output_path), bucket="wisdom-videos",
            channel_slug=slug, format_name=render_format,
        )
        thumb_storage_path = None
        if thumb_path and Path(thumb_path).exists():
            thumb_storage_path = upload_to_storage(
                str(thumb_path), bucket="wisdom-thumbnails",
                channel_slug=slug, format_name=render_format,
            )
        log_step(cid, "upload", 5, "success")
    except Exception as e:
        log_step(cid, "upload", 5, "failed", str(e))
        raise

    # 5) Update row
    full_quote = "\n\n".join(s["narration"] for s in script["scenes"])
    new_params = {
        "scenes": [s["direction"] for s in script["scenes"]],
        "num_scenes": len(script["scenes"]),
        "target_seconds": target_seconds,
        "aspect": "9:16" if is_portrait else "16:9",
        "tags": script.get("tags", []),
        "voice_settings": {"provider": "chatterbox" if slug != "wisdom" else "elevenlabs"},
        "music_track": Path(out["music_path"]).name,
        "renderer": "remotion",
        "format_pipeline": "custom_prompt_v1" if is_custom_script else "gibran_essay_v1",
        "source_passages": script.get("_source_passages", []),
    }
    if essay_voice:
        new_params["essay_voice"] = essay_voice
    # Preserve custom_prompt_source + is_custom_script so the row stays
    # auditable AND the column-routing flag survives the final write
    # (otherwise the planning grid would lose its routing key on re-render).
    if is_custom_script:
        new_params["is_custom_script"] = True
        if custom_prompt:
            new_params["custom_prompt_source"] = custom_prompt
    updates = {
        "status": "ready",
        "quote_text": full_quote,
        "title": title,
        "description": script.get("description", ""),
        "local_machine_path": str(output_path),
        "video_storage_path": video_storage_path,
        "generation_params": new_params,
    }
    if thumb_storage_path:
        updates["thumbnail_storage_path"] = thumb_storage_path
    update_supabase(cid, updates)

    print(f"\n  DONE: {cid}")
    print(f"  Video : {output_path}")
    print(f"  Storage: {video_storage_path}")


if __name__ == "__main__":
    main()
