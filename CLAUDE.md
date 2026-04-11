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

## Voice Generation (Primary: ElevenLabs)

- **Provider:** ElevenLabs API (`eleven_multilingual_v2`)
- **Plan:** Scale (100K credits/month, $22/mo)
- **Wisdom voice ID:** `0ABJJI7ZYmWZBiUBMHUW` (James Burton)
- **Gibran voice ID:** `R68HwD2GzEdWfqYZP9FQ`
- **Chatterbox** is available at port 8004 as future fallback for shorts only. Config in `config.yaml` sets `reference_audio_path: C:\AI\system\voice\recordings`. Reference clips must be under 30 seconds.
- **DO NOT** use Chatterbox unless explicitly asked — it produced garbage output in testing.

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

| | Gibran Channel | Wisdom Channel |
|---|---|---|
| **Directory** | `C:\AI\gibran\` | `C:\AI\wisdom\` |
| **Output** | `C:\AI\gibran\output\shorts\` | `C:\AI\wisdom\output\shorts\` |
| **LoRA** | `gibran_style_v1.safetensors` | `stoic_classical_v1.safetensors` (+ others) |
| **Voice** | `R68HwD2GzEdWfqYZP9FQ` | `0ABJJI7ZYmWZBiUBMHUW` (James Burton) |
| **Ollama** | `gibran` | `marcus_aurelius`, `seneca`, etc. |
| **Texts** | `C:\AI\gibran\scripts\raw\` | `C:\AI\wisdom\authors\` |

When generating content, ALWAYS verify the output path matches the channel.

## Projects Using This Infrastructure

- `C:\AI\gibran\` — Gibran partnership channel (with Elias)
- `C:\AI\wisdom\` — Wisdom solo channel (Ziad only)
- `C:\AI\simpler.os\` — Simpler RE OS help video system (Digital Joe tutorials)

## Known Issues (as of 2026-03-31)

- **Longform (15-25 min) not implemented** — routes to midform as fallback
- **Content poller may crash** after repeated pipeline failures — restart with `START_ALL.bat`
- **Older content has no thumbnails** — thumbnail code was added 2026-03-31
