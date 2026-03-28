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


def generate_voice_with_timestamps(text, voice_id, output_path):
    """Generate voice with word-level timestamps."""
    print("  Generating voice with timestamps...")
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


def generate_scene_art(art_prompt, output_prefix, use_lora=None):
    """
    Generate art for a single scene via ComfyUI.
    Stories use base SDXL (no LoRA) for style flexibility.
    Shorts use philosopher-specific LoRAs.
    """
    if use_lora:
        # With LoRA (for Shorts)
        lora_trigger = use_lora.replace("_v1.safetensors", "").replace("_", " ")
        full_prompt = f"{lora_trigger}, {art_prompt}"
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
            "2": {"class_type": "LoraLoader",
                  "inputs": {"lora_name": use_lora, "strength_model": 0.8, "strength_clip": 0.8,
                              "model": ["1", 0], "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": full_prompt, "clip": ["2", 1]}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "blurry, low quality, text, watermark, anime, cartoon, 3d render, deformed face, extra limbs",
                              "clip": ["2", 1]}},
            "5": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 1216, "height": 832, "batch_size": 1}},
            "6": {"class_type": "KSampler",
                  "inputs": {"seed": random.randint(1, 999999), "steps": 28, "cfg": 7.0,
                              "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                              "model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0],
                              "latent_image": ["5", 0]}},
            "7": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "SaveImage",
                  "inputs": {"filename_prefix": output_prefix, "images": ["7", 0]}},
        }
    else:
        # No LoRA — base SDXL for stories (better style flexibility)
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": art_prompt, "clip": ["1", 1]}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "blurry, low quality, text, watermark, anime, cartoon, 3d render, deformed face, extra limbs, disfigured, bad anatomy",
                              "clip": ["1", 1]}},
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

    resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
    prompt_id = resp.json()["prompt_id"]
    for i in range(120):
        time.sleep(2)
        hist = requests.get(f"{COMFYUI_URL}/history/{prompt_id}").json()
        if prompt_id in hist:
            outputs = hist[prompt_id].get("outputs", {})
            if "8" in outputs:
                filename = outputs["8"]["images"][0]["filename"]
                return f"C:/AI/system/ComfyUI/output/{filename}"
    return None


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

    # Build scene clips — each scene gets its own art background
    scene_clips = []
    for i, (scene, art_path) in enumerate(zip(scenes, art_paths)):
        if not art_path:
            continue

        start_idx, end_idx = boundaries[i] if i < len(boundaries) else (0, len(word_timestamps) - 1)

        # Scene timing from word timestamps
        scene_start = word_timestamps[start_idx]["start"] + voice_start
        scene_end = word_timestamps[min(end_idx, len(word_timestamps) - 1)]["end"] + voice_start

        # First scene starts at 0, last scene extends to end
        if i == 0:
            scene_start = 0
        if i == len(scenes) - 1:
            scene_end = total_duration

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

    # Audio
    narration_audio = narration.set_start(voice_start)
    music = AudioFileClip(music_path)
    if music.duration < total_duration:
        music = concatenate_audioclips([music] * (int(total_duration / music.duration) + 1))
    music = music.subclip(0, total_duration).volumex(0.12)  # quieter for stories
    final_audio = CompositeAudioClip([music, narration_audio])

    # Compose everything
    all_clips = scene_clips + caption_clips + [attr_clip, watermark]
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
    args = parser.parse_args()

    philosopher = args.philosopher
    channel = PHILOSOPHER_CHANNEL.get(philosopher, "wisdom")
    output_dir = OUTPUT_DIRS[channel]
    lora = PHILOSOPHER_LORA.get(philosopher, "stoic_classical_v1.safetensors")
    music_style = PHILOSOPHER_MUSIC.get(philosopher, "stoic_classical")
    voice_id = GIBRAN_VOICE if channel == "gibran" else WISDOM_VOICE
    slug = philosopher.lower().replace(" ", "_")
    prefix = f"story_{slug}_{datetime.now().strftime('%H%M')}"

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

    # 2. Generate voice for the full story
    voice_path = str(output_dir / f"{prefix}_voice.mp3")
    word_timestamps = generate_voice_with_timestamps(story["story_script"], voice_id, voice_path)

    if word_timestamps:
        ts_path = str(output_dir / f"{prefix}_timestamps.json")
        with open(ts_path, "w") as f:
            json.dump(word_timestamps, f, indent=2)

    # 3. Generate art for each scene
    print(f"\n  Generating {len(story['scenes'])} scene images...")
    art_paths = []
    for i, scene in enumerate(story["scenes"]):
        scene_prefix = f"{prefix}_scene{i+1}"
        print(f"  Scene {i+1}/{len(story['scenes'])}: {scene.get('mood', '')}")
        # Stories use base SDXL (no LoRA) for style flexibility and consistency
        art_path = generate_scene_art(scene["art_prompt"], scene_prefix)
        if art_path:
            print(f"    Art: {Path(art_path).name}")
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
