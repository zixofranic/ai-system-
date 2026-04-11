"""
Generate a 60-second vertical (9:16) companion video from an existing story.

Reuses an existing story's:
  - script.json (to condense via ai_writer.generate_story_vertical_script)
  - art_paths.json (landscape art → pan-and-scan vertical)
  - music style

Generates fresh:
  - Condensed 1-min voice via ElevenLabs
  - Whisper word timestamps
  - Remotion 9:16 render

Usage:
    # From an existing story script:
    python generate_story_vertical.py --script-json C:/AI/wisdom/videos/story/2026-04-07_story_seneca_script.json

    # Reuse existing voice + timestamps (skip 11labs):
    python generate_story_vertical.py --script-json ... --reuse-voice
"""

import os
import sys
import json
import random
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")
sys.path.insert(0, str(Path(__file__).parent))

VIDEO_ENGINE = Path("C:/AI/system/video-engine")
MUSIC_ROOT = Path("C:/AI/system/music")
CHATTERBOX_URL = "http://localhost:8004"

OUTPUT_DIRS = {
    "wisdom": Path("C:/AI/wisdom/videos/story_vertical"),
    "gibran": Path("C:/AI/gibran/videos/story_vertical"),
}

PHILOSOPHER_CHANNEL = {
    "Marcus Aurelius": "wisdom", "Seneca": "wisdom", "Epictetus": "wisdom",
    "Rumi": "wisdom", "Lao Tzu": "wisdom", "Nietzsche": "wisdom",
    "Emerson": "wisdom", "Thoreau": "wisdom", "Dostoevsky": "wisdom",
    "Wilde": "wisdom", "Musashi": "wisdom", "Confucius": "wisdom",
    "Gibran": "gibran",
}

PHILOSOPHER_MUSIC = {
    "Marcus Aurelius": "stoic_classical", "Seneca": "stoic_classical",
    "Epictetus": "stoic_classical", "Rumi": "persian_miniature",
    "Lao Tzu": "eastern_ink", "Nietzsche": "dark_expressionist",
    "Emerson": "romantic_landscape", "Gibran": "gibran",
}


def _sanitize_text(text):
    replacements = {
        "\u2014": " - ", "\u2013": " - ", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


# ---------------------------------------------------------------------------
# Step 1: Condense full story → vertical script
# ---------------------------------------------------------------------------
def step_condense_script(full_story):
    print("\n[1/5] Condensing full story to 60-second vertical script...")
    from ai_writer import generate_story_vertical_script
    vertical = generate_story_vertical_script(full_story)
    print(f"  Title: {vertical.get('title', '?')}")
    script_words = len(vertical.get("story_script", "").split())
    print(f"  Words: {script_words} (~{script_words / 150 * 60:.0f}s of narration)")
    return vertical


# ---------------------------------------------------------------------------
# Step 2: Generate voice via Chatterbox + Whisper timestamps
# (matches the parent story pipeline, zero 11labs credit cost)
# ---------------------------------------------------------------------------
def step_generate_voice(text, output_path, ts_path):
    """Generate voice via Chatterbox TTS (Burton clone) + Whisper word timestamps."""
    if Path(output_path).exists() and Path(ts_path).exists():
        print(f"\n[2/5] Voice exists, loading: {Path(output_path).name}")
        with open(ts_path) as f:
            return json.load(f)

    print("\n[2/5] Generating voice via Chatterbox TTS (Burton clone)...")
    text = _sanitize_text(text)
    payload = {"text": text, "exaggeration": 0.5, "cfg_weight": 0.5}
    voice_ref = Path("C:/AI/system/voice/recordings/wisdom_burton_11labs_clip.mp3")
    if voice_ref.exists():
        payload["voice_mode"] = "clone"
        payload["reference_audio_filename"] = voice_ref.name

    resp = requests.post(f"{CHATTERBOX_URL}/tts", json=payload, timeout=300)
    resp.raise_for_status()

    wav_path = output_path.replace(".mp3", ".wav")
    with open(wav_path, "wb") as f:
        f.write(resp.content)

    # Convert to mp3 for Remotion
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame",
         "-b:a", "128k", output_path],
        capture_output=True,
    )
    print(f"  Voice saved: {output_path}")

    # Extract timestamps via Whisper
    print("  Extracting word timestamps via Whisper...")
    import whisper
    model = whisper.load_model("base")
    result = model.transcribe(output_path, word_timestamps=True)
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            words.append({
                "word": w["word"].strip(),
                "start": w["start"],
                "end": w["end"],
            })
    print(f"  Whisper: {len(words)} words, {words[-1]['end']:.1f}s")

    # Forced-align to ground-truth script text (fixes Saya→Sire etc.)
    try:
        from whisper_align import align_whisper_to_script
        words = align_whisper_to_script(words, text)
        print(f"  Aligned to script: {len(words)} words after alignment")
    except Exception as e:
        print(f"  WARNING: script alignment failed ({e})")

    with open(ts_path, "w") as f:
        json.dump(words, f, indent=2)
    return words


# ---------------------------------------------------------------------------
# Step 3: Convert to Remotion timeline
# ---------------------------------------------------------------------------
def step_convert_remotion(script_path, ts_path, art_paths_path,
                          voice_path, music_path, output_name):
    print(f"\n[3/5] Converting to Remotion timeline: {output_name}")
    cmd = [
        "node", str(VIDEO_ENGINE / "scripts" / "convert-story-vertical.js"),
        "--script", str(script_path),
        "--timestamps", str(ts_path),
        "--art-paths", str(art_paths_path),
        "--voice", str(voice_path),
        "--output", output_name,
        "--format", "story_vertical",
    ]
    if music_path:
        cmd.extend(["--music", str(music_path)])
    subprocess.run(cmd, cwd=str(VIDEO_ENGINE), check=True)


# ---------------------------------------------------------------------------
# Step 4: Render via Remotion
# ---------------------------------------------------------------------------
def step_render(output_name, out_path):
    print(f"\n[4/5] Rendering via Remotion -> {out_path}")
    remotion_cmd = str(VIDEO_ENGINE / "node_modules" / ".bin" / "remotion.cmd")
    subprocess.run(
        f'"{remotion_cmd}" render {output_name} "{out_path}" --codec=h264 --crf=22',
        cwd=str(VIDEO_ENGINE), check=True, timeout=600, shell=True,
    )
    print(f"  DONE: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate a 60s vertical companion from an existing story")
    parser.add_argument("--script-json", required=True,
                        help="Path to the full story's script.json")
    parser.add_argument("--reuse-voice", action="store_true",
                        help="Reuse existing vertical voice + timestamps if present")
    args = parser.parse_args()

    full_script_path = Path(args.script_json)
    if not full_script_path.exists():
        print(f"FATAL: script not found: {full_script_path}")
        sys.exit(1)

    with open(full_script_path) as f:
        full_story = json.load(f)

    philosopher = full_story.get("philosopher", "Marcus Aurelius")
    channel = PHILOSOPHER_CHANNEL.get(philosopher, "wisdom")
    output_dir = OUTPUT_DIRS[channel]
    output_dir.mkdir(parents=True, exist_ok=True)

    music_style = PHILOSOPHER_MUSIC.get(philosopher, "stoic_classical")
    first_name = philosopher.split()[0].lower()
    date_str = datetime.now().strftime("%Y-%m-%d")
    prefix = f"{date_str}_story_vertical_{first_name}"
    output_name = prefix.replace("_", "-")

    print(f"\n{'=' * 60}")
    print(f"  STORY VERTICAL (60s companion)")
    print(f"  Source story: {full_script_path.name}")
    print(f"  Philosopher: {philosopher}  Channel: {channel}")
    print(f"{'=' * 60}")

    # Derive sibling asset paths (from the full story)
    full_base = full_script_path.stem.replace("_script", "")
    full_dir = full_script_path.parent
    art_paths_path = full_dir / f"{full_base}_art_paths.json"
    if not art_paths_path.exists():
        print(f"FATAL: missing art_paths.json at {art_paths_path}")
        sys.exit(1)

    # Vertical-specific paths (new)
    vertical_script_path = output_dir / f"{prefix}_script.json"
    vertical_voice_path = output_dir / f"{prefix}_voice.mp3"
    vertical_ts_path = output_dir / f"{prefix}_timestamps.json"
    video_path = output_dir / f"{prefix}_video.mp4"

    # 1. Condense
    vertical = step_condense_script(full_story)
    vertical["channel"] = channel
    with open(vertical_script_path, "w", encoding="utf-8") as f:
        json.dump(vertical, f, indent=2, ensure_ascii=False)

    # 2. Voice (Chatterbox + Whisper, same as parent story)
    timestamps = step_generate_voice(
        vertical["story_script"],
        str(vertical_voice_path), str(vertical_ts_path),
    )

    # 3. Pick music (same style as parent story)
    music_dir = MUSIC_ROOT / music_style
    tracks = list(music_dir.glob("*.mp3")) if music_dir.exists() else []
    music_path = str(random.choice(tracks)) if tracks else None

    # 4. Convert to Remotion timeline (reuses the parent story's art paths)
    step_convert_remotion(
        vertical_script_path, vertical_ts_path, art_paths_path,
        vertical_voice_path, music_path, output_name,
    )

    # 5. Render
    step_render(output_name, str(video_path))

    print(f"\n{'=' * 60}")
    print(f"  COMPLETE")
    print(f"  Video: {video_path}")
    print(f"  Script: {vertical_script_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
