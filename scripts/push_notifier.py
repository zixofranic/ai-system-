"""Web Push sender for the content pipeline.

Called by content_poller.py when a content row transitions to
status='ready'. Broadcasts a notification to every push_subscription
belonging to a user who has access to that row's channel.

Design decisions (CTO review 2026-04-23):
  - Fires directly from the poller. No Supabase Edge Function + webhook
    round-trip — the poller is already the system of record for state
    transitions, already runs every 5 min, already writes to Supabase.
  - VAPID private key lives in env (.env). Public key in Vercel env
    for the dashboard's /api/push/vapid endpoint.
  - Expired subscriptions (HTTP 410 Gone) are pruned immediately so
    the table doesn't bloat with dead devices.

Usage:
    from push_notifier import notify_ready
    notify_ready(content_row)   # idempotent; safe to call repeatedly
"""
from __future__ import annotations

import json
import os
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# Required env vars on the Python side:
#   VAPID_PRIVATE_KEY       — base64url private key (paired with the
#                             VAPID_PUBLIC_KEY on the dashboard side)
#   VAPID_SUBJECT           — mailto: URI for push service to contact
#                             us if deliveries fail. e.g.
#                             "mailto:ziadfeg@gmail.com"
# Generate a key pair once with:
#   python -c "from py_vapid import Vapid; v=Vapid(); v.generate_keys(); \
#              print('PUBLIC:', v.public_key_urlsafe_base64()); \
#              print('PRIVATE:', v.private_key_urlsafe_base64())"
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:ziadfeg@gmail.com")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://app.recoverycomrades.com")

_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def _fetch_subscribers_for_channel(channel_id: str) -> list[dict]:
    """Return push_subscription rows for users with access to this channel.

    Admins (user_profiles.role='admin') always receive. Non-admins
    must have an entry in user_channel_access for the target channel.
    Mirrors the RLS logic in can_access_channel().
    """
    try:
        # 1. Admin user_ids
        admin_resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/user_profiles"
            f"?select=id&role=eq.admin",
            headers=_HEADERS, timeout=10,
        )
        admin_resp.raise_for_status()
        admin_ids = {r["id"] for r in admin_resp.json()}

        # 2. Users with explicit channel access
        access_resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/user_channel_access"
            f"?select=user_id&channel_id=eq.{channel_id}",
            headers=_HEADERS, timeout=10,
        )
        access_resp.raise_for_status()
        access_ids = {r["user_id"] for r in access_resp.json()}

        user_ids = admin_ids | access_ids
        if not user_ids:
            return []

        # 3. Fetch subscriptions for those users
        in_clause = ",".join(user_ids)
        subs_resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/push_subscriptions"
            f"?select=id,user_id,endpoint,p256dh,auth"
            f"&user_id=in.({in_clause})",
            headers=_HEADERS, timeout=10,
        )
        subs_resp.raise_for_status()
        return subs_resp.json()
    except Exception as e:
        print(f"  [push] WARN: fetch_subscribers failed ({e})")
        return []


def _delete_subscription(sub_id: str) -> None:
    try:
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/push_subscriptions?id=eq.{sub_id}",
            headers=_HEADERS, timeout=10,
        )
    except Exception as e:
        print(f"  [push] WARN: delete_subscription({sub_id[:8]}) failed ({e})")


def _build_payload(row: dict) -> dict:
    """Build the JSON payload the service worker will receive.

    Shape must match the PushPayload interface in worker/index.ts.
    """
    title = row.get("title") or "New video ready"
    philosopher = row.get("philosopher") or ""
    format_tag = row.get("format") or "short"
    channel = (row.get("channels") or {}).get("slug") or ""
    short_body = f"{philosopher} · {format_tag}" if philosopher else format_tag
    # Deep-link to the review page with the content_id as a query marker
    # so the dashboard can scroll to or highlight that row.
    url = f"{DASHBOARD_URL}/review?channel={channel}&focus={row['id']}"
    return {
        "title": title[:80],
        "body": short_body[:120],
        "url": url,
        # Dedup key — same content_id only shows the latest notification.
        "tag": f"content-{row['id']}",
    }


def notify_ready(row: dict) -> Optional[dict]:
    """Send a push to every subscriber with access to this row's channel.

    Returns a summary dict: {sent, failed, pruned}. Returns None if
    VAPID is not configured (graceful no-op in dev).
    """
    if not VAPID_PRIVATE_KEY:
        print("  [push] skipped — VAPID_PRIVATE_KEY not configured")
        return None

    channel_id = row.get("channel_id")
    if not channel_id:
        print("  [push] skipped — row has no channel_id")
        return None

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("  [push] skipped — pywebpush not installed (pip install pywebpush)")
        return None

    subs = _fetch_subscribers_for_channel(channel_id)
    if not subs:
        return {"sent": 0, "failed": 0, "pruned": 0}

    payload = json.dumps(_build_payload(row))
    vapid_claims = {"sub": VAPID_SUBJECT}

    sent = 0
    failed = 0
    pruned = 0

    for sub in subs:
        sub_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=vapid_claims,
            )
            sent += 1
        except WebPushException as e:
            # 404 / 410 = subscription dead (device uninstalled or token
            # expired). Prune immediately so the table doesn't rot.
            status = getattr(e.response, "status_code", None) if hasattr(e, "response") else None
            if status in (404, 410):
                _delete_subscription(sub["id"])
                pruned += 1
            else:
                failed += 1
                print(f"  [push] WARN: send failed ({status}): {e}")
        except Exception as e:
            failed += 1
            print(f"  [push] WARN: unexpected send error: {e}")

    if sent or pruned or failed:
        print(f"  [push] {sent} sent · {pruned} pruned · {failed} failed "
              f"for '{row.get('title', '?')[:50]}'")
    return {"sent": sent, "failed": failed, "pruned": pruned}
