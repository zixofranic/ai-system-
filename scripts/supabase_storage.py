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
    file_size = file_path.stat().st_size

    # Supabase Storage's direct PUT endpoint caps at ~50 MB. Anything larger
    # must use the TUS resumable protocol. We auto-switch based on file size.
    TUS_THRESHOLD = 48 * 1024 * 1024  # 48 MB — safety margin under the 50 MB cap

    if file_size > TUS_THRESHOLD:
        _upload_via_tus(
            file_path, bucket, storage_path, content_type, file_size,
        )
    else:
        url = f"{FELLOWS_URL}/storage/v1/object/{bucket}/{storage_path}"
        headers = _storage_headers(content_type)
        headers["x-upsert"] = "true"
        with open(file_path, "rb") as f:
            resp = requests.put(url, headers=headers, data=f, timeout=600)
        resp.raise_for_status()

    print(f"  [storage] Uploaded to {bucket}/{storage_path}  ({file_size / 1024 / 1024:.1f} MB)")
    return storage_path


def _upload_via_tus(
    file_path: Path,
    bucket: str,
    storage_path: str,
    content_type: str,
    file_size: int,
) -> None:
    """
    Upload a file using the TUS 1.0 resumable protocol. Supabase Storage
    exposes this at /storage/v1/upload/resumable. Used for files >50 MB
    where the direct PUT endpoint fails with 413.

    This is a minimal TUS client — single chunked upload, no retry/resume.
    For the Wisdom pipeline files (~50–200 MB) that's sufficient; if we
    ever need to survive flaky uploads we can add PATCH retry logic.
    """
    import base64 as _b64

    tus_endpoint = f"{FELLOWS_URL}/storage/v1/upload/resumable"

    # 1) Create the upload: POST with Upload-Length, Upload-Metadata
    metadata_pairs = {
        "bucketName": bucket,
        "objectName": storage_path,
        "contentType": content_type,
        "cacheControl": "3600",
    }
    encoded_metadata = ",".join(
        f"{k} {_b64.b64encode(v.encode()).decode()}"
        for k, v in metadata_pairs.items()
    )

    create_headers = {
        "Authorization": f"Bearer {FELLOWS_KEY}",
        "apikey": FELLOWS_KEY,
        "Tus-Resumable": "1.0.0",
        "Upload-Length": str(file_size),
        "Upload-Metadata": encoded_metadata,
        "x-upsert": "true",
    }
    create_resp = requests.post(tus_endpoint, headers=create_headers, timeout=30)
    if create_resp.status_code not in (200, 201):
        raise RuntimeError(
            f"TUS create failed: {create_resp.status_code} {create_resp.text[:300]}"
        )
    upload_url = create_resp.headers.get("Location")
    if not upload_url:
        raise RuntimeError("TUS create returned no Location header")

    # 2) PATCH the binary content
    patch_headers = {
        "Authorization": f"Bearer {FELLOWS_KEY}",
        "apikey": FELLOWS_KEY,
        "Tus-Resumable": "1.0.0",
        "Upload-Offset": "0",
        "Content-Type": "application/offset+octet-stream",
    }
    with open(file_path, "rb") as f:
        patch_resp = requests.patch(
            upload_url, headers=patch_headers, data=f, timeout=1200,
        )
    if patch_resp.status_code not in (200, 204):
        raise RuntimeError(
            f"TUS patch failed: {patch_resp.status_code} {patch_resp.text[:300]}"
        )


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
