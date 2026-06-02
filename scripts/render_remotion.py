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
FELLOWS_ENDCARD_MP4 = VIDEO_ENGINE / "public" / "fellows_endcard.mp4"
# Channels that should receive the FELLOWS end card appended after render.
# Other channels (wisdom, gibran) keep their existing closing behavior.
ENDCARD_CHANNELS = {"na", "aa"}

FORMAT_DIMENSIONS = {
    "short":          {"width": 1080, "height": 1920},
    "story_vertical": {"width": 1080, "height": 1920},
    "midform":        {"width": 1920, "height": 1080},
    "longform":       {"width": 1920, "height": 1080},
}

# Padding constants (milliseconds)
INTRO_PAD_MS = 2500       # silence before first voice
OUTRO_PAD_MS = 2500       # silence after last voice
SECTION_GAP_MS = 800      # gap between voice sections (midform/longform)
ATTR_DELAY_MS = 400       # attribution appears slightly after quote

# Short Cut (NA/AA retention format) overrides. The 2.5s default intro pad
# is the #1 hook-killer — viewers hit 2.5s of silent Ken Burns and bounce.
# Short Cut opens voice + hook card almost immediately and trims the tail.
SHORT_CUT_INTRO_PAD_MS = 300
SHORT_CUT_OUTRO_PAD_MS = 1000
SHORT_CUT_MAX_CONTENT_MS = 45000   # hard ceiling for the content (excl. endcard)


def _maybe_append_fellows_endcard(video_path: str, channel_slug: str) -> str:
    """Append the FELLOWS end card to NA/AA renders. Non-fatal on failure —
    if anything goes wrong we keep the original MP4 untouched and log a
    warning so the publish path still ships."""
    if channel_slug not in ENDCARD_CHANNELS:
        return video_path
    if not FELLOWS_ENDCARD_MP4.exists():
        print(f"  [endcard] missing at {FELLOWS_ENDCARD_MP4}, skipping")
        return video_path
    try:
        video_p = Path(video_path)
        tmp = video_p.with_name(video_p.stem + "_with_endcard.mp4")
        # Re-encode via concat filter — tolerates minor stream-param mismatches
        # between Remotion output and the static end-card MP4.
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video_p), "-i", str(FELLOWS_ENDCARD_MP4),
            "-filter_complex",
            "[0:v:0][0:a:0][1:v:0][1:a:0]concat=n=2:v=1:a=1[outv][outa]",
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24",
            "-c:a", "aac", "-b:a", "192k",
            str(tmp),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        tmp.replace(video_p)
        print(f"  [endcard] appended FELLOWS end card")
    except Exception as e:
        print(f"  [endcard] WARN: append failed, keeping original. {type(e).__name__}: {e}")
    return video_path


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


def _copy_and_loop_music(music_path: str, dst: str, voice_paths: list):
    """
    Copy/convert the music file to dst, looping it if needed to cover
    the total video duration (estimated from voice clips + padding).

    Uses ffmpeg's -stream_loop to seamlessly concatenate the track
    enough times to cover the full duration, then trims to exact length.
    """
    # Estimate total video duration from voice clips
    total_voice_ms = 0
    for vp in voice_paths:
        total_voice_ms += _get_duration_ms(vp)
    # Add intro/outro/section padding — generous estimate so music covers all
    estimated_duration_ms = total_voice_ms + INTRO_PAD_MS + OUTRO_PAD_MS + 5000

    music_duration_ms = _get_duration_ms(music_path)

    if music_duration_ms >= estimated_duration_ms:
        # Music track is long enough — just copy/convert
        if music_path.lower().endswith(".mp3"):
            import shutil
            shutil.copy2(music_path, dst)
        else:
            _convert_to_mp3(music_path, dst)
    else:
        # Music track is shorter than the video — loop it
        loops_needed = int(estimated_duration_ms / music_duration_ms) + 1
        target_sec = estimated_duration_ms / 1000
        print(f"  [music] Looping {loops_needed}x to cover {target_sec:.0f}s "
              f"(track is {music_duration_ms/1000:.0f}s)")
        subprocess.run(
            ["ffmpeg", "-y",
             "-stream_loop", str(loops_needed),
             "-i", music_path,
             "-t", str(target_sec),
             "-codec:a", "libmp3lame", "-b:a", "192k",
             dst],
            capture_output=True, check=True,
        )


def _build_short_timeline(
    quotes, philosopher, voice_durations_ms, title, watermark, channel_slug,
    equalizer_color=None, short_cut=False, hook="",
):
    """Build timeline for a single-quote short video.

    Two modes:
      - default: 2.5s intro pad, full quote shown for the whole voice span.
      - short_cut: collapsed intro (300ms), a HOOK card over the first ~3s,
        then the body scroll. The voice file is hook+body concatenated, so
        we split the on-screen overlays at the hook/body boundary by word
        proportion (no whisper pass — within ~0.3-0.5s, fine for a card→
        scroll handoff).
    """
    voice_ms = voice_durations_ms[0]
    do_short_cut = bool(short_cut and hook)

    intro_pad = SHORT_CUT_INTRO_PAD_MS if do_short_cut else INTRO_PAD_MS
    outro_pad = SHORT_CUT_OUTRO_PAD_MS if do_short_cut else OUTRO_PAD_MS
    total_ms = intro_pad + voice_ms + outro_pad

    metadata = {
        "format": "short",
        "width": 1080, "height": 1920, "fps": 30,
        "philosopher": philosopher,
        # MUST be the slug ("na" / "aa" / "wisdom" / "gibran"), not the
        # human name. ShortVideo.tsx gates monologue (scrolling) overlay
        # on `channel === "na" || "aa"` — passing the human label
        # ("One Day At A Time", "Easy Does It") silently falls back to
        # the static aphorism overlay.
        "channel": channel_slug,
        "watermark": watermark,
        "equalizerColor": equalizer_color,
    }

    elements = [{
        "startMs": 0,
        "endMs": total_ms,
        "imageUrl": "scene_0",
        "enterTransition": "fade",
        "exitTransition": "fade",
        "animations": [{
            "type": "scale", "from": 1.0, "to": 1.06,
            "startMs": 0, "endMs": total_ms,
        }],
    }]

    audio = [
        {"startMs": intro_pad, "endMs": intro_pad + voice_ms, "audioUrl": "voice_0"},
        {"startMs": 0, "endMs": total_ms, "audioUrl": "music"},
    ]

    if do_short_cut:
        # --- Duration guard: warn/fail if content runs past the ceiling. ---
        if total_ms > SHORT_CUT_MAX_CONTENT_MS:
            print(f"  [short-cut] WARNING: content is {total_ms/1000:.1f}s, "
                  f"over the {SHORT_CUT_MAX_CONTENT_MS/1000:.0f}s ceiling. "
                  f"Script is too long — shorten the body.")

        # Split the voice span at the hook/body boundary by word proportion.
        body_text = quotes[0]
        hook_words = max(1, len(hook.split()))
        body_words = max(1, len(body_text.split()))
        hook_frac = hook_words / (hook_words + body_words)
        hook_ms = voice_ms * hook_frac
        hook_end = intro_pad + hook_ms
        voice_end = intro_pad + voice_ms

        text = [
            # HOOK card — payoff, first ~3s, big centered card.
            {
                "startMs": intro_pad,
                "endMs": hook_end,
                "text": hook,
                "position": "center",
                "role": "hook",
            },
            # BODY — scrolling monologue for the remainder.
            {
                "startMs": hook_end,
                "endMs": voice_end,
                "text": body_text,
                "position": "center",
                "role": "quote",
            },
            {
                "startMs": hook_end + ATTR_DELAY_MS,
                "endMs": voice_end,
                "text": f"-- {philosopher}",
                "position": "bottom",
                "role": "attribution",
            },
        ]
        print(f"  [short-cut] total {total_ms/1000:.1f}s "
              f"(intro {intro_pad}ms + voice {voice_ms/1000:.1f}s + outro {outro_pad}ms); "
              f"hook card {hook_ms/1000:.1f}s")
    else:
        text = [
            {
                "startMs": intro_pad,
                "endMs": intro_pad + voice_ms,
                "text": quotes[0],
                "position": "center",
                "role": "quote",
            },
            {
                "startMs": intro_pad + ATTR_DELAY_MS,
                "endMs": intro_pad + voice_ms,
                "text": f"-- {philosopher}",
                "position": "bottom",
                "role": "attribution",
            },
        ]

    return {
        "shortTitle": title or f"{philosopher}",
        "elements": elements,
        "text": text,
        "audio": audio,
        "metadata": metadata,
    }


def _build_multipart_timeline(
    quotes, philosopher, voice_durations_ms, title, watermark, channel_slug,
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
            # Slug, not human label — see note in _build_short_timeline.
            "channel": channel_slug,
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
    # The CHANNEL SLUG ("wisdom" / "gibran" / "na" / "aa"), NOT the human
    # name. Goes into timeline.metadata.channel which ShortVideo.tsx reads
    # to gate the monologue (scrolling) overlay. `channel_name` kwarg is
    # accepted for back-compat — internal logic uses channel_slug.
    channel_slug: str = "wisdom",
    title: str = None,
    watermark: str = "Deep Echoes of Wisdom",
    narration_segments: list = None,
    chapter_titles: list = None,
    equalizer_color: str = None,
    # Short Cut mode (NA/AA retention format). When short_cut=True and a
    # non-empty hook is given, the short timeline collapses the intro pad,
    # adds a hook card in the first ~3s, and offsets the body scroll.
    short_cut: bool = False,
    hook: str = "",
    # Ignored (kept for call-site compatibility)
    aspect_ratio: str = None,
    channel_name: str = None,
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

    # Back-compat: callers used to pass `channel_name=` (the human label).
    # If someone still does, treat it as the slug only when it looks like one
    # (lowercase short token); otherwise keep the explicit channel_slug arg.
    if channel_name is not None:
        cn = channel_name.strip().lower()
        if cn in ("wisdom", "gibran", "na", "aa"):
            channel_slug = cn
        else:
            print(f"  [render_remotion] WARN: channel_name='{channel_name}' is not a known slug; "
                  f"falling back to channel_slug='{channel_slug}'. Update caller to pass channel_slug.")

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

    # Music — loop to cover the full video if the track is shorter than
    # the total duration. Most library tracks are 2-4 minutes but 6-min
    # stories need 5:30+ of background music. Without looping, the second
    # half of the story plays in silence.
    music_dst = audio_dir / "music.mp3"
    _copy_and_loop_music(music_path, str(music_dst), voice_paths)

    # --- Get voice durations ---
    voice_durations_ms = []
    for vp in voice_paths:
        dur = _get_duration_ms(vp)
        voice_durations_ms.append(dur)
        print(f"  Voice: {Path(vp).name} = {dur/1000:.1f}s")

    # --- Build timeline ---
    if format == "short":
        timeline = _build_short_timeline(
            quotes, philosopher, voice_durations_ms, title, watermark, channel_slug,
            equalizer_color=equalizer_color,
            short_cut=short_cut, hook=hook,
        )
    else:
        timeline = _build_multipart_timeline(
            quotes, philosopher, voice_durations_ms, title, watermark, channel_slug,
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
        "channel": channel_slug,
        "watermark": watermark,
    }
    with open(project_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    # --- Render via Remotion CLI ---
    remotion_cmd = str(VIDEO_ENGINE / "node_modules" / ".bin" / "remotion.cmd")
    # CRF 24 keeps 60-90s 1080x1920 shorts under ~25MB (Supabase Storage's
    # 50MB per-object default). CRF 18 produced ~90MB+ files on the 90s AA
    # monologue and the TUS upload rejected with 413 Maximum size exceeded.
    render_cmd = f'"{remotion_cmd}" render {project_id} "{output_path}" --codec=h264 --crf=24'
    print(f"  Rendering: {render_cmd}")

    subprocess.run(
        render_cmd,
        cwd=str(VIDEO_ENGINE),
        check=True,
        timeout=600,
        shell=True,
    )

    # --- Post-process: append FELLOWS end card for na/aa shorts ---
    _maybe_append_fellows_endcard(output_path, channel_slug)

    # --- Cleanup project directory ---
    try:
        shutil.rmtree(project_dir)
    except Exception as e:
        print(f"  [cleanup] Warning: {e}")

    print(f"[render_remotion] Done: {output_path}")
    return output_path
