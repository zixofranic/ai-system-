# AI System — Shared Infrastructure

This directory contains shared GPU tools and services used by ALL channels.

## What's Here

| Tool | Path | Port | Purpose |
|---|---|---|---|
| ComfyUI | `C:\AI\system\ComfyUI\` | 8188 | Image generation (SDXL + LoRAs) |
| Chatterbox TTS | `C:\AI\system\Chatterbox-TTS-Server\` | 8004 | Voice cloning / TTS |
| kohya_ss | `C:\AI\system\kohya_ss\` | — | LoRA training |
| Ollama | System-installed | 11434 | LLM (19 philosopher models) |
| n8n | System-installed | 5678 | Automation workflows |

## LoRAs (in ComfyUI/models/loras/)

| LoRA | Trigger | For |
|---|---|---|
| gibran_style_v1 | `gibran_style` | Gibran channel |
| stoic_classical_v1 | `stoic_classical` | Wisdom channel (Marcus, Seneca, Epictetus) |
| More coming... | — | One per day |

## Voice Recordings

`C:\AI\system\voice\recordings\` — shared voice reference files
`C:\AI\system\voice\cloned\` — generated voice clones

## Start Everything

```
C:\AI\system\START_ALL.bat
```

## Conda Environments

- `chatterbox` — Python 3.11, PyTorch 2.11+cu128
- `comfyui` — Python 3.11, PyTorch 2.11+cu128
- `lora_train` — Python 3.11, PyTorch 2.11+cu128

## ComfyUI Custom Nodes (Required)

| Node | Purpose | For |
|---|---|---|
| ComfyUI-LatentSyncWrapper | LatentSync 1.6 lip sync | Simpler OS help videos |
| ComfyUI-Manager | Node management | All projects |

## Projects Using This Infrastructure

- `C:\AI\gibran\` — Gibran partnership channel
- `C:\AI\wisdom\` — Wisdom solo channel
- `C:\AI\simpler.os\` — Simpler RE OS help video system (Digital Joe tutorials)
