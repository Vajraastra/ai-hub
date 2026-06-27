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

Apps con datos (1): comfyui
Total deps distintas: 83 · cruzadas (≥2 apps): **0** · únicas: 83 · cruzadas con conflicto de versión: **0**

#### Cruzadas (candidatas a pool compartido)
| Dependencia | #apps | Versiones por app | ¿Conflicto? |
|---|---|---|---|

#### Únicas (83)
`aiohappyeyeballs`(comfyui), `aiohttp`(comfyui), `aiosignal`(comfyui), `alembic`(comfyui), `annotated-doc`(comfyui), `annotated-types`(comfyui), `anyio`(comfyui), `attrs`(comfyui), `av`(comfyui), `blake3`(comfyui), `certifi`(comfyui), `charset-normalizer`(comfyui), `click`(comfyui), `colorama`(comfyui), `comfy-aimdo`(comfyui), `comfy-kitchen`(comfyui), `comfyui-embedded-docs`(comfyui), `comfyui-frontend-package`(comfyui), `comfyui-workflow-templates`(comfyui), `comfyui-workflow-templates-core`(comfyui), `comfyui-workflow-templates-media-api`(comfyui), `comfyui-workflow-templates-media-image`(comfyui), `comfyui-workflow-templates-media-other`(comfyui), `comfyui-workflow-templates-media-video`(comfyui), `einops`(comfyui), `filelock`(comfyui), `frozenlist`(comfyui), `fsspec`(comfyui), `glfw`(comfyui), `greenlet`(comfyui), `h11`(comfyui), `hf-xet`(comfyui), `httpcore`(comfyui), `httpx`(comfyui), `huggingface-hub`(comfyui), `idna`(comfyui), `jinja2`(comfyui), `kornia`(comfyui), `kornia-rs`(comfyui), `mako`(comfyui), `markdown-it-py`(comfyui), `markupsafe`(comfyui), `mdurl`(comfyui), `mpmath`(comfyui), `multidict`(comfyui), `networkx`(comfyui), `numpy`(comfyui), `packaging`(comfyui), `pillow`(comfyui), `propcache`(comfyui), `psutil`(comfyui), `pydantic`(comfyui), `pydantic-core`(comfyui), `pydantic-settings`(comfyui), `pygments`(comfyui), `pyopengl`(comfyui), `python-dotenv`(comfyui), `pyyaml`(comfyui), `regex`(comfyui), `requests`(comfyui), `rich`(comfyui), `safetensors`(comfyui), `scipy`(comfyui), `sentencepiece`(comfyui), `setuptools`(comfyui), `shellingham`(comfyui), `simpleeval`(comfyui), `spandrel`(comfyui), `sqlalchemy`(comfyui), `sympy`(comfyui), `tokenizers`(comfyui), `torch`(comfyui), `torchaudio`(comfyui), `torchsde`(comfyui), `torchvision`(comfyui), `tqdm`(comfyui), `trampoline`(comfyui), `transformers`(comfyui), `typer`(comfyui), `typing-extensions`(comfyui), `typing-inspection`(comfyui), `urllib3`(comfyui), `yarl`(comfyui)

