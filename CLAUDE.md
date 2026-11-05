# AI System — Shared Infrastructure

This directory contains shared GPU tools and services used by ALL channels.

## What's Here

| Tool | Path | Port | Purpose |
|---|---|---|---|
| ComfyUI | `C:\AI\system\ComfyUI\` | 8188 | Image generation (SDXL + LoRAs) |
| Chatterbox TTS | `C:\AI\system\Chatterbox-TTS-Server\` | 8004 | Voice cloning / TTS (fallback, not primary) |
| kohya_ss | `C:\AI\system\kohya_ss\` | — | LoRA training |
| Ollama | System-installed | 11434 | LLM (19 philosopher models) |
| n8n | System-installed | 5678 | Automation workflows |

## Pipeline Scripts (`C:\AI\system\scripts\`)

| Script | Role | Called By |
|--------|------|-----------|
| `content_poller.py` | 5-min loop: queue check → orchestrator → uploaders | `START_ALL.bat` |
| `orchestrator.py` | Main generation pipeline (quote → art → voice → video → upload) | content_poller |
| `ai_writer.py` | Claude Sonnet/Haiku + Ollama for scripts, quotes, metadata | orchestrator |
| `assemble_video.py` | MoviePy video assembly with Ken Burns, equalizer, overlays | orchestrator |
| `thumbnail_generator.py` | PIL thumbnail: gradient + title text overlay | orchestrator |
| `youtube_uploader.py` | Google API resumable upload, Shorts detection | content_poller |
| `tiktok_uploader.py` | TikTok API upload (shorts only, 9:16 validated) | content_poller |
| `generate_story_video.py` | Standalone story pipeline (Remotion, IP-Adapter, Whisper) | manual |
| `generate_batch.py` | Original standalone batch (Saturday working version) | manual |

## Voice Generation (Primary: Chatterbox)

**All four channels run on Chatterbox today.** `C:\AI\.env` sets
`WISDOM_TTS_PROVIDER`, `GIBRAN_TTS_PROVIDER`, `NA_TTS_PROVIDER`, and
`AA_TTS_PROVIDER` all to `chatterbox`. The older "ElevenLabs is primary,
DO NOT use Chatterbox" guidance is obsolete — it predates the 2026-04-22
migration. ElevenLabs remains the rollback path and is still hard-wired
into the `story` format (see the gap table below).

Chatterbox serves at port 8004; `config.yaml` sets
`reference_audio_path: C:\AI\system\voice\recordings`.

| Channel | Reference clip | Notes |
|---|---|---|
| wisdom | `wisdom_burton_long_ref.wav` | atempo 0.80 + reverb + tail fade |
| gibran | `gibran_long_ref.wav` | do NOT stack Wisdom's slow_factor |
| na / aa | per-persona, see below | atempo 0.85, exaggeration 0.25 |

ElevenLabs voice IDs (rollback / story path only): Wisdom
`0ABJJI7ZYmWZBiUBMHUW` (James Burton), Gibran `R68HwD2GzEdWfqYZP9FQ`.

### NA/AA persona voices

NA and AA share four selectable voices. The picker writes
`content.philosopher`; `_resolve_voice_config` (orchestrator.py) maps it to
a reference clip via `CHANNEL_VOICE[slug].chatterbox.persona_refs`.

| Persona | Reference clip | Register |
|---|---|---|
| The Old-Timer | `na_old_timer_5min.wav` | male, ~103 Hz — 20+ yrs, steady, spare |
| The Sponsor | `na_sponsor_5min.wav` | male, ~96 Hz — direct, warm, practical |
| The Voice of the Rooms | `na_rooms_5min.wav` | **female, ~180 Hz — intentional**; the rooms are everyone |
| Ziad | `ziad_anniversary_25sec.wav` | male, ~87 Hz — channel owner, first year |

All four are valid on every format: short, story, story_vertical, midform.

`Ziad` is the one named voice — Tradition 11 protects *other* members'
anonymity, not the channel owner's.

The persona list is duplicated in four places. **Keep them in sync:**
`orchestrator.py` `persona_refs`, `ai_writer.py` `_RECOVERY_PERSONA_VOICES`,
dashboard `lib/constants.ts` `PHILOSOPHERS.na/.aa`, and dashboard
`lib/channel-philosopher.ts` `NA_AA_PERSONAS` (the server-side validator —
a persona missing *here* is rejected on save no matter what the UI offers).

### Two invariants (2026-07-27 — learned the hard way)

Every NA/AA story rendered in the wrong voice for ~10 weeks. The row's
persona was correct in the database and correct in the log, then discarded
at the last step. Two rules prevent the whole class of bug:

1. **No writer function may set `result["philosopher"]`.** A writer consumes
   the persona; it never redefines it. The row is authoritative downstream.
   If an artifact needs the value stamped on it, the *orchestration* layer
   does that with the row's value (see `generate_story_video.py`).
2. **No TTS call site may contain a persona string literal.** Always
   `generate_voice(philosopher=<row value>)`. Never re-read it back out of
   the writer's return value — laundering it through the writer is how it
   got lost.

A third guard now backs these: `_resolve_voice_config` logs a WARNING
instead of silently falling back to the channel default when a persona
isn't found in `persona_refs`.

## Content Generation Flow

```
content_poller.py (every 5 min)
  ├── Checks status=queued → runs orchestrator.py
  │     ├── Shorts:  1 quote (Ollama) → 1 art (ComfyUI) → 1 voice (11Labs) → assemble → Drive → Supabase
  │     └── Midform: 4 quotes+narration (Claude Sonnet) → 4 art → 4 voice → assemble → Drive → Supabase
  ├── Checks youtube_publish_requested → runs youtube_uploader.py
  └── Checks tiktok_publish_requested + format=short → runs tiktok_uploader.py
```

## Quote Deduplication

Before generating quotes, the orchestrator fetches the last 20 published quotes for the same philosopher from Supabase. These are injected into the prompt as "do not repeat" context. Works for both Ollama (shorts) and Claude Sonnet (midform) paths.

## Midform Narration Linking

`generate_midform_script()` returns `narration_segments` (bridge text between quotes). The orchestrator combines `narration[i] + quote[i]` into one ElevenLabs call per section, creating connected speech with a narrative arc: problem → exploration → insight → resolution.

## Platform Compatibility

| Format | YouTube | TikTok | Instagram |
|--------|---------|--------|-----------|
| short (9:16) | Shorts | Yes | Reels |
| story (16:9) | Regular | NO | NO |
| midform (16:9) | Regular | NO | NO |
| longform (16:9) | Regular | NO | NO |

Content poller enforces `format=eq.short` for TikTok queue. Dashboard disables TK/IG buttons for non-short formats.

## LoRAs (in ComfyUI/models/loras/)

| LoRA | Trigger | For |
|---|---|---|
| gibran_style_v1 | `gibran_style` | Gibran channel |
| stoic_classical_v1 | `stoic_classical` | Wisdom channel (Marcus, Seneca, Epictetus) |
| More coming... | — | One per day |

## Ollama Philosopher Models

`marcus_aurelius`, `seneca`, `epictetus`, `rumi`, `lao_tzu`, `nietzsche`, `gibran`, `confucius`, `dostoevsky`, `emerson`, `musashi`, `thoreau`, `wilde`, `sun_tzu`, `da_vinci`, `franklin`, `tesla`, `vivekananda`, `llama3.1:8b`

Orchestrator resolves names: `philosopher.lower().replace(" ", "_")`

## Conda Environments

- `chatterbox` — Python 3.11, PyTorch 2.11+cu128 (pipeline runs here)
- `comfyui` — Python 3.11, PyTorch 2.11+cu128
- `lora_train` — Python 3.11, PyTorch 2.11+cu128

## Start Everything

```
C:\AI\system\START_ALL.bat
```

Starts: Chatterbox (8004), ComfyUI (8188), Ollama (11434), Content Poller

## CHANNEL SEPARATION (NON-NEGOTIABLE)

Each channel is a completely separate project. NEVER mix content, output, or assets between them.

| | Gibran | Wisdom | NA | AA |
|---|---|---|---|---|
| **Directory** | `C:\AI\gibran\` | `C:\AI\wisdom\` | `C:\AI\na\` | `C:\AI\aa\` |
| **Output** | `C:\AI\gibran\videos\` | `C:\AI\wisdom\videos\` | `C:\AI\na\videos\` | `C:\AI\aa\videos\` |
| **LoRA** | `gibran_style_v1` | `stoic_classical_v1` (+ others) | `_NA_STYLE` art | `_NA_STYLE` art |
| **Voice (CB ref)** | `gibran_long_ref.wav` | `wisdom_burton_long_ref.wav` | 4 persona refs | same 4 as NA |
| **Ollama** | `gibran` | `marcus_aurelius`, `seneca`, etc. | `the_old_timer`, etc. | same as NA |
| **Texts** | `C:\AI\gibran\scripts\raw\` | `C:\AI\wisdom\authors\` | written fresh | written fresh |

When generating content, ALWAYS verify the output path matches the channel.

Voice references are channel-scoped too. NA/AA never borrow Burton or
Gibran clips, and vice versa — a cross-channel reference is a bug, not a
fallback. One known live violation: `generate_story_vertical.py` hard-codes
the Burton clip for *both* wisdom and gibran, so a Gibran story_vertical
renders in James Burton's voice.

## Projects Using This Infrastructure

- `C:\AI\gibran\` — Gibran partnership channel (with Elias)
- `C:\AI\wisdom\` — Wisdom solo channel (Ziad only)
- `C:\AI\na\` — One Day At A Time (NA recovery shorts → `@OneDayAtATime-26`)
- `C:\AI\aa\` — Easy Does It (AA recovery shorts → `@EasyDoesIt-26`)
- `C:\AI\simpler.os\` — Simpler RE OS help video system (Digital Joe tutorials)
- **`C:\dev\fellows\comrades\`** — FELLOWS app one-off marketing assets (anniversary videos, launch pieces). Reuses this stack for VO + art + render. See "FELLOWS One-Off Asset Generation" section below.

## FELLOWS One-Off Asset Generation

The FELLOWS app (recoveryfellows.com) has its own personal-voice marketing pieces that don't fit the automated NA/AA channel pipeline. These are one-off scripts authored manually (or drafted by Claude and approved by Ziad), generated through the existing infrastructure, and used on the FELLOWS social accounts and landing page.

**Voice clones available:**
- Ziad's cloned voice (`gOEUY8IEUhraboLFmcGQ`, "Ziad Instant Clone") — lives in `C:\dev\vibes\pipeline\.env` for the Vibes/Rewire project, reused here for FELLOWS personal-voice content. Verified live on the Vibes account 2026-06-15. This is the ONLY current Ziad EL clone.
- ~~Backup clone: `HZTk7bUIkiI7yT7FKH4h`~~ — STALE: verified 2026-06-15 that this id is now reassigned to a professional voice ("Brad – Meditation & Relaxation"), NOT a Ziad backup. Do not use it for Ziad.

**Two API keys on this machine:**
- `C:\AI\.env` → `ELEVENLABS_API_KEY` (AI pipeline). Missing `voices_read` scope.
- `C:\dev\vibes\pipeline\.env` → `ELEVENLABS_API_KEY` (Vibes/personal). Has full scope.
- FELLOWS one-off scripts should prefer the Vibes key (try it first, fall back to AI key).

**Existing FELLOWS one-off scripts in `C:\AI\system\scripts\`:**
- `generate_fellows_promo_vo.py` — launch IG promo VO (eleven_multilingual_v2, James Burton voice)
- `generate_fellows_anniversary_y1_vo.py` — 1-year anniversary VO (eleven_v3, Ziad clone, with tail silence padding)

**End-to-end FELLOWS video stack** (all on this machine, none external):
- **Script** — drafted in Claude, saved as a doc under `C:\dev\fellows\comrades\Doc\`, then embedded in a Python generator under `C:\AI\system\scripts\`
- **Voice** — ElevenLabs v3 (audio tags supported: `[pause]`, `[long pause]`, `[softly]`, `[warmly]`, etc. — but watch for tag bleed, 5-10% rate)
- **Art** — ComfyUI at port 8188, can use any of the trained LoRAs in `ComfyUI/models/loras/`
- **Render** — Remotion engine at `C:\AI\system\video-engine\` invoked via `render_remotion.py`. Supports short (1080×1920), story_vertical (1080×1920), midform (1920×1080), longform (1920×1080). Builds timeline.json + metadata.json, copies assets, runs Remotion CLI.
- **Post** — ffmpeg on PATH (via WinGet's Gyan.FFmpeg). Use `shutil.which("ffmpeg")` in Python scripts, fall back to `C:/ffmpeg/ffmpeg.exe` for backward compat with older code.

**Implication:** CapCut is no longer required for FELLOWS one-off videos. The full chain — script → VO → art → composition → MP4 — can run inside this stack. CapCut stays as a fallback when Ziad wants manual visual control on a specific piece.

## Supabase Free-Tier Quota (2026-10-13) — READ BEFORE ADDING ANY POLLING

The Wisdom Supabase project (`kwyqaewdvvdhodxieqrh`) is on the **Free plan,
and Ziad will NOT upgrade** — wait for the monthly reset instead of paying.
Billing cycle resets on the 17th. Free limits: 5 GB egress, 1 GB log
ingestion (log ingestion was "upcoming"/unenforced as of Oct 2026).

**Cycle Sep 17 – Oct 17 2026 hit 121% egress (6.06 GB) and 517% log
ingestion (5.17 GB).** Root cause (found by code reading — the Supabase MCP
here is on the SimplerOS org and cannot see this project; prod DB reads were
blocked, so not verified against pg_stat_statements):

- `analytics_fetcher.py` ran every 30 min (poller tick 6) over EVERY
  published row on all channels: per row a `channels?select=*` GET, a
  `generation_params` GET, and a PATCH sent with
  `Prefer: return=representation` — echoing the full content row back for
  nothing. Hundreds of rows × ~3-4 requests × 48 runs/day.
- Every request is also an edge-log line → the log-ingestion overage.

Fixes shipped 2026-10-13:
1. `analytics_fetcher.py` HEADERS → `Prefer: return=minimal`.
2. `get_channel()` cached per run (`_channel_cache`).
3. `content_poller.py` `ANALYTICS_EVERY_N_TICKS` 6 → 72 (every 6 h).
   Needs a poller restart to take effect.

Not yet verified that usage dropped — check the Wisdom project's
Reports → API in the dashboard after a day or two. If it didn't drop,
next suspects: other scripts still using `return=representation`
(orchestrator, meta_uploader, tiktok_daily_batch, generate_story_video,
ai_writer — all low-frequency), and dashboard pages selecting `*` from
`content`. Rules going forward: never `select=*` on `content` in a loop,
always `return=minimal` on PATCH unless the response is used, and keep
any new periodic job at ≥ hourly cadence.

## Daily Automation (2026-11-04)

Ziad only presses Publish. Everything before that runs on this machine.

| Time | Task | What it does |
|---|---|---|
| Sun 4:00 PM | `AI_WeeklyPlanner` | Plans the week. Skips channels already planned that week |
| 5:00 AM | `AI_DailyAutoGen` | `daily_autogen.py`: queues today's short per channel from the current week's plan only |
| 8:30 AM | `AI_DailyPublishDigest` | `daily_publish_digest.py`: emails what's ready, with a link to `/publish` |
| 9:00 AM | `AI_TikTokDailyBatch` | **Disabled 2026-11-04.** Sent old backlog to TikTok, which used up the 5 draft slots |

`PlanNAAA Weekly` is disabled: it planned NA and AA a second time every week.

- **Nothing here publishes.** Publishing only happens from the Publish button
  (`publishEverywhere` in the dashboard), and never on a page load, so opening
  an email link can't post anything.
- **`auto_queued_on` marks a row as automatic.** The digest and `/publish` both
  filter on it. A render's final write replaces `generation_params` whole, so
  `update_supabase` carries the stamp across. Don't remove that.
- **`daily_autogen.py` stops at `MAX_UNREVIEWED` (7) per channel**, so unreviewed
  videos can't pile up if Ziad is away.
- **Email goes through the Gmail connector**, to `ziadfeg@gmail.com` by default.
  The connector only exists inside Claude, so the digest starts `claude -p` with
  that one tool. It depends on Claude Code staying logged in on this machine. If
  it fails, the task exits 1 and the log says `EMAIL FAILED`. Optional in
  `C:\AI\.env`: `PUBLISH_EMAIL_TO`, `PUBLISH_PAGE_URL`.
- **Captions and hashtags live in one place:** `caption_tags.py`, used by the
  TikTok, Meta and YouTube uploaders. At most 5 tags, from curated per-channel
  lists. Recovery tags never name NA or AA programs.
- **Planner freshness:** `_apply_freshness_gate` in `ai_writer.py` checks subjects
  against 26 weeks of history by word overlap, then by meaning with Claude. NA and
  AA share one history. Stock phrases and weekday names are rejected.
- Logs: `scripts\logs\daily_autogen_*.log`, `daily_publish_digest_*.log`.
  Tests: `test_content_automation.py`, `test_plan_dedup.py`.

## TikTok Drafts (2026-11-05)

- **Captions must be pasted by hand.** Inbox drafts can't carry our caption. Posts
  show `#RecoveryComrades` unless it's replaced. That tag isn't in our scripts, so
  TikTok probably adds it from the app name (not confirmed).
- **`analytics_fetcher.py` reads every row in pages.** A flat limit of 500 used to
  skip rows, which got no stats and stayed listed as drafts after being posted.
- **Dead drafts clear after 48 hours**, for both `SEND_TO_USER_INBOX` and
  `PROCESSING_UPLOAD`. The status API never reports them as expired.
- **`/publish` lists drafts sent in the last 30 hours**, newest first. A posted
  draft can stay listed until the next analytics run, up to 6 hours.
- **The app lacks TikTok's read scopes**, so there are no TikTok view counts and
  posted captions and bios can only be read from the public profile page.

## One Upload Per Video (2026-11-04) — READ BEFORE TOUCHING AN UPLOADER

For months two `content_poller.py` processes ran at once: the elevated
`AI_ContentPoller` task and `WisdomPipeline.bat` in the Startup folder. The
poller's lock read "access denied" on the elevated PID as "dead", so both ran and
both uploaded every approved video. That left 129 extra copies on YouTube and
similar doubles on Facebook and Instagram.

- **Only the Startup folder window should run the poller.** `AI_ContentPoller`
  should stay disabled. It shares a session with ComfyUI and Chatterbox, so it
  needs Ziad's Windows user logged in.
- **Every uploader must win `publish_claim.claim_upload` before posting**, then
  re-read the row, and release only after saving the platform's id. Checking
  whether a video was posted isn't enough, because two processes can both check
  first. Tested with 8 processes racing on a real row: one winner every round.
- **A claim left by a crash is never taken over.** After 2 hours the row is halted
  with `<platform>_last_error`, because the upload may have gone through.
- **`Get-CimInstance` hides elevated command lines from a normal shell.** A blank
  `python.exe` in session 0 can be a poller.

## Known Issues (as of 2026-07-27)

- **Longform (15-25 min) not implemented** — routes to midform as fallback
- **Content poller may crash** after repeated pipeline failures — restart with `START_ALL.bat`
- **Older content has no thumbnails** — thumbnail code was added 2026-03-31

### Open voice-routing gaps (found in the 2026-07-27 audit, NOT yet fixed)

These are real and confirmed by reading the code. None affect NA/AA shorts,
midform, or (post-fix) stories.

- **`story` format ignores `*_TTS_PROVIDER` for wisdom/gibran.**
  `generate_story_video.py` routes na/aa to Chatterbox but sends everything
  else straight to ElevenLabs. Wisdom/Gibran stories therefore render on EL
  even though the env says chatterbox. Right voice identity, wrong provider —
  and for Gibran, the EL original and the CB clone are not the same voice.
- **`story_vertical` uses the Burton clip for both wisdom AND gibran**, with
  `exaggeration 0.5` and none of the atempo/reverb/tail post-chain the other
  wisdom formats get. So wisdom verticals sound unlike wisdom shorts, and
  Gibran verticals are outright cross-channel (see channel separation above).
- **`PERSONA_TO_VOICE_ID` is checked before `CHANNEL_DEFAULT_VOICE_ID`** on
  the ElevenLabs path, and wisdom's philosopher field is free-form
  server-side. A wisdom row typed `The Old-Timer` would pull NA's EL voice.
  Currently masked only because wisdom runs on Chatterbox.
- **`PERSONA_TO_VOICE_SETTINGS` (orchestrator.py) is dead code** — defined,
  never read. Per-persona exaggeration is not actually applied.
- **The EL rollback path is not intact for NA/AA.** `PERSONA_TO_VOICE_ID` has
  no `Ziad` entry and `CHANNEL_DEFAULT_VOICE_ID` has no `na`/`aa` entry, so
  flipping `NA_TTS_PROVIDER=elevenlabs` raises ValueError on a Ziad row.
  Fails loud, not silent — but the documented rollback would not work.
- **5 Gibran story rows carry `philosopher='Marcus Aurelius'`** (pre-dating
  the `channel-philosopher.ts` canonicalizer). Voice was unaffected; they got
  mood-derived writing/art styles instead of the Gibran style card.
