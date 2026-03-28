"""
Generate a batch of Shorts — different philosophers, different LoRAs.
Each Short: quote → voice → art → assemble video → upload to YouTube.
"""

import os
import sys
import json
import time
import random
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
WISDOM_VOICE = os.environ.get("ELEVENLABS_VOICE_WISDOM", "0ABJJI7ZYmWZBiUBMHUW")
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
OUTPUT_DIR = Path("C:/AI/wisdom/output/shorts")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PHILOSOPHER_LORA = {
    "Marcus Aurelius": "stoic_classical_v1.safetensors",
    "Seneca": "stoic_classical_v1.safetensors",
    "Epictetus": "stoic_classical_v1.safetensors",
    "Rumi": "persian_miniature_v1.safetensors",
    "Lao Tzu": "eastern_ink_v1.safetensors",
    "Nietzsche": "dark_expressionist_v1.safetensors",
    "Emerson": "romantic_landscape_v1.safetensors",
}

PHILOSOPHER_MUSIC = {
    "Marcus Aurelius": "stoic_classical",
    "Seneca": "stoic_classical",
    "Epictetus": "stoic_classical",
    "Rumi": "persian_miniature",
    "Lao Tzu": "eastern_ink",
    "Nietzsche": "dark_expressionist",
    "Emerson": "romantic_landscape",
}

PHILOSOPHER_ART_PROMPT = {
    "Marcus Aurelius": "stoic_classical painting, roman emperor meditating alone in marble palace at dawn, dramatic golden light through columns, oil painting masterpiece",
    "Epictetus": "stoic_classical painting, ancient greek teacher lecturing students in shadowy courtyard, torchlight, weathered face full of conviction",
    "Rumi": "persian_miniature painting, sufi mystic whirling in ecstasy under starlit sky, flowing robes, gold leaf borders, jewel tones",
    "Lao Tzu": "eastern_ink painting, sage sitting by misty waterfall, bamboo and pine trees, monochrome brush strokes, zen minimalist",
    "Nietzsche": "dark_expressionist painting, solitary figure on mountain peak during storm, lightning, dramatic shadows, existential anguish, oil painting",
}

BATCH = [
    {"philosopher": "Marcus Aurelius", "topic": "controlling your reactions", "ollama_model": "marcus_aurelius",
     "prompt": "Write a powerful passage about controlling your reactions to what others do. Under 50 words. Speak directly. No quotation marks. No labels."},
    {"philosopher": "Epictetus", "topic": "what you can and cannot control", "ollama_model": "epictetus",
     "prompt": "Write a direct teaching about the difference between what is in our control and what is not. Under 50 words. No quotation marks. No labels."},
    {"philosopher": "Rumi", "topic": "the wound is where light enters", "ollama_model": "rumi",
     "prompt": "Write a mystical poetic passage about how pain opens us to love and light. Under 50 words. No quotation marks. No labels."},
    {"philosopher": "Lao Tzu", "topic": "the power of stillness", "ollama_model": "lao_tzu",
     "prompt": "Write a paradoxical wisdom passage about how stillness is more powerful than action. Under 50 words. No quotation marks. No labels."},
    {"philosopher": "Nietzsche", "topic": "becoming who you are", "ollama_model": "nietzsche",
     "prompt": "Write a provocative passage about the courage needed to become who you truly are. Under 50 words. No quotation marks. No labels."},
]


def generate_quote(model, prompt):
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json={"model": model, "prompt": prompt, "stream": False})
    quote = resp.json()["response"].strip().replace('"', '').strip()
    if "[" in quote:
        quote = quote.split("]")[-1].strip()
    return quote


def generate_voice(text, output_path):
    client = ElevenLabs(api_key=ELEVENLABS_KEY)
    audio = client.text_to_speech.convert(
        voice_id=WISDOM_VOICE, text=text, model_id="eleven_multilingual_v2",
        voice_settings=VoiceSettings(stability=0.70, similarity_boost=0.85, style=0.25, use_speaker_boost=True),
    )
    with open(output_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)


def generate_art(lora, art_prompt, output_prefix):
    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "2": {"class_type": "LoraLoader", "inputs": {"lora_name": lora, "strength_model": 0.8, "strength_clip": 0.8, "model": ["1", 0], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": art_prompt, "clip": ["2", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality, text, watermark, modern, photograph, anime, cartoon", "clip": ["2", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"seed": random.randint(1, 999999), "steps": 28, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": output_prefix, "images": ["7", 0]}},
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


def assemble_video(quote, philosopher, voice_path, art_path, music_path, output_path):
    from moviepy.editor import (AudioFileClip, ImageClip, TextClip, CompositeVideoClip,
                                 CompositeAudioClip, concatenate_audioclips, ColorClip)

    narration = AudioFileClip(voice_path)
    duration = narration.duration + 5

    bg = ImageClip(art_path).set_duration(duration).resize(height=2400).set_position("center")
    def zoom_in(t): return 1 + 0.10 * (t / duration)
    bg = bg.resize(zoom_in)

    dark_overlay = ColorClip(size=(1080, 700), color=(0, 0, 0)).set_duration(duration).set_position((0, 1250)).set_opacity(0.5).crossfadein(2)

    # Split quote into larger chunks (2-3 sentences each) for readable captions
    import re
    raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', quote) if s.strip()]
    if not raw_sentences:
        raw_sentences = [quote]

    # Group into chunks of 1-2 sentences (max ~15 words per chunk)
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

    # Weight timing by word count — longer chunks get more screen time
    total_words = sum(len(c.split()) for c in chunks)
    voice_start = 2.5
    current_time = voice_start + 0.5
    available_time = narration.duration * 0.95

    caption_clips = []
    for chunk in chunks:
        word_count = len(chunk.split())
        chunk_duration = (word_count / total_words) * available_time
        chunk_duration = max(chunk_duration, 2.0)  # minimum 2 seconds per caption

        words = chunk.split()
        display = "\n".join([" ".join(words[i:i+5]) for i in range(0, len(words), 5)])
        txt = (TextClip(display, fontsize=58, color="white", font="Georgia-Bold", method="caption",
                        size=(900, None), align="center", interline=6)
               .set_position(("center", 1350)).set_start(current_time)
               .set_duration(chunk_duration).crossfadein(0.3).crossfadeout(0.3))
        caption_clips.append(txt)
        current_time += chunk_duration + 0.15

    author_clip = (TextClip(f"— {philosopher}", fontsize=36, color="#D4AF37", font="Georgia-Italic", method="label")
                   .set_position(("center", 1800)).set_start(3.0).set_duration(duration - 4).crossfadein(1))

    watermark = (TextClip("Deep Echoes of Wisdom", fontsize=22, color="white", font="Arial", method="label")
                 .set_position(("center", 50)).set_duration(duration).set_opacity(0.4))

    narration = narration.set_start(2.5)
    music = AudioFileClip(music_path)
    if music.duration < duration:
        music = concatenate_audioclips([music] * (int(duration / music.duration) + 1))
    music = music.subclip(0, duration).volumex(0.15)
    final_audio = CompositeAudioClip([music, narration])

    video = CompositeVideoClip([bg, dark_overlay] + caption_clips + [author_clip, watermark], size=(1080, 1920)).set_audio(final_audio)
    video = video.fadein(2.5).fadeout(2.5)
    video.write_videofile(output_path, fps=30, codec="libx264", audio_codec="aac", bitrate="8000k", threads=8, preset="fast", logger=None)


def upload_to_youtube(video_path, title, description, tags):
    resp = requests.get(f"{SUPABASE_URL}/rest/v1/channels?slug=eq.wisdom&select=settings",
                        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
    settings = resp.json()[0]["settings"]
    if isinstance(settings, str): settings = json.loads(settings)
    refresh_token = settings.get("youtube_refresh_token")
    if not refresh_token:
        print("  No YouTube token!")
        return None

    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token, "grant_type": "refresh_token"})
    access_token = token_resp.json().get("access_token")
    if not access_token:
        print(f"  Token refresh failed")
        return None

    file_size = os.path.getsize(video_path)
    metadata = {
        "snippet": {"title": title, "description": description, "tags": tags, "categoryId": "27"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }

    init_resp = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Length": str(file_size), "X-Upload-Content-Type": "video/mp4"},
        json=metadata)

    if init_resp.status_code != 200:
        print(f"  Upload init failed: {init_resp.status_code}")
        return None

    with open(video_path, "rb") as f:
        upload_resp = requests.put(init_resp.headers["Location"], headers={"Content-Type": "video/mp4"}, data=f)

    if upload_resp.status_code in (200, 201):
        return upload_resp.json()["id"]
    return None


def main():
    print("=" * 60)
    print("  BATCH SHORT GENERATOR")
    print(f"  Generating {len(BATCH)} Shorts")
    print(f"  {datetime.now()}")
    print("=" * 60)

    results = []
    for i, item in enumerate(BATCH, 1):
        philosopher = item["philosopher"]
        topic = item["topic"]
        lora = PHILOSOPHER_LORA[philosopher]
        music_style = PHILOSOPHER_MUSIC[philosopher]
        art_prompt = PHILOSOPHER_ART_PROMPT[philosopher]
        first_name = philosopher.split()[0].lower()
        date_str = datetime.now().strftime('%Y-%m-%d')
        prefix = f"{date_str}_short_{first_name}"

        print(f"\n[{i}/{len(BATCH)}] {philosopher}: {topic}")

        # Quote
        print("  Generating quote...")
        quote = generate_quote(item["ollama_model"], item["prompt"])
        print(f"  Quote: {quote[:60]}...")

        # Voice
        voice_path = str(OUTPUT_DIR / f"{prefix}_voice.mp3")
        print("  Generating voice...")
        generate_voice(quote, voice_path)

        # Art
        print("  Generating art...")
        art_path = generate_art(lora, art_prompt, prefix)
        if not art_path:
            print("  ART FAILED — skipping")
            continue

        # Music
        tracks = list(Path(f"C:/AI/system/music/{music_style}").glob("*.mp3"))
        music_path = str(random.choice(tracks)) if tracks else None

        # Assemble
        video_path = str(OUTPUT_DIR / f"{prefix}_short.mp4")
        print("  Assembling video...")
        assemble_video(quote, philosopher, voice_path, art_path, music_path, video_path)

        # Upload
        title = f"{philosopher}: {topic.title()} #shorts"
        description = f"{quote}\n\n— {philosopher}\n\n#Shorts #Philosophy #Wisdom #DeepEchoesOfWisdom #{philosopher.replace(' ', '')}"
        tags = [philosopher, "Philosophy", "Wisdom", "Shorts", "Stoicism", "Ancient Wisdom"]
        print("  Uploading to YouTube...")
        video_id = upload_to_youtube(video_path, title, description, tags)

        if video_id:
            url = f"https://youtube.com/shorts/{video_id}"
            print(f"  LIVE: {url}")
            results.append({"philosopher": philosopher, "url": url})
        else:
            print("  Upload failed")
            results.append({"philosopher": philosopher, "url": "FAILED"})

    print(f"\n{'=' * 60}")
    print(f"  BATCH COMPLETE — {len(results)} Shorts")
    print(f"{'=' * 60}")
    for r in results:
        print(f"  {r['philosopher']}: {r['url']}")


if __name__ == "__main__":
    main()
