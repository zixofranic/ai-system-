"""
Multi-format Video Assembler for Wisdom Content Pipeline
========================================================
Assembles videos from components: art + voice + music + text overlays.
Supports short (9:16), midform (16:9), and longform (16:9) formats.

Features:
- Ken Burns effect (slow zoom/pan) on each art piece
- Crossfade transitions between art pieces
- Audio equalizer/visualizer bars synced to voice narration
- Background music mixed under voice at 15-20% volume
- Text overlays: quote, philosopher attribution, channel watermark
- Output: MP4 with libx264 + aac

Replaces the old assemble_short.py.

Usage:
    python assemble_video.py --quotes "Quote text" --philosopher "Marcus Aurelius" \
        --art image.png --voice narration.wav --music bg.mp3 --output out.mp4 \
        --format short --aspect-ratio 9:16
"""

import sys
import argparse
import numpy as np
from pathlib import Path

from moviepy.editor import (
    AudioFileClip, ImageClip, TextClip, CompositeVideoClip,
    CompositeAudioClip, ColorClip, concatenate_videoclips
)
from moviepy.video.fx.all import fadein, fadeout
import librosa


# ---------------------------------------------------------------------------
# Format presets
# ---------------------------------------------------------------------------
FORMAT_PRESETS = {
    "short": {
        "aspect": "9:16",
        "width": 1080,
        "height": 1920,
        "intro_pad": 3.0,        # dreamy slow intro
        "outro_pad": 3.0,        # graceful fade out
        "text_fade_start": 2.5,
        "transition_dur": 1.5,   # smooth crossfade
        "fps": 30,
        "bitrate": "8000k",
        "intro_fade": 2.5,       # slow fade from black
        "outro_fade": 2.5,       # slow fade to black
        "text_glow": True,        # ethereal glow on text
        "zoom_range": (1.0, 1.06), # gentle dreamy zoom
    },
    "midform": {
        "aspect": "16:9",
        "width": 1920,
        "height": 1080,
        "intro_pad": 3.0,
        "outro_pad": 3.0,
        "text_fade_start": 2.0,
        "transition_dur": 2.0,   # dreamy slow crossfade
        "fps": 30,
        "bitrate": "10000k",
        "intro_fade": 3.0,
        "outro_fade": 3.0,
        "text_glow": True,
        "zoom_range": (1.0, 1.05),
    },
    "longform": {
        "aspect": "16:9",
        "width": 1920,
        "height": 1080,
        "intro_pad": 4.0,        # cinematic slow open
        "outro_pad": 4.0,
        "text_fade_start": 2.0,
        "transition_dur": 2.5,   # dreamy transitions
        "fps": 30,
        "bitrate": "12000k",
        "intro_fade": 3.5,
        "outro_fade": 3.5,
        "text_glow": True,
        "zoom_range": (1.0, 1.04),
    },
}


# ---------------------------------------------------------------------------
# Dreamy visual effects
# ---------------------------------------------------------------------------
def _apply_dreamy_vignette(frame, intensity=0.3):
    """Add a soft dark vignette around edges for dreamy focus effect."""
    h, w = frame.shape[:2]
    Y, X = np.ogrid[:h, :w]
    center_y, center_x = h / 2, w / 2
    # Distance from center, normalized
    dist = np.sqrt((X - center_x)**2 / (w/2)**2 + (Y - center_y)**2 / (h/2)**2)
    # Vignette mask: 1 at center, darker at edges
    vignette = np.clip(1.0 - dist * intensity, 0, 1)
    vignette = vignette[:, :, np.newaxis]  # broadcast to RGB
    return (frame * vignette).astype(np.uint8)


def _create_glow_text(text, fontsize, color='white', glow_color=None,
                       font='Georgia-Bold', method='caption', size=None):
    """Create text with a soft ethereal glow behind it."""
    # Main text
    main = TextClip(
        text, fontsize=fontsize, color=color, font=font,
        method=method, size=size, stroke_color='black', stroke_width=1,
        align='center'
    )
    # Glow layer (larger, blurred-looking via slight transparency)
    if glow_color is None:
        glow_color = color
    glow = TextClip(
        text, fontsize=fontsize + 4, color=glow_color, font=font,
        method=method, size=size, align='center'
    ).set_opacity(0.3)
    return glow, main


# ---------------------------------------------------------------------------
# Equalizer / waveform visualizer helpers
# ---------------------------------------------------------------------------
def _analyze_audio_energy(audio_path: str, fps: int, n_bars: int = 10):
    """
    Use librosa to compute per-frame energy across *n_bars* frequency bands.
    Returns an array of shape (n_frames, n_bars) with values in [0, 1].
    """
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    hop_length = int(sr / fps)
    # Short-time Fourier transform
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop_length))
    # Split frequency bins into n_bars groups
    freq_bins = S.shape[0]
    band_size = max(1, freq_bins // n_bars)
    bands = []
    for i in range(n_bars):
        start = i * band_size
        end = min((i + 1) * band_size, freq_bins)
        band_energy = S[start:end, :].mean(axis=0)
        bands.append(band_energy)
    energy = np.stack(bands, axis=1)  # (n_frames, n_bars)
    # Normalize per-band to [0, 1]
    maxvals = energy.max(axis=0, keepdims=True)
    maxvals[maxvals == 0] = 1.0
    energy = energy / maxvals
    # Smooth with a small rolling window to avoid jitter
    from scipy.ndimage import uniform_filter1d
    energy = uniform_filter1d(energy, size=3, axis=0)
    energy = np.clip(energy, 0.0, 1.0)
    return energy


def _hex_to_rgb(hex_color: str):
    """Convert '#RRGGBB' to (R, G, B) tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _make_equalizer_frame(t, energy, fps, n_bars, bar_color_rgb,
                          canvas_w, canvas_h, bar_area_h=80):
    """
    Render a single RGBA frame for the equalizer overlay.
    Bars are drawn at the bottom of the canvas.
    """
    frame = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    frame_idx = int(t * fps)
    frame_idx = min(frame_idx, len(energy) - 1)
    levels = energy[frame_idx]  # (n_bars,)

    total_bar_width = int(canvas_w * 0.6)
    bar_w = total_bar_width // n_bars
    gap = max(2, bar_w // 5)
    bar_w -= gap
    x_offset = (canvas_w - total_bar_width) // 2
    y_bottom = canvas_h - 12  # small margin from very bottom

    for i in range(n_bars):
        h = int(levels[i] * bar_area_h)
        if h < 2:
            h = 2
        x1 = x_offset + i * (bar_w + gap)
        x2 = x1 + bar_w
        y1 = y_bottom - h
        y2 = y_bottom
        frame[y1:y2, x1:x2, 0] = bar_color_rgb[0]
        frame[y1:y2, x1:x2, 1] = bar_color_rgb[1]
        frame[y1:y2, x1:x2, 2] = bar_color_rgb[2]
        # Alpha with slight gradient (brighter at top)
        alpha_col = np.linspace(220, 160, y2 - y1).astype(np.uint8)
        for c in range(y2 - y1):
            frame[y1 + c, x1:x2, 3] = alpha_col[c]

    return frame


def _build_equalizer_clip(voice_path: str, duration: float, fps: int,
                          canvas_w: int, canvas_h: int,
                          equalizer_color: str, n_bars: int = 10,
                          voice_start: float = 0.0):
    """
    Build a VideoClip of animated equalizer bars synced to voice audio.
    """
    bar_color_rgb = _hex_to_rgb(equalizer_color)
    energy = _analyze_audio_energy(voice_path, fps, n_bars)

    def make_frame(t):
        # Adjust t for voice offset
        adjusted_t = t - voice_start
        if adjusted_t < 0 or adjusted_t * fps >= len(energy):
            # Return transparent frame when outside voice range
            return np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
        return _make_equalizer_frame(
            adjusted_t, energy, fps, n_bars, bar_color_rgb,
            canvas_w, canvas_h, bar_area_h=80
        )

    from moviepy.editor import VideoClip
    eq_clip = VideoClip(make_frame, duration=duration, ismask=False)
    # moviepy doesn't natively support RGBA in CompositeVideoClip well,
    # so we split into RGB + mask
    def rgb_frame(t):
        f = make_frame(t)
        return f[:, :, :3]

    def mask_frame(t):
        f = make_frame(t)
        return f[:, :, 3].astype(float) / 255.0

    rgb_clip = VideoClip(rgb_frame, duration=duration).set_fps(fps)
    mask_clip = VideoClip(mask_frame, duration=duration, ismask=True).set_fps(fps)
    rgb_clip = rgb_clip.set_mask(mask_clip)
    return rgb_clip


# ---------------------------------------------------------------------------
# Ken Burns effect
# ---------------------------------------------------------------------------
def _apply_ken_burns(image_clip, duration, target_w, target_h, zoom_range=(1.0, 1.08)):
    """
    Apply a slow zoom (Ken Burns) effect to an image clip.
    The image is initially scaled to fill the frame and zooms gently.
    """
    # Scale image to cover the frame with some headroom for zoom
    img_w, img_h = image_clip.size
    scale = max(target_w / img_w, target_h / img_h) * zoom_range[1]
    base_clip = image_clip.resize(scale).set_duration(duration)

    z_start, z_end = zoom_range

    def zoom_func(t):
        progress = t / max(duration, 0.001)
        z = z_start + (z_end - z_start) * progress
        return z

    zoomed = base_clip.resize(zoom_func).set_position("center")
    return zoomed


# ---------------------------------------------------------------------------
# Section builder (one quote segment)
# ---------------------------------------------------------------------------
def _build_section(quote: str, philosopher: str, art_path: str,
                   voice_path: str, preset: dict, channel_name: str,
                   equalizer_color: str, section_index: int = 0,
                   is_short: bool = False):
    """
    Build a single quote section: art background + text + equalizer.
    Returns (video_clip_without_audio, voice_audio_clip, section_duration).
    """
    W, H = preset["width"], preset["height"]
    fps = preset["fps"]

    voice_audio = AudioFileClip(voice_path)
    voice_dur = voice_audio.duration
    intro_pad = preset["intro_pad"] if section_index == 0 else 0.5
    outro_pad = preset["outro_pad"] if is_short else 0.5
    section_dur = intro_pad + voice_dur + outro_pad

    # --- Art background with Ken Burns ---
    art_img = ImageClip(art_path)
    art_bg = _apply_ken_burns(art_img, section_dur, W, H)

    # Crop to exact canvas size
    art_bg = art_bg.set_position("center")
    canvas = ColorClip(size=(W, H), color=(0, 0, 0)).set_duration(section_dur)
    art_layer = CompositeVideoClip([canvas, art_bg], size=(W, H)).set_duration(section_dur)

    # --- Quote text overlay ---
    font_size_quote = 52 if preset["aspect"] == "9:16" else 44
    text_w = int(W * 0.82)
    quote_y_rel = 0.50 if preset["aspect"] == "9:16" else 0.38

    quote_clip = (
        TextClip(
            quote,
            fontsize=font_size_quote,
            color="white",
            font="Georgia-Bold",
            method="caption",
            size=(text_w, None),
            stroke_color="black",
            stroke_width=2,
            align="center",
        )
        .set_position(("center", quote_y_rel), relative=True)
        .set_duration(section_dur)
        .set_start(0)
        .crossfadein(1.0)
        .crossfadeout(0.8)
    )

    # --- Attribution ---
    font_size_attr = 36 if preset["aspect"] == "9:16" else 30
    attr_y_rel = 0.78 if preset["aspect"] == "9:16" else 0.72
    attr_clip = (
        TextClip(
            f"-- {philosopher}",
            fontsize=font_size_attr,
            color="#D4AF37",
            font="Georgia-Italic" if sys.platform != "win32" else "Georgia",
            method="label",
        )
        .set_position(("center", attr_y_rel), relative=True)
        .set_duration(section_dur)
        .set_start(0)
        .crossfadein(1.5)
    )

    # --- Channel watermark ---
    wm_clip = (
        TextClip(
            channel_name,
            fontsize=24,
            color="white",
            font="Arial",
            method="label",
        )
        .set_position((0.05, 0.93), relative=True)
        .set_duration(section_dur)
        .set_opacity(0.6)
    )

    # --- Equalizer ---
    eq_clip = _build_equalizer_clip(
        voice_path, section_dur, fps, W, H,
        equalizer_color, n_bars=10, voice_start=intro_pad,
    )

    # Compose section video (no audio yet)
    section_video = CompositeVideoClip(
        [art_layer, quote_clip, attr_clip, wm_clip, eq_clip],
        size=(W, H),
    ).set_duration(section_dur)

    # Voice audio with offset for intro pad
    voice_audio = voice_audio.set_start(intro_pad)

    return section_video, voice_audio, section_dur


# ---------------------------------------------------------------------------
# Main assembler
# ---------------------------------------------------------------------------
def assemble_video(
    quotes: list,
    philosopher: str,
    art_paths: list,
    voice_paths: list,
    music_path: str,
    output_path: str,
    format: str = "short",
    aspect_ratio: str = "9:16",
    channel_name: str = "Wisdom",
    equalizer_color: str = "#D4AF37",
):
    """
    Assemble a complete video from components.

    Args:
        quotes:           List of quote strings (1 for short, 3-5 for midform).
        philosopher:      Philosopher name for attribution.
        art_paths:         One image path per quote.
        voice_paths:       One voice narration file per quote.
        music_path:        Path to background music file.
        output_path:       Where to write the final MP4.
        format:            'short' | 'midform' | 'longform'
        aspect_ratio:      '9:16' | '16:9' (overridden by format default if not set).
        channel_name:      Watermark text.
        equalizer_color:   Hex color for equalizer bars.
    """
    if format not in FORMAT_PRESETS:
        raise ValueError(f"Unknown format '{format}'. Use: short, midform, longform")

    preset = FORMAT_PRESETS[format].copy()
    # Allow aspect ratio override
    if aspect_ratio == "16:9" and preset["aspect"] == "9:16":
        preset["width"], preset["height"] = 1920, 1080
        preset["aspect"] = "16:9"
    elif aspect_ratio == "9:16" and preset["aspect"] == "16:9":
        preset["width"], preset["height"] = 1080, 1920
        preset["aspect"] = "9:16"

    n_quotes = len(quotes)
    assert len(art_paths) == n_quotes, "Need one art image per quote"
    assert len(voice_paths) == n_quotes, "Need one voice file per quote"

    W, H = preset["width"], preset["height"]
    fps = preset["fps"]
    transition_dur = preset["transition_dur"]
    is_short = (format == "short")

    print(f"[assemble_video] Format={format}  Size={W}x{H}  Quotes={n_quotes}")

    # Build each section
    sections = []
    voice_audios = []
    cumulative_time = 0.0

    for i in range(n_quotes):
        print(f"  Building section {i + 1}/{n_quotes}: {quotes[i][:50]}...")
        section_vid, voice_aud, sec_dur = _build_section(
            quote=quotes[i],
            philosopher=philosopher,
            art_path=art_paths[i],
            voice_path=voice_paths[i],
            preset=preset,
            channel_name=channel_name,
            equalizer_color=equalizer_color,
            section_index=i,
            is_short=is_short,
        )

        # Offset section start time
        section_vid = section_vid.set_start(cumulative_time)
        # Offset voice audio to absolute timeline
        voice_aud = voice_aud.set_start(
            cumulative_time + (preset["intro_pad"] if i == 0 else 0.5)
        )

        sections.append(section_vid)
        voice_audios.append(voice_aud)

        if i < n_quotes - 1:
            # Next section starts with overlap for crossfade
            cumulative_time += sec_dur - transition_dur
        else:
            cumulative_time += sec_dur

    total_duration = cumulative_time

    # Compose all sections with crossfade
    if n_quotes == 1:
        final_video = sections[0]
    else:
        # Apply crossfade: each section fades in over transition_dur
        for i in range(1, len(sections)):
            sections[i] = sections[i].crossfadein(transition_dur)
        final_video = CompositeVideoClip(sections, size=(W, H)).set_duration(total_duration)

    # --- Audio mixing ---
    # Background music at 15-20% volume
    music = AudioFileClip(music_path)
    if music.duration < total_duration:
        # Loop music to cover full duration
        repeats = int(np.ceil(total_duration / music.duration))
        from moviepy.editor import concatenate_audioclips
        music = concatenate_audioclips([music] * repeats)
    music = music.subclip(0, total_duration).volumex(0.17)

    # Combine voice tracks + music
    all_audio = voice_audios + [music]
    mixed_audio = CompositeAudioClip(all_audio).set_duration(total_duration)

    final_video = final_video.set_audio(mixed_audio)

    # Global fade in/out
    final_video = final_video.fadein(0.5).fadeout(0.8)

    # Export
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Rendering to {output_path}  ({total_duration:.1f}s)")
    final_video.write_videofile(
        output_path,
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        bitrate=preset["bitrate"],
        threads=8,
        preset="fast",
    )
    print(f"[assemble_video] Done: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Assemble a Wisdom video from art, voice, music, and text."
    )
    parser.add_argument("--quotes", nargs="+", required=True,
                        help="Quote text(s). One per section.")
    parser.add_argument("--philosopher", required=True)
    parser.add_argument("--art", nargs="+", required=True,
                        help="Art image path(s), one per quote.")
    parser.add_argument("--voice", nargs="+", required=True,
                        help="Voice narration path(s), one per quote.")
    parser.add_argument("--music", required=True,
                        help="Background music file path.")
    parser.add_argument("--output", required=True,
                        help="Output MP4 file path.")
    parser.add_argument("--format", default="short",
                        choices=["short", "midform", "longform"])
    parser.add_argument("--aspect-ratio", default=None,
                        choices=["9:16", "16:9"],
                        help="Override default aspect ratio for the format.")
    parser.add_argument("--channel", default="Wisdom",
                        help="Channel watermark name.")
    parser.add_argument("--eq-color", default="#D4AF37",
                        help="Equalizer bar color (hex).")

    args = parser.parse_args()

    aspect = args.aspect_ratio
    if aspect is None:
        aspect = FORMAT_PRESETS[args.format]["aspect"]

    assemble_video(
        quotes=args.quotes,
        philosopher=args.philosopher,
        art_paths=args.art,
        voice_paths=args.voice,
        music_path=args.music,
        output_path=args.output,
        format=args.format,
        aspect_ratio=aspect,
        channel_name=args.channel,
        equalizer_color=args.eq_color,
    )


if __name__ == "__main__":
    main()
