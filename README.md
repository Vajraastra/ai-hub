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
| **Model Vault** | Offline model inventory, architecture filters, optional Civitai metadata, and protected file moves |
| **Painter** | Browser-based AI image editor: inpaint, outpaint, upscale, regional conditioning, ADetailer, LoRA inline |
| **Forge Fusion** | Checkpoint derivation and block-wise LoRA fusion/purification, across SDXL, Anima, and Z-Image |
| **Ideogram 4** | Text-heavy image generation via JSON + bounding boxes (sharp typography, controlled layout) |
| **Face Swap** | Face replacement with real pose (LivePortrait + ReActor/HyperSwap) |
| **MiniMax H3** | Local video generation with native audio: single clips or chained segments |
| **Anima Studio (PoC)** | Game art workshop: project styles, reusable prompt pieces and facial expression edits |

Retired modules (kept out of the active tree, history in `git log`): FaceFusion, dataset-refiner,
TagGUI, LoRA Merger, Forge Lab — the last two were superseded by Forge Fusion.

### Using Model Vault

Set your models directory in Settings, then open Model Vault.

- **Update library** discovers local files, then obtains Civitai metadata, JSON sidecars and
  preview images for pending models. Models remain visible when Civitai is unavailable or
  does not recognize them. Missing files retain their personal notes and tags.
- **Architecture** searches across physical folders. Search also accepts filenames and full
  Windows paths; **Clear filters** restores the whole library.
- **Civitai data** retries missing information and images. **Advanced** can refresh data for
  every registered model while preserving personal notes, tags, existing previews and custom
  JSON fields. Interrupted or invalid downloads remain pending for retry.
- **Thumbnails** filters cards with or without images. Separate states identify pending work,
  models not found on Civitai and confirmed absence of an image; these are not successful downloads.
  Cards with images appear first by default; **Most recent** remains available as a sort order.
- Drag models between folders of the same type, then choose **Apply moves** or **Discard moves**.
  Existing destination files are rejected, and failed moves remain pending for retry.
- Identical files share one entry; its details list all known locations. Moves affect the
  displayed primary path. An existing library keeps its cached hashes during the first scan
  after upgrading; subsequent scans detect changes by file size, timestamp, and identity.

---

### Using Anima Studio

Open **Applications → Taller Anima**. The workshop starts ComfyUI in the background and
waits for active jobs before closing it after the last workshop tab exits. Manually started
or shared backends remain available.

Models and LoRAs are selected through searchable thumbnail galleries. Compatibility comes
from model headers, including Anima files stored in `checkpoints`; previews and display names
come from the local library. See [the workshop guide](apps/anima_studio/README.md) for styles,
reusable prompt pieces, facial expressions and offline testing.

## Architecture

- **Backend**: FastAPI — app launcher and every built-in utility served from a single hub
  process (`hub-webui/app.py`)
- **Frontend**: Vanilla JS + HTML5 Canvas served by FastAPI; opens in the default browser
- **Real-time**: WebSocket progress streaming for ComfyUI jobs; SSE for setup and log tailing
- **Launcher**: `run.bat` (root) bootstraps everything from scratch on Windows — downloads
  `uv`, Python, Node.js, and ffmpeg portably if not present on the system
- **Isolation**: each managed app runs in its own virtual environment (`uv`), managed
  independently; no reliance on system-level Python
- **Shared caches**: downloads and runtime caches for `uv`, pip, Hugging Face, PyTorch,
  and Ultralytics live under `.cache/` instead of being duplicated in each app or `AppData`
- **i18n**: ES/EN locale system (`locale.js`) — all UI strings switch at runtime without reload

---

## Requirements

- Windows 11 (tested target) — NVIDIA GPU with drivers installed (`nvidia-smi` must work)
- Linux is best-effort only via `run.sh`, not actively tested

Everything else (Python, Node.js, uv, ffmpeg) is provisioned automatically.
No prior `uv` or Python installation is required: `run.bat` keeps both inside the project.

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

Each file answers one question, and nothing else:

- `TASKS.md` — what is still open. Finished work is deleted from it, not archived.
- `BITACORA.md` — why things are the way they are: root causes, measurements that cost GPU time,
  and approaches that were tried and rejected. Organized by module, not by date — `git log` keeps
  the chronology.
- `HANDOFF.md` — where the last session left off. Overwritten each time, not versioned.

---

## License

Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0)
Free for personal and non-commercial use with attribution.
See [LICENSE](LICENSE) for details.
