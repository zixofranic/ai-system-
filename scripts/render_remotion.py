"""
Remotion-based Video Renderer for Wisdom Content Pipeline
==========================================================
Replaces assemble_video.py (MoviePy) with Remotion rendering.

Builds a timeline.json + metadata.json project directory, copies assets,
and invokes the Remotion CLI to render the final MP4.

Supports: short (9:16), midform (16:9), longform (16:9).
"""

import json
import shutil
import subprocess
from pathlib import Path

VIDEO_ENGINE = Path("C:/AI/system/video-engine")
CONTENT_DIR = VIDEO_ENGINE / "public" / "content"

FORMAT_DIMENSIONS = {
    "short":    {"width": 1080, "height": 1920},
    "midform":  {"width": 1920, "height": 1080},
    "longform": {"width": 1920, "height": 1080},
}

# Padding constants (milliseconds)
INTRO_PAD_MS = 2500       # silence before first voice
OUTRO_PAD_MS = 2500       # silence after last voice
SECTION_GAP_MS = 800      # gap between voice sections (midform/longform)
ATTR_DELAY_MS = 400       # attribution appears slightly after quote


def _get_duration_ms(audio_path: str) -> float:
    """Get audio duration in milliseconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip()) * 1000


def _convert_to_mp3(src: str, dst: str):
    """Convert audio file to mp3 via ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-codec:a", "libmp3lame", "-b:a", "192k", dst],
        capture_output=True, check=True,
    )


def _build_short_timeline(
    quotes, philosopher, voice_durations_ms, title, watermark, channel_name,
    equalizer_color=None,
):
    """Build timeline for a single-quote short video."""
    voice_ms = voice_durations_ms[0]
    total_ms = INTRO_PAD_MS + voice_ms + OUTRO_PAD_MS

    return {
        "shortTitle": title or f"{philosopher}",
        "elements": [{
            "startMs": 0,
            "endMs": total_ms,
            "imageUrl": "scene_0",
            "enterTransition": "fade",
            "exitTransition": "fade",
            "animations": [{
                "type": "scale", "from": 1.0, "to": 1.06,
                "startMs": 0, "endMs": total_ms,
            }],
        }],
        "text": [
            {
                "startMs": INTRO_PAD_MS,
                "endMs": INTRO_PAD_MS + voice_ms,
                "text": quotes[0],
                "position": "center",
                "role": "quote",
            },
            {
                "startMs": INTRO_PAD_MS + ATTR_DELAY_MS,
                "endMs": INTRO_PAD_MS + voice_ms,
                "text": f"-- {philosopher}",
                "position": "bottom",
                "role": "attribution",
            },
        ],
        "audio": [
            {"startMs": INTRO_PAD_MS, "endMs": INTRO_PAD_MS + voice_ms, "audioUrl": "voice_0"},
            {"startMs": 0, "endMs": total_ms, "audioUrl": "music"},
        ],
        "metadata": {
            "format": "short",
            "width": 1080, "height": 1920, "fps": 30,
            "philosopher": philosopher,
            "channel": channel_name,
            "watermark": watermark,
            "equalizerColor": equalizer_color,
        },
    }


def _build_multipart_timeline(
    quotes, philosopher, voice_durations_ms, title, watermark, channel_name,
    fmt, narration_segments=None, chapter_titles=None, equalizer_color=None,
):
    """Build timeline for midform or longform (multiple quotes/sections)."""
    elements = []
    text_items = []
    audio_items = []

    cursor_ms = 0  # all times relative to post-intro (component adds intro offset)

    for i, quote in enumerate(quotes):
        voice_ms = voice_durations_ms[i]
        pad_before = INTRO_PAD_MS if i == 0 else SECTION_GAP_MS
        section_start = cursor_ms + pad_before
        section_end = section_start + voice_ms

        # --- Chapter title (longform only) ---
        if fmt == "longform" and chapter_titles and i < len(chapter_titles):
            ch_start = cursor_ms
            ch_end = cursor_ms + pad_before
            text_items.append({
                "startMs": ch_start,
                "endMs": ch_end,
                "text": chapter_titles[i],
                "position": "center",
                "role": "chapter-title",
            })

        # --- Background image for this section ---
        bg_start = cursor_ms if i == 0 else cursor_ms
        bg_end = section_end + (OUTRO_PAD_MS if i == len(quotes) - 1 else SECTION_GAP_MS // 2)
        elements.append({
            "startMs": bg_start,
            "endMs": bg_end,
            "imageUrl": f"scene_{i}",
            "enterTransition": "fade",
            "exitTransition": "fade",
            "animations": [{
                "type": "scale", "from": 1.0, "to": 1.05,
                "startMs": bg_start, "endMs": bg_end,
            }],
        })

        # --- Text: narration + quote split ---
        has_narration = (narration_segments and i < len(narration_segments)
                         and narration_segments[i])
        if has_narration:
            narr_text = narration_segments[i].strip()
            narr_chars = len(narr_text)
            quote_chars = len(quote)
            total_chars = narr_chars + quote_chars
            narr_ms = voice_ms * (narr_chars / total_chars)

            narr_end = section_start + narr_ms
            text_items.append({
                "startMs": section_start,
                "endMs": narr_end,
                "text": narr_text,
                "position": "center",
                "role": "narration",
            })
            text_items.append({
                "startMs": narr_end,
                "endMs": section_end,
                "text": quote,
                "position": "center",
                "role": "quote",
                "attribution": philosopher,
            })
        else:
            text_items.append({
                "startMs": section_start,
                "endMs": section_end,
                "text": quote,
                "position": "center",
                "role": "quote",
                "attribution": philosopher,
            })

        # --- Voice audio ---
        audio_items.append({
            "startMs": section_start,
            "endMs": section_end,
            "audioUrl": f"voice_{i}",
        })

        cursor_ms = section_end

    # Add outro padding
    total_ms = cursor_ms + OUTRO_PAD_MS

    # Music spans entire duration
    audio_items.append({
        "startMs": 0,
        "endMs": total_ms,
        "audioUrl": "music",
    })

    dims = FORMAT_DIMENSIONS[fmt]
    return {
        "shortTitle": title or f"{philosopher}",
        "elements": elements,
        "text": text_items,
        "audio": audio_items,
        "metadata": {
            "format": fmt,
            "width": dims["width"], "height": dims["height"], "fps": 30,
            "philosopher": philosopher,
            "channel": channel_name,
            "watermark": watermark,
            "equalizerColor": equalizer_color,
        },
    }


def render_remotion_video(
    quotes: list,
    philosopher: str,
    art_paths: list,
    voice_paths: list,
    music_path: str,
    output_path: str,
    format: str = "short",
    channel_name: str = "Wisdom",
    title: str = None,
    watermark: str = "Deep Echoes of Wisdom",
    narration_segments: list = None,
    chapter_titles: list = None,
    equalizer_color: str = None,
    # Ignored (kept for call-site compatibility)
    aspect_ratio: str = None,
) -> str:
    """
    Render a video via Remotion. Drop-in replacement for assemble_video().

    1. Creates a project directory under video-engine/public/content/
    2. Copies images and audio assets
    3. Writes timeline.json + metadata.json
    4. Invokes Remotion CLI render
    5. Cleans up project directory
    6. Returns output_path
    """
    if format not in FORMAT_DIMENSIONS:
        raise ValueError(f"Unknown format '{format}'. Use: short, midform, longform")

    # Project ID from output filename (Remotion only allows a-z, A-Z, 0-9, -)
    project_id = Path(output_path).stem.replace("_", "-")
    project_dir = CONTENT_DIR / project_id
    images_dir = project_dir / "images"
    audio_dir = project_dir / "audio"

    print(f"[render_remotion] Format={format}  Project={project_id}")

    # --- Setup directories ---
    if project_dir.exists():
        shutil.rmtree(project_dir)
    images_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)

    # --- Copy images ---
    for i, art_path in enumerate(art_paths):
        shutil.copy2(art_path, images_dir / f"scene_{i}.png")

    # --- Copy/convert audio ---
    for i, voice_path in enumerate(voice_paths):
        dst = audio_dir / f"voice_{i}.mp3"
        if voice_path.lower().endswith(".mp3"):
            shutil.copy2(voice_path, dst)
        else:
            _convert_to_mp3(voice_path, str(dst))

    # Music
    music_dst = audio_dir / "music.mp3"
    if music_path.lower().endswith(".mp3"):
        shutil.copy2(music_path, music_dst)
    else:
        _convert_to_mp3(music_path, str(music_dst))

    # --- Get voice durations ---
    voice_durations_ms = []
    for vp in voice_paths:
        dur = _get_duration_ms(vp)
        voice_durations_ms.append(dur)
        print(f"  Voice: {Path(vp).name} = {dur/1000:.1f}s")

    # --- Build timeline ---
    if format == "short":
        timeline = _build_short_timeline(
            quotes, philosopher, voice_durations_ms, title, watermark, channel_name,
            equalizer_color=equalizer_color,
        )
    else:
        timeline = _build_multipart_timeline(
            quotes, philosopher, voice_durations_ms, title, watermark, channel_name,
            fmt=format, narration_segments=narration_segments,
            chapter_titles=chapter_titles, equalizer_color=equalizer_color,
        )

    # --- Write timeline.json ---
    with open(project_dir / "timeline.json", "w") as f:
        json.dump(timeline, f, indent=2)

    # --- Write metadata.json ---
    dims = FORMAT_DIMENSIONS[format]
    metadata = {
        "format": format,
        "width": dims["width"],
        "height": dims["height"],
        "fps": 30,
        "philosopher": philosopher,
        "channel": channel_name,
        "watermark": watermark,
    }
    with open(project_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    # --- Render via Remotion CLI ---
    remotion_cmd = str(VIDEO_ENGINE / "node_modules" / ".bin" / "remotion.cmd")
    render_cmd = f'"{remotion_cmd}" render {project_id} "{output_path}" --codec=h264 --crf=18'
    print(f"  Rendering: {render_cmd}")

    subprocess.run(
        render_cmd,
        cwd=str(VIDEO_ENGINE),
        check=True,
        timeout=600,
        shell=True,
    )

    # --- Cleanup project directory ---
    try:
        shutil.rmtree(project_dir)
    except Exception as e:
        print(f"  [cleanup] Warning: {e}")

    print(f"[render_remotion] Done: {output_path}")
    return output_path
