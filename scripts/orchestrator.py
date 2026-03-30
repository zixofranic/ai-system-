"""
Content Generation Orchestrator for Wisdom Pipeline
====================================================
Main pipeline script that connects all individual tools into an automated
video production workflow.

Reads queued content from Supabase, then for each item:
  1. Generate quote via Ollama (fallback: Claude Haiku)
  2. Generate metadata via Claude Haiku (ai_writer.py)
  3. Generate art via ComfyUI API
  4. Generate voice via Chatterbox TTS API
  5. Pick background music from library
  6. Assemble video (assemble_video.py)
  7. Upload to Google Drive
  8. Update Supabase with Drive URL + status

VRAM management: art generation is batched first, then voice generation,
so ComfyUI and Chatterbox never compete for GPU memory.

Usage:
    python orchestrator.py                 # process full queue
    python orchestrator.py --limit 3       # process at most 3 items
    python orchestrator.py --dry-run       # preview queue without processing
"""

import sys
import os
import json
import time
import uuid
import random
import argparse
import traceback
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
import calendar

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv("C:/AI/.env")

# Our pipeline modules
sys.path.insert(0, "C:/AI/system/scripts")
from ai_writer import generate_short_script, generate_youtube_metadata
from assemble_video import assemble_video

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

OLLAMA_URL = "http://localhost:11434"
COMFYUI_URL = "http://localhost:8188"
CHATTERBOX_URL = "http://localhost:8004"

VOICE_REFERENCE = "C:/AI/system/voice/recordings/ziad_reference_voice.wav"
MUSIC_ROOT = Path("C:/AI/system/music")
WORK_DIR = Path("C:/AI/system/pipeline_work")

# ---------------------------------------------------------------------------
# Philosopher -> Style mappings
# ---------------------------------------------------------------------------
PHILOSOPHER_TO_LORA = {
    "Marcus Aurelius": "stoic_classical_v1",
    "Seneca": "stoic_classical_v1",
    "Epictetus": "stoic_classical_v1",
    "Gibran Khalil Gibran": "gibran_style_v1",
    "Rumi": "persian_miniature_v1",
    "Lao Tzu": "eastern_ink_v1",
    "Sun Tzu": "eastern_ink_v1",
    "Confucius": "eastern_ink_v1",
    "Musashi": "eastern_ink_v1",
    "Emerson": "romantic_landscape_v1",
    "Thoreau": "romantic_landscape_v1",
    "Nietzsche": "dark_expressionist_v1",
    "Dostoevsky": "dark_expressionist_v1",
    "Wilde": "aesthetic_gilded_v1",
    "Franklin": "aesthetic_gilded_v1",
    "Da Vinci": "renaissance_genius_v1",
    "Tesla": "renaissance_genius_v1",
    "Vivekananda": "vedic_sacred_v1",
}

PHILOSOPHER_TO_MUSIC_STYLE = {
    "Marcus Aurelius": "stoic_classical",
    "Seneca": "stoic_classical",
    "Epictetus": "stoic_classical",
    "Gibran Khalil Gibran": "gibran",
    "Rumi": "persian_miniature",
    "Lao Tzu": "eastern_ink",
    "Sun Tzu": "eastern_ink",
    "Confucius": "eastern_ink",
    "Musashi": "eastern_ink",
    "Emerson": "romantic_landscape",
    "Thoreau": "romantic_landscape",
    "Nietzsche": "dark_expressionist",
    "Dostoevsky": "dark_expressionist",
    "Wilde": "aesthetic_gilded",
    "Franklin": "aesthetic_gilded",
    "Da Vinci": "renaissance_genius",
    "Tesla": "renaissance_genius",
    "Vivekananda": "vedic_sacred",
}

PHILOSOPHER_TO_VOICE_SETTINGS = {
    "Marcus Aurelius": {"exaggeration": 0.3},
    "Seneca": {"exaggeration": 0.5},
    "Epictetus": {"exaggeration": 0.6},
    "Gibran Khalil Gibran": {"exaggeration": 0.4},
    "Rumi": {"exaggeration": 0.5},
    "Lao Tzu": {"exaggeration": 0.3},
    "Sun Tzu": {"exaggeration": 0.4},
    "Confucius": {"exaggeration": 0.3},
    "Musashi": {"exaggeration": 0.4},
    "Emerson": {"exaggeration": 0.4},
    "Thoreau": {"exaggeration": 0.4},
    "Nietzsche": {"exaggeration": 0.6},
    "Dostoevsky": {"exaggeration": 0.5},
    "Wilde": {"exaggeration": 0.5},
    "Franklin": {"exaggeration": 0.4},
    "Da Vinci": {"exaggeration": 0.4},
    "Tesla": {"exaggeration": 0.5},
    "Vivekananda": {"exaggeration": 0.5},
}

EQUALIZER_COLORS = {
    "stoic_classical": "#8B7355",
    "gibran": "#D4AF37",
    "persian_miniature": "#C19A6B",
    "eastern_ink": "#708090",
    "romantic_landscape": "#DAA520",
    "dark_expressionist": "#8B0000",
    "aesthetic_gilded": "#FFD700",
    "renaissance_genius": "#CD853F",
    "vedic_sacred": "#FF8C00",
}

# Default ComfyUI SDXL + LoRA workflow template
# Placeholder values are filled at runtime via _build_comfyui_workflow()
_COMFYUI_WORKFLOW_TEMPLATE = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 30,
            "cfg": 7.0,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["10", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "width": 832,
            "height": 1216,
            "batch_size": 1,
        },
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "",
            "clip": ["10", 1],
        },
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "text, watermark, logo, blurry, low quality, deformed, ugly",
            "clip": ["10", 1],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["3", 0],
            "vae": ["10", 2],
        },
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "wisdom_gen",
            "images": ["8", 0],
        },
    },
    "10": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": "sd_xl_base_1.0.safetensors",
        },
    },
    "11": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "",
            "strength_model": 0.85,
            "strength_clip": 0.85,
            "model": ["10", 0],
            "clip": ["10", 1],
        },
    },
}


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------
def _supabase_headers():
    """Standard headers for Supabase REST API calls."""
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def fetch_queued_content():
    """Get all content rows with status='queued' from Supabase, oldest first."""
    url = (
        f"{SUPABASE_URL}/rest/v1/content"
        f"?status=eq.queued&deleted_at=is.null"
        f"&order=created_at.asc"
        f"&select=*,channels:channel_id(id,name,slug,google_drive_folder_id,settings)"
    )
    resp = requests.get(url, headers=_supabase_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def update_supabase(content_id: str, updates: dict):
    """PATCH a content row in Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}"
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    resp = requests.patch(url, headers=_supabase_headers(),
                          json=updates, timeout=30)
    resp.raise_for_status()
    return resp.json()


def log_step(content_id: str, step: str, step_order: int, status: str,
             error: str = None, gpu_stats: dict = None):
    """Insert or update a generation_log row for a pipeline step."""
    url = f"{SUPABASE_URL}/rest/v1/generation_log"
    payload = {
        "content_id": content_id,
        "step": step,
        "step_order": step_order,
        "status": status,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if status in ("success", "failed"):
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    if error:
        payload["error_message"] = error
    if gpu_stats:
        payload["gpu_stats"] = gpu_stats

    # Upsert: if a row for this content_id + step already exists, update it.
    # We use POST with Prefer: resolution=merge-duplicates when possible.
    # Since generation_log has no unique constraint on (content_id, step),
    # we just insert a new row for each status change.
    resp = requests.post(url, headers=_supabase_headers(),
                         json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Quote generation (Ollama with Claude fallback)
# ---------------------------------------------------------------------------
def generate_quote(philosopher: str, topic: str) -> str:
    """
    Generate a single philosophical quote via Ollama (local).
    Falls back to Claude Haiku if Ollama is unavailable.
    """
    prompt = (
        f"Write a single original philosophical quote in the authentic style "
        f"and voice of {philosopher}, on the topic of \"{topic}\".\n\n"
        f"Requirements:\n"
        f"- Must sound authentically like {philosopher}\n"
        f"- 1-3 sentences, poetic and quotable\n"
        f"- Deep insight, not surface-level advice\n"
        f"- Do NOT include attribution or quotation marks\n\n"
        f"Return ONLY the quote text, nothing else."
    )

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": "qwen3:32b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.8},
            },
            timeout=120,
        )
        resp.raise_for_status()
        quote = resp.json().get("response", "").strip().strip('"').strip("'")
        if quote:
            return quote
    except (requests.ConnectionError, requests.Timeout) as e:
        print(f"  [quote] Ollama unavailable ({e}), falling back to Haiku")

    # Fallback: use ai_writer's generate_short_script which calls Haiku
    result = generate_short_script(philosopher, topic)
    return result.get("quote", "")


# ---------------------------------------------------------------------------
# Art generation via ComfyUI
# ---------------------------------------------------------------------------
def _build_comfyui_workflow(prompt: str, lora_name: str,
                            width: int, height: int,
                            filename_prefix: str) -> dict:
    """Build a ComfyUI workflow JSON with LoRA for SDXL generation."""
    import copy
    workflow = copy.deepcopy(_COMFYUI_WORKFLOW_TEMPLATE)

    # Seed
    workflow["3"]["inputs"]["seed"] = random.randint(0, 2**32 - 1)

    # Dimensions
    workflow["5"]["inputs"]["width"] = width
    workflow["5"]["inputs"]["height"] = height

    # Positive prompt
    workflow["6"]["inputs"]["text"] = prompt

    # LoRA
    lora_file = f"{lora_name}.safetensors"
    workflow["11"]["inputs"]["lora_name"] = lora_file

    # Wire the KSampler through the LoRA loader instead of direct checkpoint
    workflow["3"]["inputs"]["model"] = ["11", 0]
    workflow["6"]["inputs"]["clip"] = ["11", 1]
    workflow["7"]["inputs"]["clip"] = ["11", 1]

    # Output filename
    workflow["9"]["inputs"]["filename_prefix"] = filename_prefix

    return workflow


def generate_art(prompt: str, lora_name: str, width: int, height: int,
                 output_path: str) -> str:
    """
    Call ComfyUI API to generate an image using SDXL + LoRA.
    Polls for completion and downloads the result.

    Returns the local file path of the saved image.
    """
    output_path = str(output_path)
    filename_prefix = Path(output_path).stem

    workflow = _build_comfyui_workflow(prompt, lora_name, width, height,
                                       filename_prefix)

    # Queue the prompt
    payload = {"prompt": workflow}
    resp = requests.post(f"{COMFYUI_URL}/prompt", json=payload, timeout=30)
    resp.raise_for_status()
    prompt_id = resp.json()["prompt_id"]
    print(f"  [art] ComfyUI prompt queued: {prompt_id}")

    # Poll for completion
    max_wait = 300  # 5 minutes
    poll_interval = 3
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        hist_resp = requests.get(
            f"{COMFYUI_URL}/history/{prompt_id}", timeout=15
        )
        hist_resp.raise_for_status()
        history = hist_resp.json()

        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            # Find the SaveImage node output
            for node_id, node_output in outputs.items():
                images = node_output.get("images", [])
                if images:
                    img_info = images[0]
                    img_filename = img_info["filename"]
                    subfolder = img_info.get("subfolder", "")

                    # Download the image
                    params = {
                        "filename": img_filename,
                        "subfolder": subfolder,
                        "type": "output",
                    }
                    img_resp = requests.get(
                        f"{COMFYUI_URL}/view", params=params, timeout=30
                    )
                    img_resp.raise_for_status()

                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(img_resp.content)

                    print(f"  [art] Saved: {output_path}")
                    return output_path

    raise TimeoutError(
        f"ComfyUI did not complete prompt {prompt_id} within {max_wait}s"
    )


# ---------------------------------------------------------------------------
# Voice generation via Chatterbox TTS
# ---------------------------------------------------------------------------
def generate_voice(text: str, output_path: str,
                   exaggeration: float = 0.5,
                   cfg_weight: float = 0.5) -> str:
    """
    Call Chatterbox TTS API to generate voice narration.

    Returns the local file path of the saved WAV.
    """
    output_path = str(output_path)
    payload = {
        "text": text,
        "exaggeration": exaggeration,
        "cfg_weight": cfg_weight,
    }

    # Include reference audio if it exists
    if Path(VOICE_REFERENCE).exists():
        payload["reference_audio"] = VOICE_REFERENCE

    resp = requests.post(
        f"{CHATTERBOX_URL}/tts",
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(resp.content)

    print(f"  [voice] Saved: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Music selection
# ---------------------------------------------------------------------------
def pick_music(philosopher: str) -> str:
    """
    Pick a random music track from the philosopher's style folder.
    Falls back to any available style if the specific folder is empty.
    """
    style = PHILOSOPHER_TO_MUSIC_STYLE.get(philosopher, "stoic_classical")
    style_dir = MUSIC_ROOT / style

    if style_dir.exists():
        tracks = list(style_dir.glob("*.mp3")) + list(style_dir.glob("*.wav"))
        if tracks:
            chosen = random.choice(tracks)
            print(f"  [music] Selected: {chosen.name} (style: {style})")
            return str(chosen)

    # Fallback: pick from any style that has tracks
    for fallback_dir in MUSIC_ROOT.iterdir():
        if fallback_dir.is_dir():
            tracks = list(fallback_dir.glob("*.mp3")) + list(fallback_dir.glob("*.wav"))
            if tracks:
                chosen = random.choice(tracks)
                print(f"  [music] Fallback: {chosen.name} (from {fallback_dir.name})")
                return str(chosen)

    raise FileNotFoundError(
        f"No music tracks found in {MUSIC_ROOT} for style '{style}'"
    )


# ---------------------------------------------------------------------------
# Google Drive upload
# ---------------------------------------------------------------------------
def _refresh_google_token(channel_id: str, refresh_token: str) -> str:
    """Refresh Google access token using the stored refresh token."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
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
    ch_url = f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel_id}&select=settings"
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
        update_url = f"{SUPABASE_URL}/rest/v1/channels?id=eq.{channel_id}"
        requests.patch(
            update_url,
            headers=_supabase_headers(),
            json={"settings": existing_settings},
            timeout=15,
        )

    return new_token


def _get_google_access_token(channel: dict) -> str:
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
            # Refresh if expiring within 5 minutes
            if (expiry - now).total_seconds() > 300:
                existing_token = settings.get("google_access_token", "")
                if existing_token:
                    return existing_token
        except (ValueError, TypeError):
            pass  # Token expiry unparseable, refresh anyway

    return _refresh_google_token(channel["id"], refresh_token)


def _week_folder_name(target_date: datetime = None) -> str:
    """
    Build the weekly folder name: ``Month-W#-MonDD-MonDD``

    Examples:
        ``March-W1-Feb23-Mar1``   (week spans two months)
        ``March-W4-Mar22-Mar28``  (week within one month)

    The week number is relative to the month: W1 contains the first Monday
    that falls on or after the 1st, and so on.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()
    elif hasattr(target_date, "date"):
        target_date = target_date.date()

    # Monday of the target date's week
    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)

    # Month name comes from the target date (the date we are scheduling for)
    month_name = calendar.month_name[target_date.month]

    # Week number within the month: how many Mondays from the 1st to this Monday
    first_of_month = target_date.replace(day=1)
    # Find the first Monday on or after the 1st
    days_until_monday = (7 - first_of_month.weekday()) % 7
    first_monday = first_of_month + timedelta(days=days_until_monday)
    if monday < first_monday:
        week_num = 1
    else:
        week_num = ((monday - first_monday).days // 7) + 1
        if first_of_month.weekday() != 0:
            week_num += 1  # account for partial first week

    # Short month abbreviations for range
    mon_start = f"{calendar.month_abbr[monday.month]}{monday.day}"
    mon_end = f"{calendar.month_abbr[sunday.month]}{sunday.day}"

    return f"{month_name}-W{week_num}-{mon_start}-{mon_end}"


def _find_drive_subfolder(access_token: str, parent_id: str,
                          folder_name: str) -> str | None:
    """Search for an existing subfolder by name inside a parent Drive folder."""
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


def _create_drive_subfolder(access_token: str, parent_id: str,
                            folder_name: str) -> str:
    """Create a new subfolder inside a parent Drive folder. Returns the new folder id."""
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


def _get_or_create_week_folder(access_token: str, parent_folder_id: str) -> str:
    """
    Ensure a weekly subfolder exists under the channel's root Drive folder.
    Returns the subfolder id to upload into.
    """
    folder_name = _week_folder_name()
    existing_id = _find_drive_subfolder(access_token, parent_folder_id, folder_name)
    if existing_id:
        print(f"  [drive] Using existing week folder: {folder_name}")
        return existing_id

    new_id = _create_drive_subfolder(access_token, parent_folder_id, folder_name)
    print(f"  [drive] Created week folder: {folder_name}")
    return new_id


def upload_to_drive(file_path: str, channel: dict) -> str:
    """
    Upload a video file to the channel's Google Drive folder inside a
    weekly subfolder (``Month-W#-MonDD-MonDD``).
    Returns the Drive web view URL.
    """
    access_token = _get_google_access_token(channel)
    folder_id = channel.get("google_drive_folder_id")
    if not folder_id:
        raise ValueError(
            f"No Google Drive folder configured for channel '{channel.get('name', '?')}'"
        )

    # Resolve (or create) the weekly subfolder
    week_folder_id = _get_or_create_week_folder(access_token, folder_id)

    filename = Path(file_path).name
    file_size = Path(file_path).stat().st_size

    # Use multipart upload for files under 5MB, resumable for larger
    if file_size < 5 * 1024 * 1024:
        return _upload_multipart(access_token, week_folder_id, file_path, filename)
    else:
        return _upload_resumable(access_token, week_folder_id, file_path, filename)


def _upload_multipart(access_token: str, folder_id: str,
                      file_path: str, filename: str) -> str:
    """Multipart upload for smaller files."""
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


def _upload_resumable(access_token: str, folder_id: str,
                      file_path: str, filename: str) -> str:
    """Resumable upload for larger files (>5MB)."""
    metadata = json.dumps({"name": filename, "parents": [folder_id]})

    # Initiate resumable session
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

    # Upload file content
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


# ---------------------------------------------------------------------------
# Art prompt builder
# ---------------------------------------------------------------------------
def _build_art_prompt(philosopher: str, quote: str, topic: str) -> str:
    """Build a ComfyUI-friendly image generation prompt for a quote."""
    style_hints = {
        "stoic_classical_v1": (
            "ancient Roman marble hall, dramatic chiaroscuro lighting, "
            "classical columns, Mediterranean golden hour"
        ),
        "gibran_style_v1": (
            "ethereal Lebanese mountain landscape, cedar trees, "
            "warm golden light, mystical atmosphere, soft watercolor"
        ),
        "persian_miniature_v1": (
            "ornate Persian garden, intricate tile patterns, "
            "moonlit courtyard, roses and cypress trees"
        ),
        "eastern_ink_v1": (
            "traditional Chinese ink wash landscape, misty mountains, "
            "bamboo, flowing water, zen minimalism"
        ),
        "romantic_landscape_v1": (
            "lush New England forest, golden autumn light, "
            "Walden Pond atmosphere, romantic landscape painting"
        ),
        "dark_expressionist_v1": (
            "dramatic expressionist scene, deep shadows, "
            "stormy sky, Gothic architecture, candlelight"
        ),
        "aesthetic_gilded_v1": (
            "opulent Art Nouveau interior, gilded details, "
            "stained glass, Victorian elegance, warm lamplight"
        ),
        "renaissance_genius_v1": (
            "Renaissance workshop, anatomical sketches, "
            "warm candlelight, Leonardo-style sfumato"
        ),
        "vedic_sacred_v1": (
            "sacred Indian temple at sunrise, intricate carvings, "
            "lotus pond, golden spiritual light, Himalayan backdrop"
        ),
    }

    lora = PHILOSOPHER_TO_LORA.get(philosopher, "stoic_classical_v1")
    scene = style_hints.get(lora, "dramatic cinematic landscape, golden hour")

    prompt = (
        f"masterpiece, best quality, highly detailed, cinematic, "
        f"{scene}, "
        f"philosophical atmosphere, contemplative mood, "
        f"inspired by the theme of {topic}, "
        f"no text, no words, no letters, no watermark"
    )
    return prompt


# ---------------------------------------------------------------------------
# Working directory management
# ---------------------------------------------------------------------------
def _content_work_dir(content_id: str) -> Path:
    """Create and return a working directory for a content item."""
    work = WORK_DIR / content_id
    work.mkdir(parents=True, exist_ok=True)
    return work


# ---------------------------------------------------------------------------
# Short-form pipeline
# ---------------------------------------------------------------------------
def process_short(content: dict):
    """
    Full pipeline for a single Short video.

    VRAM note: this function runs art generation first, then voice,
    assuming ComfyUI is shut down or idle before Chatterbox starts.
    In the batched flow (main), all art is done before all voice.
    For single-item processing, we still do art-then-voice sequentially.
    """
    content_id = content["id"]
    philosopher = content["philosopher"]
    topic = content.get("topic", "life and wisdom")
    channel = content.get("channels", {})
    channel_name = channel.get("name", "Wisdom")
    channel_slug = channel.get("slug", "wisdom")

    work = _content_work_dir(content_id)
    print(f"\n  Processing short: {philosopher} / {topic}")

    # --- Step 1: Quote ---
    log_step(content_id, "quote", 1, "running")
    try:
        # Use existing quote if provided, otherwise generate
        quote = content.get("quote_text", "").strip()
        if not quote:
            quote = generate_quote(philosopher, topic)
        log_step(content_id, "quote", 1, "success")
        print(f"  [quote] {quote[:80]}...")
    except Exception as e:
        log_step(content_id, "quote", 1, "failed", str(e))
        raise

    # --- Step 2: Metadata ---
    try:
        meta = generate_youtube_metadata(philosopher, quote, topic)
        title = meta.get("title", f"{philosopher} on {topic}")
        description = meta.get("description", "")
        tags = meta.get("tags", [])
    except Exception as e:
        print(f"  [meta] Warning: metadata generation failed ({e}), using defaults")
        title = f"{philosopher} on {topic}"
        description = quote
        tags = [philosopher, topic, "philosophy", "wisdom"]

    # --- Step 3: Art ---
    log_step(content_id, "image", 2, "running")
    try:
        art_prompt = _build_art_prompt(philosopher, quote, topic)
        lora = PHILOSOPHER_TO_LORA.get(philosopher, "stoic_classical_v1")
        art_path = str(work / "art.png")
        generate_art(art_prompt, lora, 832, 1216, art_path)
        log_step(content_id, "image", 2, "success")
    except Exception as e:
        log_step(content_id, "image", 2, "failed", str(e))
        raise

    # --- Step 4: Voice ---
    log_step(content_id, "voice", 3, "running")
    try:
        voice_settings = PHILOSOPHER_TO_VOICE_SETTINGS.get(
            philosopher, {"exaggeration": 0.5}
        )
        voice_path = str(work / "voice.wav")
        generate_voice(quote, voice_path, **voice_settings)
        log_step(content_id, "voice", 3, "success")
    except Exception as e:
        log_step(content_id, "voice", 3, "failed", str(e))
        raise

    # --- Step 5: Music ---
    music_path = pick_music(philosopher)

    # --- Step 6: Assemble video ---
    log_step(content_id, "video", 4, "running")
    try:
        music_style = PHILOSOPHER_TO_MUSIC_STYLE.get(philosopher, "stoic_classical")
        eq_color = EQUALIZER_COLORS.get(music_style, "#D4AF37")

        video_path = str(work / f"{channel_slug}_{content_id[:8]}.mp4")
        assemble_video(
            quotes=[quote],
            philosopher=philosopher,
            art_paths=[art_path],
            voice_paths=[voice_path],
            music_path=music_path,
            output_path=video_path,
            format="short",
            aspect_ratio="9:16",
            channel_name=channel_name,
            equalizer_color=eq_color,
        )
        log_step(content_id, "video", 4, "success")
    except Exception as e:
        log_step(content_id, "video", 4, "failed", str(e))
        raise

    # --- Step 7: Upload to Google Drive ---
    drive_url = None
    log_step(content_id, "upload", 5, "running")
    try:
        if channel.get("google_drive_folder_id"):
            drive_url = upload_to_drive(video_path, channel)
            print(f"  [upload] Drive URL: {drive_url}")
        else:
            print("  [upload] Skipped: no Google Drive folder configured")
        log_step(content_id, "upload", 5, "success")
    except Exception as e:
        log_step(content_id, "upload", 5, "failed", str(e))
        # Upload failure is non-fatal; video is still produced locally
        print(f"  [upload] WARNING: Drive upload failed: {e}")

    # --- Step 7b: Generate thumbnail ---
    thumb_drive_url = None
    try:
        from thumbnail_generator import generate_thumbnail, generate_thumbnail_from_video
        thumb_path = video_path.replace(".mp4", "_thumb.jpg")
        if art_path:
            generate_thumbnail(art_path, title, thumb_path, 1080, 1920)  # portrait for shorts
        else:
            generate_thumbnail_from_video(video_path, title, thumb_path, 1080, 1920)
        if channel.get("google_drive_folder_id") and drive_url:
            thumb_drive_url = upload_to_drive(thumb_path, channel)
        print(f"  [thumb] {thumb_path}")
    except Exception as e:
        print(f"  [thumb] WARNING: {e}")

    # --- Step 8: Update Supabase ---
    # status='ready' means generation is complete; human approves via dashboard
    # to advance to 'approved', which triggers youtube_uploader.py.
    updates = {
        "status": "ready",
        "quote_text": quote,
        "title": title,
        "description": description,
        "local_machine_path": video_path,
        "generation_params": {
            "lora": lora,
            "art_prompt": art_prompt,
            "voice_settings": voice_settings,
            "music_track": Path(music_path).name,
            "equalizer_color": eq_color,
            "tags": tags,
        },
    }
    if drive_url:
        updates["video_drive_url"] = drive_url
    if thumb_drive_url:
        updates["thumbnail_drive_url"] = thumb_drive_url
    update_supabase(content_id, updates)

    print(f"  DONE: {content_id} -> {video_path}")
    return video_path


# ---------------------------------------------------------------------------
# Mid-form pipeline
# ---------------------------------------------------------------------------
def process_midform(content: dict):
    """
    Full pipeline for a midform (multi-quote, landscape) video.
    Similar to short but generates multiple quotes + art pieces.
    """
    content_id = content["id"]
    philosopher = content["philosopher"]
    topic = content.get("topic", "life and wisdom")
    channel = content.get("channels", {})
    channel_name = channel.get("name", "Wisdom")
    channel_slug = channel.get("slug", "wisdom")

    work = _content_work_dir(content_id)
    num_quotes = 4
    print(f"\n  Processing midform: {philosopher} / {topic} ({num_quotes} quotes)")

    # --- Step 1: Quotes ---
    log_step(content_id, "quote", 1, "running")
    try:
        from ai_writer import generate_midform_script
        script = generate_midform_script(philosopher, topic, num_quotes=num_quotes)
        quotes = script.get("quotes", [])
        if not quotes:
            raise ValueError("Midform script returned no quotes")
        log_step(content_id, "quote", 1, "success")
        for i, q in enumerate(quotes):
            print(f"  [quote {i+1}] {q[:60]}...")
    except Exception as e:
        log_step(content_id, "quote", 1, "failed", str(e))
        raise

    # --- Step 2: Art (one per quote) ---
    log_step(content_id, "image", 2, "running")
    try:
        lora = PHILOSOPHER_TO_LORA.get(philosopher, "stoic_classical_v1")
        art_paths = []
        art_prompts = script.get("art_prompts", [])
        for i, quote in enumerate(quotes):
            # Use script-provided art prompt if available, else build one
            if i < len(art_prompts) and art_prompts[i]:
                art_prompt = art_prompts[i]
            else:
                art_prompt = _build_art_prompt(philosopher, quote, topic)
            art_path = str(work / f"art_{i}.png")
            generate_art(art_prompt, lora, 1216, 832, art_path)  # landscape
            art_paths.append(art_path)
        log_step(content_id, "image", 2, "success")
    except Exception as e:
        log_step(content_id, "image", 2, "failed", str(e))
        raise

    # --- Step 3: Voice (one per quote) ---
    log_step(content_id, "voice", 3, "running")
    try:
        voice_settings = PHILOSOPHER_TO_VOICE_SETTINGS.get(
            philosopher, {"exaggeration": 0.5}
        )
        voice_paths = []
        for i, quote in enumerate(quotes):
            voice_path = str(work / f"voice_{i}.wav")
            generate_voice(quote, voice_path, **voice_settings)
            voice_paths.append(voice_path)
        log_step(content_id, "voice", 3, "success")
    except Exception as e:
        log_step(content_id, "voice", 3, "failed", str(e))
        raise

    # --- Step 4: Music ---
    music_path = pick_music(philosopher)

    # --- Step 5: Assemble ---
    log_step(content_id, "video", 4, "running")
    try:
        music_style = PHILOSOPHER_TO_MUSIC_STYLE.get(philosopher, "stoic_classical")
        eq_color = EQUALIZER_COLORS.get(music_style, "#D4AF37")

        video_path = str(work / f"{channel_slug}_{content_id[:8]}_mid.mp4")
        assemble_video(
            quotes=quotes,
            philosopher=philosopher,
            art_paths=art_paths,
            voice_paths=voice_paths,
            music_path=music_path,
            output_path=video_path,
            format="midform",
            aspect_ratio="16:9",
            channel_name=channel_name,
            equalizer_color=eq_color,
        )
        log_step(content_id, "video", 4, "success")
    except Exception as e:
        log_step(content_id, "video", 4, "failed", str(e))
        raise

    # --- Step 6: Upload ---
    drive_url = None
    log_step(content_id, "upload", 5, "running")
    try:
        if channel.get("google_drive_folder_id"):
            drive_url = upload_to_drive(video_path, channel)
            print(f"  [upload] Drive URL: {drive_url}")
        else:
            print("  [upload] Skipped: no Google Drive folder configured")
        log_step(content_id, "upload", 5, "success")
    except Exception as e:
        log_step(content_id, "upload", 5, "failed", str(e))
        print(f"  [upload] WARNING: Drive upload failed: {e}")

    # --- Step 7: Update Supabase ---
    # Generate/refresh YouTube metadata from ai_writer for best SEO
    try:
        yt_meta = generate_youtube_metadata(philosopher, quotes[0], topic)
        title = yt_meta.get("title") or script.get("title", f"{philosopher} on {topic}")
        description = yt_meta.get("description") or script.get("description", "")
        tags = yt_meta.get("tags") or script.get("tags", [])
    except Exception as _yt_meta_err:
        print(f"  [meta] Warning: YouTube metadata gen failed ({_yt_meta_err}), using script values")
        title = script.get("title", f"{philosopher} on {topic}")
        description = script.get("description", "")
        tags = script.get("tags", [])

    # status='ready' — awaiting human approval in dashboard before YouTube upload
    updates = {
        "status": "ready",
        "quote_text": " | ".join(quotes),
        "title": title,
        "description": description,
        "local_machine_path": video_path,
        "generation_params": {
            "lora": lora,
            "quotes": quotes,
            "voice_settings": voice_settings,
            "music_track": Path(music_path).name,
            "equalizer_color": eq_color,
            "tags": tags,
        },
    }
    if drive_url:
        updates["video_drive_url"] = drive_url
    update_supabase(content_id, updates)

    print(f"  DONE: {content_id} -> {video_path}")
    return video_path


# ---------------------------------------------------------------------------
# Batched processing (VRAM-aware)
# ---------------------------------------------------------------------------
def _batch_process(items: list):
    """
    Process a list of content items with VRAM-aware batching:
    1. Generate all art first (ComfyUI uses GPU)
    2. Generate all voice next (Chatterbox uses GPU)
    3. Assemble + upload (CPU-only)

    This avoids running ComfyUI and Chatterbox simultaneously.
    """
    if not items:
        return

    results = {}  # content_id -> {art_paths, voice_paths, quote, ...}

    # ---------------------------------------------------------------
    # Phase 1: Quotes + Metadata (CPU/network, no GPU)
    # ---------------------------------------------------------------
    print("\n=== PHASE 1: Quote & Metadata Generation ===")
    for content in items:
        cid = content["id"]
        philosopher = content["philosopher"]
        topic = content.get("topic", "life and wisdom")
        content_type = content.get("format", "short")
        work = _content_work_dir(cid)

        try:
            log_step(cid, "quote", 1, "running")

            if content_type == "short":
                quote = content.get("quote_text", "").strip()
                if not quote:
                    quote = generate_quote(philosopher, topic)
                quotes = [quote]
                art_prompt_base = _build_art_prompt(philosopher, quote, topic)
                art_prompts = [art_prompt_base]
            else:
                from ai_writer import generate_midform_script
                script = generate_midform_script(philosopher, topic)
                quotes = script.get("quotes", [])
                art_prompts = script.get("art_prompts", [])
                if not quotes:
                    raise ValueError("Script returned no quotes")

            log_step(cid, "quote", 1, "success")
            results[cid] = {
                "quotes": quotes,
                "art_prompts": art_prompts,
                "content": content,
                "work": work,
            }
            print(f"  [{cid[:8]}] {len(quotes)} quote(s) ready")

        except Exception as e:
            log_step(cid, "quote", 1, "failed", str(e))
            update_supabase(cid, {"status": "failed"})
            print(f"  [{cid[:8]}] FAILED at quote: {e}")

    # ---------------------------------------------------------------
    # Phase 2: Art generation (GPU - ComfyUI)
    # ---------------------------------------------------------------
    print("\n=== PHASE 2: Art Generation (ComfyUI) ===")
    for cid, data in list(results.items()):
        content = data["content"]
        philosopher = content["philosopher"]
        lora = PHILOSOPHER_TO_LORA.get(philosopher, "stoic_classical_v1")
        content_type = content.get("format", "short")
        work = data["work"]

        # Determine dimensions based on format
        if content_type == "short":
            art_w, art_h = 832, 1216
        else:
            art_w, art_h = 1216, 832

        try:
            log_step(cid, "image", 2, "running")
            art_paths = []
            for i, quote in enumerate(data["quotes"]):
                if i < len(data["art_prompts"]) and data["art_prompts"][i]:
                    prompt = data["art_prompts"][i]
                else:
                    prompt = _build_art_prompt(
                        philosopher, quote,
                        content.get("topic", "life and wisdom")
                    )
                art_path = str(work / f"art_{i}.png")
                generate_art(prompt, lora, art_w, art_h, art_path)
                art_paths.append(art_path)

            data["art_paths"] = art_paths
            data["lora"] = lora
            log_step(cid, "image", 2, "success")
            print(f"  [{cid[:8]}] {len(art_paths)} image(s) generated")

        except Exception as e:
            log_step(cid, "image", 2, "failed", str(e))
            update_supabase(cid, {"status": "failed"})
            del results[cid]
            print(f"  [{cid[:8]}] FAILED at art: {e}")

    # ---------------------------------------------------------------
    # Phase 3: Voice generation (GPU - Chatterbox)
    # ---------------------------------------------------------------
    print("\n=== PHASE 3: Voice Generation (Chatterbox) ===")
    for cid, data in list(results.items()):
        content = data["content"]
        philosopher = content["philosopher"]
        voice_settings = PHILOSOPHER_TO_VOICE_SETTINGS.get(
            philosopher, {"exaggeration": 0.5}
        )
        work = data["work"]

        try:
            log_step(cid, "voice", 3, "running")
            voice_paths = []
            for i, quote in enumerate(data["quotes"]):
                voice_path = str(work / f"voice_{i}.wav")
                generate_voice(quote, voice_path, **voice_settings)
                voice_paths.append(voice_path)

            data["voice_paths"] = voice_paths
            data["voice_settings"] = voice_settings
            log_step(cid, "voice", 3, "success")
            print(f"  [{cid[:8]}] {len(voice_paths)} voice clip(s) generated")

        except Exception as e:
            log_step(cid, "voice", 3, "failed", str(e))
            update_supabase(cid, {"status": "failed"})
            del results[cid]
            print(f"  [{cid[:8]}] FAILED at voice: {e}")

    # ---------------------------------------------------------------
    # Phase 4: Assembly + Upload (CPU)
    # ---------------------------------------------------------------
    print("\n=== PHASE 4: Assembly & Upload ===")
    for cid, data in results.items():
        content = data["content"]
        philosopher = content["philosopher"]
        topic = content.get("topic", "life and wisdom")
        content_type = content.get("format", "short")
        channel = content.get("channels", {})
        channel_name = channel.get("name", "Wisdom")
        channel_slug = channel.get("slug", "wisdom")
        work = data["work"]
        quotes = data["quotes"]

        try:
            # Music
            music_path = pick_music(philosopher)

            # Assembly
            log_step(cid, "video", 4, "running")
            music_style = PHILOSOPHER_TO_MUSIC_STYLE.get(philosopher, "stoic_classical")
            eq_color = EQUALIZER_COLORS.get(music_style, "#D4AF37")

            if content_type == "short":
                vid_format, aspect = "short", "9:16"
                suffix = ""
            else:
                vid_format, aspect = "midform", "16:9"
                suffix = "_mid"

            video_path = str(work / f"{channel_slug}_{cid[:8]}{suffix}.mp4")
            assemble_video(
                quotes=quotes,
                philosopher=philosopher,
                art_paths=data["art_paths"],
                voice_paths=data["voice_paths"],
                music_path=music_path,
                output_path=video_path,
                format=vid_format,
                aspect_ratio=aspect,
                channel_name=channel_name,
                equalizer_color=eq_color,
            )
            log_step(cid, "video", 4, "success")

            # Upload
            drive_url = None
            log_step(cid, "upload", 5, "running")
            try:
                if channel.get("google_drive_folder_id"):
                    drive_url = upload_to_drive(video_path, channel)
                    print(f"  [{cid[:8]}] Drive: {drive_url}")
                else:
                    print(f"  [{cid[:8]}] Upload skipped (no Drive folder)")
                log_step(cid, "upload", 5, "success")
            except Exception as e:
                log_step(cid, "upload", 5, "failed", str(e))
                print(f"  [{cid[:8]}] Upload warning: {e}")

            # YouTube metadata — generate via ai_writer for SEO-optimised
            # title, description, and tags before marking ready
            try:
                meta = generate_youtube_metadata(philosopher, quotes[0], topic)
                title = meta.get("title") or f"{philosopher} on {topic}"
                description = meta.get("description", "")
                tags = meta.get("tags", [])
            except Exception as _meta_e:
                print(f"  [{cid[:8]}] Warning: metadata gen failed ({_meta_e}), using defaults")
                title = f"{philosopher} on {topic}"
                description = quotes[0]
                tags = [philosopher, topic, "philosophy"]

            # Update Supabase — status='ready' signals generation is done;
            # human approves in dashboard → status becomes 'approved' →
            # content_poller triggers youtube_uploader.py automatically.
            updates = {
                "status": "ready",
                "quote_text": " | ".join(quotes) if len(quotes) > 1 else quotes[0],
                "title": title,
                "description": description,
                "local_machine_path": video_path,
                "generation_params": {
                    "lora": data.get("lora", ""),
                    "voice_settings": data.get("voice_settings", {}),
                    "music_track": Path(music_path).name,
                    "equalizer_color": eq_color,
                    "tags": tags,
                },
            }
            if drive_url:
                updates["video_drive_url"] = drive_url
            update_supabase(cid, updates)
            print(f"  [{cid[:8]}] DONE -> {video_path}")

        except Exception as e:
            log_step(cid, "video", 4, "failed", str(e))
            update_supabase(cid, {"status": "failed"})
            print(f"  [{cid[:8]}] FAILED at assembly: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Wisdom pipeline orchestrator. Processes queued content."
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="Max items to process (0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show queue without processing")
    parser.add_argument("--no-batch", action="store_true",
                        help="Process items one-by-one instead of VRAM-aware batching")
    args = parser.parse_args()

    start = datetime.now()
    print(f"{'='*60}")
    print(f"Wisdom Orchestrator")
    print(f"Started: {start.isoformat()}")
    print(f"{'='*60}")

    # Fetch queue
    try:
        queued = fetch_queued_content()
    except Exception as e:
        print(f"FATAL: Could not fetch queue from Supabase: {e}")
        sys.exit(1)

    if args.limit > 0:
        queued = queued[:args.limit]

    print(f"Found {len(queued)} queued item(s)")

    if not queued:
        print("Nothing to process. Exiting.")
        return

    # Dry run: just print the queue
    if args.dry_run:
        print(f"\n{'ID':<40} {'Type':<10} {'Philosopher':<25} {'Topic'}")
        print("-" * 100)
        for item in queued:
            print(
                f"{item['id']:<40} "
                f"{item.get('format', '?'):<10} "
                f"{item['philosopher']:<25} "
                f"{item.get('topic', '?')}"
            )
        return

    # Process
    if args.no_batch:
        # Sequential, one-by-one (simpler, no VRAM optimization)
        for content in queued:
            try:
                content_type = content.get("format", "short")
                if content_type in ("short", "story"):
                    process_short(content)
                elif content_type in ("midform", "longform", "compilation"):
                    process_midform(content)
                else:
                    process_short(content)
                print(f"  Done: {content['id']}")
            except Exception as e:
                print(f"  FAILED: {content['id']} - {e}")
                traceback.print_exc()
                log_step(content["id"], "publish", 0, "failed", str(e))
                update_supabase(content["id"], {"status": "failed"})
    else:
        # Batched processing (VRAM-aware: all art first, then all voice)
        _batch_process(queued)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n{'='*60}")
    print(f"Orchestrator finished in {elapsed:.1f}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
