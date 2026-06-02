"""
Backfill 16:9 YouTube thumbnails onto already-published NA/AA shorts.

Old published shorts have YouTube's auto-grabbed frame (or a broken 9:16
custom thumbnail). This regenerates a proper 1280x720 16:9 thumbnail from a
clean frame of each video and re-runs thumbnails.set.

QUOTA-SAFE: thumbnails.set costs 50 Data-API units each. Caps at
MAX_PER_CHANNEL most-recent videos, hard-stops on any quota 403, and prints
running quota usage so it can't starve same-day publishing.

Usage:
    python backfill_yt_thumbnails.py            # default cap
    python backfill_yt_thumbnails.py --max 15   # tighter cap per channel
"""
import sys, os, argparse, subprocess, tempfile
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")
sys.path.insert(0, r"C:\AI\system\scripts")
from thumbnail_generator import generate_youtube_thumbnail
from youtube_uploader import _youtube_set_thumbnail

URL = os.environ["SUPABASE_URL"]; KEY = os.environ["SUPABASE_SERVICE_KEY"]
CID = os.environ["GOOGLE_CLIENT_ID"]; CSEC = os.environ["GOOGLE_CLIENT_SECRET"]
FU = os.environ.get("FELLOWS_SUPABASE_URL", "https://cujwhqoezvehwhhigxmr.supabase.co")
FK = os.environ.get("FELLOWS_SUPABASE_SERVICE_KEY", "")
H = {"Authorization": f"Bearer {KEY}", "apikey": KEY}

ap = argparse.ArgumentParser()
ap.add_argument("--max", type=int, default=30, help="max videos per channel")
args = ap.parse_args()
MAX_PER_CHANNEL = args.max

QUOTA_PER_SET = 50
quota_used = 0
WORK = Path(r"C:\AI\system\pipeline_work\_yt_backfill"); WORK.mkdir(parents=True, exist_ok=True)


def channel_ids():
    chans = requests.get(f"{URL}/rest/v1/channels?select=id,slug&slug=in.(na,aa)", headers=H, timeout=15).json()
    return {c["slug"]: c["id"] for c in chans}


def refresh_token(channel_slug):
    s = requests.get(f"{URL}/rest/v1/channels?select=settings&slug=eq.{channel_slug}", headers=H, timeout=15).json()[0]["settings"]
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CID, "client_secret": CSEC,
        "refresh_token": s["youtube_refresh_token"], "grant_type": "refresh_token",
    }, timeout=20)
    return r.json()["access_token"]


def clean_frame(video_local, video_storage, out_png):
    """Extract a watermark-free frame (intro pad, top cropped) from the video.
    Prefer the local file; download from storage only if needed."""
    src = video_local if (video_local and os.path.exists(video_local)) else None
    tmp_dl = None
    if not src and video_storage:
        tmp_dl = str(WORK / "_dl.mp4")
        r = requests.get(f"{FU}/storage/v1/object/wisdom-videos/{video_storage}",
                         headers={"Authorization": f"Bearer {FK}", "apikey": FK}, timeout=120)
        if r.status_code != 200:
            return False
        open(tmp_dl, "wb").write(r.content); src = tmp_dl
    if not src:
        return False
    # frame @1.0s (within the intro pad, before captions); crop top 8% to
    # drop the burned-in "ONE DAY AT A TIME" watermark so it doesn't double
    # with the wordmark the 16:9 generator adds.
    rc = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0", "-i", src,
        "-frames:v", "1", "-vf", "crop=iw:ih*0.92:0:ih*0.08", out_png,
    ], capture_output=True)
    if tmp_dl:
        try: os.unlink(tmp_dl)
        except Exception: pass
    return os.path.exists(out_png) and os.path.getsize(out_png) > 0


def run():
    global quota_used
    ids = channel_ids()
    done = skipped = failed = 0
    for slug in ("na", "aa"):
        token = refresh_token(slug)
        rows = requests.get(
            f"{URL}/rest/v1/content?select=id,title,youtube_video_id,local_machine_path,"
            f"video_storage_path&channel_id=eq.{ids[slug]}&format=eq.short&"
            f"youtube_video_id=not.is.null&order=published_at.desc&limit={MAX_PER_CHANNEL}",
            headers=H, timeout=20).json()
        print(f"\n=== {slug.upper()}: {len(rows)} most-recent shorts (cap {MAX_PER_CHANNEL}) ===")
        for x in rows:
            vid = x["youtube_video_id"]; title = x.get("title") or ""
            frame = str(WORK / "frame.png")
            if not clean_frame(x.get("local_machine_path"), x.get("video_storage_path"), frame):
                print(f"  [skip] {vid}  (no usable video source)"); skipped += 1; continue
            yt = str(WORK / "yt.jpg")
            generate_youtube_thumbnail(frame, title, yt, channel_slug=slug)
            try:
                _youtube_set_thumbnail(token, vid, yt)
                quota_used += QUOTA_PER_SET
                done += 1
                print(f"  [ok]  {vid}  ({quota_used} units used)  {title[:42]}")
            except Exception as e:
                m = str(e)
                if "quota" in m.lower():
                    print(f"  [STOP] quota exhausted at {quota_used} units. Halting cleanly.")
                    print(f"\n  Backfilled {done}, skipped {skipped}, failed {failed}. "
                          f"Re-run after midnight PT reset for the rest.")
                    return
                print(f"  [fail] {vid}: {m[:120]}"); failed += 1
    print(f"\n=== DONE: backfilled {done}, skipped {skipped}, failed {failed}. "
          f"Quota used: {quota_used} units (~{10000 - quota_used} left for publishing). ===")


if __name__ == "__main__":
    run()
