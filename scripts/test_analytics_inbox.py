"""Tests for analytics row paging and TikTok inbox draft cleanup.

Run with pytest OR directly:

    python test_analytics_inbox.py
"""
import time
from unittest.mock import MagicMock, patch

import analytics_fetcher as af


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def _publish_id(age_hours):
    created = int(time.time() - age_hours * 3600)
    return f"v_inbox_file~v2.{created << 32}"


def _reconcile(status, age_hours):
    """Run the reconcile for one draft. Returns the PATCH mock."""
    row = {"id": "row-1", "tiktok_video_id": _publish_id(age_hours)}
    with patch.object(af, "_tiktok_access_token", return_value="token"), \
            patch.object(af.requests, "post",
                         return_value=_response({"data": {"status": status}})), \
            patch.object(af.requests, "get",
                         return_value=_response([{"generation_params": {"a": 1}}])), \
            patch.object(af.requests, "patch") as patched:
        af.reconcile_inbox_share({}, row)
    return patched


def test_fetch_reads_every_page_past_the_page_size():
    pages = [[{"id": str(i)} for i in range(af.ROW_PAGE_SIZE)],
             [{"id": "last"}]]
    with patch.object(af.requests, "get",
                      side_effect=[_response(p) for p in pages]) as fetched:
        rows = af.fetch_rows_with_published_content()
    assert len(rows) == af.ROW_PAGE_SIZE + 1
    offsets = [call.kwargs["params"]["offset"] for call in fetched.call_args_list]
    assert offsets == ["0", str(af.ROW_PAGE_SIZE)]
    assert fetched.call_args.kwargs["params"]["order"].startswith("created_at.desc")


def test_fetch_surfaces_a_failed_page():
    failed = _response({"message": "boom"})
    failed.raise_for_status.side_effect = RuntimeError("500")
    with patch.object(af.requests, "get", return_value=failed):
        try:
            af.fetch_rows_with_published_content()
        except RuntimeError:
            return
    raise AssertionError("a failed page must not look like an empty result")


def test_old_stuck_processing_upload_is_cleared():
    patched = _reconcile("PROCESSING_UPLOAD", af.INBOX_DRAFT_MAX_AGE_H + 100)
    body = patched.call_args.kwargs["json"]
    assert body["tiktok_video_id"] is None
    assert "PROCESSING_UPLOAD" in body["generation_params"]["tiktok_last_error"]


def test_recent_processing_upload_is_left_alone():
    assert not _reconcile("PROCESSING_UPLOAD", 1).called


def test_old_inbox_draft_is_cleared():
    assert _reconcile("SEND_TO_USER_INBOX", af.INBOX_DRAFT_MAX_AGE_H + 1).called


def test_recent_inbox_draft_is_left_alone():
    assert not _reconcile("SEND_TO_USER_INBOX", 5).called


def test_api_error_never_clears_an_old_row():
    assert not _reconcile(None, af.INBOX_DRAFT_MAX_AGE_H + 100).called


if __name__ == "__main__":
    import sys
    import traceback

    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception:
            failed += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
