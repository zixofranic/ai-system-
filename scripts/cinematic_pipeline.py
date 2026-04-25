"""
Shared cinematic-essay pipeline for Gibran longform.

The pilot and the upcoming generate_gibran_essay.py both want the same
end-to-end: scenes -> SDXL art per scene -> Chatterbox voice -> Whisper
word alignment -> per-scene timings -> convert-story.js -> Remotion
landscape render. Extracted here so neither caller has to re-implement
those steps and they can't drift apart.

Usage from a caller:

    from cinematic_pipeline import render_cinematic_essay
    out = render_cinematic_essay(
        title="Gibran — Voices That Echo",
        philosopher="Gibran",
        channel_slug="gibran",
        scenes=[
            {"direction": "Soft music. Old film grain. Fade in to Gibran portrait.",
             "narration": "There are voices in history that don't just speak..."},
            ...
        ],
        output_path="C:/AI/gibran/videos/pilots/2026-04-20-essay.mp4",
        work_dir=Path("C:/AI/gibran/videos/pilots/_work/2026-04-20-essay"),
        reuse=False,
    )
    # out = {"video_path": ..., "voice_path": ..., "art_paths": [...], "music_path": ...}
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator import (
    CHANNEL_DEFAULT_LORA,
    EQUALIZER_COLORS,
    PERSONA_TO_LORA,
    _get_philosopher_style,
    generate_art,
    generate_voice,
    pick_music,
    watermark_for_channel,
)

VIDEO_ENGINE = Path("C:/AI/system/video-engine")


# --- Art prompt building -------------------------------------------------

def _build_art_prompt_from_direction(direction: str) -> str:
    """Convert a [stage direction] into a portrait/landscape SDXL prompt
    with consistent Gibran-channel cinematic styling on top.
    """
    base = direction.strip().rstrip(".")
    return (
        f"{base}, soft warm cinematic lighting, gentle film grain, "
        f"painterly atmosphere, intimate composition, shallow depth of field, "
        f"muted earth-tone palette with gold accents, "
        f"award-winning fine art, masterpiece quality"
    )


# --- Whisper alignment ---------------------------------------------------

def _whisper_align(audio_mp3: str, ground_truth_text: str) -> list:
    """Whisper word timestamps, force-aligned to the original script when
    the alignment helper succeeds. Falls back to raw Whisper output."""
    import whisper
    model = whisper.load_model("base")
    res = model.transcribe(audio_mp3, word_timestamps=True, language="en")
    words = []
    for seg in res.get("segments", []):
        for w in seg.get("words", []):
            words.append({"word": w["word"].strip(),
                          "start": w["start"], "end": w["end"]})
    try:
        from whisper_align import align_whisper_to_script
        words = align_whisper_to_script(words, ground_truth_text)
    except Exception as e:
        print(f"  [whisper] align skipped ({e})")
    return words


# --- Scene timing distribution ------------------------------------------

def _split_timings(words: list, scene_word_counts: list) -> list:
    """Distribute Whisper words across scenes proportionally.

    Whisper sometimes returns fewer words than the source text (merges,
    skips). All indices are clamped to bounds, and counts are scaled to
    match the actual transcript length so a short transcript can't blow
    up the slicing.
    """
    if not words:
        return [{"startMs": 0, "endMs": 0} for _ in scene_word_counts]
    n_total = len(words)
    src_total = sum(scene_word_counts) or 1
    timings = []
    cursor = 0
    for n_words in scene_word_counts:
        scaled = int(round(n_words * n_total / src_total))
        start_w = max(0, min(cursor, n_total - 1))
        end_w = max(start_w, min(cursor + scaled - 1, n_total - 1))
        timings.append({
            "startMs": int(words[start_w]["start"] * 1000),
            "endMs": int(words[end_w]["end"] * 1000),
        })
        cursor += scaled
    timings[0]["startMs"] = 0
    timings[-1]["endMs"] = int(words[-1]["end"] * 1000)
    return timings


# --- Art aspect helpers --------------------------------------------------

LANDSCAPE = (1216, 832)
PORTRAIT  = (832, 1216)


# --- Main pipeline -------------------------------------------------------

def render_cinematic_essay(
    *,
    title: str,
    philosopher: str,
    channel_slug: str,
    scenes: list,
    output_path: str,
    work_dir: Path,
    reuse: bool = False,
    art_aspect: tuple = LANDSCAPE,
    closing_attribution: str | None = None,
    equalizer_color: str | None = None,
) -> dict:
    """End-to-end render of a multi-scene cinematic essay.

    Args:
        title: shown in the AIVideo intro card
        philosopher: drives voice + LoRA + watermark resolution
        channel_slug: "gibran" today; future channels qualify by adding to
            CHANNEL_VOICE in orchestrator.py
        scenes: list of {direction, narration}; the directions become art
            prompts, the narrations become caption text + voice input
        output_path: final MP4 path on disk
        work_dir: where intermediate art/voice/timestamps live; reused
            on subsequent calls when reuse=True
        reuse: skip art + voice generation if cached files exist
        art_aspect: (width, height) — LANDSCAPE for story format,
            PORTRAIT for story_vertical
        closing_attribution: defaults to "Inspired by <philosopher>"
        equalizer_color: defaults to amber (#D49A45) — set per channel
            policy

    Returns dict with: video_path, voice_path, art_paths, music_path,
    full_narration, scene_timings, timestamps_path.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1) Art per scene
    art_paths = [str(work_dir / f"art_{i+1}.png") for i in range(len(scenes))]
    if reuse and all(Path(p).exists() for p in art_paths):
        print(f"\n[art] --reuse: keeping {len(scenes)} existing tile(s)")
    else:
        print(f"\n[art] generating {len(scenes)} {'landscape' if art_aspect == LANDSCAPE else 'portrait'} tile(s)")
        lora = (PERSONA_TO_LORA.get(philosopher)
                or CHANNEL_DEFAULT_LORA.get(channel_slug))
        for i, scene in enumerate(scenes):
            prompt = _build_art_prompt_from_direction(scene["direction"])
            print(f"  scene {i+1}: {prompt[:90]}...")
            generate_art(prompt, lora, art_aspect[0], art_aspect[1], art_paths[i])

    # 2) Voice — full narration with brackets stripped
    full_narration = "\n\n".join(
        s["narration"] for s in scenes if s.get("narration")
    ).strip()
    voice_wav = str(work_dir / "voice.wav")
    voice_mp3 = str(work_dir / "voice.mp3")
    if reuse and Path(voice_mp3).exists():
        print(f"\n[voice] --reuse: keeping {voice_mp3}")
    else:
        print(f"\n[voice] {len(full_narration.split())} words via channel '{channel_slug}'")
        generate_voice(full_narration, voice_wav,
                       channel_slug=channel_slug,
                       philosopher=philosopher)
        subprocess.run(
            ["ffmpeg", "-y", "-i", voice_wav, "-codec:a", "libmp3lame",
             "-b:a", "192k", voice_mp3],
            check=True, capture_output=True,
        )

    # 3) Whisper align + per-scene timings
    print("\n[whisper] aligning words")
    words = _whisper_align(voice_mp3, full_narration)
    scene_word_counts = [len(s.get("narration", "").split()) for s in scenes]
    scene_timings = _split_timings(words, scene_word_counts)

    # 4) Music (channel-default style pool)
    music_path = pick_music(philosopher, channel_slug=channel_slug)
    print(f"\n[music] {Path(music_path).name}")

    # 5) Write convert-*.js inputs
    # Branch by art_aspect: PORTRAIT routes to convert-story-vertical.js +
    # format="story_vertical"; LANDSCAPE routes to convert-story.js +
    # format="story". Both converters accept the same input file shape.
    is_portrait = art_aspect == PORTRAIT
    render_format = "story_vertical" if is_portrait else "story"
    converter = ("convert-story-vertical.js" if is_portrait
                 else "convert-story.js")

    # Amber-red (terracotta / burnt sienna) for Gibran cinematic — picked
    # 2026-04-20 to replace the brighter amber #D49A45. Matches the new
    # AIVideo intro accent so the equalizer reads as the same color
    # language as the divider.
    eq_color = equalizer_color or "#C2603C"
    script_path = work_dir / "script.json"
    script_path.write_text(json.dumps({
        "title": title,
        "story_script": full_narration,
        "philosopher": philosopher,
        "channel": channel_slug,
        "format": render_format,
        "watermark": watermark_for_channel(channel_slug),
        "closing_attribution": closing_attribution or f"Inspired by {philosopher}",
        "equalizerColor": eq_color,
        # Force cinematic styling for ALL channels going through this
        # pipeline — EBGaramond italic, aged-paper, equalizer band. Without
        # this, convert-story.js defaults non-Gibran channels to the old
        # Hormozi look (uppercase BreeSerif white-on-stroke). User flagged
        # the difference 2026-04-25 on a Wisdom Sun Tzu midform.
        "theme": {"cinematic": True},
    }, indent=2), encoding="utf-8")
    timestamps_path = work_dir / "timestamps.json"
    timestamps_path.write_text(json.dumps(words, indent=2), encoding="utf-8")
    art_paths_path = work_dir / "art_paths.json"
    art_paths_path.write_text(json.dumps(art_paths, indent=2), encoding="utf-8")
    scene_timings_path = work_dir / "scene_timings.json"
    scene_timings_path.write_text(json.dumps(scene_timings, indent=2), encoding="utf-8")

    # 6) Convert to Remotion timeline (writes public/content/<id>/...)
    project_id = Path(output_path).stem.replace("_", "-")[:60]
    print(f"\n[convert/{render_format}] -> {project_id}")
    subprocess.run([
        "node", str(VIDEO_ENGINE / "scripts" / converter),
        "--script", str(script_path),
        "--timestamps", str(timestamps_path),
        "--art-paths", str(art_paths_path),
        "--scene-timings", str(scene_timings_path),
        "--voice", voice_mp3,
        "--music", music_path,
        "--output", project_id,
        "--format", render_format,
    ], cwd=str(VIDEO_ENGINE), check=True)

    # 7) Render
    remotion_cmd = str(VIDEO_ENGINE / "node_modules" / ".bin" / "remotion.cmd")
    render_cmd = (f'"{remotion_cmd}" render {project_id} "{output_path}" '
                  f'--codec=h264 --crf=22')
    print(f"\n[render] {render_cmd}")
    subprocess.run(render_cmd, cwd=str(VIDEO_ENGINE),
                   check=True, shell=True, timeout=1800)

    # 8) Thumbnail — first scene art + title overlay. Aspect matches the
    # rendered video so the dashboard review card isn't letterboxed.
    # Lives next to the video file. Caller can choose to upload or skip.
    thumb_path = None
    try:
        from thumbnail_generator import generate_thumbnail
        thumb_path = str(Path(output_path).with_suffix("")) + "_thumb.jpg"
        thumb_w, thumb_h = (1080, 1920) if is_portrait else (1920, 1080)
        generate_thumbnail(art_paths[0], title, thumb_path, thumb_w, thumb_h)
        if not Path(thumb_path).exists():
            print(f"[thumb] WARN: generated path missing: {thumb_path}")
            thumb_path = None
        else:
            print(f"[thumb] {thumb_path}")
    except Exception as e:
        print(f"[thumb] WARN: thumbnail generation failed ({e})")
        thumb_path = None

    print(f"\n[done] {output_path}")
    return {
        "video_path": output_path,
        "voice_path": voice_wav,
        "voice_mp3":  voice_mp3,
        "art_paths":  art_paths,
        "music_path": music_path,
        "thumb_path": thumb_path,
        "full_narration": full_narration,
        "scene_timings": scene_timings,
        "timestamps_path": str(timestamps_path),
        "project_id": project_id,
    }
