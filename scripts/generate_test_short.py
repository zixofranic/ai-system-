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

OUTPUT_DIRS = {
    "wisdom": Path("C:/AI/wisdom/output/shorts"),
    "gibran": Path("C:/AI/gibran/output/shorts"),
}
for d in OUTPUT_DIRS.values():
    d.mkdir(parents=True, exist_ok=True)

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
        "art_prompt": "gibran_style painting, prophet standing on hilltop overlooking vast sea at twilight, flowing robes in wind, warm golden amber light, oil painting masterpiece",
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
    # Sanitize special chars to prevent mojibake in captions
    text = text.replace("\u2014", " - ").replace("\u2013", " - ")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2026", "...").replace("\u00a0", " ")

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
    Each caption is a single line (~6-8 words), breaking at natural points
    (after punctuation like commas, periods, semicolons).

    voice_offset: seconds of silence before narration starts in video
    """
    if not words:
        return []

    MAX_WORDS = 5
    PUNCTUATION = {",", ".", "!", "?", ";", ":"}

    chunks = []
    current_words = []

    for w in words:
        current_words.append(w)
        word_text = w["word"]
        at_punctuation = any(word_text.endswith(p) for p in PUNCTUATION)

        # Break at punctuation if we have 2+ words, or at max words
        if (at_punctuation and len(current_words) >= 2) or len(current_words) >= MAX_WORDS:
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
        # Minimum duration for readability
        if end - start < 1.2:
            end = start + 1.2
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

    # No wide overlay — each caption gets its own tight dark pill behind it
    # Text positioned in the middle third of the frame (y ~750-900)
    CAPTION_Y = 820
    AUTHOR_Y = 1680

    # Build captions from word timestamps
    caption_clips = []
    if word_timestamps:
        captions = build_synced_captions(word_timestamps, voice_start)
        print(f"  Building {len(captions)} synced caption segments")

        for cap in captions:
            display_text = cap["text"].lower()

            # Create text clip — use caption method with max width to prevent overflow
            txt = TextClip(display_text,
                           fontsize=54,
                           color="white",
                           font="Georgia-Bold",
                           method="caption",
                           size=(950, None),
                           align="center")
            txt_w, txt_h = txt.size

            # Tight dark pill behind text (padding around text)
            pad_x, pad_y = 30, 14
            pill_w = min(txt_w + pad_x * 2, 1020)  # never wider than frame
            pill = (ColorClip(size=(pill_w, txt_h + pad_y * 2), color=(0, 0, 0))
                    .set_opacity(0.6)
                    .set_position(("center", CAPTION_Y - pad_y))
                    .set_start(cap["start"])
                    .set_duration(cap["end"] - cap["start"])
                    .crossfadein(0.15)
                    .crossfadeout(0.15))

            txt = (txt
                   .set_position(("center", CAPTION_Y))
                   .set_start(cap["start"])
                   .set_duration(cap["end"] - cap["start"])
                   .crossfadein(0.15)
                   .crossfadeout(0.15))

            caption_clips.append(pill)
            caption_clips.append(txt)
    else:
        # Fallback: estimate timing from text
        print("  No timestamps — using estimated timing")
        raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', quote) if s.strip()]
        if not raw_sentences:
            raw_sentences = [quote]

        # Break into phrase-sized chunks (~6-8 words, break at punctuation)
        chunks = []
        for sentence in raw_sentences:
            words = sentence.split()
            if len(words) <= 8:
                chunks.append(sentence)
            else:
                # Split at commas or mid-point
                parts = re.split(r',\s*', sentence)
                for part in parts:
                    pw = part.split()
                    if len(pw) <= 8:
                        chunks.append(part)
                    else:
                        mid = len(pw) // 2
                        chunks.append(" ".join(pw[:mid]))
                        chunks.append(" ".join(pw[mid:]))

        total_words = sum(len(c.split()) for c in chunks)
        current_time = voice_start + 0.5
        available_time = narration.duration * 0.95

        for chunk in chunks:
            word_count = len(chunk.split())
            chunk_duration = (word_count / total_words) * available_time
            chunk_duration = max(chunk_duration, 1.8)

            display_text = chunk.lower()
            txt = TextClip(display_text, fontsize=54, color="white",
                           font="Georgia-Bold", method="caption",
                           size=(950, None), align="center")
            txt_w, txt_h = txt.size

            pad_x, pad_y = 30, 14
            pill_w = min(txt_w + pad_x * 2, 1020)
            pill = (ColorClip(size=(pill_w, txt_h + pad_y * 2), color=(0, 0, 0))
                    .set_opacity(0.6)
                    .set_position(("center", CAPTION_Y - pad_y))
                    .set_start(current_time)
                    .set_duration(chunk_duration)
                    .crossfadein(0.15).crossfadeout(0.15))

            txt = (txt.set_position(("center", CAPTION_Y))
                   .set_start(current_time)
                   .set_duration(chunk_duration)
                   .crossfadein(0.15).crossfadeout(0.15))

            caption_clips.append(pill)
            caption_clips.append(txt)
            current_time += chunk_duration + 0.1

    # Author attribution — bottom area
    author_clip = (TextClip(f"— {philosopher}", fontsize=36, color="#D4AF37",
                            font="Georgia-Italic", method="label")
                   .set_position(("center", AUTHOR_Y))
                   .set_start(voice_start + 0.5)
                   .set_duration(duration - voice_start - 1.5)
                   .crossfadein(1))

    # Channel watermark — top
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

    # Compose — no wide overlay, just pill + text pairs
    all_clips = [bg] + caption_clips + [author_clip, watermark]
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
    channel = config["channel"]  # Always use channel from config, not default
    output_dir = OUTPUT_DIRS[channel]
    first_name = philosopher_name.split()[0].lower()
    date_str = datetime.now().strftime('%Y-%m-%d')
    prefix = f"{date_str}_short_{first_name}"

    print(f"\n{'='*60}")
    print(f"  GENERATING: {philosopher_name} — {topic}")
    print(f"  Channel: {channel}")
    print(f"  Output: {output_dir}")
    print(f"{'='*60}")

    # 1. Quote
    quote = generate_quote(config["ollama_model"], prompt_text)
    print(f"  Quote: {quote}")

    # 2. Voice with timestamps
    voice_path = str(output_dir / f"{prefix}_voice.mp3")
    word_timestamps = generate_voice_with_timestamps(quote, config["voice"], voice_path)

    if word_timestamps:
        # Save timestamps for debugging
        ts_path = str(output_dir / f"{prefix}_timestamps.json")
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
    video_path = str(output_dir / f"{prefix}_short.mp4")
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
