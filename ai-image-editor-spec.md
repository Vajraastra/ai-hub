# Módulo: Editor de Imágenes Iterativo con IA (Painter)

> **Cómo usar este documento**: describe qué construir y cómo. Las convenciones del hub (`CLAUDE.md` raíz) siempre tienen prioridad si hay conflicto. Este doc está alineado con el estado acordado en TASKS.md.

---

## 1. Visión y alcance

### Qué es

Un módulo del hub que ofrece un editor de imágenes minimalista en el navegador (canvas web integrado en la UI del hub) cuyo único propósito es **generar y editar imágenes con IA** usando ComfyUI como backend.

### El workflow central

```
generar → seleccionar región → regenerar región → aceptar/rechazar → repetir
```

Todo el diseño gira alrededor de este loop. No es Photoshop. No es Krita. Es una herramienta enfocada para inpainting/outpainting iterativo.

### Qué NO es

- No es un editor de pintura digital (no hay presión de stylus, no hay pinceles expresivos)
- No es un editor con sistema de capas múltiples
- No tiene herramientas de ajuste de color, filtros, niveles, etc.
- No reimplementa lógica que ya viva en otros módulos del hub (gestión de modelos, descargas, etc.)

### Modelo mental: doble buffer, no capas

El estado del canvas en cualquier momento es:

- `current`: la imagen aceptada actual (el "ground truth" del usuario)
- `preview`: la generación candidata de ComfyUI, mostrada sobre `current`
- `mask`: la región marcada para regenerar (overlay magenta semitransparente)

Cuando el usuario acepta `preview`, el backend compone preview sobre current (respetando la máscara con feathering) y lo convierte en el nuevo `current`. `preview` se descarta. El historial guarda snapshots para undo/redo.

**No hay pila de capas persistente. No hay compositing complejo.**

---

## 2. Stack

- **Backend generativo**: ComfyUI (único backend soportado — soporta SD1.5, SDXL y Flux)
- **Backend hub**: Python + FastAPI, montado en el hub via `painter_routes.py`
- **Frontend**: HTML5 Canvas + JS vanilla (un único `painter.js`, igual que `vault.js` / `merger.js`)
- **Procesamiento de imagen**: Pillow (PIL) en backend; Canvas API en frontend
- **Comunicación con ComfyUI**: cliente HTTP + WebSocket en `comfy_client.py`
- **Locale**: `locale.js` existente del hub (ES/EN)

### Arquitecturas de modelos — estrategia incremental

El Painter soporta una arquitectura a la vez, implementada completa antes de pasar a la siguiente:

| Fase | Arquitectura | Modelos incluidos |
|---|---|---|
| **Actual** | **SDXL** | Pony, Illustrious, NoobAI, Juggernaut, WaiAI, SDXL base, y derivados anime (son SDXL bajo el capó) |
| Futura | Flux | Flux.1-dev, Flux.1-schnell, Z-Image Turbo |

La selección de arquitectura es **manual** por ahora — un selector en la UI del Painter. La detección automática (via `base_model` del Model Vault) se evalúa cuando SDXL esté completo.

Los workflows viven en subcarpetas por arquitectura: `workflows/sdxl/`, `workflows/flux/`. `comfy_client.load_workflow(name, arch)` abstrae la ruta. `SUPPORTED_ARCHITECTURES` en `comfy_client.py` define qué arquitecturas están habilitadas.

### Por qué ComfyUI y no Forge Neo

Forge Neo también está instalado en el hub y tiene API REST (`/sdapi/v1/`), pero se descartó como backend por una razón irrecuperable: **no soporta Flux**. ComfyUI soporta SD1.5, SDXL y Flux nativamente. No se implementa capa de abstracción multi-backend — ComfyUI es el único.

### Nodos: existentes vs custom

Se usan **nodos core de ComfyUI** para los 5 workflows principales. Todos están disponibles en la instalación base sin dependencias externas. La única excepción es `comfyui_controlnet_aux` (preprocessors de ControlNet: Canny, Depth, DWPose, etc.), que se instala automáticamente en el primer uso del módulo — el usuario no interviene.

Resoluciones soportadas: 512×512, 768×768, 1024×1024, 1536×1536, 2048×2048. Fuera de rango: rechazar con mensaje claro.

---

## 3. Estructura del módulo

```
apps/painter/
├── core/
│   ├── comfy_client.py       # cliente HTTP + WS para ComfyUI (POST /prompt, WS /ws, GET /view)
│   ├── models.py             # modelos Pydantic para requests/responses
│   ├── image_utils.py        # PIL: b64↔PIL, feather_mask, pad_for_outpaint, resize_to_fit, validate_resolution
│   ├── session.py            # estado en memoria: current_image, history (undo stack), redo_stack
│   └── api.py                # endpoints FastAPI (ver sección 4)
├── workflows/
│   ├── txt2img.json          # plantilla con placeholders {{param}}
│   ├── inpaint.json
│   ├── outpaint.json         # usa ImagePadForOutpaint
│   ├── upscale.json
│   └── controlnet.json
└── scripts/
    └── validate_comfy.py     # smoke test standalone: valida txt2img + inpaint end-to-end

hub-webui/
├── painter_routes.py         # monta api.py en el FastAPI del hub
├── app.py                    # registra painter_routes (modificación)
└── static/
    ├── painter.html          # layout grid 4 filas / 3 columnas
    └── painter.js            # toda la lógica frontend (canvas, herramientas, API calls, UI)

hub/config/app_registry.json  # registrar entrada painter (modificación)
```

---

## 4. API — Endpoints

Todos bajo prefijo `/api/painter/` (consistente con `/api/vault/` y `/api/merger/`).

### Generación

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/api/painter/generate` | txt2img → devuelve `{ job_id }` |
| `POST` | `/api/painter/inpaint` | inpaint con imagen + máscara |
| `POST` | `/api/painter/outpaint` | outpaint con padding; construye imagen extendida + máscara internamente |
| `GET`  | `/api/painter/jobs/{job_id}` | estado del job |
| `GET`  | `/api/painter/jobs/{job_id}/result` | imagen resultante (PNG bytes o b64) |
| `WS`   | `/api/painter/progress/{job_id}` | progreso en tiempo real: `queued`, `started`, `step {n,total}`, `done`, `error` |

### Sesión

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/api/painter/session/accept` | compone preview → current, limpia preview |
| `POST` | `/api/painter/session/reject` | descarta preview, preserva current |
| `POST` | `/api/painter/session/undo` | restaura snapshot anterior del historial |
| `POST` | `/api/painter/session/redo` | rehace snapshot |

### Modelos

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET`  | `/api/painter/models` | lista checkpoints (reusa enumeración del hub) |
| `GET`  | `/api/painter/controlnet-models` | lista modelos ControlNet |
| `GET`  | `/api/painter/upscale-models` | lista upscalers |

---

## 5. Componentes internos (backend)

### `comfy_client.py`
- Encapsula `POST /prompt`, `WS /ws`, `GET /view`
- Correlaciona mensajes WS por `prompt_id`
- Maneja reconexión y timeouts
- URL de ComfyUI leída de `hub_config.json`, nunca hardcodeada

### `workflows/*.json`
- Plantillas JSON parametrizables con placeholders `{{param_name}}`
- Se sustituyen en runtime antes de enviar a ComfyUI
- Documentar en un `.md` paralelo qué params se sustituyen y qué custom nodes asume
- Versionar: son frágiles entre actualizaciones de ComfyUI

### `image_utils.py`
- `feather_mask(mask, radius)` — blur gaussiano de bordes. **Crítico** para evitar inpaints recortados. Default radius = 4px, siempre activado.
- `pad_for_outpaint(image, padding)` — crea imagen extendida + máscara para outpainting
- `b64_to_pil(b64)`, `pil_to_b64(img)`
- `resize_to_fit(img, max_w, max_h)` — redimensiona preservando aspecto
- `validate_resolution(w, h)` — rechaza tamaños fuera de rango o no múltiplos de 8

### `session.py`
- Estado en memoria, una sesión por cliente
- Guarda `current_image` (PIL), `history` (lista de snapshots PNG comprimidos), `redo_stack`
- Métodos: `set_current()`, `push_history()`, `undo()`, `redo()`
- Límite de historial: 20 snapshots (configurable). A 2048×2048 son ~100-200MB.
- Si el hub se reinicia, el trabajo no guardado se pierde — documentarlo en UI

### Jobs concurrentes
- ComfyUI tiene cola interna; el módulo la respeta
- Si hay un job activo y llega otro: rechazar con mensaje "Una generación está en curso"
- Botón Cancelar → `POST /interrupt` a ComfyUI

---

## 6. Frontend (`painter.html` + `painter.js`)

### Layout
Grid 4 filas × 3 columnas, tema cyberpunk del hub (`#0F0023`, `#600DB5`, `#54EFEA`, `#EC00F0`).

### Canvas — doble buffer
- Dos `<canvas>` superpuestos: uno para `current`, otro para `preview` + overlay de máscara
- `devicePixelRatio` manejado correctamente: buffer interno en píxeles reales, no CSS pixels
- Render loop con `requestAnimationFrame`
- `commit_edit()` — composite edit sobre original con feathering de máscara
- `discard_edit()` — descarta capa edit

### Herramientas de selección/máscara
- **Brush** — pinta máscara con círculo. Atajos `[` `]` para tamaño, `X` toggle pintar/borrar
- **Rect marquee** — click+drag → rasteriza rect a máscara. Shift=sumar, Alt=restar
- **Lasso freehand** — captura puntos del mouse, cierra polígono al soltar, rasteriza a máscara
- **Eraser mask** — borra zonas de la máscara
- Overlay: magenta `#EC00F0` @ 40% sobre zonas marcadas

### Panel IA — tres tabs

**Tab principal**
- Selector de checkpoint
- Prompt positivo + negativo
- Sampler, scheduler, steps, CFG
- Seed + botón randomize
- Denoise strength
- Dimensiones + ratio

**Tab Upscaler**
- Selector de modelo upscaler
- Factor de escala

**Tab ControlNet**
- Carga de imagen de referencia
- Tipo de ControlNet
- Strength

### Controles generales
- Botón **Load Image** — abre imagen, resize-to-fit al tamaño objetivo
- Botones **Accept / Reject** — llaman a `/session/accept` y `/session/reject`
- **Undo / Redo** — Ctrl+Z / Ctrl+Y, conectados a `/session/undo` y `/session/redo`
- Barra de progreso WS durante generación
- Botón **Cancelar** job activo
- Status bar: zoom %, dimensiones doc, posición cursor, VRAM, cola
- Locale ES/EN via `locale.js` existente
- Entrada de navegación en `index.html` del hub

---

## 7. Fases de implementación

**Implementar en orden. No saltar fases.**

### Fase 0 — Setup automático + Validación del pipeline ComfyUI

#### 0a — Setup automático (primer uso)

`apps/painter/core/setup.py` orquesta el flujo completo al cargar el módulo por primera vez:

```
1. ¿ComfyUI instalado?
   → hub_config.json → installed_apps.comfyui.installed
   → Si no: mostrar pantalla "Instala ComfyUI desde el hub" — detener aquí

2. ¿ComfyUI corriendo?
   → GET http://localhost:8188/system_stats
   → Si no: mostrar "Inicia ComfyUI antes de usar Painter" + botón al hub — detener aquí

3. Custom nodes — leer apps/painter/nodes_registry.json
   Para cada nodo en la lista:
   → Si ya instalado (carpeta existe en custom_nodes/): skip
   → Si no: git clone + uv pip install -r requirements.txt
     - Si falla (404, red, otro error):
       * "required": bloquear setup, mostrar error con repo URL
       * "enhanced": continuar, registrar warning, usar workflow fallback
   → Si algún nodo nuevo fue instalado: notificar "ComfyUI necesita reiniciarse"

4. Modelos
   → Checkpoints: si ninguno → advertencia (no bloquea)
   → ControlNet: si ninguno → tab ControlNet deshabilitada (no bloquea)
   → Upscalers: si ninguno → tab Upscaler deshabilitada (no bloquea)

5. Guardar estado en apps/painter/painter_setup.json
   → Incluye: nodos instalados OK, nodos con fallback, warnings activos
   → En usos subsiguientes: solo verificar que ComfyUI sigue corriendo (pasos 1-2)
```

#### `apps/painter/nodes_registry.json` — lista manejable sin tocar código

```json
{
  "required": [
    {
      "id": "comfyui-tooling-nodes",
      "repo": "https://github.com/Acly/comfyui-tooling-nodes",
      "probe_node": "ETN_LoadImageBase64",
      "reason": "Carga de imagen/máscara como base64 sin subir archivos"
    },
    {
      "id": "comfyui_controlnet_aux",
      "repo": "https://github.com/comfyorg/comfyui-controlnet-aux",
      "probe_node": "ControlNetPreprocessorSelector",
      "reason": "Preprocessors de ControlNet (Canny, Depth, Pose, etc.)"
    }
  ],
  "enhanced": [
    {
      "id": "comfyui-inpaint-nodes",
      "repo": "https://github.com/Acly/comfyui-inpaint-nodes",
      "probe_node": "INPAINT_InpaintWithModel",
      "reason": "Inpaint mejorado — Fooocus (SDXL), LaMa, MAT",
      "fallback_workflow": "inpaint_basic.json"
    }
  ]
}
```

`probe_node`: nodo que se verifica en `/object_info` de ComfyUI para confirmar que el paquete cargó correctamente (no solo que la carpeta existe).

Actualizar URLs o agregar nuevos paquetes es editar este JSON — sin tocar código Python.

Endpoints de setup:
- `GET  /api/painter/setup/status` — estado actual (JSON con warnings activos)
- `POST /api/painter/setup/run`    — ejecuta setup con SSE de progreso en tiempo real

El frontend muestra un overlay de setup antes del canvas. Desaparece cuando `status == ready`. Los warnings (nodos enhanced que fallaron) se muestran como banner no-bloqueante en la UI principal.

#### 0b — Validación del pipeline

Script standalone `apps/painter/scripts/validate_comfy.py` que:
1. Carga `txt2img.json`, lo envía a ComfyUI, espera evento `executed` por WS, descarga y guarda la imagen
2. Repite con inpaint: carga imagen + máscara fijas desde disco, envía workflow, verifica resultado

Este script también sirve como smoke test permanente tras cualquier actualización de ComfyUI.

**Criterio de éxito**: ejecutar el script y obtener imágenes correctas. No avanzar hasta aquí.

### Fase 1 — Backend FastAPI
- `core/comfy_client.py`, `core/models.py`, `core/image_utils.py`, `core/session.py`, `core/api.py`
- `hub-webui/painter_routes.py` montado en `app.py`
- Registro en `app_registry.json`
- Workflows: `txt2img.json`, `inpaint.json`, `outpaint.json`, `upscale.json`, `controlnet.json`

**Criterio de éxito**: con curl — generate → inpaint → accept/reject → undo.

### Fase 2 — Frontend Canvas MVP
- `hub-webui/static/painter.html` y `painter.js`
- Canvas doble buffer, coordenadas imagen-espacio vs CSS-espacio
- Herramientas: Brush, Rect marquee, Lasso freehand, Eraser
- Panel IA con las tres tabs
- Barra de progreso WS, Accept/Reject/Undo/Redo
- Locale ES/EN, entrada en `index.html`

**Criterio de éxito**: generar imagen → pintar máscara → inpaint → aceptar o rechazar → undo.

### Fase 3 — Pulido
- Outpaint con handles arrastrables en bordes del canvas
- Feather radius slider conectado a `image_utils.feather_mask`
- Guardar imagen final a disco
- Mensajes de error visibles (toasts estilo hub)
- Atajos de teclado documentados en UI
- Magic wand — flood fill por similitud de color (`POST /api/painter/select/wand`)
- SAM (post-MVP, requiere custom node en ComfyUI) — `POST /api/painter/select/sam`

---

## 8. Integración con el hub

- Rutas montadas en `hub-webui/app.py` igual que `vault_routes` y `merger_routes`
- Modelos leídos via enumeración del hub — no reimplementar
- URL de ComfyUI desde `hub_config.json → comfyui.url` (o el key equivalente)
- Paths de modelos desde `hub_config.json → paths.models`
- Logging/notificaciones: usar el sistema del hub si aplica

---

## 9. Testing

- `validate_comfy.py` sirve como smoke test permanente de Fase 0
- Tests unitarios de `image_utils`: feathering, padding, b64, validate_resolution
- Tests de sustitución de placeholders en workflows
- Tests de integración con ComfyUI real (configurable por env var)
- Checklist manual en `apps/painter/scripts/manual_checklist.md` cubriendo cada herramienta y flujo
