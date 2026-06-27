# Análisis para un pool compartido de dependencias

_Documento vivo. Se actualiza al instalar cada app externa. Objetivo:
cuantificar el duplicado y decidir si/cómo migrar del modelo "un venv completo
por app" a un **pool compartido** (sobre todo para torch + CUDA)._

## Por qué duele hoy (medición real)

| App | venv total | torch (incl. CUDA bundled) | % torch |
|---|---|---|---|
| comfyui | **3.6 GB** | **2.7 GB** | 75% |
| sd-webui-forge-neo | _(pendiente)_ | | |
| ai-toolkit | _(pendiente)_ | | |
| anima-standalone-trainer | _(pendiente)_ | | |
| taggui | _(pendiente)_ | | |
| facefusion | _(sin torch — usa onnxruntime)_ | — | — |
| dataset-refiner | _(sin torch)_ | — | — |

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
