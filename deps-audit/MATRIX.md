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

Apps con datos (6): ai-toolkit, comfyui, dataset-refiner, facefusion, sd-webui-forge-neo, taggui
Total deps distintas: 252 · cruzadas (≥2 apps): **123** · únicas: 129 · cruzadas con conflicto de versión: **46**

#### Cruzadas (candidatas a pool compartido)
| Dependencia | #apps | Versiones por app | ¿Conflicto? |
|---|---|---|---|
| `certifi` | 6 | ai-toolkit=2026.6.17, comfyui=2026.6.17, dataset-refiner=2026.6.17, facefusion=2026.6.17, sd-webui-forge-neo=2026.6.17, taggui=2026.6.17 | ok |
| `charset-normalizer` | 6 | ai-toolkit=3.4.7, comfyui=3.4.7, dataset-refiner=3.4.7, facefusion=3.4.7, sd-webui-forge-neo=3.4.7, taggui=3.4.7 | ok |
| `colorama` | 6 | ai-toolkit=0.4.6, comfyui=0.4.6, dataset-refiner=0.4.6, facefusion=0.4.6, sd-webui-forge-neo=0.4.6, taggui=0.4.6 | ok |
| `filelock` | 6 | ai-toolkit=3.29.0, comfyui=3.29.0, dataset-refiner=3.29.4, facefusion=3.29.4, sd-webui-forge-neo=3.29.0, taggui=3.29.0 | ⚠️ SÍ |
| `fsspec` | 6 | ai-toolkit=2026.4.0, comfyui=2026.4.0, dataset-refiner=2026.6.0, facefusion=2026.6.0, sd-webui-forge-neo=2026.4.0, taggui=2026.4.0 | ⚠️ SÍ |
| `huggingface-hub` | 6 | ai-toolkit=1.10.1, comfyui=1.21.0, dataset-refiner=1.21.0, facefusion=0.36.2, sd-webui-forge-neo=0.36.2, taggui=0.35.3 | ⚠️ SÍ |
| `idna` | 6 | ai-toolkit=3.18, comfyui=3.18, dataset-refiner=3.18, facefusion=3.18, sd-webui-forge-neo=3.18, taggui=3.18 | ok |
| `jinja2` | 6 | ai-toolkit=3.1.6, comfyui=3.1.6, dataset-refiner=3.1.6, facefusion=3.1.6, sd-webui-forge-neo=3.1.6, taggui=3.1.6 | ok |
| `markupsafe` | 6 | ai-toolkit=3.0.3, comfyui=3.0.3, dataset-refiner=3.0.3, facefusion=3.0.3, sd-webui-forge-neo=2.1.5, taggui=3.0.3 | ⚠️ SÍ |
| `numpy` | 6 | ai-toolkit=2.4.4, comfyui=2.4.4, dataset-refiner=2.5.0, facefusion=2.2.1, sd-webui-forge-neo=2.3.5, taggui=2.4.4 | ⚠️ SÍ |
| `packaging` | 6 | ai-toolkit=26.2, comfyui=26.2, dataset-refiner=26.2, facefusion=26.2, sd-webui-forge-neo=26.0, taggui=26.2 | ⚠️ SÍ |
| `pillow` | 6 | ai-toolkit=11.3.0, comfyui=12.2.0, dataset-refiner=12.2.0, facefusion=11.3.0, sd-webui-forge-neo=12.2.0, taggui=11.3.0 | ⚠️ SÍ |
| `pyyaml` | 6 | ai-toolkit=6.0.3, comfyui=6.0.3, dataset-refiner=6.0.3, facefusion=6.0.3, sd-webui-forge-neo=6.0.3, taggui=6.0.3 | ok |
| `requests` | 6 | ai-toolkit=2.34.2, comfyui=2.34.2, dataset-refiner=2.34.2, facefusion=2.34.2, sd-webui-forge-neo=2.34.2, taggui=2.34.2 | ok |
| `tqdm` | 6 | ai-toolkit=4.68.3, comfyui=4.68.3, dataset-refiner=4.68.3, facefusion=4.67.3, sd-webui-forge-neo=4.67.3, taggui=4.68.3 | ⚠️ SÍ |
| `typing-extensions` | 6 | ai-toolkit=4.15.0, comfyui=4.15.0, dataset-refiner=4.15.0, facefusion=4.15.0, sd-webui-forge-neo=4.15.0, taggui=4.15.0 | ok |
| `urllib3` | 6 | ai-toolkit=2.7.0, comfyui=2.7.0, dataset-refiner=2.7.0, facefusion=2.7.0, sd-webui-forge-neo=2.7.0, taggui=2.7.0 | ok |
| `annotated-doc` | 5 | ai-toolkit=0.0.4, comfyui=0.0.4, dataset-refiner=0.0.4, facefusion=0.0.4, sd-webui-forge-neo=0.0.4 | ok |
| `annotated-types` | 5 | ai-toolkit=0.7.0, comfyui=0.7.0, dataset-refiner=0.7.0, facefusion=0.7.0, sd-webui-forge-neo=0.7.0 | ok |
| `anyio` | 5 | ai-toolkit=4.14.1, comfyui=4.14.1, dataset-refiner=4.14.1, facefusion=4.14.1, sd-webui-forge-neo=4.14.1 | ok |
| `click` | 5 | ai-toolkit=8.4.2, comfyui=8.4.2, dataset-refiner=8.4.2, facefusion=8.4.2, sd-webui-forge-neo=8.4.2 | ok |
| `h11` | 5 | ai-toolkit=0.16.0, comfyui=0.16.0, dataset-refiner=0.16.0, facefusion=0.16.0, sd-webui-forge-neo=0.14.0 | ⚠️ SÍ |
| `httpcore` | 5 | ai-toolkit=1.0.9, comfyui=1.0.9, dataset-refiner=1.0.9, facefusion=1.0.9, sd-webui-forge-neo=0.17.3 | ⚠️ SÍ |
| `httpx` | 5 | ai-toolkit=0.28.1, comfyui=0.28.1, dataset-refiner=0.28.1, facefusion=0.28.1, sd-webui-forge-neo=0.24.1 | ⚠️ SÍ |
| `markdown-it-py` | 5 | ai-toolkit=4.2.0, comfyui=4.2.0, dataset-refiner=4.2.0, facefusion=4.2.0, sd-webui-forge-neo=4.2.0 | ok |
| `mdurl` | 5 | ai-toolkit=0.1.2, comfyui=0.1.2, dataset-refiner=0.1.2, facefusion=0.1.2, sd-webui-forge-neo=0.1.2 | ok |
| `pydantic` | 5 | ai-toolkit=2.12.3, comfyui=2.13.4, dataset-refiner=2.13.4, facefusion=2.11.10, sd-webui-forge-neo=2.10.6 | ⚠️ SÍ |
| `pydantic-core` | 5 | ai-toolkit=2.41.4, comfyui=2.46.4, dataset-refiner=2.46.4, facefusion=2.33.2, sd-webui-forge-neo=2.27.2 | ⚠️ SÍ |
| `pygments` | 5 | ai-toolkit=2.20.0, comfyui=2.20.0, dataset-refiner=2.20.0, facefusion=2.20.0, sd-webui-forge-neo=2.20.0 | ok |
| `rich` | 5 | ai-toolkit=15.0.0, comfyui=15.0.0, dataset-refiner=15.0.0, facefusion=15.0.0, sd-webui-forge-neo=14.3.3 | ⚠️ SÍ |
| `scipy` | 5 | ai-toolkit=1.18.0, comfyui=1.18.0, dataset-refiner=1.18.0, facefusion=1.17.0, sd-webui-forge-neo=1.18.0 | ⚠️ SÍ |
| `shellingham` | 5 | ai-toolkit=1.5.4, comfyui=1.5.4, dataset-refiner=1.5.4, facefusion=1.5.4, sd-webui-forge-neo=1.5.4 | ok |
| `typer` | 5 | ai-toolkit=0.26.8, comfyui=0.25.1, dataset-refiner=0.25.1, facefusion=0.26.8, sd-webui-forge-neo=0.25.1 | ⚠️ SÍ |
| `typing-inspection` | 5 | ai-toolkit=0.4.2, comfyui=0.4.2, dataset-refiner=0.4.2, facefusion=0.4.2, sd-webui-forge-neo=0.4.2 | ok |
| `fastapi` | 4 | ai-toolkit=0.138.1, dataset-refiner=0.138.1, facefusion=0.138.1, sd-webui-forge-neo=0.127.1 | ⚠️ SÍ |
| `gradio` | 4 | ai-toolkit=5.50.0, dataset-refiner=6.19.0, facefusion=5.44.1, sd-webui-forge-neo=4.40.0 | ⚠️ SÍ |
| `gradio-client` | 4 | ai-toolkit=1.14.0, dataset-refiner=2.5.0, facefusion=1.12.1, sd-webui-forge-neo=1.2.0 | ⚠️ SÍ |
| `hf-xet` | 4 | ai-toolkit=1.5.1, comfyui=1.5.1, dataset-refiner=1.5.1, sd-webui-forge-neo=1.5.1 | ok |
| `mpmath` | 4 | ai-toolkit=1.3.0, comfyui=1.3.0, sd-webui-forge-neo=1.3.0, taggui=1.3.0 | ok |
| `networkx` | 4 | ai-toolkit=3.6.1, comfyui=3.6.1, sd-webui-forge-neo=3.6.1, taggui=3.6.1 | ok |
| `orjson` | 4 | ai-toolkit=3.11.9, dataset-refiner=3.11.9, facefusion=3.11.9, sd-webui-forge-neo=3.11.9 | ok |
| `pandas` | 4 | ai-toolkit=2.3.3, dataset-refiner=3.0.3, facefusion=2.3.3, sd-webui-forge-neo=2.3.3 | ⚠️ SÍ |
| `protobuf` | 4 | ai-toolkit=7.35.1, facefusion=7.35.1, sd-webui-forge-neo=4.25.9, taggui=7.35.1 | ⚠️ SÍ |
| `psutil` | 4 | ai-toolkit=7.2.2, comfyui=7.2.2, sd-webui-forge-neo=6.1.1, taggui=7.2.2 | ⚠️ SÍ |
| `pydub` | 4 | ai-toolkit=0.25.1, dataset-refiner=0.25.1, facefusion=0.25.1, sd-webui-forge-neo=0.25.1 | ok |
| `python-dateutil` | 4 | ai-toolkit=2.9.0.post0, dataset-refiner=2.9.0.post0, facefusion=2.9.0.post0, sd-webui-forge-neo=2.9.0.post0 | ok |
| `python-multipart` | 4 | ai-toolkit=0.0.32, dataset-refiner=0.0.32, facefusion=0.0.32, sd-webui-forge-neo=0.0.32 | ok |
| `pytz` | 4 | ai-toolkit=2026.2, dataset-refiner=2026.2, facefusion=2026.2, sd-webui-forge-neo=2026.2 | ok |
| `regex` | 4 | ai-toolkit=2026.5.9, comfyui=2026.5.9, sd-webui-forge-neo=2026.5.9, taggui=2026.5.9 | ok |
| `safetensors` | 4 | ai-toolkit=0.8.0, comfyui=0.8.0, sd-webui-forge-neo=0.7.0, taggui=0.8.0 | ⚠️ SÍ |
| `semantic-version` | 4 | ai-toolkit=2.10.0, dataset-refiner=2.10.0, facefusion=2.10.0, sd-webui-forge-neo=2.10.0 | ok |
| `setuptools` | 4 | ai-toolkit=69.5.1, comfyui=70.2.0, sd-webui-forge-neo=69.5.1, taggui=70.2.0 | ⚠️ SÍ |
| `six` | 4 | ai-toolkit=1.17.0, dataset-refiner=1.17.0, facefusion=1.17.0, sd-webui-forge-neo=1.17.0 | ok |
| `starlette` | 4 | ai-toolkit=0.52.1, dataset-refiner=1.3.1, facefusion=0.52.1, sd-webui-forge-neo=0.50.0 | ⚠️ SÍ |
| `sympy` | 4 | ai-toolkit=1.14.0, comfyui=1.14.0, sd-webui-forge-neo=1.14.0, taggui=1.14.0 | ok |
| `tokenizers` | 4 | ai-toolkit=0.22.2, comfyui=0.22.2, sd-webui-forge-neo=0.22.2, taggui=0.21.4 | ⚠️ SÍ |
| `tomlkit` | 4 | ai-toolkit=0.13.3, dataset-refiner=0.14.0, facefusion=0.13.3, sd-webui-forge-neo=0.12.0 | ⚠️ SÍ |
| `torch` | 4 | ai-toolkit=2.10.0+cu130, comfyui=2.10.0+cu130, sd-webui-forge-neo=2.10.0+cu130, taggui=2.10.0+cu130 | ok |
| `torchaudio` | 4 | ai-toolkit=2.10.0+cu130, comfyui=2.10.0+cu130, sd-webui-forge-neo=2.10.0+cu130, taggui=2.10.0+cu130 | ok |
| `torchvision` | 4 | ai-toolkit=0.25.0+cu130, comfyui=0.25.0+cu130, sd-webui-forge-neo=0.25.0+cu130, taggui=0.25.0+cu130 | ok |
| `transformers` | 4 | ai-toolkit=5.5.3, comfyui=5.12.1, sd-webui-forge-neo=4.56.2, taggui=4.48.3 | ⚠️ SÍ |
| `tzdata` | 4 | ai-toolkit=2026.2, dataset-refiner=2026.2, facefusion=2026.2, sd-webui-forge-neo=2026.2 | ok |
| `uvicorn` | 4 | ai-toolkit=0.49.0, dataset-refiner=0.49.0, facefusion=0.49.0, sd-webui-forge-neo=0.49.0 | ok |
| `accelerate` | 3 | ai-toolkit=1.14.0, sd-webui-forge-neo=1.13.0, taggui=1.10.1 | ⚠️ SÍ |
| `aiofiles` | 3 | ai-toolkit=24.1.0, facefusion=24.1.0, sd-webui-forge-neo=23.2.1 | ⚠️ SÍ |
| `av` | 3 | ai-toolkit=16.0.1, comfyui=17.1.0, sd-webui-forge-neo=17.0.0 | ⚠️ SÍ |
| `brotli` | 3 | ai-toolkit=1.2.0, dataset-refiner=1.2.0, facefusion=1.2.0 | ok |
| `einops` | 3 | ai-toolkit=0.8.2, comfyui=0.8.2, sd-webui-forge-neo=0.8.2 | ok |
| `ffmpy` | 3 | ai-toolkit=1.0.0, facefusion=1.0.0, sd-webui-forge-neo=1.0.0 | ok |
| `flatbuffers` | 3 | facefusion=25.12.19, sd-webui-forge-neo=25.12.19, taggui=25.12.19 | ok |
| `groovy` | 3 | ai-toolkit=0.1.2, dataset-refiner=0.1.2, facefusion=0.1.2 | ok |
| `kornia` | 3 | ai-toolkit=0.8.3, comfyui=0.8.3, sd-webui-forge-neo=0.6.12 | ⚠️ SÍ |
| `opencv-python` | 3 | ai-toolkit=4.13.0.92, facefusion=4.13.0.92, sd-webui-forge-neo=4.10.0.84 | ⚠️ SÍ |
| `pyparsing` | 3 | ai-toolkit=3.3.2, sd-webui-forge-neo=3.3.2, taggui=3.2.5 | ⚠️ SÍ |
| `ruff` | 3 | ai-toolkit=0.15.20, facefusion=0.15.20, sd-webui-forge-neo=0.15.20 | ok |
| `safehttpx` | 3 | ai-toolkit=0.1.7, dataset-refiner=0.1.7, facefusion=0.1.7 | ok |
| `timm` | 3 | ai-toolkit=1.0.22, sd-webui-forge-neo=1.0.27, taggui=1.0.20 | ⚠️ SÍ |
| `torchsde` | 3 | ai-toolkit=0.2.6, comfyui=0.2.6, sd-webui-forge-neo=0.2.6 | ok |
| `trampoline` | 3 | ai-toolkit=0.1.2, comfyui=0.1.2, sd-webui-forge-neo=0.1.2 | ok |
| `websockets` | 3 | ai-toolkit=15.0.1, facefusion=15.0.1, sd-webui-forge-neo=12.0 | ⚠️ SÍ |
| `absl-py` | 2 | ai-toolkit=2.4.0, sd-webui-forge-neo=2.4.0 | ok |
| `antlr4-python3-runtime` | 2 | ai-toolkit=4.9.3, sd-webui-forge-neo=4.9.3 | ok |
| `attrs` | 2 | ai-toolkit=26.1.0, comfyui=26.1.0 | ok |
| `audioop-lts` | 2 | ai-toolkit=0.2.2, sd-webui-forge-neo=0.2.2 | ok |
| `bitsandbytes` | 2 | ai-toolkit=0.49.2, taggui=0.48.1 | ⚠️ SÍ |
| `cffi` | 2 | ai-toolkit=2.0.0, sd-webui-forge-neo=2.0.0 | ok |
| `comfy-kitchen` | 2 | comfyui=0.2.10, sd-webui-forge-neo=0.2.8 | ⚠️ SÍ |
| `contourpy` | 2 | ai-toolkit=1.3.3, sd-webui-forge-neo=1.3.3 | ok |
| `cycler` | 2 | ai-toolkit=0.12.1, sd-webui-forge-neo=0.12.1 | ok |
| `diffusers` | 2 | ai-toolkit=url, sd-webui-forge-neo=0.37.1 | ⚠️ SÍ |
| `fonttools` | 2 | ai-toolkit=4.63.0, sd-webui-forge-neo=4.63.0 | ok |
| `ftfy` | 2 | ai-toolkit=6.3.1, sd-webui-forge-neo=6.3.1 | ok |
| `gitdb` | 2 | ai-toolkit=4.0.12, sd-webui-forge-neo=4.0.12 | ok |
| `gitpython` | 2 | ai-toolkit=3.1.50, sd-webui-forge-neo=3.1.46 | ⚠️ SÍ |
| `gradio-rangeslider` | 2 | facefusion=0.0.8, sd-webui-forge-neo=0.0.8 | ok |
| `imageio` | 2 | ai-toolkit=2.37.3, sd-webui-forge-neo=2.37.3 | ok |
| `importlib-metadata` | 2 | ai-toolkit=9.0.0, sd-webui-forge-neo=9.0.0 | ok |
| `joblib` | 2 | ai-toolkit=1.5.3, sd-webui-forge-neo=1.5.3 | ok |
| `kiwisolver` | 2 | ai-toolkit=1.5.0, sd-webui-forge-neo=1.5.0 | ok |
| `kornia-rs` | 2 | ai-toolkit=0.1.14, comfyui=0.1.14 | ok |
| `lazy-loader` | 2 | ai-toolkit=0.5, sd-webui-forge-neo=0.5 | ok |
| `llvmlite` | 2 | ai-toolkit=0.47.0, sd-webui-forge-neo=0.47.0 | ok |
| `matplotlib` | 2 | ai-toolkit=3.10.1, sd-webui-forge-neo=3.11.0 | ⚠️ SÍ |
| `ml-dtypes` | 2 | facefusion=0.5.4, sd-webui-forge-neo=0.5.4 | ok |
| `narwhals` | 2 | ai-toolkit=2.22.1, dataset-refiner=2.22.1 | ok |
| `numba` | 2 | ai-toolkit=0.65.1, sd-webui-forge-neo=0.65.1 | ok |
| `omegaconf` | 2 | ai-toolkit=2.3.1, sd-webui-forge-neo=2.2.3 | ⚠️ SÍ |
| `onnx` | 2 | facefusion=1.20.1, sd-webui-forge-neo=1.22.0 | ⚠️ SÍ |
| `onnxruntime` | 2 | sd-webui-forge-neo=1.27.0, taggui=1.23.1 | ⚠️ SÍ |
| `opencv-python-headless` | 2 | ai-toolkit=4.13.0.92, dataset-refiner=4.13.0.92 | ok |
| `peft` | 2 | ai-toolkit=0.18.1, sd-webui-forge-neo=0.17.1 | ⚠️ SÍ |
| `platformdirs` | 2 | ai-toolkit=4.10.0, sd-webui-forge-neo=4.10.0 | ok |
| `pycparser` | 2 | ai-toolkit=3.0, sd-webui-forge-neo=3.0 | ok |
| `python-dotenv` | 2 | ai-toolkit=1.2.2, comfyui=1.2.2 | ok |
| `pywavelets` | 2 | ai-toolkit=1.9.0, dataset-refiner=1.9.0 | ok |
| `scikit-image` | 2 | ai-toolkit=0.26.0, sd-webui-forge-neo=0.25.2 | ⚠️ SÍ |
| `sentencepiece` | 2 | ai-toolkit=0.2.1, comfyui=0.2.1 | ok |
| `smmap` | 2 | ai-toolkit=5.0.3, sd-webui-forge-neo=5.0.3 | ok |
| `spandrel` | 2 | comfyui=0.4.2, sd-webui-forge-neo=0.4.2 | ok |
| `tifffile` | 2 | ai-toolkit=2026.6.1, sd-webui-forge-neo=2026.6.1 | ok |
| `torchdiffeq` | 2 | ai-toolkit=0.2.5, sd-webui-forge-neo=0.2.5 | ok |
| `wcwidth` | 2 | ai-toolkit=0.8.1, sd-webui-forge-neo=0.8.1 | ok |
| `zipp` | 2 | ai-toolkit=4.1.0, sd-webui-forge-neo=4.1.0 | ok |

#### Únicas (129)
`addict`(sd-webui-forge-neo), `aiohappyeyeballs`(comfyui), `aiohttp`(comfyui), `aiosignal`(comfyui), `albucore`(ai-toolkit), `albumentations`(ai-toolkit), `alembic`(comfyui), `audioread`(ai-toolkit), `beautifulsoup4`(dataset-refiner), `blake3`(comfyui), `clean-fid`(ai-toolkit), `clip-anytorch`(ai-toolkit), `coloredlogs`(taggui), `comfy-aimdo`(comfyui), `comfyui-embedded-docs`(comfyui), `comfyui-frontend-package`(comfyui), `comfyui-workflow-templates`(comfyui), `comfyui-workflow-templates-core`(comfyui), `comfyui-workflow-templates-media-api`(comfyui), `comfyui-workflow-templates-media-image`(comfyui), `comfyui-workflow-templates-media-other`(comfyui), `comfyui-workflow-templates-media-video`(comfyui), `controlnet-aux`(ai-toolkit), `cssselect2`(sd-webui-forge-neo), `dctorch`(ai-toolkit), `decorator`(ai-toolkit), `deep-translator`(dataset-refiner), `depth-anything`(sd-webui-forge-neo), `depth-anything-v2`(sd-webui-forge-neo), `diskcache`(sd-webui-forge-neo), `eval-type-backport`(ai-toolkit), `exifread`(taggui), `facexlib`(sd-webui-forge-neo), `filterpy`(sd-webui-forge-neo), `flatten-json`(ai-toolkit), `frozenlist`(comfyui), `fvcore`(sd-webui-forge-neo), `glfw`(comfyui), `greenlet`(comfyui), `grpcio`(ai-toolkit), `hf-gradio`(dataset-refiner), `hf-transfer`(ai-toolkit), `humanfriendly`(taggui), `imagehash`(dataset-refiner), `imagesize`(taggui), `importlib-resources`(sd-webui-forge-neo), `inflection`(sd-webui-forge-neo), `insightface`(sd-webui-forge-neo), `invisible-watermark`(ai-toolkit), `iopath`(sd-webui-forge-neo), `jsonmerge`(ai-toolkit), `jsonschema`(ai-toolkit), `jsonschema-specifications`(ai-toolkit), `k-diffusion`(ai-toolkit), `lark`(sd-webui-forge-neo), `librosa`(ai-toolkit), `lpips`(ai-toolkit), `lxml`(sd-webui-forge-neo), `lycoris-lora`(ai-toolkit), `mako`(comfyui), `markdown`(ai-toolkit), `mediapipe`(sd-webui-forge-neo), `msgpack`(ai-toolkit), `multidict`(comfyui), `mutagen`(ai-toolkit), `ninja`(ai-toolkit), `nvidia-ml-py`(sd-webui-forge-neo), `onnxruntime-gpu`(facefusion), `open-clip-torch`(ai-toolkit), `opencv-contrib-python`(sd-webui-forge-neo), `optimum-quanto`(ai-toolkit), `oyaml`(ai-toolkit), `piexif`(sd-webui-forge-neo), `pillow-heif`(sd-webui-forge-neo), `pillow-jxl-plugin`(sd-webui-forge-neo), `plotly`(dataset-refiner), `polars`(sd-webui-forge-neo), `polars-runtime-32`(sd-webui-forge-neo), `pooch`(ai-toolkit), `portalocker`(sd-webui-forge-neo), `prodigyopt`(ai-toolkit), `propcache`(comfyui), `pydantic-settings`(comfyui), `pyopengl`(comfyui), `pyreadline3`(taggui), `pyside6`(taggui), `pyside6-addons`(taggui), `pyside6-essentials`(taggui), `python-slugify`(ai-toolkit), `pytorch-fid`(ai-toolkit), `pytorch-wavelets`(ai-toolkit), `pywin32`(sd-webui-forge-neo), `referencing`(ai-toolkit), `reportlab`(sd-webui-forge-neo), `rpds-py`(ai-toolkit), `scikit-learn`(ai-toolkit), `sentry-sdk`(ai-toolkit), `shiboken6`(taggui), `simpleeval`(comfyui), `sniffio`(sd-webui-forge-neo), `sounddevice`(sd-webui-forge-neo), `soundfile`(ai-toolkit), `soupsieve`(dataset-refiner), `soxr`(ai-toolkit), `spandrel-extra-arches`(sd-webui-forge-neo), `sqlalchemy`(comfyui), `standard-aifc`(ai-toolkit), `standard-chunk`(ai-toolkit), `standard-sunau`(ai-toolkit), `svglib`(sd-webui-forge-neo), `tabulate`(sd-webui-forge-neo), `tensorboard`(ai-toolkit), `tensorboard-data-server`(ai-toolkit), `termcolor`(sd-webui-forge-neo), `text-unidecode`(ai-toolkit), `threadpoolctl`(ai-toolkit), `tinycss2`(sd-webui-forge-neo), `tomesd`(sd-webui-forge-neo), `toml`(ai-toolkit), `torchao`(ai-toolkit), `torchcodec`(ai-toolkit), `ultralytics`(sd-webui-forge-neo), `ultralytics-thop`(sd-webui-forge-neo), `wandb`(ai-toolkit), `webencodings`(sd-webui-forge-neo), `werkzeug`(ai-toolkit), `yacs`(sd-webui-forge-neo), `yapf`(sd-webui-forge-neo), `yarl`(comfyui)

