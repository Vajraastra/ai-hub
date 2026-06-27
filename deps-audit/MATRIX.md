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

Apps con datos (2): comfyui, dataset-refiner
Total deps distintas: 110 · cruzadas (≥2 apps): **35** · únicas: 75 · cruzadas con conflicto de versión: **3**

#### Cruzadas (candidatas a pool compartido)
| Dependencia | #apps | Versiones por app | ¿Conflicto? |
|---|---|---|---|
| `annotated-doc` | 2 | comfyui=0.0.4, dataset-refiner=0.0.4 | ok |
| `annotated-types` | 2 | comfyui=0.7.0, dataset-refiner=0.7.0 | ok |
| `anyio` | 2 | comfyui=4.14.1, dataset-refiner=4.14.1 | ok |
| `certifi` | 2 | comfyui=2026.6.17, dataset-refiner=2026.6.17 | ok |
| `charset-normalizer` | 2 | comfyui=3.4.7, dataset-refiner=3.4.7 | ok |
| `click` | 2 | comfyui=8.4.2, dataset-refiner=8.4.2 | ok |
| `colorama` | 2 | comfyui=0.4.6, dataset-refiner=0.4.6 | ok |
| `filelock` | 2 | comfyui=3.29.0, dataset-refiner=3.29.4 | ⚠️ SÍ |
| `fsspec` | 2 | comfyui=2026.4.0, dataset-refiner=2026.6.0 | ⚠️ SÍ |
| `h11` | 2 | comfyui=0.16.0, dataset-refiner=0.16.0 | ok |
| `hf-xet` | 2 | comfyui=1.5.1, dataset-refiner=1.5.1 | ok |
| `httpcore` | 2 | comfyui=1.0.9, dataset-refiner=1.0.9 | ok |
| `httpx` | 2 | comfyui=0.28.1, dataset-refiner=0.28.1 | ok |
| `huggingface-hub` | 2 | comfyui=1.21.0, dataset-refiner=1.21.0 | ok |
| `idna` | 2 | comfyui=3.18, dataset-refiner=3.18 | ok |
| `jinja2` | 2 | comfyui=3.1.6, dataset-refiner=3.1.6 | ok |
| `markdown-it-py` | 2 | comfyui=4.2.0, dataset-refiner=4.2.0 | ok |
| `markupsafe` | 2 | comfyui=3.0.3, dataset-refiner=3.0.3 | ok |
| `mdurl` | 2 | comfyui=0.1.2, dataset-refiner=0.1.2 | ok |
| `numpy` | 2 | comfyui=2.4.4, dataset-refiner=2.5.0 | ⚠️ SÍ |
| `packaging` | 2 | comfyui=26.2, dataset-refiner=26.2 | ok |
| `pillow` | 2 | comfyui=12.2.0, dataset-refiner=12.2.0 | ok |
| `pydantic` | 2 | comfyui=2.13.4, dataset-refiner=2.13.4 | ok |
| `pydantic-core` | 2 | comfyui=2.46.4, dataset-refiner=2.46.4 | ok |
| `pygments` | 2 | comfyui=2.20.0, dataset-refiner=2.20.0 | ok |
| `pyyaml` | 2 | comfyui=6.0.3, dataset-refiner=6.0.3 | ok |
| `requests` | 2 | comfyui=2.34.2, dataset-refiner=2.34.2 | ok |
| `rich` | 2 | comfyui=15.0.0, dataset-refiner=15.0.0 | ok |
| `scipy` | 2 | comfyui=1.18.0, dataset-refiner=1.18.0 | ok |
| `shellingham` | 2 | comfyui=1.5.4, dataset-refiner=1.5.4 | ok |
| `tqdm` | 2 | comfyui=4.68.3, dataset-refiner=4.68.3 | ok |
| `typer` | 2 | comfyui=0.25.1, dataset-refiner=0.25.1 | ok |
| `typing-extensions` | 2 | comfyui=4.15.0, dataset-refiner=4.15.0 | ok |
| `typing-inspection` | 2 | comfyui=0.4.2, dataset-refiner=0.4.2 | ok |
| `urllib3` | 2 | comfyui=2.7.0, dataset-refiner=2.7.0 | ok |

#### Únicas (75)
`aiohappyeyeballs`(comfyui), `aiohttp`(comfyui), `aiosignal`(comfyui), `alembic`(comfyui), `attrs`(comfyui), `av`(comfyui), `beautifulsoup4`(dataset-refiner), `blake3`(comfyui), `brotli`(dataset-refiner), `comfy-aimdo`(comfyui), `comfy-kitchen`(comfyui), `comfyui-embedded-docs`(comfyui), `comfyui-frontend-package`(comfyui), `comfyui-workflow-templates`(comfyui), `comfyui-workflow-templates-core`(comfyui), `comfyui-workflow-templates-media-api`(comfyui), `comfyui-workflow-templates-media-image`(comfyui), `comfyui-workflow-templates-media-other`(comfyui), `comfyui-workflow-templates-media-video`(comfyui), `deep-translator`(dataset-refiner), `einops`(comfyui), `fastapi`(dataset-refiner), `frozenlist`(comfyui), `glfw`(comfyui), `gradio`(dataset-refiner), `gradio-client`(dataset-refiner), `greenlet`(comfyui), `groovy`(dataset-refiner), `hf-gradio`(dataset-refiner), `imagehash`(dataset-refiner), `kornia`(comfyui), `kornia-rs`(comfyui), `mako`(comfyui), `mpmath`(comfyui), `multidict`(comfyui), `narwhals`(dataset-refiner), `networkx`(comfyui), `opencv-python-headless`(dataset-refiner), `orjson`(dataset-refiner), `pandas`(dataset-refiner), `plotly`(dataset-refiner), `propcache`(comfyui), `psutil`(comfyui), `pydantic-settings`(comfyui), `pydub`(dataset-refiner), `pyopengl`(comfyui), `python-dateutil`(dataset-refiner), `python-dotenv`(comfyui), `python-multipart`(dataset-refiner), `pytz`(dataset-refiner), `pywavelets`(dataset-refiner), `regex`(comfyui), `safehttpx`(dataset-refiner), `safetensors`(comfyui), `semantic-version`(dataset-refiner), `sentencepiece`(comfyui), `setuptools`(comfyui), `simpleeval`(comfyui), `six`(dataset-refiner), `soupsieve`(dataset-refiner), `spandrel`(comfyui), `sqlalchemy`(comfyui), `starlette`(dataset-refiner), `sympy`(comfyui), `tokenizers`(comfyui), `tomlkit`(dataset-refiner), `torch`(comfyui), `torchaudio`(comfyui), `torchsde`(comfyui), `torchvision`(comfyui), `trampoline`(comfyui), `transformers`(comfyui), `tzdata`(dataset-refiner), `uvicorn`(dataset-refiner), `yarl`(comfyui)

