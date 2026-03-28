"""
Generate a single test Short with voice-synced captions.
Uses ElevenLabs convert_with_timestamps for precise word timing.
Does NOT upload to YouTube — for local preview only.
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

COMFYUI_URL = "http://localhost:8188"
OLLAMA_URL = "http://localhost:11434"
ELEVENLABS_KEY = os.environ["ELEVENLABS_API_KEY"]

# Voices
WISDOM_VOICE = os.environ.get("ELEVENLABS_VOICE_WISDOM", "0ABJJI7ZYmWZBiUBMHUW")
GIBRAN_VOICE = os.environ.get("ELEVENLABS_VOICE_GIBRAN", "R68HwD2GzEdWfqYZP9FQ")

OUTPUT_DIR = Path("C:/AI/wisdom/output/shorts")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Philosopher configs
PHILOSOPHER_CONFIG = {
    "Marcus Aurelius": {
        "lora": "stoic_classical_v1.safetensors",
        "music": "stoic_classical",
        "art_prompt": "stoic_classical painting, roman emperor meditating alone in marble palace at dawn, dramatic golden light through columns, oil painting masterpiece",
        "ollama_model": "marcus_aurelius",
        "voice": WISDOM_VOICE,
        "channel": "wisdom",
    },
    "Gibran": {
        "lora": "gibran_style_v1.safetensors",
        "music": "gibran",
        "art_prompt": "ethereal mystical painting, prophet standing on hilltop overlooking vast sea at twilight, flowing robes in wind, warm golden amber light, oil painting masterpiece",
        "ollama_model": "gibran",
        "voice": GIBRAN_VOICE,
        "channel": "gibran",
    },
}


def generate_quote(model, prompt):
    """Generate a quote using Ollama."""
    print("  Generating quote via Ollama...")
    resp = requests.post(f"{OLLAMA_URL}/api/generate",
                         json={"model": model, "prompt": prompt, "stream": False},
                         timeout=120)
    quote = resp.json()["response"].strip().replace('"', '').strip()
    # Clean any model artifacts
    if "[" in quote:
        quote = quote.split("]")[-1].strip()
    return quote


def generate_voice_with_timestamps(text, voice_id, output_path):
    """
    Generate voice audio with character-level timestamps.
    Returns list of word dicts: [{"word": "...", "start": float, "end": float}, ...]
    """
    print("  Generating voice with timestamps...")
    client = ElevenLabs(api_key=ELEVENLABS_KEY)

    result = client.text_to_speech.convert_with_timestamps(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        voice_settings=VoiceSettings(
            stability=0.70,
            similarity_boost=0.85,
            style=0.25,
            use_speaker_boost=True,
        ),
    )

    # Decode and save audio
    audio_bytes = base64.b64decode(result.audio_base_64)
    with open(output_path, "wb") as f:
        f.write(audio_bytes)
    print(f"  Audio saved: {output_path}")

    # Extract word-level timing from character alignment
    alignment = result.alignment
    if not alignment:
        print("  WARNING: No alignment data returned, falling back to estimate")
        return None

    chars = alignment.characters
    starts = alignment.character_start_times_seconds
    ends = alignment.character_end_times_seconds

    # Reconstruct words from characters with their timing
    words = []
    current_word = ""
    word_start = None

    for i, ch in enumerate(chars):
        if ch.strip() == "" or ch == " ":
            # Space or whitespace — end current word
            if current_word:
                words.append({
                    "word": current_word,
                    "start": word_start,
                    "end": ends[i - 1] if i > 0 else starts[i],
                })
                current_word = ""
                word_start = None
        else:
            if word_start is None:
                word_start = starts[i]
            current_word += ch

    # Don't forget the last word
    if current_word:
        words.append({
            "word": current_word,
            "start": word_start,
            "end": ends[-1],
        })

    print(f"  Got timing for {len(words)} words")
    return words


def generate_art(lora, art_prompt, output_prefix):
    """Generate art via ComfyUI API."""
    print("  Generating art via ComfyUI...")
    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "2": {"class_type": "LoraLoader",
              "inputs": {"lora_name": lora, "strength_model": 0.8, "strength_clip": 0.8,
                          "model": ["1", 0], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": art_prompt, "clip": ["2", 1]}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "blurry, low quality, text, watermark, modern, photograph, anime, cartoon",
                          "clip": ["2", 1]}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
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


def build_synced_captions(words, voice_offset):
    """
    Build caption groups from word timestamps.
    Groups words into readable chunks (max ~6-7 words per caption line),
    using actual voice timing for start/end.

    voice_offset: seconds of silence before narration starts in video
    """
    if not words:
        return []

    # Group words into chunks of ~6 words for readability
    MAX_WORDS_PER_CHUNK = 6
    chunks = []
    current_words = []

    for w in words:
        current_words.append(w)
        if len(current_words) >= MAX_WORDS_PER_CHUNK:
            chunks.append(current_words)
            current_words = []
    if current_words:
        chunks.append(current_words)

    # Build caption entries with precise timing
    captions = []
    for chunk in chunks:
        text = " ".join(w["word"] for w in chunk)
        start = chunk[0]["start"] + voice_offset
        end = chunk[-1]["end"] + voice_offset
        # Minimum duration of 1.5s for readability
        if end - start < 1.5:
            end = start + 1.5
        captions.append({"text": text, "start": start, "end": end})

    return captions


def assemble_video(quote, philosopher, voice_path, art_path, music_path, output_path,
                   word_timestamps=None):
    """
    Assemble the final Short video with voice-synced captions.
    """
    from moviepy.editor import (AudioFileClip, ImageClip, TextClip, CompositeVideoClip,
                                 CompositeAudioClip, concatenate_audioclips, ColorClip)

    print("  Assembling video...")

    narration = AudioFileClip(voice_path)
    voice_start = 2.5  # seconds of art before voice begins
    duration = narration.duration + voice_start + 2.5  # padding at end

    # Background art with Ken Burns zoom
    bg = ImageClip(art_path).set_duration(duration).resize(height=2400).set_position("center")
    def zoom_in(t):
        return 1 + 0.08 * (t / duration)
    bg = bg.resize(zoom_in)

    # Dark overlay for text readability
    dark_overlay = (ColorClip(size=(1080, 700), color=(0, 0, 0))
                    .set_duration(duration)
                    .set_position((0, 1200))
                    .set_opacity(0.55)
                    .crossfadein(2))

    # Build captions from word timestamps
    caption_clips = []
    if word_timestamps:
        captions = build_synced_captions(word_timestamps, voice_start)
        print(f"  Building {len(captions)} synced caption segments")

        for cap in captions:
            # Wrap text into readable lines (max 4 words per line)
            words = cap["text"].split()
            lines = []
            for i in range(0, len(words), 4):
                lines.append(" ".join(words[i:i+4]))
            display_text = "\n".join(lines)

            txt = (TextClip(display_text,
                            fontsize=60,
                            color="white",
                            font="Georgia-Bold",
                            method="caption",
                            size=(900, None),
                            align="center",
                            interline=8)
                   .set_position(("center", 1320))
                   .set_start(cap["start"])
                   .set_duration(cap["end"] - cap["start"])
                   .crossfadein(0.2)
                   .crossfadeout(0.2))
            caption_clips.append(txt)
    else:
        # Fallback: estimate timing from text (same as generate_batch.py)
        print("  No timestamps — using estimated timing")
        raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', quote) if s.strip()]
        if not raw_sentences:
            raw_sentences = [quote]

        chunks = []
        current_chunk = ""
        for s in raw_sentences:
            if current_chunk and len((current_chunk + " " + s).split()) > 15:
                chunks.append(current_chunk)
                current_chunk = s
            else:
                current_chunk = (current_chunk + " " + s).strip() if current_chunk else s
        if current_chunk:
            chunks.append(current_chunk)

        total_words = sum(len(c.split()) for c in chunks)
        current_time = voice_start + 0.5
        available_time = narration.duration * 0.95

        for chunk in chunks:
            word_count = len(chunk.split())
            chunk_duration = (word_count / total_words) * available_time
            chunk_duration = max(chunk_duration, 2.0)

            words = chunk.split()
            display = "\n".join([" ".join(words[i:i+5]) for i in range(0, len(words), 5)])
            txt = (TextClip(display, fontsize=58, color="white", font="Georgia-Bold",
                            method="caption", size=(900, None), align="center", interline=6)
                   .set_position(("center", 1350)).set_start(current_time)
                   .set_duration(chunk_duration).crossfadein(0.3).crossfadeout(0.3))
            caption_clips.append(txt)
            current_time += chunk_duration + 0.15

    # Author attribution
    author_clip = (TextClip(f"— {philosopher}", fontsize=38, color="#D4AF37",
                            font="Georgia-Italic", method="label")
                   .set_position(("center", 1750))
                   .set_start(voice_start + 0.5)
                   .set_duration(duration - voice_start - 1.5)
                   .crossfadein(1))

    # Channel watermark
    watermark = (TextClip("Deep Echoes of Wisdom", fontsize=22, color="white",
                          font="Arial", method="label")
                 .set_position(("center", 50))
                 .set_duration(duration)
                 .set_opacity(0.4))

    # Audio mix
    narration = narration.set_start(voice_start)
    music = AudioFileClip(music_path)
    if music.duration < duration:
        music = concatenate_audioclips([music] * (int(duration / music.duration) + 1))
    music = music.subclip(0, duration).volumex(0.15)
    final_audio = CompositeAudioClip([music, narration])

    # Compose
    all_clips = [bg, dark_overlay] + caption_clips + [author_clip, watermark]
    video = (CompositeVideoClip(all_clips, size=(1080, 1920))
             .set_audio(final_audio)
             .fadein(2.5)
             .fadeout(2.5))

    video.write_videofile(output_path, fps=30, codec="libx264", audio_codec="aac",
                          bitrate="8000k", threads=8, preset="fast", logger=None)
    print(f"  Video saved: {output_path}")


def generate_short(philosopher_name, topic, prompt_text, channel="wisdom"):
    """Generate a complete Short for the given philosopher."""
    config = PHILOSOPHER_CONFIG[philosopher_name]
    slug = philosopher_name.lower().replace(" ", "_")
    prefix = f"test_{slug}_{datetime.now().strftime('%H%M')}"

    print(f"\n{'='*60}")
    print(f"  GENERATING: {philosopher_name} — {topic}")
    print(f"  Channel: {channel}")
    print(f"{'='*60}")

    # 1. Quote
    quote = generate_quote(config["ollama_model"], prompt_text)
    print(f"  Quote: {quote}")

    # 2. Voice with timestamps
    voice_path = str(OUTPUT_DIR / f"{prefix}_voice.mp3")
    word_timestamps = generate_voice_with_timestamps(quote, config["voice"], voice_path)

    if word_timestamps:
        # Save timestamps for debugging
        ts_path = str(OUTPUT_DIR / f"{prefix}_timestamps.json")
        with open(ts_path, "w") as f:
            json.dump(word_timestamps, f, indent=2)
        print(f"  Timestamps saved: {ts_path}")

    # 3. Art
    art_path = generate_art(config["lora"], config["art_prompt"], prefix)
    if not art_path:
        print("  ART GENERATION FAILED — aborting")
        return None

    # 4. Music
    music_dir = Path(f"C:/AI/system/music/{config['music']}")
    tracks = list(music_dir.glob("*.mp3"))
    if not tracks:
        print(f"  WARNING: No music tracks in {music_dir}")
        return None
    music_path = str(random.choice(tracks))
    print(f"  Music: {Path(music_path).name}")

    # 5. Assemble video
    video_path = str(OUTPUT_DIR / f"{prefix}_short.mp4")
    assemble_video(quote, philosopher_name, voice_path, art_path, music_path,
                   video_path, word_timestamps)

    print(f"\n  DONE — NOT uploaded (test only)")
    print(f"  Video: {video_path}")
    return video_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate a test Short")
    parser.add_argument("--philosopher", default="Marcus Aurelius",
                        choices=list(PHILOSOPHER_CONFIG.keys()))
    parser.add_argument("--topic", default=None)
    parser.add_argument("--prompt", default=None)
    args = parser.parse_args()

    philosopher = args.philosopher

    if philosopher == "Marcus Aurelius":
        topic = args.topic or "the discipline of perception"
        prompt = args.prompt or ("Write a powerful passage about how we suffer more in imagination "
                                  "than in reality, and the discipline of seeing things as they truly are. "
                                  "Under 50 words. Speak directly. No quotation marks. No labels.")
    elif philosopher == "Gibran":
        topic = args.topic or "on love and loss"
        prompt = args.prompt or ("Write a profound poetic passage about how love and loss are inseparable, "
                                  "and how through grief we discover the depth of our capacity to love. "
                                  "Under 50 words. Speak as the Prophet. No quotation marks. No labels.")
    else:
        topic = args.topic or "wisdom"
        prompt = args.prompt or f"Write a powerful passage of wisdom. Under 50 words. No quotation marks. No labels."

    generate_short(philosopher, topic, prompt)
