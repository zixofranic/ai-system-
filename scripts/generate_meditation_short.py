"""
Generate a standalone 60-second philosopher meditation in 9:16 portrait
format (story_vertical). No parent story required — this is the path the
dashboard's "Portrait Short" button feeds when an item has
`format=story_vertical` in Supabase.

Pipeline:
  1. Opus writes a 130-175 word philosopher-voice meditation with a story
     arc + a list of `num_scenes` scene descriptions.
  2. SDXL generates one 832x1216 portrait per scene.
  3. ElevenLabs synthesizes the narration; Whisper extracts word timestamps
     (force-aligned to the script via whisper_align).
  4. We split the timestamps into per-scene timing windows.
  5. `convert-story-vertical.js` writes the Remotion timeline + assets;
     Remotion renders the final 1080x1920 MP4.
  6. The MP4 + thumbnail are uploaded to Supabase Storage and the row's
     status flips to `ready`.

Invoked by orchestrator's `_run_meditation_pipeline` as a subprocess
(mirrors the story pipeline pattern). Can also be run directly:

    python generate_meditation_short.py \\
        --content-id <uuid> \\
        --philosopher "Marcus Aurelius" \\
        --topic "the morning the sky stopped looking the same" \\
        --channel-slug wisdom \\
        [--queued-title "..."] \\
        [--num-scenes 3] [--target-seconds 60]
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("C:/AI/.env")
sys.path.insert(0, str(Path(__file__).parent))

# Import orchestrator helpers — voice, art, music, storage, paths.
from orchestrator import (
    CHANNEL_DEFAULT_LORA,
    CHANNEL_DEFAULT_MUSIC_STYLE,
    EQUALIZER_COLORS,
    PERSONA_TO_LORA,
    PERSONA_TO_MUSIC_STYLE,
    _content_work_dir,
    _ensure_channel_data,
    _fetch_recent_quotes,
    _final_video_path,
    _slugify_title,
    generate_art,
    generate_voice,
    log_step,
    pick_music,
    update_supabase,
    watermark_for_channel,
)
from supabase_storage import upload_to_storage
from thumbnail_generator import generate_thumbnail

import requests

VIDEO_ENGINE = Path("C:/AI/system/video-engine")
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SENTENCE_ENDINGS = {".", "!", "?", ";", ":"}


def _supa_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }


def _fetch_content(content_id: str) -> dict:
    url = (
        f"{SUPABASE_URL}/rest/v1/content"
        f"?id=eq.{content_id}"
        f"&select=*,channels:channel_id(id,name,slug,settings)"
    )
    resp = requests.get(url, headers=_supa_headers(), timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError(f"Content {content_id} not found")
    return rows[0]


def _whisper_words(audio_path: str, ground_truth_text: str) -> list:
    """Whisper word timestamps, force-aligned to the original script."""
    import whisper
    model = whisper.load_model("base")
    result = model.transcribe(audio_path, word_timestamps=True)
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            words.append({
                "word": w["word"].strip(),
                "start": w["start"],
                "end": w["end"],
            })
    try:
        from whisper_align import align_whisper_to_script
        words = align_whisper_to_script(words, ground_truth_text)
    except Exception as e:
        print(f"  [whisper] WARNING: script alignment failed ({e}); using raw output")
    return words


def _scene_timings_from_words(words: list, num_scenes: int) -> list:
    """Split the Whisper word list into N scene windows, snapping each break
    to the nearest sentence boundary so a scene change never lands mid-clause.
    """
    if not words:
        return [{"startMs": 0, "endMs": 0} for _ in range(num_scenes)]

    voice_end_ms = words[-1]["end"] * 1000

    # Sentence boundary candidates: end-of-word index where word ends with
    # a sentence-ending punctuation mark.
    boundaries = [{"wordIdx": 0, "ms": 0}]
    for i, w in enumerate(words):
        token = w["word"].strip()
        if token and token[-1] in SENTENCE_ENDINGS:
            boundaries.append({"wordIdx": i + 1, "ms": w["end"] * 1000})
    # Always include the absolute end so the last window snaps to it
    if boundaries[-1]["ms"] < voice_end_ms:
        boundaries.append({"wordIdx": len(words), "ms": voice_end_ms})

    target_each = voice_end_ms / num_scenes
    cuts = []
    for s in range(1, num_scenes):
        ideal = s * target_each
        nearest = min(boundaries[1:-1] or boundaries, key=lambda b: abs(b["ms"] - ideal))
        cuts.append(nearest["ms"])

    timings = []
    prev = 0
    for s in range(num_scenes):
        end = cuts[s] if s < num_scenes - 1 else voice_end_ms
        timings.append({"startMs": prev, "endMs": end})
        prev = end
    return timings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content-id", required=True)
    ap.add_argument("--philosopher", required=True)
    ap.add_argument("--topic", default="life and wisdom")
    ap.add_argument("--channel-slug", required=True)
    ap.add_argument("--queued-title", default=None)
    ap.add_argument("--num-scenes", type=int, default=3)
    ap.add_argument("--target-seconds", type=int, default=60)
    args = ap.parse_args()

    cid = args.content_id
    philosopher = args.philosopher
    topic = args.topic
    channel_slug = args.channel_slug
    num_scenes = max(1, min(5, args.num_scenes))

    print(f"\n{'='*70}")
    print(f"  STORY_VERTICAL meditation")
    print(f"  content_id : {cid}")
    print(f"  philosopher: {philosopher}  topic: {topic}")
    print(f"  channel    : {channel_slug}  scenes: {num_scenes}")
    print(f"{'='*70}\n")

    update_supabase(cid, {"status": "generating"})
    work = _content_work_dir(cid)

    # Fetch the row so we can read writing_style. The CLI flags carry
    # philosopher/topic/etc but writing_style is row-only — it's set by
    # the dashboard Create dialog and not echoed onto the orchestrator
    # subprocess command line. NULL row -> in_character default.
    try:
        row = _fetch_content(cid)
    except Exception as e:
        print(f"  [warn] _fetch_content failed ({e}); using in_character style default")
        row = {}

    from orchestrator import _resolve_writing_style
    writing_style = _resolve_writing_style(row)

    # ---- 1. Script ----
    log_step(cid, "quote", 1, "running")
    try:
        from ai_writer import generate_wisdom_meditation_script
        previous = _fetch_recent_quotes(philosopher)
        script = generate_wisdom_meditation_script(
            philosopher=philosopher, topic=topic,
            channel_slug=channel_slug,
            target_seconds=args.target_seconds,
            previous_quotes=previous,
            num_scenes=num_scenes,
            style=writing_style,
        )
        story_text = script["story_script"]
        scenes = script["scene_descriptions"]
        # Honor the queued title if planning provided one
        title = args.queued_title or script.get("title") or f"{philosopher}: {topic[:40]}"
        description = script.get("description", "")
        tags = script.get("tags", [])
        word_count = len(story_text.split())
        print(f"  [script] {word_count} words, {len(scenes)} scenes")
        log_step(cid, "quote", 1, "success")
    except Exception as e:
        log_step(cid, "quote", 1, "failed", str(e))
        raise

    # ---- 2. Art (one portrait per scene) ----
    log_step(cid, "image", 2, "running")
    art_paths = []
    try:
        lora = (
            PERSONA_TO_LORA.get(philosopher)
            or CHANNEL_DEFAULT_LORA.get(channel_slug)
        )
        for i, scene_desc in enumerate(scenes):
            # Scene-first prompt: scene_desc IS the subject, style follows.
            from orchestrator import _get_philosopher_style
            style = _get_philosopher_style(philosopher) if channel_slug != "na" else ""
            prompt = (
                f"{scene_desc}, "
                f"strong compositional silhouette, rule of thirds, "
                f"shallow depth of field, volumetric light, "
                f"atmospheric perspective, "
                + (f"{style}, " if style else "")
                + "ultra detailed, masterpiece"
            )
            art_path = str(work / f"art_{i}.png")
            generate_art(prompt, lora, 832, 1216, art_path)
            art_paths.append(art_path)
        log_step(cid, "image", 2, "success")
    except Exception as e:
        log_step(cid, "image", 2, "failed", str(e))
        raise

    # ---- 3. Voice (ElevenLabs) ----
    log_step(cid, "voice", 3, "running")
    try:
        voice_wav = str(work / "voice.wav")
        # slow_factor=0.92 — match the calmer cadence wisdom shorts use; full
        # speed feels rushed for a reflective meditation.
        generate_voice(story_text, voice_wav,
                       channel_slug=channel_slug,
                       philosopher=philosopher, slow_factor=0.92)
        # Convert to mp3 for Remotion
        voice_mp3 = str(work / "voice.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-i", voice_wav, "-codec:a", "libmp3lame",
             "-b:a", "192k", voice_mp3],
            check=True, capture_output=True,
        )
        log_step(cid, "voice", 3, "success")
    except Exception as e:
        log_step(cid, "voice", 3, "failed", str(e))
        raise

    # ---- 4. Whisper word-level timestamps ----
    print("  [whisper] aligning words...")
    words = _whisper_words(voice_mp3, story_text)
    print(f"  [whisper] {len(words)} words, {words[-1]['end']:.1f}s")

    scene_timings = _scene_timings_from_words(words, len(art_paths))
    for i, t in enumerate(scene_timings):
        print(f"  [scene {i+1}] {t['startMs']/1000:.1f}s - {t['endMs']/1000:.1f}s")

    # ---- 5. Music ----
    music_path = pick_music(philosopher, channel_slug=channel_slug)
    music_style = (
        PERSONA_TO_MUSIC_STYLE.get(philosopher)
        or CHANNEL_DEFAULT_MUSIC_STYLE.get(channel_slug, "stoic_classical")
    )
    eq_color = EQUALIZER_COLORS.get(music_style, "#D4AF37")

    # ---- 6. Write the convert-story-vertical inputs ----
    script_path = work / "story_vertical_script.json"
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump({
            "title": title,
            "story_script": story_text,
            "philosopher": philosopher,
            "channel": channel_slug,
            "closing_attribution": script.get("closing_attribution",
                                              f"Inspired by {philosopher}"),
            "equalizerColor": eq_color,
        }, f, indent=2, ensure_ascii=False)

    timestamps_path = work / "voice_timestamps.json"
    with open(timestamps_path, "w") as f:
        json.dump(words, f, indent=2)

    art_paths_path = work / "art_paths.json"
    with open(art_paths_path, "w") as f:
        json.dump(art_paths, f, indent=2)

    scene_timings_path = work / "scene_timings.json"
    with open(scene_timings_path, "w") as f:
        json.dump(scene_timings, f, indent=2)

    # ---- 7. Convert to Remotion timeline ----
    log_step(cid, "video", 4, "running")
    project_id = ("svm-" + _slugify_title(title)).replace("_", "-")[:60]
    try:
        cmd = [
            "node",
            str(VIDEO_ENGINE / "scripts" / "convert-story-vertical.js"),
            "--script", str(script_path),
            "--timestamps", str(timestamps_path),
            "--art-paths", str(art_paths_path),
            "--scene-timings", str(scene_timings_path),
            "--voice", voice_mp3,
            "--music", music_path,
            "--output", project_id,
            "--format", "story_vertical",
        ]
        subprocess.run(cmd, cwd=str(VIDEO_ENGINE), check=True)
    except Exception as e:
        log_step(cid, "video", 4, "failed", str(e))
        raise

    # ---- 8. Render via Remotion ----
    output_path = _final_video_path(channel_slug, "story_vertical", title, cid)
    remotion_cmd = str(VIDEO_ENGINE / "node_modules" / ".bin" / "remotion.cmd")
    render_cmd = (
        f'"{remotion_cmd}" render {project_id} "{output_path}" '
        f'--codec=h264 --crf=24'
    )
    print(f"  [render] {render_cmd}")
    try:
        subprocess.run(render_cmd, cwd=str(VIDEO_ENGINE),
                       check=True, timeout=900, shell=True)
        log_step(cid, "video", 4, "success")
    except Exception as e:
        log_step(cid, "video", 4, "failed", str(e))
        raise

    # ---- 9. Thumbnail ----
    thumb_path = work / "thumbnail.jpg"
    try:
        generate_thumbnail(
            image_path=art_paths[0],
            title=title,
            output_path=str(thumb_path),
            width=1080, height=1920,
        )
    except Exception as e:
        print(f"  [thumb] WARN: thumbnail generation failed ({e})")
        thumb_path = None

    # ---- 10. Upload to Supabase Storage ----
    log_step(cid, "upload", 5, "running")
    try:
        video_storage_path = upload_to_storage(
            str(output_path), bucket="wisdom-videos",
            channel_slug=channel_slug, format_name="story_vertical",
        )
        thumb_storage_path = None
        if thumb_path and Path(thumb_path).exists():
            thumb_storage_path = upload_to_storage(
                str(thumb_path), bucket="wisdom-thumbnails",
                channel_slug=channel_slug, format_name="story_vertical",
            )
        log_step(cid, "upload", 5, "success")
    except Exception as e:
        log_step(cid, "upload", 5, "failed", str(e))
        raise

    # ---- 11. Update content row ----
    updates = {
        "status": "ready",
        "quote_text": story_text,
        "title": title,
        "description": description,
        "local_machine_path": str(output_path),
        "video_storage_path": video_storage_path,
        "generation_params": {
            "lora": lora,
            "scenes": scenes,
            "num_scenes": len(art_paths),
            "tags": tags,
            "voice_settings": {"provider": "elevenlabs"},
            "music_track": Path(music_path).name,
            "renderer": "remotion",
            "format_pipeline": "meditation_v1",
        },
    }
    if thumb_storage_path:
        updates["thumbnail_storage_path"] = thumb_storage_path
    update_supabase(cid, updates)

    print(f"\n  DONE: {cid}")
    print(f"  Video: {output_path}")
    print(f"  Storage: {video_storage_path}")


if __name__ == "__main__":
    main()
