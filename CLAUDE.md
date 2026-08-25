# AI Hub — instrucciones del proyecto

Launcher autocontenido para herramientas de IA generativa: instala, gestiona y sirve un conjunto
de apps externas y de módulos propios desde una sola interfaz. **Windows-first**, sin retorno a
Linux. Todo debe funcionar sin pasos manuales externos (nada de WSL2, exclusiones de antivirus o
instalaciones de administrador): si el cliente no puede reproducirlo, no entra al hub.

## Arrancar y detener

```bash
hub-webui/.venv/Scripts/python.exe app.py     # hub → :9753
apps/comfyui/venv/Scripts/python.exe main.py  # backend de generación → :8188
```

- El objeto FastAPI se llama **`api`**, no `app` — lanzar con `uvicorn hub-webui.app:app` falla.
- `run.bat` en la raíz es el launcher del usuario final (bootstrap completo desde cero); para
  desarrollo se arranca a mano como arriba.
- **El hub cachea los módulos Python en memoria al arrancar**: editar un `.py` en disco no aplica
  hasta reiniciar el proceso. Antes de dudar de un fix, reiniciar.
- Apagar todo proceso que se haya arrancado antes de cerrar la sesión.

## Estructura

- `apps/` — un directorio por app. **Nuestros** (se versionan): `minimax_h3`, `painter`,
  `ideogram`, `faceswap`, `forge_fusion`, `model_vault`. **De terceros** (gitignored, no editar
  salvo parche documentado en el propio archivo): `comfyui`, `sd-webui-forge-neo`, `ai-toolkit`,
  `anima-standalone-trainer`.
- `hub-webui/` — servidor FastAPI + frontend. Un `<modulo>_routes.py` y un `static/<modulo>.{html,js}`
  por módulo interno.
- Los modelos viven fuera del repo, en `E:/Models/`.

## Módulos internos — patrón fijo

Un módulo nuevo se **calca** de `apps/ideogram/`, no se inventa: `core/comfy_client.py` (encolar,
WebSocket, `build_workflow` por placeholders `{{key}}`), `core/pipeline.py` (dataclass de params +
`generate()` por fases con callback), historial plano `<run_id>.<ext>` + `<run_id>.json`, rutas en
`hub-webui/<modulo>_routes.py` con `/generate` → `/jobs/{id}`. Sin entrada en `app_registry.json`
(eso es solo para apps instalables aparte).

**ComfyUI es compartido y ejecuta un job a la vez**: cualquier módulo que encole debe respetar el
lock de job activo. Las trampas conocidas de su cliente (cache hit, nombres de LoRA, inputs
dinámicos, timeouts) están en `BITACORA.md` sección ComfyUI — leerla antes de escribir un cliente
nuevo, no después.

## Qué se versiona

- `apps/*` está gitignored salvo los módulos propios (lista explícita en `.gitignore`).
- `TASKS.md`, `BITACORA.md` y `HANDOFF.md` **están gitignored**: git no los respalda, hacer copia
  antes de reescribirlos.
- **Nunca versionar fotos de personas** (`data/reference_samples/` y similares), ni pesos de
  modelos, ni historiales de generación.
- Backup de `requirements.txt` antes de añadir una dependencia; borrar el `.bak` antes de
  commitear.

## Documentación

`TASKS.md` = qué falta · `BITACORA.md` = por qué es así y qué ya se descartó (por módulo, se busca,
no se lee entera) · `HANDOFF.md` = dónde quedamos. Regla completa en C2 del CLAUDE.md global.
