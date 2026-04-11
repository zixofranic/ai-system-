"""
Generate a test Story video (3-5 min) from ai_writer's story script.
Multiple scenes, each with unique art, voice-synced captions.
Does NOT upload — for local preview only.
"""

import os
import sys
import re
import json
import time
import random
import base64
import requests
from pathlib import Path
from datetime import datetime
from elevenlabs import ElevenLabs, VoiceSettings
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")
os.environ['IMAGEMAGICK_BINARY'] = r'C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe'

sys.path.insert(0, str(Path(__file__).parent))

COMFYUI_URL = "http://localhost:8188"
ELEVENLABS_KEY = os.environ["ELEVENLABS_API_KEY"]
WISDOM_VOICE = os.environ.get("ELEVENLABS_VOICE_WISDOM", "0ABJJI7ZYmWZBiUBMHUW")
GIBRAN_VOICE = os.environ.get("ELEVENLABS_VOICE_GIBRAN", "R68HwD2GzEdWfqYZP9FQ")

OUTPUT_DIRS = {
    "wisdom": Path("C:/AI/wisdom/output/stories"),
    "gibran": Path("C:/AI/gibran/output/stories"),
}
for d in OUTPUT_DIRS.values():
    d.mkdir(parents=True, exist_ok=True)

# Map philosopher to LoRA for scene art
PHILOSOPHER_LORA = {
    "Marcus Aurelius": "stoic_classical_v1.safetensors",
    "Seneca": "stoic_classical_v1.safetensors",
    "Epictetus": "stoic_classical_v1.safetensors",
    "Rumi": "persian_miniature_v1.safetensors",
    "Lao Tzu": "eastern_ink_v1.safetensors",
    "Nietzsche": "dark_expressionist_v1.safetensors",
    "Emerson": "romantic_landscape_v1.safetensors",
    "Gibran": "gibran_style_v1.safetensors",
}

PHILOSOPHER_MUSIC = {
    "Marcus Aurelius": "stoic_classical",
    "Seneca": "stoic_classical",
    "Epictetus": "stoic_classical",
    "Rumi": "persian_miniature",
    "Lao Tzu": "eastern_ink",
    "Nietzsche": "dark_expressionist",
    "Emerson": "romantic_landscape",
    "Gibran": "gibran",
}

PHILOSOPHER_CHANNEL = {
    "Marcus Aurelius": "wisdom", "Seneca": "wisdom", "Epictetus": "wisdom",
    "Rumi": "wisdom", "Lao Tzu": "wisdom", "Nietzsche": "wisdom",
    "Emerson": "wisdom", "Gibran": "gibran",
}


def _sanitize_text(text):
    """Replace special Unicode chars with ASCII equivalents for clean captions."""
    replacements = {
        "\u2014": " - ",   # em dash
        "\u2013": " - ",   # en dash
        "\u2018": "'",     # left single curly quote
        "\u2019": "'",     # right single curly quote
        "\u201c": '"',     # left double curly quote
        "\u201d": '"',     # right double curly quote
        "\u2026": "...",   # ellipsis
        "\u00a0": " ",     # non-breaking space
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _generate_voice_chatterbox(text, output_path, exaggeration=0.5, cfg_weight=0.5):
    """Generate voice via local Chatterbox TTS (free, unlimited)."""
    print("  Generating voice via Chatterbox TTS...")
    text = _sanitize_text(text)
    payload = {"text": text, "exaggeration": exaggeration, "cfg_weight": cfg_weight}

    voice_ref = Path("C:/AI/system/voice/recordings/ziad_reference_voice.wav")
    if voice_ref.exists():
        payload["voice_mode"] = "clone"
        payload["reference_audio_filename"] = voice_ref.name

    resp = requests.post(f"http://localhost:8004/tts", json=payload, timeout=300)
    resp.raise_for_status()

    # Chatterbox returns WAV — save it, then convert to MP3 for consistency
    wav_path = output_path.replace(".mp3", ".wav")
    with open(wav_path, "wb") as f:
        f.write(resp.content)

    # Convert WAV to MP3 via ffmpeg
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame",
                    "-b:a", "128k", output_path], capture_output=True)
    print(f"  Voice saved: {output_path}")
    return output_path


def _extract_timestamps_whisper(audio_path):
    """Extract word-level timestamps from audio using Whisper."""
    print("  Extracting word timestamps via Whisper...")
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(audio_path, word_timestamps=True)

        words = []
        for segment in result.get("segments", []):
            for w in segment.get("words", []):
                words.append({
                    "word": w["word"].strip(),
                    "start": w["start"],
                    "end": w["end"],
                })

        if words:
            print(f"  Whisper: {len(words)} words, {words[-1]['end']:.1f}s")
        return words if words else None
    except ImportError:
        print("  WARNING: whisper not installed, falling back to even-split timestamps")
        return None


def _generate_timestamps_from_text(text, audio_path):
    """Fallback: estimate word timestamps by evenly distributing across audio duration."""
    from moviepy.editor import AudioFileClip
    duration = AudioFileClip(audio_path).duration
    words_list = _sanitize_text(text).split()
    time_per_word = duration / len(words_list)
    words = []
    for i, w in enumerate(words_list):
        words.append({
            "word": w,
            "start": i * time_per_word,
            "end": (i + 1) * time_per_word,
        })
    print(f"  Even-split timestamps: {len(words)} words, {duration:.1f}s")
    return words


def _generate_voice_elevenlabs(text, voice_id, output_path):
    """Generate voice via ElevenLabs with word-level timestamps (paid API)."""
    print("  Generating voice via ElevenLabs...")
    text = _sanitize_text(text)
    client = ElevenLabs(api_key=ELEVENLABS_KEY)
    result = client.text_to_speech.convert_with_timestamps(
        voice_id=voice_id, text=text, model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        voice_settings=VoiceSettings(stability=0.70, similarity_boost=0.85,
                                      style=0.25, use_speaker_boost=True),
    )
    audio_bytes = base64.b64decode(result.audio_base_64)
    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    alignment = result.alignment
    if not alignment:
        return None

    words = []
    current_word = ""
    word_start = None
    for i, ch in enumerate(alignment.characters):
        if ch.strip() == "" or ch == " ":
            if current_word:
                words.append({"word": current_word,
                              "start": word_start,
                              "end": alignment.character_end_times_seconds[i - 1]})
                current_word = ""
                word_start = None
        else:
            if word_start is None:
                word_start = alignment.character_start_times_seconds[i]
            current_word += ch
    if current_word:
        words.append({"word": current_word, "start": word_start,
                      "end": alignment.character_end_times_seconds[-1]})

    print(f"  Voice: {len(words)} words, {words[-1]['end']:.1f}s")
    return words


def generate_voice_with_timestamps(text, voice_id, output_path, use_elevenlabs=False):
    """
    Generate voice + word-level timestamps.
    Default: Chatterbox (free) + Whisper for timestamps.
    Fallback: ElevenLabs (paid, has built-in timestamps).
    """
    if use_elevenlabs:
        return _generate_voice_elevenlabs(text, voice_id, output_path)

    # Try Chatterbox first
    try:
        _generate_voice_chatterbox(text, output_path)
    except Exception as e:
        print(f"  Chatterbox failed: {e}")
        print("  Falling back to ElevenLabs...")
        return _generate_voice_elevenlabs(text, voice_id, output_path)

    # Get word timestamps via Whisper, or fallback to even-split
    timestamps = _extract_timestamps_whisper(output_path)
    if not timestamps:
        timestamps = _generate_timestamps_from_text(text, output_path)

    return timestamps


def _copy_to_input(image_path):
    """Copy an image to ComfyUI's input directory so LoadImage can find it."""
    import shutil
    input_dir = Path("C:/AI/system/ComfyUI/input")
    input_dir.mkdir(exist_ok=True)
    dest = input_dir / Path(image_path).name
    shutil.copy2(image_path, dest)
    return dest.name


def _submit_and_wait(workflow, output_prefix):
    """Submit a ComfyUI workflow and wait for the result image path."""
    resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
    data = resp.json()
    if "prompt_id" not in data:
        print(f"    ComfyUI error: {data}")
        return None
    prompt_id = data["prompt_id"]
    for i in range(120):
        time.sleep(2)
        hist = requests.get(f"{COMFYUI_URL}/history/{prompt_id}").json()
        if prompt_id in hist:
            outputs = hist[prompt_id].get("outputs", {})
            if "8" in outputs:
                filename = outputs["8"]["images"][0]["filename"]
                return f"C:/AI/system/ComfyUI/output/{filename}"
    return None


def generate_scene_art(art_prompt, output_prefix, reference_image_path=None):
    """
    Generate art for a single scene via ComfyUI.
    If reference_image_path is provided, uses IP-Adapter to maintain
    character consistency with the reference image.
    """
    neg_prompt = "blurry, low quality, text, watermark, anime, cartoon, 3d render, deformed face, extra limbs, disfigured, bad anatomy, multiple people where one expected"

    if reference_image_path:
        # IP-Adapter workflow: use reference image for character consistency
        # IPAdapterUnifiedLoader auto-selects the right IP-Adapter model
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": art_prompt, "clip": ["1", 1]}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": neg_prompt, "clip": ["1", 1]}},
            "5": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 1216, "height": 832, "batch_size": 1}},
            # Load reference image — copy to ComfyUI input dir first
            "10": {"class_type": "LoadImage",
                   "inputs": {"image": _copy_to_input(reference_image_path)}},
            # Unified loader: auto-loads IP-Adapter + CLIP Vision
            # Use STANDARD preset — PLUS (high strength) copies entire composition
            "12": {"class_type": "IPAdapterUnifiedLoader",
                   "inputs": {
                       "model": ["1", 0],
                       "preset": "STANDARD (medium strength)",
                   }},
            # Apply IP-Adapter — style transfer only (layer 6 in SDXL)
            # Transfers art style/palette/mood without constraining composition
            "13": {"class_type": "IPAdapterAdvanced",
                   "inputs": {
                       "model": ["12", 0],
                       "ipadapter": ["12", 1],
                       "image": ["10", 0],
                       "weight": 0.5,
                       "weight_type": "style transfer",
                       "start_at": 0.0,
                       "end_at": 0.3,
                       "combine_embeds": "concat",
                       "embeds_scaling": "K+V w/ C penalty",
                   }},
            # Sample with IP-Adapter-conditioned model
            "6": {"class_type": "KSampler",
                  "inputs": {"seed": random.randint(1, 999999), "steps": 30, "cfg": 7.0,
                              "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                              "model": ["13", 0], "positive": ["3", 0], "negative": ["4", 0],
                              "latent_image": ["5", 0]}},
            "7": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "SaveImage",
                  "inputs": {"filename_prefix": output_prefix, "images": ["7", 0]}},
        }
    else:
        # No reference — base SDXL (Scene 1, establishing shot)
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": art_prompt, "clip": ["1", 1]}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": neg_prompt, "clip": ["1", 1]}},
            "5": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 1216, "height": 832, "batch_size": 1}},
            "6": {"class_type": "KSampler",
                  "inputs": {"seed": random.randint(1, 999999), "steps": 30, "cfg": 7.5,
                              "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                              "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
                              "latent_image": ["5", 0]}},
            "7": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "SaveImage",
                  "inputs": {"filename_prefix": output_prefix, "images": ["7", 0]}},
        }

    return _submit_and_wait(workflow, output_prefix)


def find_scene_word_boundaries(words, scenes_narration):
    """
    Match scene narrations to word timestamps.
    Returns list of (start_word_idx, end_word_idx) per scene.
    """
    boundaries = []
    word_idx = 0
    full_text = " ".join(w["word"] for w in words).lower()

    for scene_narr in scenes_narration:
        # Find first few words of this scene's narration in the word list
        scene_words = scene_narr.lower().split()[:6]
        target = " ".join(scene_words)

        # Search for the target starting from current position
        best_start = word_idx
        for search_idx in range(word_idx, min(word_idx + 50, len(words))):
            candidate = " ".join(w["word"].lower() for w in words[search_idx:search_idx + len(scene_words)])
            if candidate.startswith(target[:20]):
                best_start = search_idx
                break

        # Find the end by counting words in the scene narration
        scene_word_count = len(scene_narr.split())
        best_end = min(best_start + scene_word_count, len(words) - 1)

        boundaries.append((best_start, best_end))
        word_idx = best_end

    return boundaries


def _build_waveform_pulse(voice_path, total_duration, fps, canvas_w, canvas_h,
                          voice_start=0.0, color="#D4AF37", n_points=48,
                          wave_height=30, y_position=850):
    """
    Build a smooth waveform/pulse animation synced to the narration.
    Renders as a glowing horizontal waveform line that pulses with the voice.
    Positioned just above the captions.
    """
    import numpy as np
    import librosa
    from scipy.ndimage import uniform_filter1d
    from moviepy.editor import VideoClip

    # Analyze audio energy — single band (overall amplitude)
    y, sr = librosa.load(voice_path, sr=22050, mono=True)
    hop = int(sr / fps)
    # RMS energy per frame
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    rms = rms / (rms.max() + 1e-8)  # normalize to [0, 1]
    rms = uniform_filter1d(rms, size=3)  # smooth
    rms = np.clip(rms, 0.0, 1.0)

    # Also get spectral centroid for wave shape variation
    cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
    cent = cent / (cent.max() + 1e-8)
    cent = uniform_filter1d(cent, size=5)

    hex_c = color.lstrip("#")
    cr, cg, cb = (int(hex_c[i:i+2], 16) for i in (0, 2, 4))

    # Pre-compute wave x positions
    wave_w = int(canvas_w * 0.75)
    x_start = (canvas_w - wave_w) // 2
    seg_w = wave_w // n_points
    phases = np.linspace(0, np.pi * 2, n_points)

    def make_frame(t):
        frame = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
        adj_t = t - voice_start
        if adj_t < 0 or adj_t * fps >= len(rms):
            return frame

        idx = min(int(adj_t * fps), len(rms) - 1)
        amplitude = rms[idx]
        shape_var = cent[min(idx, len(cent) - 1)]

        freq_mod = 2.0 + shape_var * 3.0
        wave_vals = np.abs(np.sin(phases * freq_mod + adj_t * 4.0))
        heights = np.maximum(2, (wave_vals * amplitude * wave_height).astype(int))

        for p in range(n_points):
            px = x_start + p * seg_w
            px2 = px + seg_w
            h = heights[p]

            y1 = max(0, y_position - h)
            y2 = min(canvas_h - 1, y_position + h)
            if y2 <= y1:
                continue

            alpha = int(180 * amplitude)
            frame[y1:y2, px:px2, 0] = cr
            frame[y1:y2, px:px2, 1] = cg
            frame[y1:y2, px:px2, 2] = cb
            frame[y1:y2, px:px2, 3] = alpha

        return frame

    def rgb_frame(t):
        return make_frame(t)[:, :, :3]

    def mask_frame(t):
        return make_frame(t)[:, :, 3].astype(float) / 255.0

    rgb_clip = VideoClip(rgb_frame, duration=total_duration).set_fps(fps)
    mask_clip = VideoClip(mask_frame, duration=total_duration, ismask=True).set_fps(fps)
    rgb_clip = rgb_clip.set_mask(mask_clip)
    return rgb_clip


def assemble_story_video(story_data, voice_path, word_timestamps, art_paths,
                          music_path, output_path, channel="wisdom"):
    """Assemble a multi-scene story video."""
    from moviepy.editor import (AudioFileClip, ImageClip, TextClip, CompositeVideoClip,
                                 CompositeAudioClip, concatenate_audioclips, ColorClip,
                                 concatenate_videoclips)

    print("  Assembling story video...")
    narration = AudioFileClip(voice_path)
    voice_start = 2.0  # brief intro pause
    total_duration = narration.duration + voice_start + 3.0  # padding at end

    scenes = story_data["scenes"]
    scenes_narration = [s["narration"] for s in scenes]

    # Map word timestamps to scene boundaries
    boundaries = find_scene_word_boundaries(word_timestamps, scenes_narration)

    # Base background — prevents black frames during any timing gaps
    base_bg = ColorClip(size=(1920, 1080), color=(0, 0, 0)).set_duration(total_duration)
    scene_clips = [base_bg]

    # Build scene clips — each scene gets its own art background
    # Pre-calculate all scene boundaries so we can close timing gaps
    scene_times = []
    for i in range(len(scenes)):
        start_idx, end_idx = boundaries[i] if i < len(boundaries) else (0, len(word_timestamps) - 1)
        s_start = word_timestamps[start_idx]["start"] + voice_start
        s_end = word_timestamps[min(end_idx, len(word_timestamps) - 1)]["end"] + voice_start
        if i == 0:
            s_start = 0
        if i == len(scenes) - 1:
            s_end = total_duration
        scene_times.append((s_start, s_end))

    # Close gaps: each scene starts where the previous one ended
    for i in range(1, len(scene_times)):
        prev_end = scene_times[i - 1][1]
        curr_start = scene_times[i][0]
        if curr_start > prev_end:
            scene_times[i] = (prev_end, scene_times[i][1])

    for i, (scene, art_path) in enumerate(zip(scenes, art_paths)):
        if not art_path:
            continue

        scene_start, scene_end = scene_times[i]

        scene_duration = scene_end - scene_start

        # Art background with Ken Burns — 16:9 landscape
        bg = (ImageClip(art_path)
              .set_duration(scene_duration)
              .resize(width=2200)
              .set_position("center"))

        # Alternate zoom direction per scene for variety
        if i % 2 == 0:
            def zoom_in(t, d=scene_duration):
                return 1 + 0.06 * (t / d)
        else:
            def zoom_in(t, d=scene_duration):
                return 1.06 - 0.06 * (t / d)
        bg = bg.resize(zoom_in)

        # Set scene start time
        bg = bg.set_start(scene_start)

        # Crossfade between scenes
        if i > 0:
            bg = bg.crossfadein(1.5)

        scene_clips.append(bg)

    # Build caption clips from word timestamps
    # 16:9 landscape — captions in lower third
    caption_clips = []
    CAPTION_Y = 880
    MAX_WORDS = 8
    PUNCTUATION = {",", ".", "!", "?", ";", ":", "—"}

    chunks = []
    current_words = []
    for w in word_timestamps:
        current_words.append(w)
        at_punct = any(w["word"].endswith(p) for p in PUNCTUATION)
        if (at_punct and len(current_words) >= 2) or len(current_words) >= MAX_WORDS:
            chunks.append(current_words)
            current_words = []
    if current_words:
        chunks.append(current_words)

    for chunk in chunks:
        text = " ".join(w["word"] for w in chunk).lower()
        start = chunk[0]["start"] + voice_start
        end = chunk[-1]["end"] + voice_start
        if end - start < 1.0:
            end = start + 1.0

        txt = TextClip(text, fontsize=44, color="white", font="Georgia-Bold",
                       method="caption", size=(1600, None), align="center")
        txt_w, txt_h = txt.size

        pad_x, pad_y = 30, 12
        pill_w = min(txt_w + pad_x * 2, 1800)
        pill = (ColorClip(size=(pill_w, txt_h + pad_y * 2), color=(0, 0, 0))
                .set_opacity(0.55)
                .set_position(("center", CAPTION_Y - pad_y))
                .set_start(start).set_duration(end - start)
                .crossfadein(0.12).crossfadeout(0.12))

        txt = (txt.set_position(("center", CAPTION_Y))
               .set_start(start).set_duration(end - start)
               .crossfadein(0.12).crossfadeout(0.12))

        caption_clips.append(pill)
        caption_clips.append(txt)

    # Closing attribution — centered on screen for 16:9
    closing = story_data.get("closing_attribution", f"Inspired by the philosophy of {story_data['philosopher']}")
    attr_clip = (TextClip(closing, fontsize=40, color="#D4AF37",
                          font="Georgia-Italic", method="label")
                 .set_position(("center", "center"))
                 .set_start(total_duration - 5)
                 .set_duration(4)
                 .crossfadein(1.5).crossfadeout(1.5))

    # Watermark — bottom right for 16:9
    watermark_text = "Gibran Khalil Gibran" if channel == "gibran" else "Deep Echoes of Wisdom"
    watermark = (TextClip(watermark_text, fontsize=20, color="white",
                          font="Arial", method="label")
                 .set_position((1650, 1040))
                 .set_duration(total_duration)
                 .set_opacity(0.35))

    # --- Waveform pulse visualizer (synced to narration) ---
    waveform_clip = _build_waveform_pulse(voice_path, total_duration, 30,
                                           1920, 1080, voice_start)

    # Audio
    narration_audio = narration.set_start(voice_start)
    music = AudioFileClip(music_path)
    if music.duration < total_duration:
        music = concatenate_audioclips([music] * (int(total_duration / music.duration) + 1))
    music = music.subclip(0, total_duration).volumex(0.12)  # quieter for stories
    final_audio = CompositeAudioClip([music, narration_audio])

    # Compose everything
    all_clips = scene_clips + caption_clips + [waveform_clip, attr_clip, watermark]
    video = (CompositeVideoClip(all_clips, size=(1920, 1080))
             .set_duration(total_duration)
             .set_audio(final_audio)
             .fadein(2.0)
             .fadeout(3.0))

    video.write_videofile(output_path, fps=30, codec="libx264", audio_codec="aac",
                          bitrate="8000k", threads=8, preset="fast", logger=None)
    print(f"  Video saved: {output_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate a test Story video")
    parser.add_argument("--philosopher", default="Marcus Aurelius")
    parser.add_argument("--theme", default="betrayal")
    parser.add_argument("--setting", default=None)
    parser.add_argument("--mood", default=None)
    parser.add_argument("--script-json", default=None,
                        help="Path to pre-generated story script JSON (skip generation)")
    parser.add_argument("--use-elevenlabs", action="store_true",
                        help="Use ElevenLabs for voice (default: Chatterbox)")
    args = parser.parse_args()

    philosopher = args.philosopher
    channel = PHILOSOPHER_CHANNEL.get(philosopher, "wisdom")
    output_dir = OUTPUT_DIRS[channel]
    lora = PHILOSOPHER_LORA.get(philosopher, "stoic_classical_v1.safetensors")
    music_style = PHILOSOPHER_MUSIC.get(philosopher, "stoic_classical")
    voice_id = GIBRAN_VOICE if channel == "gibran" else WISDOM_VOICE
    first_name = philosopher.split()[0].lower()
    date_str = datetime.now().strftime('%Y-%m-%d')
    prefix = f"{date_str}_story_{first_name}"

    print(f"\n{'='*60}")
    print(f"  STORY VIDEO GENERATOR")
    print(f"  Philosopher: {philosopher}")
    print(f"  Theme: {args.theme}")
    print(f"  Channel: {channel}")
    print(f"  Output: {output_dir}")
    print(f"{'='*60}")

    # 1. Generate or load story script
    if args.script_json:
        print(f"\n  Loading script from {args.script_json}...")
        with open(args.script_json) as f:
            story = json.load(f)
    else:
        print(f"\n  Generating story script via Claude Sonnet...")
        from ai_writer import generate_story_script
        story = generate_story_script(
            philosopher=philosopher,
            theme=args.theme,
            setting=args.setting,
            mood=args.mood,
        )

    # Save script for reference
    script_path = str(output_dir / f"{prefix}_script.json")
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(story, f, indent=2, ensure_ascii=False)
    print(f"  Script saved: {script_path}")
    print(f"  Title: {story['title']}")
    print(f"  Scenes: {len(story['scenes'])}")
    word_count = len(story['story_script'].split())
    print(f"  Words: {word_count} (~{word_count // 150} min narration)")

    # 2. Generate voice for the full story (skip if voice + timestamps already exist)
    voice_path = str(output_dir / f"{prefix}_voice.mp3")
    ts_path = str(output_dir / f"{prefix}_timestamps.json")

    if Path(voice_path).exists() and Path(ts_path).exists():
        print(f"  Voice already exists: {voice_path}")
        with open(ts_path) as f:
            word_timestamps = json.load(f)
        print(f"  Timestamps loaded: {len(word_timestamps)} words")
    else:
        word_timestamps = generate_voice_with_timestamps(
            story["story_script"], voice_id, voice_path,
            use_elevenlabs=args.use_elevenlabs)
        if word_timestamps:
            with open(ts_path, "w") as f:
                json.dump(word_timestamps, f, indent=2)

    # 3. Generate art for each scene (IP-Adapter for character consistency)
    print(f"\n  Generating {len(story['scenes'])} scene images...")
    print(f"  IP-Adapter: Scene 1 = hero shot, Scenes 2+ use Scene 1 as reference")
    art_paths = []
    reference_image = None  # Scene 1 becomes the reference for all subsequent scenes

    for i, scene in enumerate(story["scenes"]):
        scene_prefix = f"{prefix}_scene{i+1}"
        print(f"  Scene {i+1}/{len(story['scenes'])}: {scene.get('mood', '')}")

        if i == 0:
            # Scene 1: no reference, establish the character
            art_path = generate_scene_art(scene["art_prompt"], scene_prefix)
            if art_path:
                reference_image = art_path  # Save as reference for subsequent scenes
                print(f"    Art (hero): {Path(art_path).name}")
        else:
            # Scenes 2+: use Scene 1 as IP-Adapter reference
            art_path = generate_scene_art(scene["art_prompt"], scene_prefix,
                                           reference_image_path=reference_image)
            if art_path:
                print(f"    Art (ref): {Path(art_path).name}")

        if art_path:
            art_paths.append(art_path)
        else:
            print(f"    ART FAILED for scene {i+1}")
            art_paths.append(None)

    # Check we have at least some art
    valid_art = [a for a in art_paths if a]
    if not valid_art:
        print("  ALL ART FAILED — aborting")
        return

    # Fill missing art with nearest valid art
    for i in range(len(art_paths)):
        if art_paths[i] is None:
            art_paths[i] = valid_art[0]

    # 4. Pick music
    music_dir = Path(f"C:/AI/system/music/{music_style}")
    tracks = list(music_dir.glob("*.mp3"))
    if not tracks:
        print(f"  WARNING: No music in {music_dir}")
        return
    music_path = str(random.choice(tracks))
    print(f"  Music: {Path(music_path).name}")

    # 5. Assemble video
    video_path = str(output_dir / f"{prefix}_video.mp4")
    assemble_story_video(story, voice_path, word_timestamps, art_paths,
                          music_path, video_path, channel)

    print(f"\n{'='*60}")
    print(f"  DONE — NOT uploaded (test only)")
    print(f"  Video: {video_path}")
    print(f"  Script: {script_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
