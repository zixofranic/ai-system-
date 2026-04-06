"""
Supabase Storage upload/download utility for the Wisdom pipeline.
Uploads to the Fellows project's wisdom-videos and wisdom-thumbnails buckets.
"""
import os
import re
import mimetypes
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

FELLOWS_URL = os.environ.get("FELLOWS_SUPABASE_URL", "https://cujwhqoezvehwhhigxmr.supabase.co")
FELLOWS_KEY = os.environ.get("FELLOWS_SUPABASE_SERVICE_KEY", "")


def _storage_headers(content_type: str = "application/octet-stream") -> dict:
    return {
        "Authorization": f"Bearer {FELLOWS_KEY}",
        "apikey": FELLOWS_KEY,
        "Content-Type": content_type,
    }


def upload_to_storage(
    file_path: str,
    bucket: str,
    channel_slug: str,
    format_name: str,
    filename: str | None = None,
) -> str:
    """
    Upload a file to Supabase Storage on the Fellows project.

    Args:
        file_path: Local path to the file
        bucket: 'wisdom-videos' or 'wisdom-thumbnails'
        channel_slug: Channel slug (e.g., 'wisdom', 'gibran')
        format_name: Content format (e.g., 'short', 'midform', 'story')
        filename: Override filename (defaults to file's basename)

    Returns:
        Storage path (e.g., 'wisdom/short/video_abc123.mp4')
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if filename is None:
        filename = file_path.name

    # Sanitize filename
    name, ext = os.path.splitext(filename)
    name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
    filename = f"{name}{ext}"

    storage_path = f"{channel_slug}/{format_name}/{filename}"
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

    url = f"{FELLOWS_URL}/storage/v1/object/{bucket}/{storage_path}"

    # Use PUT with x-upsert so re-renders overwrite cleanly. POST returns 400/409
    # when the object already exists; PUT with upsert handles both create and update.
    headers = _storage_headers(content_type)
    headers["x-upsert"] = "true"
    with open(file_path, "rb") as f:
        resp = requests.put(
            url,
            headers=headers,
            data=f,
            timeout=600,
        )

    resp.raise_for_status()
    print(f"  [storage] Uploaded to {bucket}/{storage_path}")
    return storage_path


def get_public_url(bucket: str, storage_path: str) -> str:
    """Get the public CDN URL for a file in Supabase Storage."""
    return f"{FELLOWS_URL}/storage/v1/object/public/{bucket}/{storage_path}"


def download_from_storage(bucket: str, storage_path: str, dest_path: str) -> str:
    """Download a file from Supabase Storage to a local path."""
    url = get_public_url(bucket, storage_path)
    resp = requests.get(url, stream=True, timeout=600)
    resp.raise_for_status()

    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    print(f"  [storage] Downloaded {bucket}/{storage_path} -> {dest_path}")
    return dest_path
