# ai-hub

A self-contained launcher for AI tools focused on image generation, LoRA training, and
checkpoint/LoRA derivation. Installs, manages, and runs a curated set of applications from a
single interface — no manual environment setup required.

**Windows-first.** The project moved from Linux to Windows 11; Windows is the supported,
tested target. `run.sh` remains as a best-effort, untested fallback for Linux.

---

## What it manages

### External apps (installed/updated via the hub, own venv each)

| App | What it does |
|---|---|
| **ComfyUI** | Node-based image generation workflow engine |
| **SD WebUI Forge Neo** | Stable Diffusion WebUI with Forge optimizations |
| **AI Toolkit** | LoRA and fine-tuning trainer for image generation models |
| **Anima Standalone Trainer** | LoRA trainer for the Anima (Cosmos 2B) architecture |

### Built-in utilities (own module under `apps/`, served directly by the hub)

| Tool | What it does |
|---|---|
| **Model Vault** | Local model library manager with Civitai tag sync and subfolder organization |
| **Painter** | Browser-based AI image editor: inpaint, outpaint, upscale, regional conditioning, ADetailer, LoRA inline |
| **Forge Fusion** | Checkpoint derivation and block-wise LoRA fusion/purification, across SDXL, Anima, and Z-Image |
| **Ideogram 4** | Text-heavy image generation via JSON + bounding boxes (sharp typography, controlled layout) |
| **Face Swap** | Face replacement with real pose (LivePortrait + ReActor/HyperSwap) |

Retired modules (kept out of the active tree, history in `BITACORA.md`): FaceFusion,
dataset-refiner, TagGUI, LoRA Merger, Forge Lab — the last two were superseded by Forge Fusion.

---

## Architecture

- **Backend**: FastAPI — app launcher and every built-in utility served from a single hub
  process (`hub-webui/app.py`)
- **Frontend**: Vanilla JS + HTML5 Canvas served by FastAPI; opens in the default browser
- **Real-time**: WebSocket progress streaming for ComfyUI jobs; SSE for setup and log tailing
- **Launcher**: `run.bat` (root) bootstraps everything from scratch on Windows — downloads
  `uv`, Python, Node.js, and ffmpeg portably if not present on the system
- **Isolation**: each managed app runs in its own virtual environment (`uv`), managed
  independently; no reliance on system-level Python
- **i18n**: ES/EN locale system (`locale.js`) — all UI strings switch at runtime without reload

---

## Requirements

- Windows 11 (tested target) — NVIDIA GPU with drivers installed (`nvidia-smi` must work)
- Linux is best-effort only via `run.sh`, not actively tested

Everything else (Python, Node.js, uv, ffmpeg) is provisioned automatically.

---

## Installation

```powershell
git clone https://github.com/Vajraastra/ai-hub.git
cd ai-hub
.\run.bat
```

That's it. `run.bat` will:
1. Verify your NVIDIA GPU
2. Ensure `uv` is available (installs it if missing)
3. Ensure a portable Node.js
4. Ensure a portable ffmpeg
5. Create/repair the hub's venv and install dependencies
6. Launch the WebUI

---

## Documentation

- `TASKS.md` — live task list and open work
- `BITACORA.md` — production log: what was implemented, root causes, fixes
- `HANDOFF.md` — immediate state for picking work back up

---

## License

Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0)
Free for personal and non-commercial use with attribution.
See [LICENSE](LICENSE) for details.
