# Handoff Sesión 27 → 28

## Tema de la sesión: Mudanza Linux → Windows 11 (Fases 1 y 2)

El proyecto se migró del entorno Linux (Bazzite/distrobox, disco externo) a **Windows 11 Pro**. Esta sesión dejó el **hub arrancando limpio en Windows** y la **capa de links reparada**. Falta Fase 3 (recrear venvs de apps).

---

## Entorno Windows (memorizar)

| Antes (Linux) | Ahora (Windows) |
|---|---|
| `/run/media/system/Kilaya/ai-hub` | `E:\githubs\ai-hub` |
| `/run/media/system/Kilaya/Models/` | `E:\Models` (árbol ComfyUI intacto) |
| disco externo "Kilaya" | montado como **E:** |
| RTX 5060 Ti Blackwell | **igual** (sm_120) |

- `uv 0.11.24` nativo en PATH. Python del sistema ausente (ideal: uv usa managed).
- Discos: C: (Satori), D: (Sunyata), E: (Kilaya, repo+modelos), G: (Storage).

## Decisiones del think tank (firmes)

1. **Linux congelado**: los `.sh` quedan pero NO se mantienen. Windows = única fuente de verdad.
2. **Junctions sin Modo Desarrollador** para redirigir modelos (decisión firme del usuario).
3. **Node.js portable** dentro del repo (`hub\tools\win\node`), no instalación de sistema.
4. **Trainer (anima-standalone) y captioner local APLAZADOS** hasta hub funcional en Windows.

---

## Fase 1 — Arranque del hub ✓ (validado)

**`run.bat` raíz creado** (única fuente de verdad Windows). Porta `run.sh` actual → arquitectura **WebUI FastAPI** (`hub-webui/app.py`), NO la vieja GUI PySide6 (muerta; `hub/run.bat` es chatarra). 5 pasos: GPU → uv → Node portable → venv → lanzar. Binarios Windows en `hub\tools\win\` (los `hub\tools\` son ELF Linux, NO tocar).

**Bugs de portabilidad corregidos:**
- `app.py`: `signal.SIGHUP` no existe en Windows → filtrado con `hasattr` (el `try/except` no protegía porque fallaba al construir la tupla).
- `hub_config.json`: paths Linux → `E:/Models/`, `E:/githubs/ai-hub/outputs`, `os: windows`.
- venv Linux residual bloqueaba `uv venv` → `--clear`.
- `.bat`: `^`-continuación dentro de `if()` rompe el parser; `echo` con `()` literal cierra bloques; detección Node frágil → path fijo; **`wmic` removido en Win11 24H2+** → `Get-Date` de PowerShell.

**Validado:** los 5 pasos corren, Node v22.17.0 se descarga, WebUI sirve **HTTP 200** en `127.0.0.1:9753`.

⚠️ **El usuario aún no hizo el primer doble-clic manual a `run.bat`** — conviene que confirme el flujo de terminal (logs en vivo + `pause`) él mismo.

## Fase 2 — Links sin privilegios ✓ (validado)

**Problema:** apps de terceros (no modificables) escanean su propio `models/`. El hub creaba `os.symlink`, que en Windows rompe (privilegios + `islink` no ve junctions + `os.remove` falla en reparse points).

**`storage_manager.py`** — helpers cross-platform nuevos (junctions de **directorio**):
- `_create_dir_link` → `mklink /J` (sin privilegios) / `os.symlink` en POSIX
- `_is_dir_link` → detecta junction vía `FILE_ATTRIBUTE_REPARSE_POINT`
- `_link_points_to` → `os.path.samefile`
- `_remove_dir_link` → `os.rmdir` sobre el reparse point (no borra el target)
Caso real: `ultralytics → E:\Models\ultralytics` (YOLO/ADetailer).

**`model_organizer.py`** — `_link_model_file` para **archivos** (Krita priority): hardlink (`os.link`, sin privilegios, mismo volumen) en Windows, symlink relativo en POSIX.

**Validado:** 12/12 tests junction + 2/2 hardlink + `py_compile` OK. Clave confirmada: **borrar un link nunca destruye el target**.

---

## Fase 3 — PRÓXIMA SESIÓN (el usuario delega la instalación)

El usuario pidió que **la instalación la maneje el agente** para detectar problemas con ojo clínico.

**Plan:**
1. **App piloto simple** (taggui o dataset-refiner) end-to-end: recrear venv con `uv venv --clear`, instalar deps, lanzar. Validar el flujo de `app_installer` en Windows.
2. ⚠️ **CRÍTICO — wheels PyTorch Blackwell (sm_120) para `win_amd64`.** `hub_config.json` pide `cu130` / torch 2.10.0. Confirmar que esos wheels existen para Windows (en Linux funcionaban; PyTorch a veces va por detrás en Windows). Es el mayor riesgo bloqueante.
3. Replicar en las pesadas: comfyui, sd-webui-forge-neo, facefusion, ai-toolkit.

**Estado de los 7 venvs:** todos son ELF Linux (`apps/*/venv` con `bin/python`) — inservibles, recrear todos. Se reconocen por `bin/python` + `lib64`.

**Apps en disco** (`apps/`): ai-toolkit, anima-standalone-trainer, comfyui, dataset-refiner, facefusion, sd-webui-forge-neo, taggui.

---

## Estado git al cerrar

Pendiente de **commit + push** (lo hará el usuario tras este handoff). Archivos tocados esta sesión:
- `run.bat` (nuevo, raíz)
- `hub-webui/app.py` (fix SIGHUP)
- `hub/hub_config.json` (paths Windows)
- `hub/modules/storage_manager.py` (helpers junction)
- `hub/modules/model_organizer.py` (hardlink Krita)
- `hub/config/app_registry.json` (entrada anima-standalone-trainer, de sesiones previas)
- `BITACORA.md`, `TASKS.md`, este handoff
- Sin commitear: `hub\tools\win\node\` (Node portable, debería ir en .gitignore)

⚠️ Antes del commit: verificar que `hub\tools\win\` y los venvs estén en `.gitignore` (no commitear binarios).

## Bugs conocidos activos (heredados)

| Bug | Estado | Prioridad |
|-----|--------|-----------|
| Wheels PyTorch Blackwell/win sin confirmar | Riesgo Fase 3 | **Alta** |
| ControlNet sin probar con modelos reales | Implementado (s23) | Media |
| ComfyUI workflows guardados desaparecen | Conocido | Baja |
| beforeunload dispara en refresh | Conocido | Baja |
