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
| sd-webui-forge-neo | **3.84 GB** | **2.59 GB** | 67% |
| anima-standalone-trainer | **3.37 GB** | **2.62 GB** | 78% |
| facefusion | **897 MB** | sin torch (onnxruntime-gpu) | — |
| dataset-refiner | **494 MB** | sin torch | — |

**TOTAL 7 venvs ≈ 19.4 GB.**

> **5 apps con torch `2.10.0+cu130` idéntico** (comfyui, taggui, ai-toolkit,
> forge-neo, anima) pese a pins distintos (taggui pedía 2.8+cu128, anima 2.7+cu128,
> forge 0.22 torchvision…). El guardian fuerza la build común vía pre_install, o
> vía `TORCH_COMMAND` env var que forge/anima leen. torch+torchvision+torchaudio
> = misma build en las 5 → **~2.6 GB × 5 ≈ ~13 GB poolables solo en torch**.

## Conclusión (7/7 apps medidas)

Vista RESUELTA: **133 deps cruzadas → 85 con la MISMA versión exacta (poolables),
48 en conflicto.** El ahorro se concentra en una capa:

| Capa | Poolable | Ahorro estimado |
|---|---|---|
| **torch + CUDA bundled** | ✅ sí (guardian unifica a cu130) | **~13 GB** (de 19.4 GB totales) |
| utilidades comunes (requests, jinja2, pyyaml, rich, fastapi…) | ✅ sí | cientos de MB |
| libs ML medianas (numpy, pillow, transformers, gradio, hf-hub…) | ⚠️ no sin unificar versión | — |

**Recomendación para cuando se aborde el pool:** atacar **solo la capa torch**
primero (es ~67% del disco total y ya está homogeneizada por el guardian). Un
venv-base compartido con torch/torchvision/torchaudio + las apps enlazando a él
(o `--system-site-packages` apuntando al base) recupera la mayor parte sin tocar
las divergencias de numpy/pillow/transformers. Las capas 2 y 3 dan rendimientos
decrecientes y más fricción de versiones.

---

## Think tank (sesión 29) — decisión: ir por el NIVEL 0

**Hallazgo empírico:** los 5 torch son COPIAS reales (`stat -c %h` = `links=1`,
inodes distintos), no hardlinks. Los 13 GB son duplicación verdadera.
**Causa:** el `pre_install` del registry instala torch con **`--no-cache-dir`**
(`uv pip install --no-cache-dir torch==…`), lo que impide que uv hardlinkee
desde su cache. Cache y venvs están en el mismo volumen E: → el hardlink ES
posible; solo lo estamos desactivando con ese flag.

**Tres niveles posibles de consolidación:**
- **Nivel 0 (elegido) — hardlinks de uv:** quitar `--no-cache-dir`, forzar
  `UV_LINK_MODE=hardlink`, cache en el mismo volumen. uv deja 1 copia física en
  el cache y los venvs son hardlinks. Recupera ~10 GB sin pool gestionado, sin
  tocar las apps, riesgo ≈0 (al actualizar, uv borra+reescribe → el hardlink se
  separa limpio). Es el pool *implícito* de uv.
- **Nivel 1 — venv-base + .pth/junction** para la capa torch (recupera aunque
  las versiones diverjan; más frágil: sys.path + `.dist-info`).
- **Nivel 2 — pool por capas completo** (torch + utils + ML): rendimientos
  decrecientes, evitar.

**Matiz:** "ahorro de espacio en esta máquina" (lo da el Nivel 0/hardlinks) ≠
"portabilidad a otra máquina" (copiar rompe los hardlinks; lo que da
portabilidad es el instalador reproducible, no copiar GB).

### Plan de acción — PRÓXIMA SESIÓN
1. **Nivel 0:** quitar `--no-cache-dir` del `pre_install_commands` de torch en
   `app_registry.json`; garantizar `UV_LINK_MODE=hardlink`. Validar con UNA app
   (reinstalar → confirmar `links>1` en un .dll de torch) antes de generalizar.
   Reinstalar/re-linkear las 5 apps con torch y **medir el espacio físico real**
   (esperado: ~19.4 GB → ~9-10 GB).
2. **Deprecar taggui:** el usuario tiene un tagger propio más avanzado.
   Calcular cómo cambia la proyección al remover taggui **con sus deps**:
   - taggui aporta deps ÚNICAS (PySide6, ExifRead, pyparsing…) → se liberan.
   - su torch es compartido (capa 1) → con Nivel 0 NO libera espacio físico de
     torch (lo comparten comfyui/ai-toolkit/forge/anima); sin Nivel 0 liberaría
     ~2.7 GB lógicos.
   - actualizar `EXTERNAL_APPS` en `audit.py`, quitar `resolved/taggui.txt`,
     regenerar MATRIX/POOL_ANALYSIS, y recalcular el total.

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

---

## Cierre (sesión 31, 2026-07-02) — Nivel 0 EJECUTADO + taggui deprecada

**taggui deprecada y desinstalada** (el usuario tiene un tagger propio más
avanzado): fuera de `EXTERNAL_APPS`, snapshot `resolved/taggui.txt` borrado,
fuera del catálogo (`app_registry.json`) y del hub. Venv borrado: **3.64 GB
recuperados** (su torch era copia, no hardlink, así que se liberó completo).

**Nivel 0 aplicado (commit `012a782`):** cache uv unificado en
`<root>\.cache\uv` (mismo volumen E: que los venvs) + `UV_LINK_MODE=hardlink`
+ `--no-cache-dir` fuera del pre_install de torch. Torch re-linkeado con
`--reinstall-package torch`:

| Grupo ABI | Apps | Copias físicas de torch |
|---|---|---|
| cp313 | ai-toolkit, sd-webui-forge-neo | **1** (compartida vía cache) |
| cp312 | comfyui, anima-standalone-trainer | **1** (compartida vía cache) |

Verificado con `fsutil hardlink list` (venv + cache = mismo archivo físico).
Matiz que la sesión 29 no contemplaba: los hardlinks solo dedupean entre
**wheels idénticos** (misma versión de torch Y misma versión de Python/ABI) —
por eso son 2 copias físicas, no 1. Aun así: 4 venvs con torch → 2 copias.

Caches obsoletos borrados (default de C: + `hub\.cache` legacy): **8.1 GB
liberados** en total (4.5 C: + 3.6 E:). Con los 3.64 GB de taggui: **~11.7 GB
recuperados en la sesión**. Instalaciones futuras vía hub deduplican solas
(`_get_install_env` fuerza cache y link mode). Purga de emergencia disponible
en WebUI → Ajustes → Mantenimiento.

**Niveles 1/2 (venv-base / pool gestionado): NO se abordan** — el grueso del
ahorro ya está capturado con riesgo cero. Este documento queda como registro
histórico; `MATRIX.md` sigue regenerable con `audit.py matrix`.
