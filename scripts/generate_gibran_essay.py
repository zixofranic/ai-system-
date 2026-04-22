"""
Generate a cinematic Gibran essay end-to-end.

Reads a Gibran content row that has gibran_long_form_style='essay' and a
target_seconds set, calls Opus for the script, hands off to
cinematic_pipeline.render_cinematic_essay, uploads to Supabase storage,
updates the row to status=ready.

Invoked by orchestrator's `_run_gibran_essay_pipeline` as a subprocess
(mirrors the existing story / meditation pipeline pattern). Can also be
run directly:

    python generate_gibran_essay.py --content-id <uuid>
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

from cinematic_pipeline import LANDSCAPE, render_cinematic_essay
from orchestrator import (
    _final_video_path,
    _fetch_recent_quotes,
    _resolve_gibran_choice,
    log_step,
    update_supabase,
)
from supabase_storage import upload_to_storage
from thumbnail_generator import generate_thumbnail

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content-id", required=True)
    args = ap.parse_args()

    row = _fetch(args.content_id)
    cid = row["id"]

    slug = (row.get("channels") or {}).get("slug")
    if slug != "gibran":
        raise SystemExit(f"Refusing — Gibran-only by design, got channel='{slug}'")

    # Re-validate the format gate (orchestrator already checked, but a
    # direct CLI call should fail here too rather than hit Opus and find
    # out at write time).
    style, target_seconds, err = _resolve_gibran_choice(row)
    if err:
        raise SystemExit(f"Format gate: {err}")
    if style != "essay":
        raise SystemExit(f"Refusing — this script handles 'essay' only, got '{style}'")

    topic = row.get("topic") or row.get("title") or "life"
    queued_title = row.get("title")

    # Prose voice: 'narrator' (ChatGPT-style meta-interpretive, the primary
    # Format A) or 'prophet_voice' (corpus-faithful Prophet emulation, the
    # hidden Format B). NULL on the row defaults to narrator so migrations
    # don't need to run before this code does — pre-migration rows get the
    # primary format automatically.
    # Prefer the new generalized writing_style column over the legacy
    # gibran_essay_voice. They're parallel vocabularies:
    #   writing_style       gibran_essay_voice
    #   ──────────────      ──────────────────
    #   narrator       <->  narrator
    #   in_character   <->  prophet_voice
    # The Gibran writer (generate_gibran_essay_script) still takes the
    # legacy 'narrator' | 'prophet_voice' vocabulary, so we map back when
    # the row carries the new column.
    new_style = row.get("writing_style")
    if new_style == "narrator":
        essay_voice = "narrator"
    elif new_style == "in_character":
        essay_voice = "prophet_voice"
    else:
        # Fall back to legacy column or Gibran's narrator default
        essay_voice = row.get("gibran_essay_voice") or "narrator"

    if essay_voice not in ("narrator", "prophet_voice"):
        raise SystemExit(
            f"Invalid essay voice='{essay_voice}'; "
            f"must be 'narrator' or 'prophet_voice'"
        )

    print(f"\n{'='*70}")
    print(f"  Gibran cinematic essay")
    print(f"  content_id   : {cid}")
    print(f"  topic        : {topic}")
    print(f"  target       : {target_seconds}s ({target_seconds/60:.1f} min)")
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

    # Bypass path: Custom Prompts dashboard tile stashes a pasted LLM essay
    # in generation_params.custom_prompt_source. When present, skip Opus
    # and feed the user's prose through verbatim via the format adapter.
    custom_prompt = (row.get("generation_params") or {}).get("custom_prompt_source")

    if custom_prompt:
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
        # 1) Pick source passages from the corpus + write
        try:
            from ai_writer import (
                fetch_gibran_sources,
                generate_gibran_essay_script,
            )
            previous = _fetch_recent_quotes("Gibran")
            # Fetch explicitly so we can log which passages anchored this essay,
            # then pass them through (writer would auto-fetch otherwise).
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
            # Stash for traceability — `generation_params.source_passages`
            # tells future-me which Gibran chunks the essay was rooted in.
            script["_source_passages"] = [
                {"book": s["book"], "title": s["title"]} for s in sources
            ]
            log_step(cid, "quote", 1, "success")
        except Exception as e:
            log_step(cid, "quote", 1, "failed", str(e))
            raise

    title = queued_title or script.get("title") or f"Khalil Gibran: {topic[:40]}"
    print(f"  [script] {len(script['scenes'])} scenes, "
          f"{sum(len(s['narration'].split()) for s in script['scenes'])} words")

    # 2) Cinematic pipeline (art + voice + whisper + render)
    log_step(cid, "video", 4, "running")
    try:
        work = Path("C:/AI/system/pipeline_work") / cid / "essay"
        output_path = _final_video_path("gibran", "story", title, cid)
        out = render_cinematic_essay(
            title=title,
            philosopher="Gibran",
            channel_slug="gibran",
            scenes=script["scenes"],
            output_path=str(output_path),
            work_dir=work,
            reuse=False,
            art_aspect=LANDSCAPE,
            closing_attribution=script.get("closing_attribution"),
        )
        log_step(cid, "video", 4, "success")
    except Exception as e:
        log_step(cid, "video", 4, "failed", str(e))
        raise

    # 3) Thumbnail (first scene art + title)
    thumb_path = work / "thumbnail.jpg"
    try:
        generate_thumbnail(
            image_path=out["art_paths"][0],
            title=title,
            output_path=str(thumb_path),
            width=1920, height=1080,
        )
    except Exception as e:
        print(f"  [thumb] WARN: thumbnail generation failed ({e})")
        thumb_path = None

    # 4) Upload to Supabase Storage
    log_step(cid, "upload", 5, "running")
    try:
        video_storage_path = upload_to_storage(
            str(output_path), bucket="wisdom-videos",
            channel_slug="gibran", format_name="story",
        )
        thumb_storage_path = None
        if thumb_path and Path(thumb_path).exists():
            thumb_storage_path = upload_to_storage(
                str(thumb_path), bucket="wisdom-thumbnails",
                channel_slug="gibran", format_name="story",
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
        "tags": script.get("tags", []),
        "voice_settings": {"provider": "chatterbox"},
        "music_track": Path(out["music_path"]).name,
        "renderer": "remotion",
        "format_pipeline": "gibran_essay_v1",
        "essay_voice": essay_voice,
        "source_passages": script.get("_source_passages", []),
    }
    # Preserve custom_prompt_source so the row stays auditable —
    # otherwise the original LLM paste is lost on the final write.
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
