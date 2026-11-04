"""One upload per video per platform, even if two uploaders run at once.

Before posting, an uploader must win a claim on the row. The claim is a
conditional database write that only matches while the claim is empty, so when
two processes race, the database lets exactly one through. Checking "has this
been posted?" first isn't enough: both processes can check before either finishes.
That gap put 129 extra videos on YouTube by 2026-11-04.

Usage:
    token = claim_upload(content_id, "youtube")
    if not token:
        return
    try:
        # Re-read the row here. Another uploader may have finished just before this.
        ...post, then save the platform's id on the row...
    finally:
        release_upload(content_id, "youtube", token)

A claim left behind by a crash is never taken over automatically, because the
crashed upload may have gone through. After CLAIM_STALE_MINUTES the row is halted
with an error, so a person can check the platform and publish again if needed.
Any error while claiming counts as losing: skipping a tick is safe, and a second
post isn't.
"""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
CLAIM_STALE_MINUTES = 120


def _headers(prefer="return=minimal"):
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json", "Prefer": prefer}


def _claim_key(platform):
    return f"{platform}_upload_claim"


def _read_params(content_id):
    resp = requests.get(f"{SUPABASE_URL}/rest/v1/content", headers=_headers(), timeout=15,
                        params={"id": f"eq.{content_id}", "select": "generation_params"})
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    params = rows[0].get("generation_params") or {}
    return json.loads(params) if isinstance(params, str) else params


def _claim_age(value):
    """Age of a claim value, "<iso time>|<holder>". Unreadable counts as stale."""
    try:
        stamp = datetime.fromisoformat(str(value).split("|")[0])
        return datetime.now(timezone.utc) - stamp
    except ValueError:
        return timedelta(days=365)


def _halt_stale_claim(content_id, platform, params, value):
    key = _claim_key(platform)
    params = {k: v for k, v in params.items()
              if k not in (key, f"{platform}_publish_requested")}
    now = datetime.now(timezone.utc).isoformat()
    params[f"{platform}_retry_halted"] = True
    params[f"{platform}_retry_halted_at"] = now
    params[f"{platform}_last_error"] = (
        f"An upload started at {str(value).split('|')[0]} never finished. Check "
        f"{platform} for this video before publishing it again, so it isn't posted twice.")
    params[f"{platform}_error_at"] = now
    requests.patch(f"{SUPABASE_URL}/rest/v1/content", headers=_headers(), timeout=15,
                   params={"id": f"eq.{content_id}",
                           f"generation_params->>{key}": f"eq.{value}"},
                   json={"generation_params": params}).raise_for_status()


def claim_upload(content_id, platform):
    """Return a token if this caller may post the row, otherwise None."""
    key = _claim_key(platform)
    try:
        params = _read_params(content_id)
        if params is None:
            return None
        existing = params.get(key)
        if existing:
            if _claim_age(existing) > timedelta(minutes=CLAIM_STALE_MINUTES):
                print(f"  [{content_id[:8]}] Interrupted {platform} upload found, halting the row")
                _halt_stale_claim(content_id, platform, params, existing)
            else:
                print(f"  [{content_id[:8]}] Another uploader has {platform}, skipping")
            return None
        token = f"{datetime.now(timezone.utc).isoformat()}|{uuid.uuid4().hex}"
        resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/content",
            headers=_headers("return=representation"), timeout=15,
            params={"id": f"eq.{content_id}", "deleted_at": "is.null", "select": "id",
                    f"generation_params->>{key}": "is.null"},
            json={"generation_params": {**params, key: token}},
        )
        resp.raise_for_status()
        if resp.json():
            return token
        print(f"  [{content_id[:8]}] Lost the {platform} claim to another uploader, skipping")
        return None
    except (requests.RequestException, ValueError) as e:
        print(f"  [{content_id[:8]}] Couldn't claim {platform}, skipping this tick: {e}")
        return None


def release_upload(content_id, platform, token):
    """Clear our claim. Call only after the platform's id is saved on the row, so
    the next claimer sees it. If this fails, the claim goes stale and halts safely."""
    key = _claim_key(platform)
    try:
        params = _read_params(content_id)
        if params is None or params.get(key) != token:
            return
        params.pop(key)
        requests.patch(f"{SUPABASE_URL}/rest/v1/content", headers=_headers(), timeout=15,
                       params={"id": f"eq.{content_id}",
                               f"generation_params->>{key}": f"eq.{token}"},
                       json={"generation_params": params}).raise_for_status()
    except (requests.RequestException, ValueError) as e:
        print(f"  [{content_id[:8]}] WARN: couldn't release the {platform} claim: {e}")
