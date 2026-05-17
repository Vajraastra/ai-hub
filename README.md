# ai-hub

A self-contained launcher for AI tools focused on image generation and model training. Installs, manages, and runs a curated set of applications from a single interface — no manual environment setup required.

---

## What it manages

| App | What it does |
|---|---|
| **ComfyUI** | Node-based image generation workflow engine |
| **SD WebUI Forge Neo** | Stable Diffusion WebUI with Forge optimizations |
| **AI Toolkit** | LoRA and fine-tuning trainer for image generation models |
| **FaceFusion** | Face swap and restoration tool |
| **TagGUI** | Image tagger for dataset preparation |
| **LoRA Merger** | Merge and blend LoRA weights |
| **Model Vault** | Local model library manager with Civitai tag sync and subfolder organization |
| **Painter** | Browser-based AI image editor: inpainting, outpainting, upscaling, and regional conditioning via ComfyUI |

---

## Architecture

- **Backend**: FastAPI — app launcher, model vault, and AI editor all served from a single hub process
- **Frontend**: Vanilla JS + HTML5 Canvas served by FastAPI; opens in the default browser (pywebview optional native wrapper)
- **Real-time**: WebSocket progress streaming for ComfyUI jobs; SSE for setup and log tailing
- **Launcher**: `run.sh` bootstraps everything from scratch — downloads `uv`, Python 3.13, and Node.js portably if not present on the system
- **Isolation**: each managed app runs in its own virtual environment, managed independently
- **i18n**: ES/EN locale system (`locale.js`) — all UI strings switch at runtime without reload

---

## Requirements

- Linux (x86_64 or ARM64)
- NVIDIA GPU with drivers installed (`nvidia-smi` must work)
- `curl` or `wget`

Everything else (Python, Node.js, uv) is provisioned automatically.

---

## Installation

```bash
git clone https://github.com/Vajraastra/ai-hub.git
cd ai-hub
./run.sh
```

That's it. `run.sh` will:
1. Verify your NVIDIA GPU
2. Download `uv` (portable Python package manager)
3. Download Python 3.13 if needed
4. Verify or download Node.js
5. Prepare the base Python environment
6. Install WebUI dependencies and launch

---

## License

Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0)  
Free for personal and non-commercial use with attribution.  
See [LICENSE](LICENSE) for details.
