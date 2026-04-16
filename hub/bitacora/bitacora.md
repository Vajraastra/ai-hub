# AI Hub — Bitácora de Producción

## 2026-02-24 — Sesión Inicial

### Ambiente Detectado
- **Host**: Bazzite (Fedora inmutable)
- **Container**: Distrobox `ubuntu-ai` (Ubuntu 24.04)
- **GPU**: NVIDIA GeForce RTX 5060 Ti (16GB VRAM, Blackwell)
- **Driver**: 590.48.01 — CUDA 13.1
- **Python**: 3.12.3 (container)
- **Workspace**: `/run/media/system/Kilaya/ai-hub/` (disco externo, 1739.5GB libres)

### Decisiones de Diseño
- CUDA: NO se instala toolkit. PyTorch wheels (+cu130) traen su propio runtime
- Hub: zero dependencias externas, solo Python stdlib
- Config: JSON (no YAML, evita dependencias)
- Cada app: venv aislado con Python propio via `uv`
- CUDA enforcement: via env vars (TORCH_INDEX_URL, TORCH_COMMAND)

### Módulos Implementados
1. `gpu_detector.py` — detección GPU via nvidia-smi + plataforma
2. `cuda_guardian.py` — mapeo CUDA→wheel + verificación post-install
3. `logger.py` — bitácora timestamped
4. `storage_manager.py` — gestión de paths de modelos/outputs
5. `app_installer.py` — instalación de apps (clone + venv + CUDA)
6. `app_launcher.py` — lanzamiento con env vars + model paths
7. `app_updater.py` — updates con backup + rollback
8. `hub.py` — CLI principal + first-run wizard
9. `run.sh` / `run.bat` — launchers multiplataforma

### Error Encontrado
- **Problema**: Comandos Python se colgaban después de ejecutar múltiples tests
- **Causa**: Procesos zombie acumulados en el entorno de ejecución de comandos
- **Solución**: `pkill -9 -f "python3.*ai-hub"` para limpiar zombies
- **Nota**: No era un bug del código. Después de limpiar, todos los tests pasaron al 100%

### Tests
- Todos los módulos compilan correctamente
- Integration test: GPU, CUDA, Logger, Disk, Registry → ALL PASSED
- Pendiente: test interactivo del first-run wizard por el usuario

---

## 2026-02-25/26 — Optimización de Dependencias y ai-toolkit UI

### Objetivos
- Optimizar dependencias compartidas entre apps
- Resolver mismatches de CUDA entre host (RTX 5060, CUDA 13.1) y apps
- Configurar ai-toolkit con modo gráfico (UI)

### Cambios en forge-neo
- `install_deps: false` — forge-neo maneja sus propias dependencias via `launch.py`
- Python: 3.13

### Cambios en ai-toolkit

#### Python y CUDA
- Python: 3.12 → 3.11 (PoC 1) → **3.13** (PoC 2, final) — alineado con forge-neo
- torch: 2.7.0+cu126 (hardcoded) → **2.10.0+cu130** (dinámico via cuda_guardian)
- `pre_install_commands`: ahora usa templates `{torch_version}`, `{torchvision_version}`, `{cuda_tag}`

#### pip_overrides (nuevo sistema)
- `scipy>=1.14.1` — la pinneada 1.12.0 no tiene wheel para Python 3.13
- `numpy>=2.1.0` — la pinneada 1.26.4 no tiene wheel para Python 3.13
- `gradio<6.0` — flux_train_ui.py usa API de Gradio 5.x (`show_share_button` removido en 6.x)
- `app_installer.py`: genera `.pip_overrides.txt` y pasa `--override` a `uv pip install`

#### Lanzamiento UI
- **Descubrimiento**: `flux_train_ui.py` (Gradio) es solo para Flux LoRAs, NO es la UI principal
- **UI real**: Next.js app en `ui/`, lanzada via `npm run build_and_start`, puerto **8675**
- `launch_type`: python → **npm**
- `launch_script`: run.py → **build_and_start**
- `launch_subdir`: **ui** (nuevo campo)
- `default_port`: null → **8675**

#### Output paths
- Decisión: **dejar defaults** — ai-toolkit tiene Settings internas (TRAINING_FOLDER, DATASETS_FOLDER)
- Los LoRAs entrenados quedan en `<ai-toolkit>/output/` (más seguro para el usuario)

### Cambios en app_launcher.py
- Nuevo handler `npm`: resuelve npm con 4 niveles de fallback (which → AI_HUB_NODE_DIR → tools/node/bin → /usr/bin)
- Soporte `launch_subdir`: cwd del subprocess se ajusta al subdirectorio
- venv `bin/` agregado a PATH para que el worker de Next.js acceda a Python+torch
- Node.js portable `bin/` agregado a PATH del subprocess

### Cambios en run.sh
- **Nuevo step 4b**: Provisión automática de Node.js
  - Busca node/npm del sistema primero
  - Si no hay → busca en `tools/node/` (ya descargado)
  - Si no hay → descarga Node.js 22 LTS (~30MB) a `tools/node/`
  - Exporta `AI_HUB_NODE_DIR` y agrega a PATH

### Errores Encontrados y Soluciones

| Error | Causa | Solución |
|---|---|---|
| scipy/numpy build failure (3.13) | No hay wheels pre-built para Python 3.13 con versiones pinneadas | `pip_overrides` fuerza versiones con wheels |
| Gradio `show_share_button` error | Gradio 6.x removió ese parámetro | `gradio<6.0` en pip_overrides |
| `npm: command not found` | Bazzite host no tiene Node.js | Provisión portable de Node.js en tools/ |
| `npm: No such file or directory` | PID lanzado via run.sh no hereda PATH completo | `shutil.which` + fallback a `AI_HUB_NODE_DIR` + tools/node/bin |
| Scripts Python colgados en distrobox | Inline python -c no funciona en distrobox | Usar archivos .py en vez de comandos inline |

### PoC Results
1. **PoC Python 3.11 + cu130**: ✅ torch 2.10.0+cu130, CUDA OK, GPU tensor OK
2. **PoC Python 3.13 + cu130**: ✅ torch 2.10.0+cu130, CUDA OK, GPU tensor OK

### Estado Final

| App | Python | torch | CUDA | Launch | Puerto |
|---|---|---|---|---|---|
| forge-neo | 3.13 | (auto en 1er launch) | cu130 | shell (webui.sh) | 7860 |
| ai-toolkit | 3.13 | 2.10.0+cu130 | cu130 | npm (Next.js UI) | 8675 |

### Pendientes
- [ ] Pruebas manuales de usuario: confirmar que ambas apps funcionan end-to-end
- [ ] Implementar UI del hub
- [ ] Integrar ComfyUI (app compleja, arquitectura diferente)

---

## 2026-02-26 — Migración a PySide6 y Corrección de Bugs

### Errores Encontrados y Soluciones

| Error | Causa | Solución |
|---|---|---|
| `ImportError: cannot import name 'build_model_args'` en GUI | El CLI usa `build_app_model_args` de `storage_manager.py`, pero la UI de Flet estaba llamando a una función inexistente o vieja. Además Flet intentaba importar `get_central_outputs_dir` que no existe como tal (sale del config). | Se actualizó `hub/gui/workers.py` para leer `outputs_dir` directo del `config.json` y usar `build_app_model_args(app_cfg, models_dir)`. |
| Aplicación congelada sin imprimir en GUI ("Press Enter to Continue...") | El subprocess de Python captaba `stdout` pero no `stdin`, causando que los prompts interactivos se bloquearan infinitamente al no tener una terminalTTY real adjunta. | Se desactivó `capture_output=True` en `workers.py`. Las aplicaciones (como `forge-neo`) heredan directamente la entrada y salida (`stdin`/`stdout`) de la terminal nativa donde inicia el AI Hub. Esto permite al usuario ver logs completos nativamente y confirmar "Enter" sin intermediarios. |
| Ventana negra vacía de `LogViewerWindow` | Al desactivar `capture_output`, el visualizador de logs de la GUI (`LogViewerWindow`) dejó de recibir datos, volviéndose inútil y mostrándose vacío al presionar "Lanzar". | Se eliminó por completo `hub/gui/components/log_viewer.py`. Se actualizaron `app_card.py` (eliminado botón "Ver Consola") y `main.py` para no llamar a la ventana vacía. Menos código muerto. |

---

## 2026-03-04 — Reparación y Completado de GUI (PySide6)

### Causa raíz de botones silenciosos
- `update_app()` y `uninstall_app()` en `workers.py` eran `pass`.

### Cambios implementados

| Archivo | Cambio |
|---|---|
| `gui/state.py` | `_verify_installed_apps()` — limpia entradas stale sin directorio en disco. `busy_apps` dict. Prioridad de estado: busy > running > installed > not_installed. |
| `gui/workers.py` | `update_app()` conecta `app_updater.py`. `uninstall_app()` hace `shutil.rmtree()` + `save_state()`. Single-app safeguard: sólo 1 app corre a la vez. `_stop_running_app()` SIGINT→SIGKILL. |
| `gui/components/app_card.py` | Estados `installing/updating/uninstalling` con botón deshabilitado para prevenir doble-acción. |
| `gui/main.py` | `QMessageBox` de confirmación para update (question) y uninstall (warning). Default: No. |

### Verificación
- `py_compile` en los 4 archivos: ✅ sin errores de sintaxis.
- Pruebas de interfaz manuales pendientes.


---

## 2026-03-04 — Fase 1: TaggerGUI integrado en el Hub

### Contexto
Integración de TaggerGUI (`jhc13/taggui`) como app Tipo A (AppCard + venv aislado) en el Hub.

### Problema detectado: conflicto de CUDA
- TaggerGUI hardcodea `torch==2.8.0+cu128` en `requirements.txt`
- Nuestro sistema usa `cu130` (RTX 5060 Ti, Blackwell)
- También usa `flash-attn==2.8.3` compilado para cu128 — incompatible

### Solución implementada

| Archivo | Cambio |
|---|---|
| `config/app_registry.json` | Nueva entrada `"taggui"` con `pre_install_commands` (torch cu130 primero), `pip_overrides` (torch≥cu130), `pip_exclude_packages: ["flash-attn"]` |
| `modules/app_installer.py` | `pip_exclude_packages`: filtra líneas de requirements.txt (paquetes + URLs de wheels). Template vars en `pip_overrides` |
| `gui/components/app_card.py` | Badge `🖥 Desktop` / `🌐 WebUI` en el título del card |

### Resultado
- ✅ TaggerGUI instalado y lanzando sin errores con GPU (RTX 5060 Ti cu130)
- ✅ Velocidad de captioning mejorada (torch 2.10 SDPA nativo > flash-attn cu128 en Blackwell)

### Warning cosmético detectado
```
endResetModel called on ProxyImageListModel without calling beginResetModel first
```
**Causa**: Bug interno de TaggerGUI en `models/image_list_model.py`.
**Impacto**: Ninguno — la app funciona correctamente. Se corregirá si se hace el fork futuro.

### Decisión de arquitectura
- Fork TaggerGUI → **TODO futuro** al finalizar el proyecto
- Tagger propio desde cero → Descartado por ahora (3+ meses para igualar lo existente)


---

## [2026-03-05] Implementación de Model Vault (Standalone)
- **Objetivo**: Crear un organizador de modelos independiente del Hub para evitar inestabilidad.
- **Implementación**:
    - Ubicación: `apps/model_vault/`.
    - UI: PySide6 con vista de tarjetas y escaneo en hilo separado.
    - Core:
        - `Architecture Guard`: Lógica para filtrar actualizaciones por arquitectura base (SDXL, Flux, etc.).
        - `SQLite Cache`: Base de datos local para carga instantánea.
        - `Civitai Sync`: Automatización de descarga de previews y metadatos (.cm-info.json).
- **Integración**: 
    - Botón "Gestionar Modelos" añadido al Top Bar.
    - Nueva sección `utilities` en `app_registry.json` para herramientas pre-instaladas.
    - `AppCard` personalizado para herramientas internas (solo botón Lanzar, sin Uninstall/Update).
- **Categorización y Pulido**:
    - Escalado de cartas a 240x360 (thumbnails de 240px).
    - Identificación visual de arquitecturas (SDXL, Flux, Pony, SD 1.5) con badges de colores.
    - Sidebar de categorías (Checkpoint, LORA, LoCon, etc.) para filtrado rápido.
    - Búsqueda mejorada por nombre, arquitectura o tipo.
    - **Corrección**: Solucionado crash `AttributeError` al limpiar la parilla y mejorado el rendimiento del scroll.
    - **Refinamiento Estético**: Badges de arquitectura ajustados a una paleta discreta (Indigo, Teal, SlateBlue) para máximo contraste con texto blanco.
    - **Filtros Normalizados**: Soporte mejorado para detectar categorías de Civitai (Checkpoint vs Embedding).
- **Optimización de Rendimiento y Rutas**:
    - **Multi-Root Scanning**: Implementado soporte para múltiples rutas de modelos (Lora, StableDiffusion, checkpoints) para compatibilidad con ComfyUI.
    - **Async Batch Loading**: La UI ahora carga las tarjetas en lotes de 12 cada 10ms usando un `QTimer`. Esto elimina el "freezing" y permite un scroll fluido incluso con +1300 modelos.
    - **Seguridad**: Detención automática de hilos de carga al cambiar de categoría para evitar solapamientos.
- **Vista Detallada y Notas**:
    - **ModelDetailsDialog**: Implementada ventana emergente con doble click que muestra descripción rica (HTML), metadatos técnicos y palabras clave.
    - **User Notes**: Añadido sistema de persistencia para apuntes del usuario en SQLite, permitiendo guardar consejos de generación por cada modelo.
    - **Acciones Rápidas**: Botones integrados para abrir la ubicación del archivo en el explorador y ver la ficha original en Civitai.
    - **Paleta Oficial**: Aplicados colores corporativos (#1F004B, #600DB5, #51CCDC, #54EFEA, #EC00F0) para una integración visual perfecta y máxima legibilidad.
    - **Prompt Builder**: Añadido un generador de prompts dinámico; al clickear cada trigger chip, se añade a una lista separada por comas en un cuadro de edición, permitiendo copiar el prompt completo al portapapeles.
    ## [2026-03-06] - Model Vault Hub Integration
- **Integración como Tab Nativa**: Refactorizado `ModelVaultMainWindow` a `ModelVaultWidget` para permitir su incrustación directa como una pestaña dentro de la GUI principal del AI Hub. Esto permite que el Vault corra en paralelo con otras aplicaciones de forma fluida.
- **Sincronización Manual**: Eliminado el auto-indexado al iniciar el Hub. Ahora el Vault carga instantáneamente el último estado desde la base de datos de caché. Se ha añadido un botón "🔄 Actualizar / Sync" para que el usuario decida cuándo realizar el escaneo de archivos.
    - **Bugfix: Missing UIWorkerSignals**: Corregido un `NameError` que impedía iniciar el Vault de forma independiente tras la refactorización.
    - **Limpieza de Interfaz**: Eliminada la tarjeta redundante del Model Vault de la lista de aplicaciones; ahora se accede exclusivamente desde el botón principal de la barra superior.
    - **Estilo de Botón**: Sincronizado el botón "Gestionar Modelos" con la paleta cyan/neon de la GUI para una estética uniforme.
- **Búsqueda de Actualizaciones**: Implementado el botón "🔄 Buscar Actualización" en la ficha de detalles. Compara la versión local con la última disponible en Civitai para la misma arquitectura base.
- **Ajustes de Civitai API**: Añadida una nueva sección en la tab de configuración ("Hub Settings") para ingresar la API Key del usuario. Esto permitirá futuras integraciones con contenido privado o restringido.
- **Parrilla Dinámica Optimizada**:
    - **Cálculo de Columnas**: Las columnas se recalculan en tiempo real para maximizar la densidad de modelos en pantalla.
    - **Tarjetas Fijas de Alta Densidad (200px)**: Se ha reducido el tamaño para aumentar la visibilidad de múltiples modelos simultáneamente.
    - **Relación de Aspecto Vertical (2:3)**: Optimizado para thumbnails de Civitai, permitiendo ver más contenido con menos scroll.
    - **Alineación Centrada**: El bloque de la parrilla se centra automáticamente, manteniendo una estética equilibrada en cualquier resolución.
- **Corrección de Meta-Tags**: Identificado y corregido un fallo en el flujo de persistencia donde las etiquetas de Civitai (Tags) se extraían pero no se guardaban en la base de datos local debido a una omisión en el comando SQL `UPSERT`. Ahora las etiquetas son persistentes y permiten la navegación cruzada entre el Vault y Civitai.
- **Estado de Civitai API (Bloqueo)**: Durante la verificación final, los servidores de Civitai experimentaron inestabilidad (Errores 500, 503, 504). El código de robustez fue implementado para evitar crashes, pero la sincronización completa de etiquetas queda pendiente para cuando el servicio se estabilice.

---

## [2026-03-07] — Navegación por Tags y Paginación del CivitaiBrowser

### Bugfix
- **`civitai_browser.py:250`**: `NameError` — `t_lbl` se referenciaba sin haber sido definido en `OnlineModelDetailsDialog`. Corregido creando el `QLabel` antes de usarlo.

### Tags en ModelCard (grid del Vault)
- `ModelCard` ahora muestra hasta 3 chips de tags (`custom_tags`) al pie de cada tarjeta del grid.
- Chips estilo cyber-subtle (9px, borde teal, fondo transparente).
- Clic en chip emite `tag_clicked(str)` → abre `CivitaiExplorerDialog` filtrado por esa tag.
- `ModelVaultWidget._add_cards_batch()` conecta `card.tag_clicked` a `_open_tag_browser`.

### Tags en ModelDetailsDialog (tarjeta de detalle)
- Nueva sección **"Tags de Civitai"** posicionada después del metadata strip (prominente, arriba de descripción).
- Si `custom_tags` está en DB: chips aparecen instantáneamente.
- Si `custom_tags` está vacío pero el modelo tiene `model_id`: fetch async desde Civitai a los 400ms. Resultado mostrado via signal (`_tags_fetched`) para thread-safety.
- Sección **"Mis Etiquetas"** (antes "Etiquetas Personalizadas") renombrada y simplificada: solo campo editable para organización personal, sin duplicar chips de Civitai.

### Paginación en CivitaiExplorerDialog
- El explorador online siempre devolvía exactamente 50 resultados (limit hardcodeado). Ahora tiene paginación cursor-based.
- Botón **"Cargar más (N mostrados)"** aparece bajo el grid cuando la API devuelve `next_cursor`.
- Clic: fetch de la siguiente página con el cursor almacenado. Los nuevos modelos se **acumulan** en el grid.
- El botón se deshabilita durante la carga y desaparece al llegar al fin de resultados.
- En caso de error, el botón se re-habilita para reintentar.

### Archivos modificados
| Archivo | Cambios |
|---|---|
| `apps/model_vault/ui/civitai_browser.py` | Fix `t_lbl`, botón "Cargar más", `_load_more()`, `_show_results()` actualizado |
| `apps/model_vault/ui/components.py` | `ModelCard` con tag chips, `ModelDetailsDialog` con sección Tags de Civitai, fetch async, `_fetch_tags_async()`, `_populate_civitai_tag_chips()` |
| `apps/model_vault/main.py` | Conectar `card.tag_clicked` en `_add_cards_batch()` |

### Estado al cierre de sesión
- ~~Implementar mini-aplicación **MergeKit** integrada en el Hub.~~ ✅ Implementado


---

## 2026-03-25 — Fix: Pérdida de paths de modelos tras git pull

### Problema reportado
Al hacer update de ComfyUI via `git pull`, los symlinks dentro de `apps/comfyui/models/` apuntando a `/run/media/system/Kilaya/Models/` se destruían. Git sobreescribía los symlinks con los directorios vacíos trackeados en el repo.

### Causa raíz
El hub ya usa el enfoque correcto (YAML para ComfyUI, COMMANDLINE_ARGS para Forge), NO symlinks. Los symlinks destruidos eran de una configuración manual previa. Sin embargo, se detectaron dos bugs adicionales:

1. **Mappings incorrectos en `app_registry.json`**: Los campos `"upscalers": "upscale_models"` y `"diffusion_models": "unet"` apuntaban a carpetas inexistentes. ComfyUI usa `upscale_models` como clave interna, y la carpeta del hub se llama `upscalers`. Para diffusion models, la carpeta del hub es `diffusion_models` (no `unet`).

2. **Post-update sin refresh de modelo paths**: Tras un `git pull` exitoso, ni `hub.py` ni `workers.py` regeneraban el `extra_model_paths.yaml` de ComfyUI ni la config de modelos de otras apps. Solo se regeneraba al lanzar la app.

### Solución implementada

| Archivo | Cambio |
|---|---|
| `config/app_registry.json` | Fix mappings ComfyUI: `"upscale_models": "upscalers"` y `"diffusion_models": "diffusion_models"` |
| `hub/hub.py` | `_do_update()`: llama `build_app_model_args()` post-update exitoso |
| `hub/gui/workers.py` | `_update_thread()`: llama `build_app_model_args()` post-update exitoso |
| `apps/comfyui/extra_model_paths.yaml` | Regenerado manualmente con los mappings corregidos |

### YAML resultante
```yaml
aihub:
    base_path: /run/media/system/Kilaya/Models
    checkpoints: checkpoints
    loras: loras
    vae: vae
    controlnet: controlnet
    embeddings: embeddings
    upscale_models: upscalers
    clip_vision: clip_vision
    ipadapter: ipadapter
    diffusion_models: diffusion_models
```

### Nota sobre symlinks
No se deben crear symlinks manuales dentro de `apps/[app]/models/`. El hub gestiona los paths a través de YAML (ComfyUI) y COMMANDLINE_ARGS (Forge). Estos mecanismos sobreviven los `git pull` porque no son archivos trackeados por git.

---

## [2026-03-25] — Migración a Host (Bazzite) + Smoke Test

### Cambio de ambiente
- **Anterior**: Distrobox `ubuntu-ai` (Ubuntu 24.04)
- **Actual**: Host Bazzite directamente
- **Venv**: `hub/.ui_venv/` — Python 3.13.12 via `uv`, sin sistema site-packages
- El proyecto es ahora completamente portable desde el host sin depender de distrobox.

### Smoke Test — PASSED
| Módulo | Resultado |
|---|---|
| GPU Detector | ✅ RTX 5060 Ti, CUDA 13.2 |
| CUDA Guardian | ✅ cu130, torch 2.10.0 |
| Logger | ✅ OK |
| Disk | ✅ 1145.7GB libres |
| Registry | ✅ 5 apps |
| GUI / AppCard | ✅ Callback install correcto |

---

## [2026-03-25] — Auditoría GUI + Plan de Refactoring

### Contexto
La GUI fue construida de forma incremental (on-the-fly) en sesiones anteriores. Se realizó una auditoría completa de todos los archivos (`main.py`, `workers.py`, `state.py`, `app_card.py`, `hub_settings.py`, `app_settings_dialog.py`, `app_terminal.py`, `event_log_viewer.py`) para identificar bugs y planificar un refactoring ordenado.

### Bugs Críticos — Causa Raíz de No-Reactividad

#### Bug #1 — `refresh_apps_list` destruye todos los widgets cada segundo
**Archivo**: `hub/gui/main.py:302`
**Causa**: El método borra todos los `AppCard` con `deleteLater()` y los recrea desde cero. Esto ocurre:
- Cada 1 segundo via `QTimer`
- Cada vez que el signal `app_state_changed` dispara
Consecuencia: el usuario hace click en un botón → el timer destruye el widget antes de que el evento de click termine de procesarse → el botón no responde.

#### Bug #2 — `stop_app()` no actualiza el estado visual inmediatamente
**Archivo**: `hub/gui/workers.py:267`
**Causa**: `stop_app()` lanza un thread pero no marca `busy_apps[app_id] = "stopping"` antes de hacerlo. El botón "Detener" no muestra ningún cambio visual hasta segundos después.

### Bugs Importantes

| # | Archivo | Bug |
|---|---|---|
| 3 | `workers.py:230` | Race condition: `_read_stdout` y `proc.wait()` en threads paralelos sin sincronización |
| 4 | `app_card.py` | Sin guard anti-doble-click — posible lanzar 2 threads de la misma operación |
| 5 | `state.py` | `running_apps` puede tener entradas stale que bloquean todos los botones Lanzar |
| 6 | `app_card.py:169` | Estado busy muestra "En progreso..." genérico — no informa la operación exacta |

### Problemas de Calidad de Código

| # | Archivo | Problema |
|---|---|---|
| 7 | `hub_settings.py` | Estilo `QGroupBox` duplicado 3 veces inline (~30 líneas repetidas) |
| 8 | `app_card.py` | `AppCard` no tiene `update_status()` — requiere recrear widget entero para cambiar un label |
| 9 | `main.py` | Stylesheet global (~130 líneas) mezclado en el archivo principal |
| 10 | `workers.py` | `_make_console_logger()` instancia una clase anónima nueva en cada llamada |

### Plan de Refactoring Aprobado

#### Fase 1 — Fix crítico de reactividad
- `AppCard` gana método `update_status(status, any_running)` — reconstruye solo el panel de botones
- `refresh_apps_list` mantiene `self._app_cards: dict[str, AppCard]` — actualiza estado, no destruye
- `stop_app()` marca `busy_apps["stopping"]` antes de lanzar el thread
- Polling timer solo actualiza estado, no hace rebuild de widgets

#### Fase 2 — Correcciones de workers
- Guard anti-doble-click en acciones
- Fix race `_read_stdout` / `proc.wait()`
- Limpiar stale entries de `running_apps` con `proc.poll()`

#### Fase 3 — Calidad y mantenibilidad
- Extraer stylesheet global a `hub/gui/theme.py`
- Extraer estilo GroupBox a constante en `hub_settings.py`
- `_make_console_logger()` a clase module-level en `workers.py`

#### Fase 4 — UX visual (post-estabilización)
- Estado busy con operación exacta: "Instalando...", "Actualizando...", "Deteniendo..."
- Progress bar indeterminada durante install/update
- Mostrar puerto activo cuando app está corriendo

### Implementación completada

#### Fase 1 — Fix crítico de reactividad ✅
| Archivo | Cambio |
|---|---|
| `gui/components/app_card.py` | `AppCard` es ahora persistente. Nuevo método `update_status(status, any_running, running_proc)` reconstruye solo el panel de botones sin destruir el widget |
| `gui/main.py` | `refresh_apps_list` mantiene `self._app_cards: dict` — primera vez crea, siguientes veces llama `update_status()`. Limpia stale entries de `running_apps` con `proc.poll()` |
| `gui/workers.py` | `stop_app()` marca `busy_apps["stopping"]` antes del thread → feedback visual inmediato |
| `gui/workers.py` | Fix race condition: `threading.Event` sincroniza `_read_stdout` con `proc.wait()` |

#### Fase 2 — Guards anti-doble-click ✅
| Archivo | Cambio |
|---|---|
| `gui/main.py` | Guard global en `on_app_action`: ignora si `app_id in state.busy_apps` |
| `gui/main.py` | `launch` pre-setea `busy_apps["launching"]` antes del thread → botón reacciona instantáneamente |
| `gui/workers.py` | Eliminado `busy_apps[app_id] = "launching"` duplicado del thread body |

#### Fase 3 — Calidad y mantenibilidad ✅
| Archivo | Cambio |
|---|---|
| `gui/theme.py` | **Nuevo archivo** — stylesheet global + constante `GROUPBOX_STYLE` |
| `gui/main.py` | `_STYLESHEET` eliminado (130 líneas) → importa `STYLESHEET` de `theme.py` |
| `gui/components/hub_settings.py` | 3 bloques de estilo `QGroupBox` duplicados → `GROUPBOX_STYLE` |
| `gui/workers.py` | `_ConsoleLogger` module-level + singleton `_console_logger` |

#### Fase 4 — UX visual ✅
| Archivo | Cambio |
|---|---|
| `gui/components/app_card.py` | `QProgressBar` indeterminada (4px, animada) en estados `installing/updating/uninstalling` |
| `gui/components/app_card.py` | Estado `running` muestra puerto activo + PID del proceso |
| `gui/components/app_card.py` | Estado busy muestra operación exacta: "Instalando...", "Actualizando...", etc. |

### Bug adicional detectado durante test integral
**`update_status` — widgets fantasma con `deleteLater()`**
- **Causa**: `deleteLater()` es diferido — los widgets del estado anterior seguían siendo hijos de `_right_widget` hasta que Qt procesaba eventos. `findChildren()` los encontraba mezclados con los nuevos.
- **Síntoma en producción**: botones de estados anteriores visibles encima de los nuevos en transiciones rápidas.
- **Fix**: `widget.setParent(None)` antes de `deleteLater()` los saca del árbol inmediatamente.

### Archivos modificados
`gui/main.py`, `gui/workers.py`, `gui/components/app_card.py`, `gui/components/hub_settings.py`, `gui/theme.py` (nuevo)

### Test integral post-refactoring — PASSED
- `hub/test_gui_full.py` — **77/77 tests ✅**
  - Estado del sistema (GPU, CUDA, disk, registry)
  - Renderizado inicial de las 5 apps
  - Transiciones completas por app: not_installed→installing→installed→launching→running→stopping→installed
  - Progress bar indeterminada en installing/updating/uninstalling
  - Puerto y PID visibles en estado running
  - Botón Lanzar bloqueado con any_running=True
  - Guard anti-doble-click
  - Persistencia de widgets entre transiciones
  - Ventana principal: 5 cards + 5 tabs, sin duplicados en refresh

---

## [2026-03-25] — Fix: forge-neo crash silencioso al lanzar

### Síntoma
Al presionar "Lanzar" en forge-neo, no sucedía nada: ni output en terminal, ni cambio de estado, ni error.

### Diagnóstico
1. `app_launcher.py` lanza con `stdin=subprocess.DEVNULL` cuando `capture_output=True`
2. `apps/sd-webui-forge-neo/modules/launch_utils.py` — función `verify_version()` (línea 579):
   - Lee `config.json` buscando `VERSION_UID`
   - Si no está o no coincide: llama `input("Press Enter to Continue...")`
   - Con `stdin=DEVNULL` → `EOFError` → proceso termina inmediatamente, sin output visible
3. El `config.json` de forge solo tenía las entradas `outdir_*` inyectadas por el hub, sin `VERSION_UID`
4. El crash ocurría antes de cualquier output → terminal muestra nada

### Fix implementado

**Fix inmediato** — `apps/sd-webui-forge-neo/config.json`:
- Agregado `"VERSION_UID": "PY313"` al inicio del archivo
- Permite que `verify_version()` encuentre el UID correcto y salte el prompt

**Fix permanente** — `hub/modules/app_launcher.py` + `hub/config/app_registry.json`:
- `_inject_output_config` ahora soporta campo `version_uid_source` en `output_map`
- Si está presente, lee el archivo fuente (relativo al `app_dir`) con regex `VERSION_UID[^=]*=\s*["']([^"']+)["']`
- Inyecta el valor actual en `config.json` en cada launch → se mantiene sincronizado tras updates de forge
- Registry de forge-neo tiene: `"version_uid_source": "modules/launch_utils.py"`

### Por qué es robusto
- Si forge actualiza su `VERSION_UID` en una versión futura, el hub lo leerá automáticamente y actualizará `config.json` antes del próximo launch
- No hardcodea el valor en ningún lado del hub
- No modifica el manejo de stdin (que es correcto para capturar output del proceso)

### Archivos modificados
- `hub/modules/app_launcher.py` — nuevo bloque VERSION_UID en `_inject_output_config`
- `hub/config/app_registry.json` — `"version_uid_source"` en `output_map` de `sd-webui-forge-neo`
- `apps/sd-webui-forge-neo/config.json` — `"VERSION_UID": "PY313"` agregado

---

## [2026-04-16] — Migración a WebUI (FastAPI + pywebview) + Herramientas Web

### Contexto
La GUI PySide6 presentaba inestabilidad estructural por el patrón de reconstrucción dinámica de widgets. Se decidió reemplazarla completamente por una WebUI basada en FastAPI + vanilla JS, sin Rust ni Electron.

### Arquitectura adoptada
- **Backend**: FastAPI + uvicorn + WebSocket (push en tiempo real)
- **Frontend**: HTML/CSS/JS vanilla, tema cyberpunk existente
- **Ventana nativa**: pywebview (WebKitGTK) con fallback a browser del sistema
- **Adaptador**: `hub_bridge.py` — capa Qt-free que expone la lógica del hub via callbacks Python

### Nuevos archivos — WebUI core
| Archivo | Descripción |
|---|---|
| `hub-webui/app.py` | Servidor FastAPI, WebSocket, entry point pywebview |
| `hub-webui/hub_bridge.py` | Adaptador sin PySide6: lanza/detiene/instala apps, emite eventos via callbacks |
| `hub-webui/merger_routes.py` | API del LoRA Merger: browse dirs, analyze, suggest, merge + SSE progress |
| `hub-webui/vault_routes.py` | API del Model Vault: modelos del DB, thumbnails, scan + SSE progress |
| `hub-webui/requirements.txt` | fastapi, uvicorn, websockets, requests, numpy |
| `hub-webui/static/index.html` | SPA principal: tabs Aplicaciones, Herramientas, Ajustes, Log |
| `hub-webui/static/style.css` | Tema cyberpunk completo con CSS custom properties |
| `hub-webui/static/app.js` | Lógica principal: WebSocket, renderizado en-place, modales, terminal |
| `hub-webui/static/vault.html` + `vault.js` | Model Vault web: grid de thumbnails, sidebar de categorías, panel de detalles |
| `hub-webui/static/merger.html` + `merger.js` | LoRA Merger web: file browser, cards con barras de impacto, config panel, SSE merge |

### Herramientas (Vault + Merger) portadas a web
- **Model Vault**: carga los 1300+ modelos del DB SQLite existente, thumbnails servidos por FastAPI, búsqueda/filtros en cliente, edición de notas y tags, scan con barra de progreso SSE
- **LoRA Merger**: file browser por directorio, análisis async (detect + analyze), sugerencia automática de método/pesos, merge con stream SSE de progreso capa por capa
- Ambos tools abren en nueva pestaña del browser desde la tab Herramientas del hub principal

### Auto-open browser
Implementado `auto_open_browser` en `app_registry.json` para apps que no abren su dashboard automáticamente (ComfyUI, Forge Neo, ai-toolkit, FaceFusion). El hub sondea el puerto cada 0.5s hasta que está activo (resistente a updates).

### Fix: PYTHONPATH contamination
`hub_bridge._launch_thread` fuerza `cuda_env["PYTHONPATH"] = ""` antes de lanzar apps hijas, evitando que los site-packages de Python 3.13 del hub contaminen los venvs de las apps (e.g. ComfyUI en Python 3.12 con numpy 1.x).

### Fix: ComfyUI custom nodes rotos
| Nodo | Error | Fix |
|---|---|---|
| `ComfyUI-UNO` | `ModuleNotFoundError: No module named 'uno'` — paquete en `src/` pero imports usan `uno.*` | Creado symlink `uno → src` + `sys.path.insert(0, _dir)` en `__init__.py` |
| `ComfyUI-GGUF-FantasyTalking` | `SyntaxError: (unicode error) \U` — Windows path `C:\Users\Rahel\...` en triple-quoted string | Cambiado `"""` → `r"""` en línea 608 de `nodes.py` |
| `onnxruntime 1.18.0` | `No module named 'numpy._core._multiarray_umath'` — compilado para NumPy 1.x, incompatible con NumPy 2.4.3 | Upgrade a `onnxruntime-gpu 1.24.4` |

### Fix: vault_service.py
`scan_and_index()` llamaba a `self.db.get_model_by_hash_path()` (método inexistente). Corregido a `get_model_by_path()`.

### Limpieza GUI PySide6
Eliminados todos los archivos de la GUI vieja. Conservado únicamente `hub/gui/state.py` (usado por `hub_bridge.py` y `vault_routes.py`) y `hub/gui/__init__.py`.

| Eliminado |
|---|
| `hub/gui/main.py` |
| `hub/gui/workers.py` |
| `hub/gui/theme.py` |
| `hub/gui/components/` (6 archivos) |
| `hub/gui/controllers/` |
| `hub/gui/utils/` |
| `hub/poc_pyside.py` |

### Consolidación de run.sh
Reemplazados `hub/run.sh` y `hub-webui/run.sh` por un único `/ai-hub/run.sh` en la raíz del proyecto.

**Pasos del launcher unificado:**
1. Verificar GPU NVIDIA (nvidia-smi)
2. Provisionar uv (descarga si no existe)
3. Provisionar Python 3.13 (sistema o portable via uv)
4. Verificar git y Node.js (descarga Node.js LTS si no hay)
5. Crear `hub/.ui_venv` con Python 3.13 + PySide6 (backend Qt para pywebview)
6. Crear `hub-webui/.venv` con dependencias WebUI, lanzar `hub-webui/app.py`

### Estado al cierre
- WebUI probada y funcionando: apps, herramientas, ajustes, log
- Model Vault web: 1311 modelos cargando correctamente
- LoRA Merger web: file browser + análisis + merge funcionales
- ComfyUI corriendo sin errores de custom nodes
- Instalación limpia desde `run.sh` verificada
