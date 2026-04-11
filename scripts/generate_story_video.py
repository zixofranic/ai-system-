"""
End-to-end story video pipeline.

One command: generates story, voice, time-chunk art prompts, images, and renders
via Remotion. No manual intervention needed.

Usage:
    # Generate everything from scratch:
    python generate_story_video.py --philosopher "Epictetus" --theme "betrayal" --mood "dark"

    # Regenerate images + video from existing script + voice:
    python generate_story_video.py --script-json path/to/script.json --reuse-voice

    # Just re-render video from existing assets:
    python generate_story_video.py --script-json path/to/script.json --reuse-all
"""

import os
import sys
import json
import time
import random
import shutil
import subprocess
import requests
import calendar
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")
sys.path.insert(0, str(Path(__file__).parent))

COMFYUI_URL = "http://localhost:8188"
CHATTERBOX_URL = "http://localhost:8004"
VIDEO_ENGINE = Path("C:/AI/system/video-engine")
MUSIC_ROOT = Path("C:/AI/system/music")

OUTPUT_DIRS = {
    "wisdom": Path("C:/AI/wisdom/videos/story"),
    "gibran": Path("C:/AI/gibran/videos/story"),
}

PHILOSOPHER_CHANNEL = {
    "Marcus Aurelius": "wisdom", "Seneca": "wisdom", "Epictetus": "wisdom",
    "Rumi": "wisdom", "Lao Tzu": "wisdom", "Nietzsche": "wisdom",
    "Emerson": "wisdom",
    "Gibran": "gibran", "Gibran Khalil Gibran": "gibran",
}

PHILOSOPHER_MUSIC = {
    "Marcus Aurelius": "stoic_classical", "Seneca": "stoic_classical",
    "Epictetus": "stoic_classical", "Rumi": "persian_miniature",
    "Lao Tzu": "eastern_ink", "Nietzsche": "dark_expressionist",
    "Emerson": "romantic_landscape",
    "Gibran": "gibran", "Gibran Khalil Gibran": "gibran",
}

SENTENCE_ENDINGS = {'.', '!', '?', ';', ':'}


def _sanitize_text(text):
    replacements = {
        "\u2014": " - ", "\u2013": " - ", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


# ---------------------------------------------------------------------------
# Step 1: Generate story script
# ---------------------------------------------------------------------------
def step_generate_script(philosopher, theme, setting, mood, notes, queued_title=None):
    print("\n[1/6] Generating story script via Claude Sonnet...")
    if queued_title:
        print(f"  Honoring queued title: {queued_title!r}")
    from ai_writer import generate_story_script
    story = generate_story_script(
        philosopher=philosopher, theme=theme,
        setting=setting, mood=mood, notes=notes,
        queued_title=queued_title,
    )
    print(f"  Title: {story.get('title', '?')}")
    print(f"  Words: {len(story.get('story_script', '').split())}")
    return story


# ---------------------------------------------------------------------------
# Step 2: Generate voice + timestamps
# ---------------------------------------------------------------------------
ELEVENLABS_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_WISDOM = os.environ.get("ELEVENLABS_VOICE_WISDOM", "0ABJJI7ZYmWZBiUBMHUW")
ELEVENLABS_VOICE_GIBRAN = os.environ.get("ELEVENLABS_VOICE_GIBRAN", "R68HwD2GzEdWfqYZP9FQ")


def step_generate_voice(text, output_path, ts_path, channel_slug):
    # channel_slug is required — it determines which ElevenLabs voice to use.
    # Wisdom → Burton; Gibran → Gibran voice. No default. This exact pattern
    # matches orchestrator.generate_voice for shorts. Chatterbox was the old
    # path; it clone-cast every story in James Burton's voice regardless of
    # channel (including Gibran), which was a channel-routing bug.
    if not channel_slug:
        raise ValueError("step_generate_voice requires channel_slug — refusing to default")
    if Path(output_path).exists() and Path(ts_path).exists():
        print(f"\n[2/6] Voice exists, loading: {Path(output_path).name}")
        with open(ts_path) as f:
            return json.load(f)

    voice_id = ELEVENLABS_VOICE_GIBRAN if channel_slug == "gibran" else ELEVENLABS_VOICE_WISDOM
    print(f"\n[2/6] Generating voice via ElevenLabs (channel={channel_slug}, voice={voice_id[:8]}...)")
    text = _sanitize_text(text)

    from elevenlabs import ElevenLabs, VoiceSettings
    client = ElevenLabs(api_key=ELEVENLABS_KEY)
    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
        voice_settings=VoiceSettings(
            stability=0.70,
            similarity_boost=0.85,
            style=0.25,
            use_speaker_boost=True,
        ),
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    print(f"  Voice saved: {output_path}")

    # Extract timestamps via Whisper
    print("  Extracting word timestamps via Whisper...")
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(output_path, word_timestamps=True)
        words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                words.append({"word": w["word"].strip(), "start": w["start"], "end": w["end"]})
        print(f"  Whisper: {len(words)} words, {words[-1]['end']:.1f}s")
    except ImportError:
        # Fallback: even-split
        from moviepy.editor import AudioFileClip
        duration = AudioFileClip(output_path).duration
        text_words = _sanitize_text(text).split()
        tpw = duration / len(text_words)
        words = [{"word": w, "start": i * tpw, "end": (i + 1) * tpw} for i, w in enumerate(text_words)]
        print(f"  Even-split: {len(words)} words, {duration:.1f}s")

    # Forced-align Whisper's timings to the ground-truth script text.
    # This fixes Whisper's mis-transcriptions of uncommon proper nouns
    # (e.g. "Saya" → "Sire") by replacing each token with the correct
    # spelling from the source script while keeping Whisper's timestamps.
    try:
        from whisper_align import align_whisper_to_script
        before = sum(1 for w in words if w["word"].lower() not in text.lower())
        words = align_whisper_to_script(words, text)
        after = sum(1 for w in words if w["word"] not in text)
        print(f"  Aligned to script: {before} → {after} out-of-script tokens")
    except Exception as e:
        print(f"  WARNING: script alignment failed ({e}), using raw Whisper output")

    with open(ts_path, "w") as f:
        json.dump(words, f, indent=2)
    return words


# ---------------------------------------------------------------------------
# Step 3: Split into time chunks + generate art prompts
# ---------------------------------------------------------------------------
def step_generate_art_prompts(story, timestamps, num_chunks=10):
    print(f"\n[3/6] Splitting narration into {num_chunks} time chunks...")
    total_words = len(timestamps)
    target_chunk_size = total_words // num_chunks

    chunks = []
    current = []
    for i, w in enumerate(timestamps):
        current.append(w["word"])
        is_end = any(w["word"].endswith(p) for p in SENTENCE_ENDINGS)
        if (is_end and len(current) >= target_chunk_size - 5) or len(current) >= target_chunk_size + 10:
            chunks.append(" ".join(current))
            current = []
    if current:
        if chunks:
            chunks[-1] += " " + " ".join(current)
        else:
            chunks.append(" ".join(current))

    # Trim to exact num_chunks
    while len(chunks) > num_chunks and len(chunks) > 1:
        shortest = min(range(len(chunks) - 1), key=lambda i: len(chunks[i].split()))
        chunks[shortest] = chunks[shortest] + " " + chunks.pop(shortest + 1)

    for i, c in enumerate(chunks):
        print(f"  Chunk {i+1}: {len(c.split())} words — {c[:50]}...")

    print("  Generating art prompts from chunk text via Claude...")
    from ai_writer import generate_art_prompts_from_chunks
    prompts = generate_art_prompts_from_chunks(story, chunks)

    for i, p in enumerate(prompts):
        print(f"  Prompt {i+1}: {p[:60]}...")

    return chunks, prompts


# ---------------------------------------------------------------------------
# Step 4: Generate images via ComfyUI
# ---------------------------------------------------------------------------
def _copy_to_input(image_path):
    dest = Path("C:/AI/system/ComfyUI/input") / Path(image_path).name
    shutil.copy2(image_path, dest)
    return dest.name


def _submit_and_wait(workflow, timeout=180):
    resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
    data = resp.json()
    if "prompt_id" not in data:
        return None, str(data)[:300]
    pid = data["prompt_id"]
    for _ in range(timeout // 2):
        time.sleep(2)
        hist = requests.get(f"{COMFYUI_URL}/history/{pid}").json()
        if pid in hist:
            status = hist[pid].get("status", {})
            if status.get("status_str") == "error":
                return None, f"Error: {status}"
            outputs = hist[pid].get("outputs", {})
            if "8" in outputs:
                fn = outputs["8"]["images"][0]["filename"]
                return f"C:/AI/system/ComfyUI/output/{fn}", None
    return None, "TIMEOUT"


def step_generate_images(prompts, prefix):
    print(f"\n[4/6] Generating {len(prompts)} images via ComfyUI...")
    # Stronger negative to kill the "plastic AI look"
    neg = (
        "blurry, low quality, text, watermark, anime, cartoon, 3d render, "
        "deformed face, extra limbs, disfigured, bad anatomy, "
        "plastic skin, waxy, doll face, dead eyes, airbrushed, "
        "oversaturated, cgi render, deviantart, trending on artstation, "
        "fused fingers, mutated hands, lowres, jpeg artifacts, over-smoothed"
    )
    reference = None
    art_paths = []

    # Quality settings (2026-04-09):
    #   steps 30 → 40, cfg 7.5/7.0 → 5.5, sampler euler → dpmpp_2m_sde_gpu,
    #   scheduler normal → karras. Same tune as orchestrator's shorts path.
    SAMPLER = "dpmpp_2m_sde_gpu"
    SCHEDULER = "karras"
    STEPS = 40
    CFG_HERO = 5.5  # scene 1 — less constraint, more room for base model quality
    CFG_IPADAPTER = 6.0  # scenes 2+ — slightly firmer to balance IP-Adapter guidance

    for i, prompt in enumerate(prompts):
        print(f"  Scene {i+1}/{len(prompts)}: {prompt[:50]}...", flush=True)
        sp = f"{prefix}_scene{i+1}"

        if i == 0:
            wf = {
                "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
                "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
                "4": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["1", 1]}},
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1216, "height": 832, "batch_size": 1}},
                "6": {"class_type": "KSampler", "inputs": {"seed": random.randint(1, 999999), "steps": STEPS, "cfg": CFG_HERO, "sampler_name": SAMPLER, "scheduler": SCHEDULER, "denoise": 1.0, "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0]}},
                "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
                "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": sp, "images": ["7", 0]}},
            }
        else:
            wf = {
                "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
                "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
                "4": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["1", 1]}},
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1216, "height": 832, "batch_size": 1}},
                "10": {"class_type": "LoadImage", "inputs": {"image": _copy_to_input(reference)}},
                "12": {"class_type": "IPAdapterUnifiedLoader", "inputs": {"model": ["1", 0], "preset": "STANDARD (medium strength)"}},
                "13": {"class_type": "IPAdapterAdvanced", "inputs": {"model": ["12", 0], "ipadapter": ["12", 1], "image": ["10", 0], "weight": 0.5, "weight_type": "style transfer", "start_at": 0.0, "end_at": 0.3, "combine_embeds": "concat", "embeds_scaling": "K+V w/ C penalty"}},
                "6": {"class_type": "KSampler", "inputs": {"seed": random.randint(1, 999999), "steps": STEPS, "cfg": CFG_IPADAPTER, "sampler_name": SAMPLER, "scheduler": SCHEDULER, "denoise": 1.0, "model": ["13", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0]}},
                "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
                "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": sp, "images": ["7", 0]}},
            }

        path, err = _submit_and_wait(wf)
        if path:
            if i == 0:
                reference = path
            art_paths.append(path)
            print(f"    OK: {Path(path).name}")
        else:
            art_paths.append(None)
            print(f"    FAILED: {err}")

    # Fill missing with nearest valid
    valid = [p for p in art_paths if p]
    if not valid:
        raise RuntimeError("ALL images failed")
    for i in range(len(art_paths)):
        if art_paths[i] is None:
            art_paths[i] = valid[0]

    print(f"  {len(valid)}/{len(prompts)} images generated")
    return art_paths


# ---------------------------------------------------------------------------
# Step 5: Convert to Remotion timeline
# ---------------------------------------------------------------------------
def step_convert_remotion(script_path, ts_path, art_paths_path, voice_path, music_path, output_name):
    print(f"\n[5/6] Converting to Remotion timeline: {output_name}")
    cmd = [
        "node", str(VIDEO_ENGINE / "scripts" / "convert-story.js"),
        "--script", str(script_path),
        "--timestamps", str(ts_path),
        "--art-paths", str(art_paths_path),
        "--voice", str(voice_path),
        "--output", output_name,
        "--format", "story",
    ]
    if music_path:
        cmd.extend(["--music", str(music_path)])
    subprocess.run(cmd, cwd=str(VIDEO_ENGINE), check=True)


# ---------------------------------------------------------------------------
# Step 6: Render via Remotion
# ---------------------------------------------------------------------------
def step_render(output_name, out_path):
    print(f"\n[6/6] Rendering via Remotion -> {out_path}")
    remotion_cmd = str(VIDEO_ENGINE / "node_modules" / ".bin" / "remotion.cmd")
    subprocess.run(
        # CRF 24 keeps 6-min stories comfortably under the Supabase project
        # 50 MB global file size limit (Pro plan default). CRF 22 was landing
        # 6-min stories at ~50.8 MB, just over the cap — upload 413'd. Quality
        # diff vs CRF 22 is imperceptible on cinematic AI art. Raise the
        # project limit in Supabase Settings > Storage if you want CRF 18-20.
        f'"{remotion_cmd}" render {output_name} "{out_path}" --codec=h264 --crf=24',
        cwd=str(VIDEO_ENGINE), check=True, timeout=600, shell=True,
    )
    print(f"  DONE: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="End-to-end story video pipeline")
    parser.add_argument("--philosopher", default="Marcus Aurelius")
    parser.add_argument("--theme", default="betrayal")
    parser.add_argument("--setting", default=None)
    parser.add_argument("--mood", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--script-json", default=None, help="Reuse existing script")
    parser.add_argument("--reuse-voice", action="store_true", help="Skip voice if exists")
    parser.add_argument("--reuse-images", action="store_true", help="Skip image gen if art_paths exists")
    parser.add_argument("--reuse-all", action="store_true", help="Skip to Remotion render")
    # Bumped 10 → 15 on 2026-04-09 to pair with longer 6-min story scripts.
    # Aim for ~24s per scene on a 6-min story.
    parser.add_argument("--num-scenes", type=int, default=15)
    # When invoked by the orchestrator: --content-id binds this run to a
    # specific Supabase row so step_update_supabase updates it directly
    # instead of searching by philosopher (which created duplicate rows).
    # --channel-slug is the authoritative channel; no more silent "wisdom"
    # default when PHILOSOPHER_CHANNEL is missing an entry.
    parser.add_argument("--content-id", default=None)
    parser.add_argument("--channel-slug", default=None)
    # The dashboard plans each story with a title. Pass it here so Claude
    # delivers on that exact title instead of inventing a new one — prevents
    # the row lying about provenance after generation.
    parser.add_argument("--queued-title", default=None)
    args = parser.parse_args()

    philosopher = args.philosopher
    if args.channel_slug:
        channel = args.channel_slug
    elif philosopher in PHILOSOPHER_CHANNEL:
        channel = PHILOSOPHER_CHANNEL[philosopher]
    else:
        raise ValueError(
            f"Channel unknown for philosopher {philosopher!r}. "
            "Add it to PHILOSOPHER_CHANNEL or pass --channel-slug explicitly. "
            "Refusing to silently default to 'wisdom' (channel routing is a correctness invariant)."
        )
    if channel not in OUTPUT_DIRS:
        raise ValueError(f"Unknown channel {channel!r}; must be one of {list(OUTPUT_DIRS)}")
    output_dir = OUTPUT_DIRS[channel]
    output_dir.mkdir(parents=True, exist_ok=True)
    music_style = PHILOSOPHER_MUSIC.get(philosopher, "stoic_classical")
    first_name = philosopher.split()[0].lower()
    date_str = datetime.now().strftime('%Y-%m-%d')
    prefix = f"{date_str}_story_{first_name}"
    output_name = prefix.replace("_", "-")

    print(f"\n{'='*60}")
    print(f"  STORY VIDEO PIPELINE")
    print(f"  Philosopher: {philosopher}")
    print(f"  Theme: {args.theme}")
    print(f"  Channel: {channel}")
    print(f"{'='*60}")

    # Paths
    script_path = output_dir / f"{prefix}_script.json"
    voice_path = output_dir / f"{prefix}_voice.mp3"
    ts_path = output_dir / f"{prefix}_timestamps.json"
    art_paths_path = output_dir / f"{prefix}_art_paths.json"
    video_path = output_dir / f"{prefix}_video.mp4"

    # Step 1: Script
    if args.script_json:
        print(f"\n[1/6] Loading existing script: {args.script_json}")
        with open(args.script_json) as f:
            story = json.load(f)
        script_path = Path(args.script_json)
        # Derive paths from script location
        base = script_path.stem.replace("_script", "")
        voice_path = script_path.parent / f"{base}_voice.mp3"
        ts_path = script_path.parent / f"{base}_timestamps.json"
        art_paths_path = script_path.parent / f"{base}_art_paths.json"
        video_path = script_path.parent / f"{base}_video.mp4"
        composition_name = base.replace("_", "-")
        output_name = composition_name
        philosopher = story.get("philosopher", philosopher)
    else:
        story = step_generate_script(philosopher, args.theme, args.setting, args.mood, args.notes,
                                     queued_title=args.queued_title)
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(story, f, indent=2, ensure_ascii=False)

    # Step 2: Voice
    if not args.reuse_all:
        timestamps = step_generate_voice(story["story_script"], str(voice_path), str(ts_path), channel_slug=channel)
    else:
        with open(ts_path) as f:
            timestamps = json.load(f)
        print(f"\n[2/6] Reusing voice: {voice_path.name}")

    # Step 3: Art prompts
    if not args.reuse_all and not args.reuse_images:
        chunks, prompts = step_generate_art_prompts(story, timestamps, args.num_scenes)
    elif art_paths_path.exists():
        print(f"\n[3/6] Reusing art prompts")
        prompts = None
    else:
        chunks, prompts = step_generate_art_prompts(story, timestamps, args.num_scenes)

    # Step 4: Images
    if not args.reuse_all and not args.reuse_images:
        art_paths = step_generate_images(prompts, prefix)
        with open(art_paths_path, "w") as f:
            json.dump(art_paths, f, indent=2)
    else:
        with open(art_paths_path) as f:
            art_paths = json.load(f)
        print(f"\n[4/6] Reusing {len(art_paths)} images")

    # Step 5: Build music track by chaining multiple compositions with crossfade.
    # Suno generations cap at ~3:30, but stories run 5:30-6:30 — looping a
    # single composition over a 6-min story sounds repetitive. Chaining 2+
    # DIFFERENT compositions gives musical variety over the story's arc.
    music_dir = MUSIC_ROOT / music_style
    music_path = None
    if voice_path:
        voice_dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", str(voice_path)],
            capture_output=True, text=True,
        ).stdout.strip() or "0")
        needed = voice_dur + 10  # voice + padding
        chained_path = output_dir / f"{prefix}_music_chained.mp3"
        music_path = _build_crossfade_music(music_dir, needed, chained_path)

    # Step 5b: Convert to Remotion
    step_convert_remotion(script_path, ts_path, art_paths_path, voice_path, music_path, output_name)

    # Step 6: Render
    step_render(output_name, str(video_path))

    # Step 6b: Generate thumbnail
    thumb_path = str(video_path).replace("_video.mp4", "_thumb.jpg")
    thumb_drive_url = None
    try:
        from thumbnail_generator import generate_thumbnail, generate_thumbnail_from_video
        art_paths_list = json.load(open(art_paths_path)) if art_paths_path.exists() else []
        first_art = next((p for p in art_paths_list if p), None)
        if first_art:
            generate_thumbnail(first_art, story.get("title", ""), thumb_path)
        else:
            generate_thumbnail_from_video(str(video_path), story.get("title", ""), thumb_path)
        print(f"  Thumbnail: {thumb_path}")
    except Exception as e:
        print(f"  Thumbnail failed: {e}")

    # Step 6c: Upload to Supabase Storage (primary) + Drive (fallback)
    drive_url = None
    video_storage_path = None
    thumb_storage_path = None
    try:
        from supabase_storage import upload_to_storage
        video_storage_path = upload_to_storage(str(video_path), "wisdom-videos", channel, "story")
        print(f"  Storage: {video_storage_path}")
    except Exception as e:
        print(f"  Storage upload failed, trying Drive: {e}")
        drive_url = step_upload_drive(str(video_path), channel)

    # Upload thumbnail
    if os.path.exists(thumb_path):
        try:
            from supabase_storage import upload_to_storage as upload_thumb
            thumb_storage_path = upload_thumb(thumb_path, "wisdom-thumbnails", channel, "story")
            print(f"  Thumbnail Storage: {thumb_storage_path}")
        except Exception as e:
            print(f"  Thumbnail storage failed: {e}")
            if drive_url or video_storage_path:
                try:
                    thumb_drive_url = step_upload_drive(thumb_path, channel)
                    print(f"  Thumbnail Drive: {thumb_drive_url}")
                except Exception as e2:
                    print(f"  Thumbnail upload failed: {e2}")

    # Fail loud if we have NO playable video URL after uploads. Previously
    # the pipeline would happily continue with video_storage_path=None and
    # video_drive_url=None, patch the row to status=ready, and leave it
    # invisible on the review page (which filters by the presence of at
    # least one video URL). Now we raise so the orchestrator's mark_failed
    # runs and the dashboard surfaces the real reason.
    if not video_storage_path and not drive_url:
        raise RuntimeError(
            "Video upload failed on every backend (Supabase Storage + Drive) "
            "— refusing to mark row ready with no playable video URL. "
            "Check Supabase project file size limit (Settings > Storage) "
            "or add a Google Drive folder to the channel."
        )

    # Step 7: Update Supabase content row with metadata. If the PATCH fails
    # after the uploads succeeded, best-effort DELETE the orphan storage
    # objects before re-raising — otherwise every failed row leaves 50 MB
    # of garbage in the Fellows bucket with nothing pointing at it.
    try:
        step_update_supabase(
            story, str(video_path),
            video_drive_url=drive_url, thumbnail_drive_url=thumb_drive_url,
            video_storage_path=video_storage_path, thumbnail_storage_path=thumb_storage_path,
            content_id=args.content_id, channel_slug=channel,
        )
    except Exception:
        _cleanup_orphan_storage(video_storage_path, thumb_storage_path)
        raise

    print(f"\n{'='*60}")
    print(f"  COMPLETE")
    print(f"  Video: {video_path}")
    if drive_url:
        print(f"  Drive: {drive_url}")
    print(f"  Script: {script_path}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Step 6b: Upload to Google Drive
# ---------------------------------------------------------------------------
def _supabase_headers():
    return {
        "apikey": os.environ["SUPABASE_SERVICE_KEY"],
        "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_KEY']}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _refresh_google_token(channel_id, refresh_token):
    """Refresh Google access token using the stored refresh token."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    new_token = data["access_token"]
    expires_in = data.get("expires_in", 3600)

    # Update stored token in Supabase channel settings
    ch_url = f"{os.environ['SUPABASE_URL']}/rest/v1/channels?id=eq.{channel_id}&select=settings"
    ch_resp = requests.get(ch_url, headers=_supabase_headers(), timeout=15)
    ch_resp.raise_for_status()
    channels = ch_resp.json()
    if channels:
        existing_settings = channels[0].get("settings", {}) or {}
        existing_settings["google_access_token"] = new_token
        expiry = datetime.now(timezone.utc).timestamp() + expires_in
        existing_settings["google_token_expiry"] = (
            datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat()
        )
        update_url = f"{os.environ['SUPABASE_URL']}/rest/v1/channels?id=eq.{channel_id}"
        requests.patch(
            update_url, headers=_supabase_headers(),
            json={"settings": existing_settings}, timeout=15,
        )

    return new_token


def _get_google_access_token(channel):
    """Get a valid Google access token for a channel, refreshing if needed."""
    settings = channel.get("settings", {}) or {}
    refresh_token = settings.get("google_refresh_token")
    if not refresh_token:
        raise ValueError(
            f"Google not connected for channel '{channel.get('name', '?')}'"
        )

    token_expiry_str = settings.get("google_token_expiry", "")
    if token_expiry_str:
        try:
            expiry = datetime.fromisoformat(token_expiry_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if (expiry - now).total_seconds() > 300:
                existing_token = settings.get("google_access_token", "")
                if existing_token:
                    return existing_token
        except (ValueError, TypeError):
            pass

    return _refresh_google_token(channel["id"], refresh_token)


def _week_folder_name(target_date=None):
    """Build the weekly folder name: Month-W#-MonDD-MonDD"""
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()
    elif hasattr(target_date, "date"):
        target_date = target_date.date()

    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)
    month_name = calendar.month_name[target_date.month]

    first_of_month = target_date.replace(day=1)
    days_until_monday = (7 - first_of_month.weekday()) % 7
    first_monday = first_of_month + timedelta(days=days_until_monday)
    if monday < first_monday:
        week_num = 1
    else:
        week_num = ((monday - first_monday).days // 7) + 1
        if first_of_month.weekday() != 0:
            week_num += 1

    mon_start = f"{calendar.month_abbr[monday.month]}{monday.day}"
    mon_end = f"{calendar.month_abbr[sunday.month]}{sunday.day}"
    return f"{month_name}-W{week_num}-{mon_start}-{mon_end}"


def _find_drive_subfolder(access_token, parent_id, folder_name):
    query = (
        f"'{parent_id}' in parents "
        f"and name = '{folder_name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    resp = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"q": query, "fields": "files(id,name)", "pageSize": 1},
        timeout=30,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])
    return files[0]["id"] if files else None


def _create_drive_subfolder(access_token, parent_id, folder_name):
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    resp = requests.post(
        "https://www.googleapis.com/drive/v3/files",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=metadata,
        params={"fields": "id"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _get_or_create_week_folder(access_token, parent_folder_id):
    folder_name = _week_folder_name()
    existing_id = _find_drive_subfolder(access_token, parent_folder_id, folder_name)
    if existing_id:
        print(f"  [drive] Using existing week folder: {folder_name}")
        return existing_id
    new_id = _create_drive_subfolder(access_token, parent_folder_id, folder_name)
    print(f"  [drive] Created week folder: {folder_name}")
    return new_id


def _upload_multipart(access_token, folder_id, file_path, filename):
    metadata = json.dumps({"name": filename, "parents": [folder_id]})
    boundary = "-------wisdom_upload_boundary"

    with open(file_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--".encode("utf-8")

    resp = requests.post(
        "https://www.googleapis.com/upload/drive/v3/files"
        "?uploadType=multipart&fields=id,webViewLink",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        data=body,
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("webViewLink",
                      f"https://drive.google.com/file/d/{result['id']}/view")


def _upload_resumable(access_token, folder_id, file_path, filename):
    metadata = json.dumps({"name": filename, "parents": [folder_id]})

    init_resp = requests.post(
        "https://www.googleapis.com/upload/drive/v3/files"
        "?uploadType=resumable&fields=id,webViewLink",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
        },
        data=metadata,
        timeout=30,
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    with open(file_path, "rb") as f:
        file_data = f.read()

    resp = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=file_data,
        timeout=300,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("webViewLink",
                      f"https://drive.google.com/file/d/{result['id']}/view")


def step_upload_drive(video_path, channel):
    """Upload video to Google Drive under weekly subfolder."""
    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not supabase_key:
        print("\n[6b] Supabase not configured, skipping Drive upload")
        return None

    print("\n[6b] Uploading to Google Drive...")

    # Fetch channel info from Supabase
    slug = "gibran" if channel == "gibran" else "wisdom"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }
    resp = requests.get(
        f"{supabase_url}/rest/v1/channels",
        headers=headers,
        params={
            "slug": f"eq.{slug}",
            "select": "id,name,slug,google_drive_folder_id,settings",
        },
        timeout=15,
    )
    if resp.status_code != 200 or not resp.json():
        print(f"  Could not fetch channel '{slug}': {resp.text[:200]}")
        return None

    ch = resp.json()[0]
    folder_id = ch.get("google_drive_folder_id")
    if not folder_id:
        print(f"  No Google Drive folder configured for channel '{slug}'")
        return None

    try:
        access_token = _get_google_access_token(ch)
        week_folder_id = _get_or_create_week_folder(access_token, folder_id)
        filename = Path(video_path).name
        file_size = Path(video_path).stat().st_size

        if file_size < 5 * 1024 * 1024:
            drive_url = _upload_multipart(access_token, week_folder_id, video_path, filename)
        else:
            drive_url = _upload_resumable(access_token, week_folder_id, video_path, filename)

        print(f"  Drive URL: {drive_url}")
        return drive_url
    except Exception as e:
        print(f"  Drive upload failed: {e}")
        return None


def _probe_track(path):
    # Return (duration_sec, composition_key) for a music file.
    # Uses ID3 title tag as the dedupe key so v1/v2/v3 variants of the
    # same Suno composition (which share a title) collapse to one key.
    # Falls back to filename stem minus trailing `_v\d+` or `_\d+`.
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip() or "0")
    title = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=title",
         "-of", "default=nk=1:nw=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    if title:
        key = title.strip().lower()
    else:
        import re as _re
        key = _re.sub(r'_v?\d+$', '', Path(path).stem).lower()
    return dur, key


def _build_crossfade_music(music_dir, needed_sec, output_path, crossfade_sec=2.0):
    # Pick DIFFERENT compositions from music_dir (deduped by ID3 title tag
    # so v1/v2 of the same Suno gen don't double-dip) and chain them with
    # short crossfades until the total covers needed_sec. If there's only
    # one track in the folder, fall back to simple copy. If there aren't
    # enough unique compositions to cover needed_sec, reuse compositions
    # from the start rather than loop the same track back-to-back.
    from collections import defaultdict

    all_tracks = sorted(Path(music_dir).glob("*.mp3"))
    if not all_tracks:
        return None

    by_comp = defaultdict(list)
    for t in all_tracks:
        dur, key = _probe_track(t)
        by_comp[key].append((t, dur))

    comp_keys = list(by_comp.keys())
    random.shuffle(comp_keys)

    picked = []

    def _total():
        return sum(d for _, d in picked) - max(0, len(picked) - 1) * crossfade_sec

    # First pass: one track per unique composition
    for key in comp_keys:
        path, dur = random.choice(by_comp[key])
        picked.append((path, dur))
        if _total() >= needed_sec:
            break

    # Second pass: if exhausted and still short, cycle through again
    guard = 0
    while _total() < needed_sec and guard < 10:
        for key in comp_keys:
            if _total() >= needed_sec:
                break
            path, dur = random.choice(by_comp[key])
            picked.append((path, dur))
        guard += 1

    # Single-track fast path: just copy/normalize
    if len(picked) == 1:
        single = picked[0][0]
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(single),
             "-t", str(needed_sec),
             "-codec:a", "libmp3lame", "-b:a", "192k",
             str(output_path)],
            capture_output=True, check=True,
        )
        print(f"  Music: {Path(single).name} ({picked[0][1]:.0f}s)")
        return str(output_path)

    # Multi-track path: acrossfade chain
    inputs = []
    for p, _ in picked:
        inputs.extend(["-i", str(p)])
    filter_parts = []
    prev = "[0]"
    for i in range(1, len(picked)):
        out_tag = "[out]" if i == len(picked) - 1 else f"[a{i}]"
        filter_parts.append(
            f"{prev}[{i}]acrossfade=d={crossfade_sec}:c1=tri:c2=tri{out_tag}"
        )
        prev = out_tag
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", str(needed_sec),
        "-codec:a", "libmp3lame", "-b:a", "192k",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Music chain failed: {result.stderr[-400:]}")
        # Fallback: copy the first track alone so the pipeline still proceeds
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(picked[0][0]),
             "-t", str(needed_sec),
             "-codec:a", "libmp3lame", "-b:a", "192k",
             str(output_path)],
            capture_output=True, check=True,
        )
        return str(output_path)

    names = " + ".join(Path(p).name for p, _ in picked)
    print(f"  Music chain ({len(picked)} tracks, ~{_total():.0f}s -> {needed_sec:.0f}s): {names}")
    return str(output_path)


def _cleanup_orphan_storage(video_storage_path, thumb_storage_path):
    # Best-effort DELETE of storage objects that were uploaded but whose
    # PATCH failed. Swallows all errors — we're already in a failure path
    # and don't want to mask the original exception.
    fellows_url = os.environ.get("FELLOWS_SUPABASE_URL", "")
    fellows_key = os.environ.get("FELLOWS_SUPABASE_SERVICE_KEY", "")
    if not fellows_url or not fellows_key:
        return
    h = {"apikey": fellows_key, "Authorization": f"Bearer {fellows_key}"}
    for bucket, obj in [("wisdom-videos", video_storage_path),
                        ("wisdom-thumbnails", thumb_storage_path)]:
        if not obj:
            continue
        try:
            r = requests.delete(f"{fellows_url}/storage/v1/object/{bucket}/{obj}",
                                headers=h, timeout=15)
            if r.status_code in (200, 204):
                print(f"  [cleanup] Deleted orphan {bucket}/{obj}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Step 7: Push metadata to Supabase
# ---------------------------------------------------------------------------
def step_update_supabase(story, video_path, video_drive_url=None, thumbnail_drive_url=None,
                         video_storage_path=None, thumbnail_storage_path=None,
                         content_id=None, channel_slug=None):
    # Two modes:
    # 1. content_id provided (orchestrator path): update that exact row. No
    #    searching, no status filter, no duplicate-row fallback. This is the
    #    fix for the Gibran contamination bug where the old search-by-
    #    philosopher logic couldn't find rows in status=generating and
    #    silently created duplicates in the wrong channel.
    # 2. content_id missing (standalone CLI path): legacy behavior — search
    #    by philosopher+format and create a row if nothing matches.
    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not supabase_key:
        print("\n[7/7] Supabase not configured, skipping metadata push")
        return

    print("\n[7/7] Updating Supabase with video metadata...")
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    philosopher = story.get("philosopher", "")
    title = story.get("title", "")
    description = story.get("description", "")
    tags = story.get("tags", [])

    if content_id:
        existing = requests.get(
            f"{supabase_url}/rest/v1/content",
            headers=headers,
            params={"select": "id,generation_params", "id": f"eq.{content_id}"},
            timeout=15,
        ).json()
        if not existing:
            raise RuntimeError(
                f"content_id {content_id} not found in Supabase — refusing to "
                "fall back to create-new-row (that's how duplicate Gibran rows "
                "happened)."
            )
        existing_params = existing[0].get("generation_params") or {}
        payload = {
            "title": title,
            "description": description,
            "status": "ready",
            "local_machine_path": video_path,
            "generation_params": {
                **existing_params,
                "tags": tags,
                "closing_attribution": story.get("closing_attribution", ""),
                "writer_style": story.get("writer_style", ""),
                "comic_artist": story.get("comic_artist", ""),
            },
        }
        if video_storage_path:
            payload["video_storage_path"] = video_storage_path
        if thumbnail_storage_path:
            payload["thumbnail_storage_path"] = thumbnail_storage_path
        if video_drive_url:
            payload["video_drive_url"] = video_drive_url
        if thumbnail_drive_url:
            payload["thumbnail_drive_url"] = thumbnail_drive_url
        resp = requests.patch(
            f"{supabase_url}/rest/v1/content?id=eq.{content_id}",
            headers=headers, json=payload, timeout=30,
        )
        if resp.status_code in (200, 204):
            print(f"  Updated content [{content_id[:8]}]: {title}")
        else:
            raise RuntimeError(f"Supabase update failed: {resp.status_code} {resp.text[:200]}")
        return

    # Legacy standalone path: search by philosopher, create if missing.
    resp = requests.get(
        f"{supabase_url}/rest/v1/content",
        headers=headers,
        params={
            "philosopher": f"eq.{philosopher}",
            "format": "eq.story",
            "status": "in.(queued,ready)",
            "deleted_at": "is.null",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    rows = resp.json() if resp.status_code == 200 else []
    if not rows:
        if not channel_slug:
            raise ValueError(
                "channel_slug is required when creating a new content row. "
                "Refusing to silently default (channel routing is a correctness invariant)."
            )
        payload = {
            "title": title,
            "description": description,
            "philosopher": philosopher,
            "topic": story.get("theme", ""),
            "quote_text": story.get("story_script", "")[:500],
            "format": "story",
            "status": "ready",
            "local_machine_path": video_path,
            "scheduled_at": datetime.now(timezone.utc).isoformat(),
            "generation_params": {
                "tags": tags,
                "closing_attribution": story.get("closing_attribution", ""),
                "writer_style": story.get("writer_style", ""),
                "comic_artist": story.get("comic_artist", ""),
            },
            "is_system_generated": True,
        }
        if video_storage_path:
            payload["video_storage_path"] = video_storage_path
        if thumbnail_storage_path:
            payload["thumbnail_storage_path"] = thumbnail_storage_path
        if video_drive_url:
            payload["video_drive_url"] = video_drive_url
        if thumbnail_drive_url:
            payload["thumbnail_drive_url"] = thumbnail_drive_url
        ch_resp = requests.get(
            f"{supabase_url}/rest/v1/channels",
            headers=headers,
            params={"slug": f"eq.{channel_slug}"},
        )
        channels = ch_resp.json() if ch_resp.status_code == 200 else []
        if not channels:
            raise RuntimeError(f"Channel {channel_slug!r} not found in Supabase")
        payload["channel_id"] = channels[0]["id"]

        resp = requests.post(f"{supabase_url}/rest/v1/content", headers=headers, json=payload)
        if resp.status_code in (200, 201):
            print(f"  Created content row: {title}")
        else:
            print(f"  Failed to create: {resp.text[:200]}")
    else:
        existing_id = rows[0]["id"]
        payload = {
            "title": title,
            "description": description,
            "status": "ready",
            "local_machine_path": video_path,
            "scheduled_at": datetime.now(timezone.utc).isoformat(),
            "generation_params": {
                **({} if not rows[0].get("generation_params") else rows[0]["generation_params"]),
                "tags": tags,
                "closing_attribution": story.get("closing_attribution", ""),
            },
        }
        if video_storage_path:
            payload["video_storage_path"] = video_storage_path
        if thumbnail_storage_path:
            payload["thumbnail_storage_path"] = thumbnail_storage_path
        if video_drive_url:
            payload["video_drive_url"] = video_drive_url
        if thumbnail_drive_url:
            payload["thumbnail_drive_url"] = thumbnail_drive_url
        resp = requests.patch(
            f"{supabase_url}/rest/v1/content?id=eq.{existing_id}",
            headers=headers, json=payload,
        )
        if resp.status_code in (200, 204):
            print(f"  Updated content: {title}")
        else:
            print(f"  Failed to update: {resp.text[:200]}")


if __name__ == "__main__":
    main()
