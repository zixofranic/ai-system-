"""
Content Poller — runs on the local GPU machine.
Checks Supabase every 5 minutes for:
  1. Content with status='queued'  → start ComfyUI, run orchestrator, stop ComfyUI
  2. Content with status='approved' and video_drive_url set → run youtube_uploader

Auto-starts via Windows Startup folder.
"""

import os
import sys
import time
import subprocess
import signal
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://kwyqaewdvvdhodxieqrh.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
POLL_INTERVAL = 300  # 5 minutes
ORCHESTRATOR = Path("C:/AI/system/scripts/orchestrator.py")
YOUTUBE_UPLOADER = Path("C:/AI/system/scripts/youtube_uploader.py")
TIKTOK_UPLOADER = Path("C:/AI/system/scripts/tiktok_uploader.py")
COMFYUI_DIR = Path("C:/AI/system/ComfyUI")
COMFYUI_PORT = 8188
CONDA_BAT = Path("C:/Users/ziadf/miniconda3/condabin/conda.bat")
PYTHON_CHATTERBOX = "C:/Users/ziadf/miniconda3/envs/chatterbox/python.exe"
PYTHON_LORA = "C:/Users/ziadf/miniconda3/envs/lora_train/python.exe"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

comfyui_process = None


def check_queued_content():
    """Check Supabase for content with status='queued'."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/content"
        params = {
            "select": "id,philosopher,topic,status,format,channel_id",
            "status": "eq.queued",
            "deleted_at": "is.null",
            "order": "created_at.asc",
            "limit": "20",
        }
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"  Supabase error: {resp.status_code}")
            return []
    except Exception as e:
        print(f"  Connection error: {e}")
        return []


def check_tiktok_content():
    """Check for approved content with tiktok_publish_requested and no tiktok_video_id."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/content"
        params = {
            "select": "id,philosopher,topic,channel_id",
            "status": "eq.approved",
            "video_drive_url": "not.is.null",
            "generation_params->tiktok_publish_requested": "eq.true",
            "tiktok_video_id": "is.null",
            "deleted_at": "is.null",
            "order": "created_at.asc",
            "limit": "10",
        }
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        return resp.json() if resp.status_code == 200 else []
    except Exception as e:
        print(f"  Error checking TikTok: {e}")
        return []


def run_tiktok_uploader():
    """Run tiktok_uploader.py to publish flagged content."""
    print("  Running TikTok uploader...")
    result = subprocess.run(
        [PYTHON_CHATTERBOX, str(TIKTOK_UPLOADER)],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, timeout=3600,
    )
    if result.returncode == 0:
        print("  TikTok uploader completed")
        if result.stdout:
            for line in result.stdout.strip().split("\n")[-5:]:
                print(f"    {line}")
    else:
        print(f"  TikTok uploader failed (exit {result.returncode})")
        if result.stderr:
            print(f"  Error: {result.stderr[:300]}")
    return result.returncode


def promote_scheduled_content():
    """Promote scheduled content whose publish time has arrived to approved."""
    try:
        now_iso = datetime.utcnow().isoformat() + "Z"
        url = f"{SUPABASE_URL}/rest/v1/content"
        params = {
            "select": "id,title",
            "status": "eq.scheduled",
            "scheduled_at": f"lte.{now_iso}",
            "deleted_at": "is.null",
        }
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        items = resp.json() if resp.status_code == 200 else []
        for item in items:
            requests.patch(
                f"{url}?id=eq.{item['id']}",
                headers=HEADERS,
                json={"status": "approved"},
                timeout=10,
            )
            print(f"  Promoted scheduled -> approved: {item.get('title', item['id'][:8])}")
    except Exception as e:
        print(f"  Error promoting scheduled: {e}")


def check_approved_content():
    """Check Supabase for approved content with youtube_publish_requested and no youtube_video_id."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/content"
        params = {
            "select": "id,philosopher,topic,channel_id",
            "status": "eq.approved",
            "video_drive_url": "not.is.null",
            "generation_params->youtube_publish_requested": "eq.true",
            "youtube_video_id": "is.null",
            "deleted_at": "is.null",
            "order": "created_at.asc",
            "limit": "10",
        }
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"  Supabase error checking approved: {resp.status_code}")
            return []
    except Exception as e:
        print(f"  Connection error checking approved: {e}")
        return []


def is_comfyui_running():
    """Check if ComfyUI is responding."""
    try:
        resp = requests.get(f"http://localhost:{COMFYUI_PORT}/system_stats", timeout=3)
        return resp.status_code == 200
    except:
        return False


def start_comfyui():
    """Start ComfyUI as a background process."""
    global comfyui_process

    if is_comfyui_running():
        print("  ComfyUI already running")
        return True

    print("  Starting ComfyUI...")
    comfyui_process = subprocess.Popen(
        [
            str(CONDA_BAT), "run", "-n", "comfyui", "--no-banner",
            "python", "main.py", "--port", str(COMFYUI_PORT), "--preview-method", "auto"
        ],
        cwd=str(COMFYUI_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    # Wait for it to be ready (up to 60 seconds)
    for i in range(30):
        time.sleep(2)
        if is_comfyui_running():
            print(f"  ComfyUI started (took {(i+1)*2}s)")
            return True

    print("  WARNING: ComfyUI didn't start in 60s")
    return False


def stop_comfyui():
    """Stop ComfyUI to free VRAM."""
    global comfyui_process

    if comfyui_process:
        print("  Stopping ComfyUI...")
        comfyui_process.terminate()
        try:
            comfyui_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            comfyui_process.kill()
        comfyui_process = None
        print("  ComfyUI stopped, VRAM freed")
    elif is_comfyui_running():
        # ComfyUI was started externally — leave it alone
        print("  ComfyUI was started externally, leaving it running")


def run_orchestrator():
    """Run the orchestrator to process queued content."""
    print("  Running orchestrator...")
    result = subprocess.run(
        [PYTHON_CHATTERBOX, str(ORCHESTRATOR)],
        env={**os.environ, "PYTHONIOENCODING": "utf-8",
             "IMAGEMAGICK_BINARY": r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe"},
        capture_output=True,
        text=True,
        timeout=7200,  # 2 hours max
    )
    if result.returncode == 0:
        print("  Orchestrator completed successfully")
        if result.stdout:
            # Print last few lines of output
            lines = result.stdout.strip().split("\n")
            for line in lines[-5:]:
                print(f"    {line}")
    else:
        print(f"  Orchestrator failed (exit {result.returncode})")
        if result.stderr:
            print(f"  Error: {result.stderr[:500]}")
    return result.returncode


def update_status(content_id, status, error=None):
    """Update content status in Supabase."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/content?id=eq.{content_id}"
        body = {"status": status}
        if error:
            body["rejection_reason"] = error
        requests.patch(url, headers=HEADERS, json=body, timeout=10)
    except Exception as e:
        print(f"  Failed to update status: {e}")


def run_youtube_uploader():
    """
    Run youtube_uploader.py to publish all approved content.
    Uses the lora_train env which has google-api-python-client / requests.
    """
    print("  Running YouTube uploader...")
    result = subprocess.run(
        [PYTHON_LORA, str(YOUTUBE_UPLOADER)],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        timeout=3600,  # 1 hour max for batch uploads
    )
    if result.returncode == 0:
        print("  YouTube uploader completed successfully")
        if result.stdout:
            lines = result.stdout.strip().split("\n")
            for line in lines[-10:]:
                print(f"    {line}")
    else:
        print(f"  YouTube uploader failed (exit {result.returncode})")
        if result.stderr:
            print(f"  Error: {result.stderr[:500]}")
    return result.returncode


def main():
    print("=" * 60)
    print("  CONTENT POLLER")
    print(f"  Checking Supabase every {POLL_INTERVAL // 60} minutes")
    print(f"  Started: {datetime.now()}")
    print("  ComfyUI auto-start/stop: ENABLED")
    print("  YouTube auto-publish: ENABLED (approved content)")
    print("=" * 60)

    if not SUPABASE_KEY:
        print("ERROR: SUPABASE_SERVICE_KEY not found in .env")
        return

    while True:
        now = datetime.now().strftime("%H:%M:%S")

        try:
            # --- Check for new content to generate ---
            queued = check_queued_content()

            if queued:
                print(f"\n[{now}] Found {len(queued)} queued items!")
                for item in queued:
                    print(f"  - {item.get('philosopher', '?')}: {item.get('topic', '?')}")

                # Start ComfyUI
                comfyui_was_running = is_comfyui_running()
                if not comfyui_was_running:
                    start_comfyui()

                # Run orchestrator
                run_orchestrator()

                # Stop ComfyUI if we started it (free VRAM)
                if not comfyui_was_running:
                    stop_comfyui()

                print(f"[{now}] Generation batch complete.")
            else:
                print(f"[{now}] No queued content.")

            # --- Check for scheduled content whose time has arrived ---
            promote_scheduled_content()

            # --- Check for approved content to publish to YouTube ---
            approved = check_approved_content()

            if approved:
                print(f"\n[{now}] Found {len(approved)} approved item(s) ready to publish:")
                for item in approved:
                    print(f"  - [{item['id'][:8]}] {item.get('philosopher','?')}: "
                          f"{item.get('topic','?')}")
                exit_code = run_youtube_uploader()
                if exit_code == 0:
                    # Mark published items: set status to 'published'
                    for item in approved:
                        update_status(item["id"], "published")
                print(f"[{now}] YouTube publish batch complete.")
            else:
                print(f"[{now}] No approved content awaiting YouTube upload.")

            # --- Check for approved content to publish to TikTok ---
            tiktok_items = check_tiktok_content()
            if tiktok_items:
                print(f"\n[{now}] Found {len(tiktok_items)} item(s) for TikTok:")
                for item in tiktok_items:
                    print(f"  - [{item['id'][:8]}] {item.get('philosopher','?')}")
                run_tiktok_uploader()
                print(f"[{now}] TikTok publish batch complete.")
            else:
                print(f"[{now}] No content awaiting TikTok upload.")

            print(f"[{now}] Sleeping {POLL_INTERVAL // 60} min...")

        except Exception as e:
            print(f"[{now}] ERROR: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nPoller stopped by user.")
        stop_comfyui()
