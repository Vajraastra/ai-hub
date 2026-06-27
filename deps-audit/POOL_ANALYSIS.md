# Análisis para un pool compartido de dependencias

_Documento vivo. Se actualiza al instalar cada app externa. Objetivo:
cuantificar el duplicado y decidir si/cómo migrar del modelo "un venv completo
por app" a un **pool compartido** (sobre todo para torch + CUDA)._

## Por qué duele hoy (medición real)

| App | venv total | torch (incl. CUDA bundled) | % torch |
|---|---|---|---|
| comfyui | **3.6 GB** | **2.7 GB** | 75% |
| taggui | **3.7 GB** | **2.7 GB** | 73% |
| ai-toolkit | **3.88 GB** | **2.59 GB** | 67% |
| facefusion | **897 MB** | sin torch (onnxruntime-gpu) | — |
| dataset-refiner | **494 MB** | sin torch | — |
| sd-webui-forge-neo | _(pendiente — instala en su launch)_ | | |
| anima-standalone-trainer | _(pendiente — instala en su launch)_ | | |

> **3 apps con torch `2.10.0+cu130` idéntico** (comfyui, taggui, ai-toolkit) pese
> a pins distintos. ~2.6–2.7 GB × 3 ≈ **~8 GB poolables solo en torch**, hoy.

## Vista RESUELTA — 4 apps con snapshot (comfyui, taggui, facefusion, dataset-refiner)

**67 deps cruzadas resueltas → 50 con la MISMA versión exacta (poolables) · 17 con conflicto real.**

**El titular: torch ES poolable.** taggui PINEA `torch 2.8.0+cu128` en su
requirements, pero terminó con **`torch 2.10.0+cu130` idéntico a ComfyUI**.
El mecanismo: el guardian instala torch (cu130) en `pre_install` ANTES de
requirements, y `uv pip install -r` respeta el ya instalado (no lo cambia).
→ `torch` + `torchvision` + `torchaudio` = misma build en las 2 apps con torch
= **2.7 GB poolables hoy mismo** entre ellas (y crecerá con forge/ai-toolkit/anima).

> ⚠️ Matiz para un pool robusto: que uv "respete lo instalado" es conveniente
> pero no es una garantía formal. Para un pool de verdad conviene **excluir torch
> del requirements** de cada app (`pip_exclude_packages`) y dejar que SOLO el
> guardian lo provea. Así el pin de la app nunca compite.

**También poolables ya (misma versión):** casi todas las utilidades comunes —
certifi, requests, urllib3, idna, jinja2, markupsafe, pyyaml, packaging,
typing-extensions, click, rich, httpx, anyio, safetensors (0.8.0), protobuf,
fastapi, uvicorn… (50 en total).

**Conflictos reales (NO poolables sin unificar versión)** — los pesos medios de ML:
| dep | versiones por app |
|---|---|
| numpy | 2.2.1 / 2.4.4 / 2.5.0 |
| pillow | 11.3.0 / 12.2.0 |
| transformers | 4.48.3 / 5.12.1 |
| gradio | 5.44.1 / 6.19.0 |
| huggingface-hub | 0.35.3 / 0.36.2 / 1.21.0 |
| pydantic(-core) | 2.11 / 2.13 |
| scipy, tqdm, typer, tokenizers, fsspec, filelock, pandas, starlette | micro-diferencias |

**Lectura:** un pool por capas sería lo natural — (1) **torch/CUDA** (el 75% del
peso) poolable ya vía guardian; (2) **utilidades comunes** poolables; (3) **libs
ML medianas** (numpy/pillow/transformers/gradio) divergen por app → o se unifican
versiones, o se quedan por-app. El ahorro gordo (torch) está en la capa 1.

**Hallazgo clave (Windows / cu13x):** el wheel de torch **incluye las DLLs
de CUDA dentro de `site-packages/torch/`** (cuDNN, cuBLAS, etc.). No aparecen
paquetes `nvidia-*` sueltos como en Linux. Por eso torch pesa ~2.7 GB y es,
con diferencia, el candidato #1 a poolear.

**Proyección:** ~5 apps llevan torch. Si todas resuelven a la misma build,
son ~2.7 GB × 5 ≈ **~13 GB** de los que un pool recuperaría ~11 GB.

## El obstáculo real: NO piden el mismo torch

La viabilidad del pool depende de unificar la versión. Lo declarado diverge:

| App | torch declarado | CUDA tag |
|---|---|---|
| comfyui | sin pin → resolvió **2.10.0+cu130** | cu130 |
| taggui | **2.8.0** (pin, wheel directo) | cu128 |
| anima-standalone-trainer | **2.7.0** (pin) | cu128 |
| sd-webui-forge-neo | sin pin (instala en su propio launch) | — |
| ai-toolkit | sin pin directo (torchao lo arrastra) | — |

→ Tres versiones (2.7 / 2.8 / 2.10) y dos tags (cu128 / cu130). Un pool ingenuo
no sirve: habría que **unificar a una sola build**.

## La palanca que ya tenemos: `cuda_guardian` + `pre_install`

El instalador del hub ya fuerza torch a la build óptima del driver vía
`pre_install_commands` (`{torch_version}`/`{cuda_tag}` resueltos por
`cuda_guardian`), **antes** de `requirements.txt`. En esta máquina (driver
13.3 → cu130) instaló torch 2.10.0+cu130 en ComfyUI ignorando que el
requirements no pinea.

Implicación para el pool: si el guardian impone **la misma build a todas las
apps** (cu130/2.10), el pool pasa de inviable a viable para torch. Riesgo a
vigilar: apps que **pinean** torch a otra versión (taggui 2.8+cu128, anima 2.7)
podrían hacer que `requirements.txt` REINSTALE su versión encima de la del
pre_install → habría que excluir torch de esos requirements (`pip_exclude_packages`)
o confiar en que el guardian gane. **Anotar al instalar cada una.**

## Otras cruzadas pesadas a vigilar (de la matriz declarada)

transformers (5 apps, rango 4.48→5.5), pillow (5), opencv-python (5),
numpy (4, con el clásico salto 1.x/2.x), accelerate, huggingface-hub,
safetensors, diffusers, onnxruntime. Ver `MATRIX.md` para el detalle y los
32 conflictos de versión declarados. Lo decisivo será la vista RESUELTA: dos
apps que pinean distinto pero **resuelven** a la misma versión sí poolean.

## Próximos pasos del registro

1. Instalar cada app externa → `audit.py snapshot <app> <venv_python>`.
2. Rellenar la tabla de pesos (venv total / torch) por app.
3. Tras todas: `audit.py matrix` y revisar cuántas cruzadas-resueltas coinciden
   en versión exacta (esas son el pool real).
