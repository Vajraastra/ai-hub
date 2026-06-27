# Matriz de dependencias — apps externas AI Hub

_Generado por `deps-audit/audit.py`. DECLARADAS = lo pedido en requirements; RESUELTAS = pip freeze real del venv (incluye transitivas). El objetivo es cuantificar el duplicado (torch/CUDA) de cara a un pool compartido._

### Vista: DECLARED

Apps con datos (7): ai-toolkit, anima-standalone-trainer, comfyui, dataset-refiner, facefusion, sd-webui-forge-neo, taggui
Total deps distintas: 110 · cruzadas (≥2 apps): **35** · únicas: 75 · cruzadas con conflicto de versión: **32**

#### Cruzadas (candidatas a pool compartido)
| Dependencia | #apps | Versiones por app | ¿Conflicto? |
|---|---|---|---|
| `opencv-python` | 5 | ai-toolkit=*, anima-standalone-trainer===4.10.0.84, dataset-refiner=*, facefusion===4.13.0.92, sd-webui-forge-neo===4.10.0.84 | ⚠️ SÍ |
| `pillow` | 5 | anima-standalone-trainer=*, comfyui=*, dataset-refiner=*, sd-webui-forge-neo===12.2.0, taggui===11.3.0 | ⚠️ SÍ |
| `transformers` | 5 | ai-toolkit===5.5.3, anima-standalone-trainer===4.54.1, comfyui=>=4.50.3, sd-webui-forge-neo===4.56.2, taggui===4.48.3 | ⚠️ SÍ |
| `accelerate` | 4 | ai-toolkit=*, anima-standalone-trainer===1.6.0, sd-webui-forge-neo===1.13.0, taggui===1.10.1 | ⚠️ SÍ |
| `einops` | 4 | ai-toolkit=*, anima-standalone-trainer===0.7.0, comfyui=*, sd-webui-forge-neo===0.8.2 | ⚠️ SÍ |
| `huggingface-hub` | 4 | ai-toolkit===1.10.1, anima-standalone-trainer===0.34.3, sd-webui-forge-neo===0.36.2, taggui===0.35.3 | ⚠️ SÍ |
| `numpy` | 4 | anima-standalone-trainer=*, comfyui=>=1.25.0, facefusion===2.2.1, sd-webui-forge-neo===2.3.5 | ⚠️ SÍ |
| `safetensors` | 4 | ai-toolkit=*, anima-standalone-trainer===0.4.5, comfyui=>=0.4.2, sd-webui-forge-neo===0.7.0 | ⚠️ SÍ |
| `torch` | 4 | anima-standalone-trainer===2.7.0, comfyui=*, sd-webui-forge-neo=*, taggui=2.8.0%2Bcu128 | ⚠️ SÍ |
| `av` | 3 | ai-toolkit===16.0.1, comfyui=>=16.0.0, sd-webui-forge-neo===17.0.0 | ⚠️ SÍ |
| `bitsandbytes` | 3 | ai-toolkit=*, anima-standalone-trainer=*, taggui===0.48.1 | ⚠️ SÍ |
| `diffusers` | 3 | ai-toolkit=git, anima-standalone-trainer===0.32.1, sd-webui-forge-neo===0.37.1 | ⚠️ SÍ |
| `gradio` | 3 | ai-toolkit=*, dataset-refiner=>=4.0.0, facefusion===5.44.1 | ⚠️ SÍ |
| `kornia` | 3 | ai-toolkit=*, comfyui=>=0.7.1, sd-webui-forge-neo===0.6.12 | ⚠️ SÍ |
| `pydantic` | 3 | ai-toolkit=*, comfyui=~=2.0, sd-webui-forge-neo===2.10.6 | ⚠️ SÍ |
| `pyyaml` | 3 | ai-toolkit=*, comfyui=*, sd-webui-forge-neo===6.0.3 | ⚠️ SÍ |
| `scipy` | 3 | ai-toolkit===1.12.0, comfyui=*, facefusion===1.17.0 | ⚠️ SÍ |
| `sentencepiece` | 3 | ai-toolkit=*, anima-standalone-trainer===0.2.1, comfyui=* | ⚠️ SÍ |
| `setuptools` | 3 | ai-toolkit===69.5.1, anima-standalone-trainer===80.0.0, sd-webui-forge-neo===69.5.1 | ⚠️ SÍ |
| `tqdm` | 3 | comfyui=*, facefusion===4.67.3, sd-webui-forge-neo===4.67.3 | ⚠️ SÍ |
| `comfy-kitchen` | 2 | comfyui===0.2.10, sd-webui-forge-neo===0.2.8 | ⚠️ SÍ |
| `imagesize` | 2 | anima-standalone-trainer===1.4.1, taggui===1.4.1 | ok |
| `omegaconf` | 2 | ai-toolkit=*, sd-webui-forge-neo===2.2.3 | ⚠️ SÍ |
| `onnxruntime` | 2 | facefusion===1.24.1, taggui===1.23.1 | ⚠️ SÍ |
| `peft` | 2 | ai-toolkit===0.18.1, sd-webui-forge-neo===0.17.1 | ⚠️ SÍ |
| `prodigyopt` | 2 | ai-toolkit=*, anima-standalone-trainer===1.1.2 | ⚠️ SÍ |
| `psutil` | 2 | comfyui=*, sd-webui-forge-neo===6.1.1 | ⚠️ SÍ |
| `requests` | 2 | comfyui=*, dataset-refiner=* | ok |
| `rich` | 2 | anima-standalone-trainer===14.1.0, sd-webui-forge-neo===14.3.3 | ⚠️ SÍ |
| `spandrel` | 2 | comfyui=*, sd-webui-forge-neo===0.4.2 | ⚠️ SÍ |
| `tensorboard` | 2 | ai-toolkit=*, anima-standalone-trainer=* | ok |
| `timm` | 2 | ai-toolkit===1.0.22, taggui===1.0.20 | ⚠️ SÍ |
| `toml` | 2 | ai-toolkit=*, anima-standalone-trainer===0.10.2 | ⚠️ SÍ |
| `torchsde` | 2 | comfyui=*, sd-webui-forge-neo===0.2.6 | ⚠️ SÍ |
| `torchvision` | 2 | anima-standalone-trainer===0.22.0, comfyui=* | ⚠️ SÍ |

#### Únicas (75)
`aiohttp`(comfyui), `albucore`(ai-toolkit), `albumentations`(ai-toolkit), `alembic`(comfyui), `audioop-lts`(sd-webui-forge-neo), `blake3`(comfyui), `comfy-aimdo`(comfyui), `comfyui-embedded-docs`(comfyui), `comfyui-frontend-package`(comfyui), `comfyui-workflow-templates`(comfyui), `controlnet-aux`(ai-toolkit), `cuda-direct-pkg`(anima-standalone-trainer), `deep-translator`(dataset-refiner), `diskcache`(sd-webui-forge-neo), `exifread`(taggui), `facexlib`(sd-webui-forge-neo), `fastapi`(sd-webui-forge-neo), `filelock`(comfyui), `flash-attn`(taggui), `flask`(anima-standalone-trainer), `flatten-json`(ai-toolkit), `ftfy`(anima-standalone-trainer), `gitpython`(sd-webui-forge-neo), `glfw`(comfyui), `gradio-rangeslider`(facefusion), `hf-transfer`(ai-toolkit), `httpx`(sd-webui-forge-neo), `imagehash`(dataset-refiner), `inflection`(sd-webui-forge-neo), `invisible-watermark`(ai-toolkit), `joblib`(sd-webui-forge-neo), `k-diffusion`(ai-toolkit), `lark`(sd-webui-forge-neo), `librosa`(ai-toolkit), `lion-pytorch`(anima-standalone-trainer), `lpips`(ai-toolkit), `lycoris-lora`(ai-toolkit), `matplotlib`(ai-toolkit), `mutagen`(ai-toolkit), `onnx`(facefusion), `open-clip-torch`(ai-toolkit), `optimum-quanto`(ai-toolkit), `oyaml`(ai-toolkit), `pandas`(dataset-refiner), `piexif`(sd-webui-forge-neo), `pillow-heif`(sd-webui-forge-neo), `pillow-jxl-plugin`(sd-webui-forge-neo), `plotly`(dataset-refiner), `prodigy-plus-schedule-free`(anima-standalone-trainer), `protobuf`(sd-webui-forge-neo), `pydantic-core`(sd-webui-forge-neo), `pydantic-settings`(comfyui), `pyopengl`(comfyui), `pyparsing`(taggui), `pyside6`(taggui), `python-dotenv`(ai-toolkit), `python-slugify`(ai-toolkit), `pytorch-fid`(ai-toolkit), `pytorch-optimizer`(anima-standalone-trainer), `pytorch-wavelets`(ai-toolkit), `schedulefree`(anima-standalone-trainer), `scikit-image`(sd-webui-forge-neo), `simpleeval`(comfyui), `spandrel-extra-arches`(sd-webui-forge-neo), `sqlalchemy`(comfyui), `tokenizers`(comfyui), `tomesd`(sd-webui-forge-neo), `torchao`(ai-toolkit), `torchaudio`(comfyui), `torchcodec`(ai-toolkit), `torchdiffeq`(sd-webui-forge-neo), `voluptuous`(anima-standalone-trainer), `wandb`(anima-standalone-trainer), `wd-parallel-pkg`(anima-standalone-trainer), `yarl`(comfyui)

### Vista: RESOLVED

Apps con datos (5): ai-toolkit, comfyui, dataset-refiner, facefusion, taggui
Total deps distintas: 214 · cruzadas (≥2 apps): **88** · únicas: 126 · cruzadas con conflicto de versión: **23**

#### Cruzadas (candidatas a pool compartido)
| Dependencia | #apps | Versiones por app | ¿Conflicto? |
|---|---|---|---|
| `certifi` | 5 | ai-toolkit=2026.6.17, comfyui=2026.6.17, dataset-refiner=2026.6.17, facefusion=2026.6.17, taggui=2026.6.17 | ok |
| `charset-normalizer` | 5 | ai-toolkit=3.4.7, comfyui=3.4.7, dataset-refiner=3.4.7, facefusion=3.4.7, taggui=3.4.7 | ok |
| `colorama` | 5 | ai-toolkit=0.4.6, comfyui=0.4.6, dataset-refiner=0.4.6, facefusion=0.4.6, taggui=0.4.6 | ok |
| `filelock` | 5 | ai-toolkit=3.29.0, comfyui=3.29.0, dataset-refiner=3.29.4, facefusion=3.29.4, taggui=3.29.0 | ⚠️ SÍ |
| `fsspec` | 5 | ai-toolkit=2026.4.0, comfyui=2026.4.0, dataset-refiner=2026.6.0, facefusion=2026.6.0, taggui=2026.4.0 | ⚠️ SÍ |
| `huggingface-hub` | 5 | ai-toolkit=1.10.1, comfyui=1.21.0, dataset-refiner=1.21.0, facefusion=0.36.2, taggui=0.35.3 | ⚠️ SÍ |
| `idna` | 5 | ai-toolkit=3.18, comfyui=3.18, dataset-refiner=3.18, facefusion=3.18, taggui=3.18 | ok |
| `jinja2` | 5 | ai-toolkit=3.1.6, comfyui=3.1.6, dataset-refiner=3.1.6, facefusion=3.1.6, taggui=3.1.6 | ok |
| `markupsafe` | 5 | ai-toolkit=3.0.3, comfyui=3.0.3, dataset-refiner=3.0.3, facefusion=3.0.3, taggui=3.0.3 | ok |
| `numpy` | 5 | ai-toolkit=2.4.4, comfyui=2.4.4, dataset-refiner=2.5.0, facefusion=2.2.1, taggui=2.4.4 | ⚠️ SÍ |
| `packaging` | 5 | ai-toolkit=26.2, comfyui=26.2, dataset-refiner=26.2, facefusion=26.2, taggui=26.2 | ok |
| `pillow` | 5 | ai-toolkit=11.3.0, comfyui=12.2.0, dataset-refiner=12.2.0, facefusion=11.3.0, taggui=11.3.0 | ⚠️ SÍ |
| `pyyaml` | 5 | ai-toolkit=6.0.3, comfyui=6.0.3, dataset-refiner=6.0.3, facefusion=6.0.3, taggui=6.0.3 | ok |
| `requests` | 5 | ai-toolkit=2.34.2, comfyui=2.34.2, dataset-refiner=2.34.2, facefusion=2.34.2, taggui=2.34.2 | ok |
| `tqdm` | 5 | ai-toolkit=4.68.3, comfyui=4.68.3, dataset-refiner=4.68.3, facefusion=4.67.3, taggui=4.68.3 | ⚠️ SÍ |
| `typing-extensions` | 5 | ai-toolkit=4.15.0, comfyui=4.15.0, dataset-refiner=4.15.0, facefusion=4.15.0, taggui=4.15.0 | ok |
| `urllib3` | 5 | ai-toolkit=2.7.0, comfyui=2.7.0, dataset-refiner=2.7.0, facefusion=2.7.0, taggui=2.7.0 | ok |
| `annotated-doc` | 4 | ai-toolkit=0.0.4, comfyui=0.0.4, dataset-refiner=0.0.4, facefusion=0.0.4 | ok |
| `annotated-types` | 4 | ai-toolkit=0.7.0, comfyui=0.7.0, dataset-refiner=0.7.0, facefusion=0.7.0 | ok |
| `anyio` | 4 | ai-toolkit=4.14.1, comfyui=4.14.1, dataset-refiner=4.14.1, facefusion=4.14.1 | ok |
| `click` | 4 | ai-toolkit=8.4.2, comfyui=8.4.2, dataset-refiner=8.4.2, facefusion=8.4.2 | ok |
| `h11` | 4 | ai-toolkit=0.16.0, comfyui=0.16.0, dataset-refiner=0.16.0, facefusion=0.16.0 | ok |
| `httpcore` | 4 | ai-toolkit=1.0.9, comfyui=1.0.9, dataset-refiner=1.0.9, facefusion=1.0.9 | ok |
| `httpx` | 4 | ai-toolkit=0.28.1, comfyui=0.28.1, dataset-refiner=0.28.1, facefusion=0.28.1 | ok |
| `markdown-it-py` | 4 | ai-toolkit=4.2.0, comfyui=4.2.0, dataset-refiner=4.2.0, facefusion=4.2.0 | ok |
| `mdurl` | 4 | ai-toolkit=0.1.2, comfyui=0.1.2, dataset-refiner=0.1.2, facefusion=0.1.2 | ok |
| `pydantic` | 4 | ai-toolkit=2.12.3, comfyui=2.13.4, dataset-refiner=2.13.4, facefusion=2.11.10 | ⚠️ SÍ |
| `pydantic-core` | 4 | ai-toolkit=2.41.4, comfyui=2.46.4, dataset-refiner=2.46.4, facefusion=2.33.2 | ⚠️ SÍ |
| `pygments` | 4 | ai-toolkit=2.20.0, comfyui=2.20.0, dataset-refiner=2.20.0, facefusion=2.20.0 | ok |
| `rich` | 4 | ai-toolkit=15.0.0, comfyui=15.0.0, dataset-refiner=15.0.0, facefusion=15.0.0 | ok |
| `scipy` | 4 | ai-toolkit=1.18.0, comfyui=1.18.0, dataset-refiner=1.18.0, facefusion=1.17.0 | ⚠️ SÍ |
| `shellingham` | 4 | ai-toolkit=1.5.4, comfyui=1.5.4, dataset-refiner=1.5.4, facefusion=1.5.4 | ok |
| `typer` | 4 | ai-toolkit=0.26.8, comfyui=0.25.1, dataset-refiner=0.25.1, facefusion=0.26.8 | ⚠️ SÍ |
| `typing-inspection` | 4 | ai-toolkit=0.4.2, comfyui=0.4.2, dataset-refiner=0.4.2, facefusion=0.4.2 | ok |
| `brotli` | 3 | ai-toolkit=1.2.0, dataset-refiner=1.2.0, facefusion=1.2.0 | ok |
| `fastapi` | 3 | ai-toolkit=0.138.1, dataset-refiner=0.138.1, facefusion=0.138.1 | ok |
| `gradio` | 3 | ai-toolkit=5.50.0, dataset-refiner=6.19.0, facefusion=5.44.1 | ⚠️ SÍ |
| `gradio-client` | 3 | ai-toolkit=1.14.0, dataset-refiner=2.5.0, facefusion=1.12.1 | ⚠️ SÍ |
| `groovy` | 3 | ai-toolkit=0.1.2, dataset-refiner=0.1.2, facefusion=0.1.2 | ok |
| `hf-xet` | 3 | ai-toolkit=1.5.1, comfyui=1.5.1, dataset-refiner=1.5.1 | ok |
| `mpmath` | 3 | ai-toolkit=1.3.0, comfyui=1.3.0, taggui=1.3.0 | ok |
| `networkx` | 3 | ai-toolkit=3.6.1, comfyui=3.6.1, taggui=3.6.1 | ok |
| `orjson` | 3 | ai-toolkit=3.11.9, dataset-refiner=3.11.9, facefusion=3.11.9 | ok |
| `pandas` | 3 | ai-toolkit=2.3.3, dataset-refiner=3.0.3, facefusion=2.3.3 | ⚠️ SÍ |
| `protobuf` | 3 | ai-toolkit=7.35.1, facefusion=7.35.1, taggui=7.35.1 | ok |
| `psutil` | 3 | ai-toolkit=7.2.2, comfyui=7.2.2, taggui=7.2.2 | ok |
| `pydub` | 3 | ai-toolkit=0.25.1, dataset-refiner=0.25.1, facefusion=0.25.1 | ok |
| `python-dateutil` | 3 | ai-toolkit=2.9.0.post0, dataset-refiner=2.9.0.post0, facefusion=2.9.0.post0 | ok |
| `python-multipart` | 3 | ai-toolkit=0.0.32, dataset-refiner=0.0.32, facefusion=0.0.32 | ok |
| `pytz` | 3 | ai-toolkit=2026.2, dataset-refiner=2026.2, facefusion=2026.2 | ok |
| `regex` | 3 | ai-toolkit=2026.5.9, comfyui=2026.5.9, taggui=2026.5.9 | ok |
| `safehttpx` | 3 | ai-toolkit=0.1.7, dataset-refiner=0.1.7, facefusion=0.1.7 | ok |
| `safetensors` | 3 | ai-toolkit=0.8.0, comfyui=0.8.0, taggui=0.8.0 | ok |
| `semantic-version` | 3 | ai-toolkit=2.10.0, dataset-refiner=2.10.0, facefusion=2.10.0 | ok |
| `setuptools` | 3 | ai-toolkit=69.5.1, comfyui=70.2.0, taggui=70.2.0 | ⚠️ SÍ |
| `six` | 3 | ai-toolkit=1.17.0, dataset-refiner=1.17.0, facefusion=1.17.0 | ok |
| `starlette` | 3 | ai-toolkit=0.52.1, dataset-refiner=1.3.1, facefusion=0.52.1 | ⚠️ SÍ |
| `sympy` | 3 | ai-toolkit=1.14.0, comfyui=1.14.0, taggui=1.14.0 | ok |
| `tokenizers` | 3 | ai-toolkit=0.22.2, comfyui=0.22.2, taggui=0.21.4 | ⚠️ SÍ |
| `tomlkit` | 3 | ai-toolkit=0.13.3, dataset-refiner=0.14.0, facefusion=0.13.3 | ⚠️ SÍ |
| `torch` | 3 | ai-toolkit=2.10.0+cu130, comfyui=2.10.0+cu130, taggui=2.10.0+cu130 | ok |
| `torchaudio` | 3 | ai-toolkit=2.10.0+cu130, comfyui=2.10.0+cu130, taggui=2.10.0+cu130 | ok |
| `torchvision` | 3 | ai-toolkit=0.25.0+cu130, comfyui=0.25.0+cu130, taggui=0.25.0+cu130 | ok |
| `transformers` | 3 | ai-toolkit=5.5.3, comfyui=5.12.1, taggui=4.48.3 | ⚠️ SÍ |
| `tzdata` | 3 | ai-toolkit=2026.2, dataset-refiner=2026.2, facefusion=2026.2 | ok |
| `uvicorn` | 3 | ai-toolkit=0.49.0, dataset-refiner=0.49.0, facefusion=0.49.0 | ok |
| `accelerate` | 2 | ai-toolkit=1.14.0, taggui=1.10.1 | ⚠️ SÍ |
| `aiofiles` | 2 | ai-toolkit=24.1.0, facefusion=24.1.0 | ok |
| `attrs` | 2 | ai-toolkit=26.1.0, comfyui=26.1.0 | ok |
| `av` | 2 | ai-toolkit=16.0.1, comfyui=17.1.0 | ⚠️ SÍ |
| `bitsandbytes` | 2 | ai-toolkit=0.49.2, taggui=0.48.1 | ⚠️ SÍ |
| `einops` | 2 | ai-toolkit=0.8.2, comfyui=0.8.2 | ok |
| `ffmpy` | 2 | ai-toolkit=1.0.0, facefusion=1.0.0 | ok |
| `flatbuffers` | 2 | facefusion=25.12.19, taggui=25.12.19 | ok |
| `kornia` | 2 | ai-toolkit=0.8.3, comfyui=0.8.3 | ok |
| `kornia-rs` | 2 | ai-toolkit=0.1.14, comfyui=0.1.14 | ok |
| `narwhals` | 2 | ai-toolkit=2.22.1, dataset-refiner=2.22.1 | ok |
| `opencv-python` | 2 | ai-toolkit=4.13.0.92, facefusion=4.13.0.92 | ok |
| `opencv-python-headless` | 2 | ai-toolkit=4.13.0.92, dataset-refiner=4.13.0.92 | ok |
| `pyparsing` | 2 | ai-toolkit=3.3.2, taggui=3.2.5 | ⚠️ SÍ |
| `python-dotenv` | 2 | ai-toolkit=1.2.2, comfyui=1.2.2 | ok |
| `pywavelets` | 2 | ai-toolkit=1.9.0, dataset-refiner=1.9.0 | ok |
| `ruff` | 2 | ai-toolkit=0.15.20, facefusion=0.15.20 | ok |
| `sentencepiece` | 2 | ai-toolkit=0.2.1, comfyui=0.2.1 | ok |
| `timm` | 2 | ai-toolkit=1.0.22, taggui=1.0.20 | ⚠️ SÍ |
| `torchsde` | 2 | ai-toolkit=0.2.6, comfyui=0.2.6 | ok |
| `trampoline` | 2 | ai-toolkit=0.1.2, comfyui=0.1.2 | ok |
| `websockets` | 2 | ai-toolkit=15.0.1, facefusion=15.0.1 | ok |

#### Únicas (126)
`absl-py`(ai-toolkit), `aiohappyeyeballs`(comfyui), `aiohttp`(comfyui), `aiosignal`(comfyui), `albucore`(ai-toolkit), `albumentations`(ai-toolkit), `alembic`(comfyui), `antlr4-python3-runtime`(ai-toolkit), `audioop-lts`(ai-toolkit), `audioread`(ai-toolkit), `beautifulsoup4`(dataset-refiner), `blake3`(comfyui), `cffi`(ai-toolkit), `clean-fid`(ai-toolkit), `clip-anytorch`(ai-toolkit), `coloredlogs`(taggui), `comfy-aimdo`(comfyui), `comfy-kitchen`(comfyui), `comfyui-embedded-docs`(comfyui), `comfyui-frontend-package`(comfyui), `comfyui-workflow-templates`(comfyui), `comfyui-workflow-templates-core`(comfyui), `comfyui-workflow-templates-media-api`(comfyui), `comfyui-workflow-templates-media-image`(comfyui), `comfyui-workflow-templates-media-other`(comfyui), `comfyui-workflow-templates-media-video`(comfyui), `contourpy`(ai-toolkit), `controlnet-aux`(ai-toolkit), `cycler`(ai-toolkit), `dctorch`(ai-toolkit), `decorator`(ai-toolkit), `deep-translator`(dataset-refiner), `diffusers`(ai-toolkit), `eval-type-backport`(ai-toolkit), `exifread`(taggui), `flatten-json`(ai-toolkit), `fonttools`(ai-toolkit), `frozenlist`(comfyui), `ftfy`(ai-toolkit), `gitdb`(ai-toolkit), `gitpython`(ai-toolkit), `glfw`(comfyui), `gradio-rangeslider`(facefusion), `greenlet`(comfyui), `grpcio`(ai-toolkit), `hf-gradio`(dataset-refiner), `hf-transfer`(ai-toolkit), `humanfriendly`(taggui), `imagehash`(dataset-refiner), `imageio`(ai-toolkit), `imagesize`(taggui), `importlib-metadata`(ai-toolkit), `invisible-watermark`(ai-toolkit), `joblib`(ai-toolkit), `jsonmerge`(ai-toolkit), `jsonschema`(ai-toolkit), `jsonschema-specifications`(ai-toolkit), `k-diffusion`(ai-toolkit), `kiwisolver`(ai-toolkit), `lazy-loader`(ai-toolkit), `librosa`(ai-toolkit), `llvmlite`(ai-toolkit), `lpips`(ai-toolkit), `lycoris-lora`(ai-toolkit), `mako`(comfyui), `markdown`(ai-toolkit), `matplotlib`(ai-toolkit), `ml-dtypes`(facefusion), `msgpack`(ai-toolkit), `multidict`(comfyui), `mutagen`(ai-toolkit), `ninja`(ai-toolkit), `numba`(ai-toolkit), `omegaconf`(ai-toolkit), `onnx`(facefusion), `onnxruntime`(taggui), `onnxruntime-gpu`(facefusion), `open-clip-torch`(ai-toolkit), `optimum-quanto`(ai-toolkit), `oyaml`(ai-toolkit), `peft`(ai-toolkit), `platformdirs`(ai-toolkit), `plotly`(dataset-refiner), `pooch`(ai-toolkit), `prodigyopt`(ai-toolkit), `propcache`(comfyui), `pycparser`(ai-toolkit), `pydantic-settings`(comfyui), `pyopengl`(comfyui), `pyreadline3`(taggui), `pyside6`(taggui), `pyside6-addons`(taggui), `pyside6-essentials`(taggui), `python-slugify`(ai-toolkit), `pytorch-fid`(ai-toolkit), `pytorch-wavelets`(ai-toolkit), `referencing`(ai-toolkit), `rpds-py`(ai-toolkit), `scikit-image`(ai-toolkit), `scikit-learn`(ai-toolkit), `sentry-sdk`(ai-toolkit), `shiboken6`(taggui), `simpleeval`(comfyui), `smmap`(ai-toolkit), `soundfile`(ai-toolkit), `soupsieve`(dataset-refiner), `soxr`(ai-toolkit), `spandrel`(comfyui), `sqlalchemy`(comfyui), `standard-aifc`(ai-toolkit), `standard-chunk`(ai-toolkit), `standard-sunau`(ai-toolkit), `tensorboard`(ai-toolkit), `tensorboard-data-server`(ai-toolkit), `text-unidecode`(ai-toolkit), `threadpoolctl`(ai-toolkit), `tifffile`(ai-toolkit), `toml`(ai-toolkit), `torchao`(ai-toolkit), `torchcodec`(ai-toolkit), `torchdiffeq`(ai-toolkit), `wandb`(ai-toolkit), `wcwidth`(ai-toolkit), `werkzeug`(ai-toolkit), `yarl`(comfyui), `zipp`(ai-toolkit)

