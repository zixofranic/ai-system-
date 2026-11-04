"""Tests for the daily content automation shipped 2026-11-04.

Covers the pure logic behind:
  - the stricter weekly planner gate (ai_writer)
  - curated hashtags and recovery title cleanup (caption_tags)
  - choosing today's short (daily_autogen)
  - the morning publish digest (daily_publish_digest)

Run with pytest OR directly:

    python test_content_automation.py
"""
import ai_writer
from ai_writer import (
    _filter_repeat_topics,
    _merge_shared_history,
    _stock_phrase_in,
    _judge_repeat_subjects,
    _apply_freshness_gate,
)
from caption_tags import (
    MAX_HASHTAGS,
    build_caption,
    clean_title,
    filter_platform_tags,
    select_hashtags,
)
from daily_autogen import pick_short_for_today, week_bounds
from daily_publish_digest import build_digest


def _item(channel, topic, day="Monday", fmt="short"):
    return {"day": day, "channel": channel, "philosopher": "X",
            "topic": topic, "format": fmt, "hook": ""}


# ---------------------------------------------------------------------------
# Planner: shared memory between channels on the same account
# ---------------------------------------------------------------------------
def test_na_and_aa_share_history():
    merged = _merge_shared_history({"na": ["clean subject"], "aa": ["sober subject"],
                                    "wisdom": ["stoic subject"]})
    assert set(merged["na"]) == {"clean subject", "sober subject"}
    assert merged["na"] == merged["aa"]
    assert merged["wisdom"] == ["stoic subject"]


def test_merged_history_blocks_subject_from_other_recovery_channel():
    merged = _merge_shared_history({"na": ["The first sober wedding and the toast you dread"], "aa": []})
    kept, rejected = _filter_repeat_topics(
        [_item("aa", "The first sober wedding and the toast you dread")], merged)
    assert not kept and len(rejected) == 1


def test_same_plan_cannot_repeat_across_na_and_aa():
    plan = [_item("na", "The chair nobody wants at a funeral after relapse"),
            _item("aa", "The chair nobody wants at a funeral after relapse")]
    kept, rejected = _filter_repeat_topics(plan, {})
    assert len(kept) == 1 and len(rejected) == 1


def test_same_plan_can_repeat_across_unrelated_channels():
    plan = [_item("wisdom", "Choosing silence during a family argument"),
            _item("gibran", "Choosing silence during a family argument")]
    kept, rejected = _filter_repeat_topics(plan, {})
    assert len(kept) == 2 and not rejected


# ---------------------------------------------------------------------------
# Planner: stock phrasing and repeated openers
# ---------------------------------------------------------------------------
def test_stock_phrases_detected_case_insensitively():
    assert _stock_phrase_in("What I tell SPONSEES WHO relapse") == "sponsees who"
    assert _stock_phrase_in("Clean long enough to get bored") == "long enough to"
    assert _stock_phrase_in("The toast at your brother's sober wedding") is None


def test_stock_phrase_topic_is_rejected():
    kept, rejected = _filter_repeat_topics(
        [_item("na", "The man who has finally stopped apologizing to his mother")], {})
    assert not kept and len(rejected) == 1


def test_repeated_opener_in_one_plan_is_rejected():
    plan = [_item("gibran", "Why grief arrives on ordinary Tuesdays"),
            _item("gibran", "Why grief arrives through old recipes"),
            _item("gibran", "A mother's letter to a departing son")]
    kept, rejected = _filter_repeat_topics(plan, {})
    assert [i["topic"] for i in rejected] == ["Why grief arrives through old recipes"]
    assert len(kept) == 2


def test_same_philosopher_twice_is_not_a_repeated_opener():
    plan = [{**_item("wisdom", "Emerson on the oak and the acorn"), "philosopher": "Emerson"},
            {**_item("wisdom", "Emerson on the journal read ten years later", day="Friday"),
             "philosopher": "Emerson"}]
    kept, rejected = _filter_repeat_topics(plan, {})
    assert len(kept) == 2 and not rejected


def test_full_philosopher_name_is_stripped_from_opener():
    plan = [{**_item("wisdom", "Marcus Aurelius on the cold morning"), "philosopher": "Marcus Aurelius"},
            {**_item("wisdom", "Marcus on the cold morning walk to camp"), "philosopher": "Marcus Aurelius"}]
    assert len(_filter_repeat_topics(plan, {})[1]) == 1


def test_weekday_in_topic_is_rejected():
    assert _stock_phrase_in("The silence in a Tuesday room") == "tuesday"
    assert _stock_phrase_in("Why grief arrives on ordinary Tuesdays") is None


def test_style_rules_forbid_invented_attribution():
    assert "invent" in ai_writer._TOPIC_STYLE_RULES.lower()


def test_missing_daily_short_days_become_slots():
    specs = [{"slug": "gibran", "personas": ["Gibran Khalil Gibran"], "quotas": {"short": 7}},
             {"slug": "wisdom", "personas": ["Seneca"], "quotas": {"short": 3}}]
    days = ["Monday", "Tuesday", "Wednesday", "Friday", "Saturday", "Sunday"]
    plan = [_item("gibran", f"topic {d}", day=d) for d in days]
    plan.append(_item("gibran", "a story", day="Thursday", fmt="story"))
    slots = ai_writer._missing_daily_short_slots(plan, specs)
    assert [(s["channel"], s["day"], s["format"]) for s in slots] == [("gibran", "Thursday", "short")]
    assert slots[0]["philosopher"] == "Gibran Khalil Gibran"


def test_gate_fills_missing_day_slot():
    responses = iter([
        '{"repeats": []}',
        '[{"day": "Thursday", "channel": "gibran", "philosopher": "G", '
        '"topic": "A lamp left burning for a late traveler", "format": "short", "hook": ""}]',
        '{"repeats": []}',
    ])
    original = ai_writer._call_anthropic
    ai_writer._call_anthropic = lambda *a, **k: next(responses)
    try:
        plan = _apply_freshness_gate(
            [_item("gibran", "A mended coat")], {},
            missing_slots=[{"day": "Thursday", "channel": "gibran",
                            "philosopher": "G", "format": "short"}])
    finally:
        ai_writer._call_anthropic = original
    assert len(plan) == 2 and plan[1]["day"] == "Thursday"


def test_long_description_ends_on_a_full_sentence():
    description = "First sentence stays. " + "word " * 60
    caption = build_caption({"title": "T", "description": description}, "wisdom")
    assert "\n\nFirst sentence stays.\n\n" in caption


def test_long_sentence_is_cut_on_a_word():
    caption = build_caption({"title": "T", "description": "word " * 60}, "wisdom")
    assert "word...\n\n" in caption


def test_ai_tags_cannot_assign_the_wrong_school():
    tags = select_hashtags("wisdom", "Lao Tzu", ["Lao Tzu", "stoicism", "wu wei"])
    assert "#Stoicism" not in tags and "#Taoism" in tags


# ---------------------------------------------------------------------------
# Remotion: one retry, and the real error reaches the row
# ---------------------------------------------------------------------------

class _FakeRender:
    def __init__(self, returncode, stderr=""):
        self.returncode, self.stderr, self.stdout = returncode, stderr, ""


def _with_fake_render(results):
    import render_remotion
    calls = []
    def fake_run(*args, **kwargs):
        calls.append(args)
        return results[len(calls) - 1]
    original = render_remotion.subprocess.run
    render_remotion.subprocess.run = fake_run
    try:
        render_remotion._run_remotion_render("cmd", "cwd")
        return calls, None
    except RuntimeError as e:
        return calls, e
    finally:
        render_remotion.subprocess.run = original


def test_render_retries_once_after_a_failure():
    calls, error = _with_fake_render([_FakeRender(1, "boom"), _FakeRender(0)])
    assert len(calls) == 2 and error is None


def test_render_failure_carries_remotion_error():
    calls, error = _with_fake_render([_FakeRender(1, "first"), _FakeRender(1, "TypeError: real cause")])
    assert len(calls) == 2 and "TypeError: real cause" in str(error)


def test_successful_render_runs_once():
    calls, error = _with_fake_render([_FakeRender(0)])
    assert len(calls) == 1 and error is None


# ---------------------------------------------------------------------------
# Finished renders must keep the auto-queue stamp
# ---------------------------------------------------------------------------

def test_finished_render_keeps_auto_queue_stamp():
    """The digest and /publish find rows by auto_queued_on, and a render's final
    write replaces generation_params whole. Real PATCH body is checked here."""
    import orchestrator

    class FakeResponse:
        def __init__(self, body):
            self.body = body
        def raise_for_status(self):
            pass
        def json(self):
            return self.body

    sent = {}
    def fake_get(url, **kwargs):
        return FakeResponse([{"auto_queued_on": "2026-11-04", "auto_queued_at": "t"}])
    def fake_patch(url, headers=None, json=None, timeout=None):
        sent.update(json)
        return FakeResponse([{"id": "x"}])

    get, patch = orchestrator.requests.get, orchestrator.requests.patch
    orchestrator.requests.get, orchestrator.requests.patch = fake_get, fake_patch
    try:
        orchestrator.update_supabase("x", {"status": "ready",
                                           "generation_params": {"tags": ["a"]}})
    finally:
        orchestrator.requests.get, orchestrator.requests.patch = get, patch
    assert sent["generation_params"] == {
        "tags": ["a"], "auto_queued_on": "2026-11-04", "auto_queued_at": "t"}


def test_update_without_params_reads_nothing_extra():
    import orchestrator

    class FakeResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return [{"id": "x"}]

    def fail_get(*args, **kwargs):
        raise AssertionError("no read needed when generation_params isn't written")

    get, patch = orchestrator.requests.get, orchestrator.requests.patch
    orchestrator.requests.get = fail_get
    orchestrator.requests.patch = lambda *a, **k: FakeResponse()
    try:
        orchestrator.update_supabase("x", {"status": "generating"})
    finally:
        orchestrator.requests.get, orchestrator.requests.patch = get, patch


# ---------------------------------------------------------------------------
# Planner: judging by meaning, with Claude
# ---------------------------------------------------------------------------
def test_judge_returns_flagged_indexes(monkeypatch=None):
    original = ai_writer._call_anthropic
    ai_writer._call_anthropic = lambda *a, **k: '{"repeats": [1, 99, "bad"]}'
    try:
        flagged = _judge_repeat_subjects(
            [_item("aa", "Giving advice at meetings too soon"),
             _item("aa", "Offering guidance in rooms before you are ready")],
            {"aa": ["Handing out advice in meetings while hiding your own truth"]},
        )
    finally:
        ai_writer._call_anthropic = original
    assert flagged == {1}


def test_judge_failure_keeps_everything():
    def boom(*a, **k):
        raise RuntimeError("api down")
    original = ai_writer._call_anthropic
    ai_writer._call_anthropic = boom
    try:
        assert _judge_repeat_subjects([_item("aa", "Any topic at all")], {"aa": ["x"]}) == set()
    finally:
        ai_writer._call_anthropic = original


def test_gate_replaces_judged_repeat_and_keeps_week_full():
    """The judge flags one topic; the replacement pass fills that exact slot."""
    responses = iter([
        '{"repeats": [0]}',
        '[{"day": "Monday", "channel": "aa", "philosopher": "X", '
        '"topic": "Calling your father from the parking lot", "format": "short", "hook": ""}]',
        '{"repeats": []}',
    ])
    original = ai_writer._call_anthropic
    ai_writer._call_anthropic = lambda *a, **k: next(responses)
    try:
        plan = _apply_freshness_gate(
            [_item("aa", "Giving advice at meetings too soon"),
             _item("aa", "A borrowed truck and an old debt", day="Tuesday")],
            {"aa": ["Handing out advice in meetings"]},
        )
    finally:
        ai_writer._call_anthropic = original
    assert sorted(i["topic"] for i in plan) == [
        "A borrowed truck and an old debt", "Calling your father from the parking lot"]


def test_gate_stops_after_max_passes():
    calls = {"n": 0}

    def always_stock(system, *a, **k):
        calls["n"] += 1
        return ('[{"day": "Monday", "channel": "aa", "philosopher": "X", '
                '"topic": "What I tell sponsees who relapse", "format": "short", "hook": ""}]')

    original = ai_writer._call_anthropic
    ai_writer._call_anthropic = always_stock
    try:
        plan = _apply_freshness_gate([_item("aa", "What I tell sponsees who drift")], {},
                                     max_passes=2)
    finally:
        ai_writer._call_anthropic = original
    assert plan == []
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Hashtags
# ---------------------------------------------------------------------------
def test_recovery_hashtags_never_include_philosophy():
    tags = select_hashtags("na", "The Old-Timer",
                           ["philosophy", "wisdom", "amends", "the old timer"])
    assert not [t for t in tags if "philosoph" in t.lower() or "wisdom" in t.lower()]
    assert "#Amends" in tags
    assert "#TheOldTimer" not in tags


def test_no_channel_uses_shorts_or_exceeds_cap():
    for slug, persona in (("wisdom", "Seneca"), ("gibran", "Gibran Khalil Gibran"),
                          ("na", "The Sponsor"), ("aa", "The Sponsor")):
        tags = select_hashtags(slug, persona, ["mindfulness", "grief", "gratitude", "amends"])
        assert 1 <= len(tags) <= MAX_HASHTAGS, (slug, tags)
        assert "#shorts" not in {t.lower() for t in tags}
        assert len({t.lower() for t in tags}) == len(tags)


def test_wisdom_hashtags_follow_the_philosopher():
    tags = select_hashtags("wisdom", "Marcus Aurelius", [])
    assert "#MarcusAurelius" in tags and "#Stoicism" in tags
    assert "#LaoTzu" in select_hashtags("wisdom", "Lao Tzu", [])


def test_unapproved_ai_tags_are_ignored():
    tags = select_hashtags("aa", "The Sponsor", ["self-help philosophy", "motivational"])
    assert all(t.lower() not in {"#selfhelpphilosophy", "#motivational"} for t in tags)


def test_no_program_name_hashtags_on_recovery():
    for slug in ("na", "aa"):
        lowered = {t.lower() for t in select_hashtags(slug, "The Sponsor", ["AA", "NA", "alcoholics anonymous"])}
        assert not lowered & {"#aa", "#na", "#alcoholicsanonymous", "#narcoticsanonymous"}


# ---------------------------------------------------------------------------
# Recovery titles and captions
# ---------------------------------------------------------------------------
def test_recovery_title_drops_philosophy_segment():
    assert clean_title("The Sponsor: Your Work Isn't Done | Philosophy of Growth", "aa") \
        == "The Sponsor: Your Work Isn't Done"


def test_recovery_title_rewrites_inline_philosophy():
    assert clean_title("The Philosophy of Letting Go", "na") == "Letting Go"


def test_other_channels_keep_philosophy_titles():
    title = "Seneca | Philosophy of Time"
    assert clean_title(title, "wisdom") == title


def test_caption_uses_clean_title_and_skips_philosophy_description():
    caption = build_caption({
        "title": "The Sponsor: Your Work Isn't Done | Philosophy of Growth",
        "description": "A philosophical look at growth.\nMore.",
        "philosopher": "The Sponsor",
        "generation_params": {"tags": ["philosophy", "amends"]},
    }, "aa")
    assert "philosoph" not in caption.lower()
    assert caption.startswith("The Sponsor: Your Work Isn't Done")
    assert "#Amends" in caption


def test_caption_accepts_json_string_params_and_missing_fields():
    caption = build_caption({"title": "A title", "generation_params": '{"tags": ["grief"]}'}, "gibran")
    assert caption.startswith("A title") and "#Grief" in caption
    assert build_caption({"generation_params": None}, "wisdom").strip()


def test_youtube_tags_drop_philosophy_only_on_recovery():
    tags = ["philosophy", "amends", "Recovery Philosophy"]
    assert filter_platform_tags(tags, "na") == ["amends"]
    assert filter_platform_tags(tags, "wisdom") == tags


# ---------------------------------------------------------------------------
# Choosing today's short
# ---------------------------------------------------------------------------
def _row(row_id, date):
    return {"id": row_id, "scheduled_at": f"{date}T09:00:00+00:00"}


def test_week_bounds_are_monday_to_next_monday():
    assert week_bounds("2026-11-04") == ("2026-11-02", "2026-11-09")
    assert week_bounds("2026-11-02") == ("2026-11-02", "2026-11-09")
    assert week_bounds("2026-11-08") == ("2026-11-02", "2026-11-09")


def test_pick_prefers_today():
    rows = [_row("mon", "2026-11-02"), _row("wed-a", "2026-11-04"), _row("wed-b", "2026-11-04")]
    assert pick_short_for_today(rows, "2026-11-04")["id"] == "wed-a"


def test_pick_falls_back_inside_this_week_only():
    rows = [_row("fri", "2026-11-06"), _row("mon", "2026-11-02")]
    assert pick_short_for_today(rows, "2026-11-04")["id"] == "mon"


def test_pick_never_touches_old_backlog_or_next_week():
    rows = [_row("old", "2026-10-28"), _row("older", "2026-04-01"), _row("next", "2026-11-09")]
    assert pick_short_for_today(rows, "2026-11-04") is None


def test_pick_ignores_rows_without_a_date():
    assert pick_short_for_today([{"id": "x", "scheduled_at": None}], "2026-11-04") is None


# ---------------------------------------------------------------------------
# Morning digest
# ---------------------------------------------------------------------------
def _digest_row(**over):
    row = {"id": "1", "title": "A <b>title</b>", "status": "ready", "channel": "aa",
           "tiktok_caption": "Caption #Sobriety", "rejection_reason": None}
    row.update(over)
    return row


def test_digest_links_to_confirm_page_and_escapes_html():
    subject, text, html = build_digest([_digest_row()], "2026-11-04", "https://x.test/publish")
    assert "1 ready" in subject
    assert "https://x.test/publish" in text and 'href="https://x.test/publish"' in html
    assert "<b>title</b>" not in html and "&lt;b&gt;title" in html
    assert "Caption #Sobriety" in text


def _send_with_fake_claude(returncode, stdout, stderr=""):
    import daily_publish_digest as digest
    seen = {}

    class Result:
        pass

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["input"], seen["env"] = cmd, kwargs["input"], kwargs["env"]
        result = Result()
        result.returncode, result.stdout, result.stderr = returncode, stdout, stderr
        return result

    original = digest.subprocess.run
    digest.subprocess.run = fake_run
    try:
        return digest._send("Subject", "Text", "<b>Html</b>"), seen
    finally:
        digest.subprocess.run = original


def test_email_counts_as_sent_only_when_gmail_confirms():
    assert _send_with_fake_claude(0, "SENT abc123")[0] is True
    assert _send_with_fake_claude(0, "FAILED tool missing")[0] is False
    assert _send_with_fake_claude(0, "I sent it for you")[0] is False
    assert _send_with_fake_claude(1, "", "not logged in")[0] is False


def test_email_session_logs_in_with_the_account_not_the_api_key():
    """With the .env's key inherited, connectors vanish and no email goes out."""
    import os
    os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY") or "test-key"
    _, seen = _send_with_fake_claude(0, "SENT x")
    assert "ANTHROPIC_API_KEY" not in seen["env"]


def test_email_session_gets_only_the_gmail_send_tool():
    import daily_publish_digest as digest
    _, seen = _send_with_fake_claude(0, "SENT x")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "ToolSearch"
    assert cmd[cmd.index("--allowedTools") + 1] == digest.GMAIL_SEND_TOOL
    assert digest.DEFAULT_EMAIL_TO in seen["input"]


def test_digest_publish_link_is_readable_without_styles():
    _, _, html = build_digest([_digest_row()], "2026-11-04", "https://x.test/publish")
    assert "color:#fff" not in html and "background" not in html
    assert ">https://x.test/publish</a>" in html


def test_digest_reports_failures_and_unfinished_work():
    subject, text, _ = build_digest(
        [_digest_row(status="failed", rejection_reason="voice step: 500"),
         _digest_row(id="2", status="generating", title="In progress")],
        "2026-11-04", "https://x.test/publish")
    assert "0 ready" in subject and "1 failed" in subject
    assert "voice step: 500" in text and "In progress" in text


def test_digest_with_nothing_says_so():
    subject, text, _ = build_digest([], "2026-11-04", "https://x.test/publish")
    assert "0 ready" in subject and "no videos" in text.lower()


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
