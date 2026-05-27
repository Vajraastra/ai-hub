# Handoff — Sesión 24 (2026-05-27)

## Estado al cierre

Todo funcionando. Hub limpio, gate ComfyUI operativo, shutdown limpio implementado.

## Commits de esta sesión

```
cdc8d44  feat: shutdown limpio al cerrar hub — detiene todas las apps activas
b538290  feat(painter): gate ComfyUI — overlay bloqueante + install/start desde Painter
c756019  chore: limpieza general, ControlNet, fix Qt, remover trainers Anima
```

---

## Lo que se hizo

### 1. Limpieza general del proyecto
- Eliminado `hub/gui/` completo (Qt GUI, PySide6, pywebview — ya no se usa)
- `hub/state.py` — reubicado desde `hub/gui/state.py`; fix `HUB_DIR` que apuntaba al root del proyecto en lugar de `hub/`
- `hub_bridge.py`, `vault_routes.py` — imports actualizados a `from state import state`
- `run.sh` — removido Step 5 PySide6, pywebview, PYTHONPATH de .ui_venv; 4 pasos limpios
- `requirements.txt` — 7 dependencias: fastapi, uvicorn, websockets, requests, numpy, aiohttp, Pillow
- `app.py` — eliminado bloque webview; usa `webbrowser.open()` directamente
- Modelos eliminados (~40.7 GB): omnigen2-fp32-f16.gguf, pig_flux_vae_fp32-f16.gguf, qwen_2.5_vl-q6_k.gguf, qwen_image_edit_fp8_e4m3fn.safetensors, qwen_2.5_vl_7b_fp8_scaled.safetensors
- Conservado: `qwen_image_vae.safetensors` (254 MB) — lo usa Anima trainer (Citron)
- `app_registry.json` — removidas entradas `anima-trainer` y `anima-standalone-trainer`; Painter registrado como utility interna

### 2. Gate ComfyUI en Painter
Cuando Painter carga y ComfyUI no está instalado o no está corriendo, muestra un overlay bloqueante (full-screen, z-index:300) con mensaje y botón de acción. El usuario puede instalar o arrancar ComfyUI desde ahí sin salir del Painter.

**Archivos:**
- `painter_routes.py` — `GET /comfyui/hub-status`, `POST /comfyui/start`, `GET /comfyui/install` (SSE)
- `hub_bridge.py` — `launch(open_browser=False)` para arrancar ComfyUI sin abrir browser
- `painter.js` — `_showGate()`, `_hideGate()`, `_gateSpinner()`, `_gatePollUntilOnline()`, `_startComfyUI()`, `_installComfyUI()`; `backgroundInit()` usa gate en lugar de banner
- `painter.html` — CSS `#comfyui-gate` con `position:fixed; inset:0; background:rgba(15,0,35,.97); z-index:300`
- `locale.js` — keys `painter.comfy_gate_*` en ES y EN

**Bug encontrado y resuelto:**
- Las funciones del gate usaban `locale()` (no existe) en lugar de `t('painter.comfy_gate_...')`. Resultado: gate visible pero sin texto. Corregido a `t()` con namespace correcto.

### 3. Shutdown limpio
Al cerrar el hub (terminal, Ctrl+C, kill), todas las apps lanzadas por el hub se detienen automáticamente.

- `hub_bridge.py` — `stop_all_running(wait_secs=4)`: SIGINT a grupos de proceso → espera 4s → SIGKILL
- `app.py` — `atexit.register(_shutdown)` + handlers `SIGTERM`/`SIGHUP`
- `app.js` — `beforeunload → navigator.sendBeacon('/api/stop-all')` (best-effort para cierre de tab)

---

## Lecciones clave de esta sesión

- **`locale()` no existe en painter.js** — la función global es `t(key)` (window.t de locale.js). Todas las traducciones usan `t('painter.key_name')` con namespace de punto.
- **Keys en locale.js están bajo namespace `painter.`** — verificar siempre la estructura antes de llamar `t()`.
- **`atexit` + SIGTERM/SIGHUP** — patrón correcto para cleanup de subprocesos en Python. SIGKILL como fallback si SIGINT no termina el proceso en el tiempo límite.
- **`sendBeacon` dispara en refresh** — `beforeunload` no distingue cierre de refresh. Si esto resulta molesto (apps se detienen al recargar hub), cambiar a heartbeat periódico.

---

## Pendiente para próximas sesiones

### Alta prioridad
1. **Pruebas ControlNet** — implementado en sesión 23, nunca probado con modelos reales. Necesita un cn_model disponible y prueba de generación con imagen guía, preproc AIO_Preprocessor, strength.
2. **beforeunload en refresh** — evaluar si molesta que las apps se detengan al recargar la página del hub. Si sí → implementar heartbeat (frontend ping cada 15s, backend mata apps si no hay ping por 30s).

### Media prioridad
3. **ComfyUI save workflows** — bug conocido: workflows guardados en ComfyUI desaparecen al recargar. Investigar `apps/comfyui/user/default/workflows/` — permisos, ruta real usada por ComfyUI.
4. **Auditoría fresh install** — verificar run.sh en máquina limpia.

---

## Arquitectura actual (resumen)

```
hub-webui/
├── app.py              — FastAPI; atexit/_shutdown on exit
├── hub_bridge.py       — HubBridge; stop_all_running(), launch(open_browser=False)
├── painter_routes.py   — /comfyui/hub-status, /comfyui/start, /comfyui/install SSE
├── static/
│   ├── app.js          — beforeunload → sendBeacon /api/stop-all
│   ├── painter.js      — _showGate/_hideGate/gate functions; t('painter.*') para locale
│   ├── painter.html    — #comfyui-gate overlay CSS
│   └── locale.js       — painter.comfy_gate_* keys en ES y EN
hub/
├── state.py            — HubState; HUB_DIR = dirname(abspath(__file__))
└── config/
    └── app_registry.json — sin anima-trainer/standalone; painter como utility interna
apps/
├── comfyui/
├── sd-webui-forge-neo/
├── taggui/
├── facefusion/
├── dataset-refiner/
└── painter/
    └── core/
        ├── comfy_client.py   — inject_controlnet() implementado (sin probar con modelos)
        ├── session.py
        ├── models.py
        ├── image_utils.py
        ├── setup.py
        └── tag_engine.py
```

## Comandos útiles

```bash
# Levantar hub
cd /run/media/system/Kilaya/ai-hub && ./run.sh

# Ver procesos corriendo
ss -tlnp | grep -E '8188|9753|7875'

# Estado git
git log --oneline -6
```
