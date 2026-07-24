/* AI Hub — Locale system (ES / EN) */

(function () {
  // ── Locale data (inline para evitar fetch asíncrono) ─────────────────────
  const LOCALES = {
    es: {
      nav: { apps:"Aplicaciones", tools:"Herramientas", settings:"Ajustes", log:"Log de Eventos" },
      sysinfo: { gpu:"GPU", cuda:"CUDA", free:"Libre" },
      ws: { connecting:"conectando...", connected:"conectado", reconnecting:"reconectando...", error:"error" },
      terminal: { title:"Log de salida", copy:"Copiar", clear:"Limpiar", copied:"Log copiado al portapapeles.", copy_error:"No se pudo copiar." },
      apps: {
        count:"{installed} de {total} instaladas", cleanup:"Limpiar procesos", cleaning:"Limpiando...",
        badge_webui:"WebUI", badge_desktop:"Desktop", no_running:"Sin procesos activos.",
        status: { running:"● Corriendo", installed:"○ Instalada", not_installed:"○ No instalada", launching:"Iniciando...", installing:"Instalando...", updating:"Actualizando...", uninstalling:"Desinstalando...", stopping:"Deteniendo..." },
        btn: { launch:"▶ Lanzar", stop:"⏹ Detener", install:"↓ Instalar", update:"↑", settings:"⚙", uninstall:"✕" },
        blocked:"Otra app activa. Deténla primero.",
        uninstall_confirm:"¿Desinstalar <strong>{name}</strong>?<br><br>Se eliminará su directorio completo. Esta acción no se puede deshacer.",
        op_unavailable:"Operación no disponible en este momento.", op_error:"Error de conexión con el backend."
      },
      tools: { section_title:"Herramientas del Hub", vault_desc:"Gestiona y organiza modelos. Integración con Civitai.", painter_desc:"Editor de imagen AI: generate, inpaint, outpaint, upscale.", fusion_desc:"Derivación de checkpoints y fusión de LoRAs por bloques (SDXL, Anima, Z-Image).", open:"Abrir", launched:"Herramienta iniciada.", launch_error:"No se pudo iniciar.", error:"Error al lanzar la herramienta." },
      settings: {
        title_system:"Sistema", gpu:"GPU", vram:"VRAM", arch:"Arquitectura", cuda_tag:"CUDA Tag", pytorch:"PyTorch",
        title_paths:"Rutas", models_dir:"Directorio de Modelos", models_hint:"Ruta donde están almacenados los checkpoints, LoRAs, VAEs, etc.", models_placeholder:"/ruta/a/modelos",
        validate:"Verificar", validating:"Verificando...",
        outputs_dir:"Directorio de Salidas", outputs_hint:"Dónde se guardan las imágenes generadas.", outputs_placeholder:"/ruta/a/outputs",
        title_services:"Servicios", civitai_key:"Civitai API Key", civitai_hint:"Necesaria para descargar modelos y ver thumbnails de Civitai.",
        save:"Guardar cambios", saving:"Guardando...", discard:"Descartar",
        saved:"Ajustes guardados.", load_error:"Error cargando ajustes.", save_error:"Error guardando ajustes.",
        title_maintenance:"Mantenimiento",
        purge_label:"Purga del cache de paquetes (uv)",
        purge_placeholder:"paquete concreto, ej. torch",
        purge_btn:"🗑 Purgar paquete", purging:"Purgando...",
        purge_hint:"Si un paquete instalado se corrompe: purga aquí y reinstala/actualiza la app para descargar copias frescas. No afecta a los venvs ya instalados.",
        purge_all_btn:"🗑 Purgar TODO el cache",
        purge_all_hint:"Borra toda la cache compartida entre apps. La próxima instalación/actualización de cada app descargará todo de nuevo.",
        purge_need_pkg:"Escribe el nombre de un paquete, o usa \"Purgar TODO el cache\".",
        purge_confirm_all:"¿Purgar el cache de uv COMPLETO? Los venvs instalados no se tocan; la próxima instalación volverá a descargar los paquetes.",
        purge_confirm_pkg:"¿Purgar {pkg} del cache de uv? Los venvs instalados no se tocan; la próxima instalación lo descargará fresco.",
        purge_done:"Cache purgado — {gb} GB liberados.",
        purge_error:"Error purgando el cache.",
        path: {
          ok:"Directorio encontrado con estructura canónica completa.",
          not_found:"El directorio <code>{path}</code> no existe.",
          create_canonical:"Crear estructura canónica", cancel:"Cancelar",
          missing:"Faltan {count} carpeta(s) canónica(s): <code>{dirs}</code>.",
          will_add:"Se añadirán automáticamente al guardar.",
          orphans:"Se detectaron <strong>{count}</strong> modelo(s) fuera del árbol canónico.",
          apply:"Aplicar y añadir carpetas faltantes", move_inbox:"Mover a Inbox para organizar", ignore:"Ignorar por ahora",
          moving:"Moviendo modelos a Inbox...",
          moved:"{count} modelo(s) movidos a _inbox/. Abre la tab Inbox en Model Vault para clasificarlos.",
          created:"Directorio creado con {count} carpeta(s) canónica(s). Guarda los ajustes para activar el cambio.",
          added:"{count} carpeta(s) añadida(s). Guarda los ajustes para activar.",
          complete:"Estructura canónica completa. Guarda los ajustes para activar.",
          error:"Error al verificar el path."
        },
        modal_title:"Ajustes — {name}", port_label:"Puerto (override)", port_hint:"Puerto predeterminado: {port}",
        flags_label:"Flags de lanzamiento", args_label:"Args adicionales",
        args_placeholder:"--argumento=valor  (uno por línea o separados por espacio)",
        args_hint:"Argumentos que no están listados como flags arriba.",
        modal_saved:"Ajustes guardados.", modal_error:"Error guardando ajustes.", modal_load_error:"Error cargando ajustes de la app."
      },
      log: { title:"Eventos recientes", refresh:"↻ Actualizar", loading:"Cargando...", empty:"Sin eventos registrados aún.", error:"Error cargando log." },
      modal: { cancel:"Cancelar", save:"Guardar" },
      confirm: { cancel:"Cancelar", confirm:"Confirmar" },
      cleanup: { clean:"Sin procesos stale. Puertos libres.", killed:"{count} proceso(s) terminado(s)", title:"Resultado de limpieza", error:"Error durante la limpieza." },
      vault: {
        title:"Model Vault", back:"← AI Hub",
        search_placeholder:"Buscar por nombre, arch, creador, tags...",
        sort_recent:"Más recientes", sort_name:"A–Z", sort_arch:"Arquitectura",
        btn_sync_civitai:"☁ Sync Civitai", btn_resync_all:"☁ Re-sync Todo", btn_scan:"↻ Sincronizar",
        scanning:"Escaneando", models_count:"{count} modelo", models_count_plural:"{count} modelos",
        sidebar_type:"Tipo", cat_all:"Todos", cat_checkpoint:"Checkpoint", cat_lora:"LoRA", cat_lycoris:"LyCORIS", cat_embedding:"Embedding", cat_vae:"VAE", cat_upscaler:"Upscaler", subfolder_root:"(raíz)",
        loading:"Cargando modelos...", empty:"Sin modelos", empty_search:"Sin modelos para \"{query}\".", empty_hint:"Configura el directorio de modelos en Ajustes y pulsa Sincronizar.",
        detail_type:"Tipo", detail_base:"Base model", detail_version:"Versión", detail_creator:"Creador", detail_file:"Archivo",
        detail_description:"Descripción", detail_triggers:"Trigger words", detail_no_triggers:"Sin trigger words.",
        detail_civitai_tags:"Civitai Tags", detail_custom_tags:"Tags personalizados", detail_custom_tags_placeholder:"tag1, tag2, ...",
        detail_notes:"Notas", detail_notes_placeholder:"Notas personales sobre este modelo...", detail_save:"Guardar",
        saved:"Guardado.", save_error:"Error guardando.", load_error:"Error cargando modelos.",
        detail_not_found:"Modelo no encontrado.", detail_error:"Error cargando detalles.", conn_error:"Error de conexión.",
        scan_complete:"Scan completo. {total} modelos indexados.", scan_error:"Error de conexión con el stream de scan.", scan_start_error:"Error iniciando el scan.", scan_starting:"Iniciando...",
        civitai_pending_error:"Error consultando modelos pendientes.", civitai_all_synced:"Todos los modelos ya tienen tags de Civitai.",
        civitai_confirm:"Se sincronizarán {count} modelo(s) con Civitai.\nTiempo estimado: ~{eta} minuto(s).\n\n¿Continuar?",
        civitai_force_confirm:"Force Sync re-sincronizará TODOS los modelos con Civitai.\nTiempo estimado: ~{eta} minuto(s).\n\n¿Continuar?",
        civitai_start_error:"Error iniciando Civitai sync.", civitai_connecting:"Conectando con Civitai...",
        civitai_done:"Civitai sync completo — {synced} actualizados, {skipped} skip, {not_found} no encontrados, {errors} errores",
        civitai_stream_error:"Error de conexión con Civitai sync.",
        move_already_there:"El modelo ya está en esa carpeta.",
        move_cross_cat_warn:"Este modelo es de tipo \"{from}\" pero la carpeta destino es \"{to}\".\n¿Estás seguro de que quieres moverlo ahí?",
        copy_webui:"Copiar para WebUI", copied:"Copiado", copy_failed:"No se pudo copiar al portapapeles"
      },
      merger: {
        title:"LoRA Merger", back:"← AI Hub", no_loras:"Sin LoRAs",
        lora_count:"{count} LoRA", lora_count_plural:"{count} LoRAs",
        btn_add:"+ Agregar", btn_suggest:"✦ Sugerir", btn_suggesting:"...",
        empty:"Agrega archivos .safetensors\npara comenzar el merge", analyzing:"Analizando...", impact_title:"Impacto por bloque",
        section_method:"Método de merge", label_algorithm:"Algoritmo",
        method: { weighted_sum:"Suma ponderada de deltas. Resultado = ∑ wi × ΔWi", slerp:"Interpolación esférica. Transición suave en espacio de alta dimensión. Solo 2 LoRAs.", ties:"Elimina parámetros con baja magnitud y resuelve conflictos de signo por votación.", dare:"Poda aleatoria de parámetros por magnitud y reescala los restantes.", dare_ties:"DARE seguido de TIES: poda + resolución de conflictos combinados." },
        label_slerp_t:"SLERP t", label_dare_density:"Densidad DARE", label_ties_k:"TIES top-k",
        section_weights:"Pesos por LoRA", normalize:"Normalizar (suma = 1)",
        section_layers:"Capas y rank", label_layer_filter:"Filtro de capas",
        layer: { full:"Todas las capas", attn_only:"Solo Atención", mlp_only:"Solo MLP", attn_mlp:"Atención + MLP" },
        label_rank:"Rank objetivo", section_output:"Salida", label_out_dir:"Directorio", label_out_name:"Nombre", label_precision:"Precisión",
        dtype: { bf16:"BF16 (recomendado)", f16:"F16", f32:"F32" },
        btn_merge:"Mergear LoRAs", merging:"Procesando capas...", layer_text:"Capa {cur} / {tot}  —  {layer}",
        initializing:"Iniciando...", merge_starting:"Iniciando merge...", completed:"¡Completado!",
        merge_ok_title:"✓ Merge completado", error:"Error", merge_error_title:"✕ Error en el merge",
        btn_cancel:"✕ Cancelar merge", canceling:"Cancelando...",
        browser_up:"↑ Subir", browser_go:"Ir", browser_selected:"{count} seleccionados",
        browser_cancel:"Cancelar", browser_add:"Agregar seleccionados",
        browser_empty:"Sin directorios ni archivos .safetensors aquí.", already_loaded:"Ya está en la lista",
        no_output_dir:"Especifica un directorio de salida.",
        status_analyzing:"Analizando...", status_mixed_arch:"Arqs. mixtas — cuidado", status_ready:"Listos para merge", status_need_more:"Agrega al menos 2 LoRAs", status_errors:"{count} error(es)",
        suggestion_label:"✦ Sugerencia:",
        error_browse:"Error al consultar el browser.", error_suggestion:"Error obteniendo sugerencia.",
        error_analyze:"Error de conexión con el backend", error_status:"Error al consultar el estado del merge.",
        error_start:"Error iniciando el merge en el servidor.", error_conn:"Error de conexión con el servidor.", unknown_error:"Error desconocido en el merge."
      },
      lang: { toggle:"EN" },
      painter: {
        title:"Painter", back:"← AI Hub",
        arch_label:"Arquitectura",
        btn_load:"Cargar imagen", btn_save:"↓ Guardar", btn_generate:"Generar", btn_inpaint:"Inpaint", btn_outpaint:"Outpaint", btn_upscale:"Upscale",
        preview_ready:"Preview listo — ¿aceptar?",
        btn_accept:"✓ Aceptar", btn_reject:"✕ Rechazar",
        reg_step_label:"Región {n} de {total} — ¿aceptar?",
        reg_btn_accept:"✓ Aceptar región", reg_btn_reject:"↺ Regenerar",
        reg_all_done:"Todas las regiones generadas.",
        btn_cancel_job:"✕ Cancelar", btn_clear_mask:"Limpiar máscara",
        tab_generate:"Generar", tab_outpaint:"Outpaint", tab_upscale:"Upscale", tab_controlnet:"ControlNet",
        label_checkpoint:"Checkpoint", label_prompt:"Prompt", label_negative:"Negativo",
        label_sampler:"Sampler", label_scheduler:"Scheduler", label_steps:"Pasos",
        label_cfg:"CFG", label_seed:"Seed", label_denoise:"Denoise", label_feather:"Feather",
        label_width:"Ancho", label_height:"Alto",
        label_inpaint_model:"Modelo inpaint", label_inpaint_none:"(workflow básico)",
        label_upscaler:"Upscaler",
        label_cn_model:"Modelo ControlNet", label_cn_preprocessor:"Preprocesador",
        label_cn_strength:"Strength",
        cn_enable:"Activar ControlNet", cn_img_label:"Imagen de guía",
        cn_use_canvas:"Usar imagen actual", cn_img_drop:"Arrastra o haz clic",
        cn_union_type:"Tipo (Union)",
        btn_randomize:"↺",
        tool_brush:"Brush (B)", tool_rect:"Rect (R)", tool_lasso:"Lasso (L)", tool_eraser:"Eraser (E)",
        status_zoom:"Zoom", status_tool:"Herramienta",
        setup_checking:"Verificando setup…",
        setup_install_comfyui:"ComfyUI no está instalado — instálalo desde el hub.",
        setup_start_comfyui:"ComfyUI no está activo. Inícialo desde el hub y recarga.",
        comfy_gate_not_installed_title:"ComfyUI no está instalado",
        comfy_gate_not_installed_desc:"Painter requiere ComfyUI para generar imágenes.",
        comfy_gate_install_btn:"Instalar ComfyUI",
        comfy_gate_offline_title:"ComfyUI no está corriendo",
        comfy_gate_offline_desc:"Inícialo para usar el Painter.",
        comfy_gate_start_btn:"Arrancar ComfyUI",
        comfy_gate_starting:"Iniciando ComfyUI...",
        comfy_gate_install_done:"Instalación completa. Iniciando ComfyUI...",
        comfy_gate_error:"Error — intenta de nuevo.",
        setup_installing:"Instalando dependencias…",
        setup_restart:"Reinicia ComfyUI para cargar nuevos nodos, luego recarga esta página.",
        setup_ready:"Listo",
        setup_warning:"Setup completo con advertencias",
        generating:"Generando…", step_of:"paso {s} de {t}",
        op_top:"Arriba", op_bottom:"Abajo", op_left:"Izquierda", op_right:"Derecha",
        op_note:"Usa prompt, checkpoint y parámetros del tab Generar.",
        op_no_pad:"Configura al menos un padding mayor a 0.",
        no_checkpoint:"Selecciona un checkpoint primero.",
        no_image:"Carga una imagen primero.",
        no_mask:"Pinta una máscara sobre la imagen primero.",
        job_error:"Error en la generación: {msg}",
        accept_ok:"Imagen aceptada.", reject_ok:"Generación rechazada.",
        undo_ok:"Deshecho.", redo_ok:"Rehecho.", nothing_to_undo:"Nada que deshacer.", nothing_to_redo:"Nada que rehacer.",
        controlnet_disabled:"Sin modelos ControlNet instalados.",
        conn_error:"Error de conexión con el servidor.",
        empty_title:"¿Por dónde empezamos?",
        empty_subtitle:"Genera una imagen nueva desde un prompt o carga una existente para editar.",
        empty_btn_generate:"✦ Generar imagen",
        empty_btn_load:"↑ Cargar imagen",
        empty_no_checkpoint:"Selecciona un checkpoint en el panel primero.",
        styles_none:"Sin estilo activo",
        styles_save:"Guardar",
        styles_name_ph:"Nombre del estilo",
        styles_saved:"Estilo guardado.",
        styles_deleted:"Estilo eliminado.",
        styles_applied:"Estilo aplicado: {name}",
        styles_cleared:"Estilo desactivado."
      }
    },
    en: {
      nav: { apps:"Applications", tools:"Tools", settings:"Settings", log:"Event Log" },
      sysinfo: { gpu:"GPU", cuda:"CUDA", free:"Free" },
      ws: { connecting:"connecting...", connected:"connected", reconnecting:"reconnecting...", error:"error" },
      terminal: { title:"Output log", copy:"Copy", clear:"Clear", copied:"Log copied to clipboard.", copy_error:"Could not copy." },
      apps: {
        count:"{installed} of {total} installed", cleanup:"Clean up processes", cleaning:"Cleaning...",
        badge_webui:"WebUI", badge_desktop:"Desktop", no_running:"No active processes.",
        status: { running:"● Running", installed:"○ Installed", not_installed:"○ Not installed", launching:"Launching...", installing:"Installing...", updating:"Updating...", uninstalling:"Uninstalling...", stopping:"Stopping..." },
        btn: { launch:"▶ Launch", stop:"⏹ Stop", install:"↓ Install", update:"↑", settings:"⚙", uninstall:"✕" },
        blocked:"Another app is running. Stop it first.",
        uninstall_confirm:"Uninstall <strong>{name}</strong>?<br><br>Its entire directory will be deleted. This action cannot be undone.",
        op_unavailable:"Operation not available at this time.", op_error:"Connection error with the backend."
      },
      tools: { section_title:"Hub Tools", vault_desc:"Manage and organize models. Civitai integration.", painter_desc:"AI image editor: generate, inpaint, outpaint, upscale.", fusion_desc:"Checkpoint derivation and block-wise LoRA fusion (SDXL, Anima, Z-Image).", open:"Open", launched:"Tool launched.", launch_error:"Could not launch.", error:"Error launching tool." },
      settings: {
        title_system:"System", gpu:"GPU", vram:"VRAM", arch:"Architecture", cuda_tag:"CUDA Tag", pytorch:"PyTorch",
        title_paths:"Paths", models_dir:"Models Directory", models_hint:"Path where checkpoints, LoRAs, VAEs, etc. are stored.", models_placeholder:"/path/to/models",
        validate:"Verify", validating:"Verifying...",
        outputs_dir:"Outputs Directory", outputs_hint:"Where generated images are saved.", outputs_placeholder:"/path/to/outputs",
        title_services:"Services", civitai_key:"Civitai API Key", civitai_hint:"Required to download models and view Civitai thumbnails.",
        save:"Save changes", saving:"Saving...", discard:"Discard",
        saved:"Settings saved.", load_error:"Error loading settings.", save_error:"Error saving settings.",
        title_maintenance:"Maintenance",
        purge_label:"Package cache purge (uv)",
        purge_placeholder:"specific package, e.g. torch",
        purge_btn:"🗑 Purge package", purging:"Purging...",
        purge_hint:"If an installed package gets corrupted: purge here and reinstall/update the app to download fresh copies. Installed venvs are not affected.",
        purge_all_btn:"🗑 Purge ENTIRE cache",
        purge_all_hint:"Deletes the whole cache shared across apps. The next install/update of each app will re-download everything.",
        purge_need_pkg:"Type a package name, or use \"Purge ENTIRE cache\".",
        purge_confirm_all:"Purge the ENTIRE uv cache? Installed venvs are untouched; the next install will re-download packages.",
        purge_confirm_pkg:"Purge {pkg} from the uv cache? Installed venvs are untouched; the next install will download it fresh.",
        purge_done:"Cache purged — {gb} GB freed.",
        purge_error:"Error purging the cache.",
        path: {
          ok:"Directory found with complete canonical structure.",
          not_found:"Directory <code>{path}</code> does not exist.",
          create_canonical:"Create canonical structure", cancel:"Cancel",
          missing:"{count} canonical folder(s) missing: <code>{dirs}</code>.",
          will_add:"They will be added automatically on save.",
          orphans:"<strong>{count}</strong> model(s) found outside the canonical tree.",
          apply:"Apply and add missing folders", move_inbox:"Move to Inbox for sorting", ignore:"Ignore for now",
          moving:"Moving models to Inbox...",
          moved:"{count} model(s) moved to _inbox/. Open the Inbox tab in Model Vault to sort them.",
          created:"Directory created with {count} canonical folder(s). Save settings to apply.",
          added:"{count} folder(s) added. Save settings to apply.",
          complete:"Canonical structure complete. Save settings to apply.",
          error:"Error verifying the path."
        },
        modal_title:"Settings — {name}", port_label:"Port (override)", port_hint:"Default port: {port}",
        flags_label:"Launch flags", args_label:"Additional args",
        args_placeholder:"--argument=value  (one per line or space-separated)",
        args_hint:"Arguments not listed as flags above.",
        modal_saved:"Settings saved.", modal_error:"Error saving settings.", modal_load_error:"Error loading app settings."
      },
      log: { title:"Recent events", refresh:"↻ Refresh", loading:"Loading...", empty:"No events recorded yet.", error:"Error loading log." },
      modal: { cancel:"Cancel", save:"Save" },
      confirm: { cancel:"Cancel", confirm:"Confirm" },
      cleanup: { clean:"No stale processes. Ports are free.", killed:"{count} process(es) terminated", title:"Cleanup result", error:"Error during cleanup." },
      vault: {
        title:"Model Vault", back:"← AI Hub",
        search_placeholder:"Search by name, arch, creator, tags...",
        sort_recent:"Most recent", sort_name:"A–Z", sort_arch:"Architecture",
        btn_sync_civitai:"☁ Sync Civitai", btn_resync_all:"☁ Re-sync All", btn_scan:"↻ Scan",
        scanning:"Scanning", models_count:"{count} model", models_count_plural:"{count} models",
        sidebar_type:"Type", cat_all:"All", cat_checkpoint:"Checkpoint", cat_lora:"LoRA", cat_lycoris:"LyCORIS", cat_embedding:"Embedding", cat_vae:"VAE", cat_upscaler:"Upscaler", subfolder_root:"(root)",
        loading:"Loading models...", empty:"No models", empty_search:"No models for \"{query}\".", empty_hint:"Configure the models directory in Settings and press Scan.",
        detail_type:"Type", detail_base:"Base model", detail_version:"Version", detail_creator:"Creator", detail_file:"File",
        detail_description:"Description", detail_triggers:"Trigger words", detail_no_triggers:"No trigger words.",
        detail_civitai_tags:"Civitai Tags", detail_custom_tags:"Custom tags", detail_custom_tags_placeholder:"tag1, tag2, ...",
        detail_notes:"Notes", detail_notes_placeholder:"Personal notes about this model...", detail_save:"Save",
        saved:"Saved.", save_error:"Error saving.", load_error:"Error loading models.",
        detail_not_found:"Model not found.", detail_error:"Error loading details.", conn_error:"Connection error.",
        scan_complete:"Scan complete. {total} models indexed.", scan_error:"Connection error with scan stream.", scan_start_error:"Error starting scan.", scan_starting:"Starting...",
        civitai_pending_error:"Error checking pending models.", civitai_all_synced:"All models already have Civitai tags.",
        civitai_confirm:"{count} model(s) will be synced with Civitai.\nEstimated time: ~{eta} minute(s).\n\nContinue?",
        civitai_force_confirm:"Force Sync will re-sync ALL models with Civitai.\nEstimated time: ~{eta} minute(s).\n\nContinue?",
        civitai_start_error:"Error starting Civitai sync.", civitai_connecting:"Connecting to Civitai...",
        civitai_done:"Civitai sync complete — {synced} updated, {skipped} skipped, {not_found} not found, {errors} errors",
        civitai_stream_error:"Connection error with Civitai sync.",
        move_already_there:"Model is already in that folder.",
        move_cross_cat_warn:"This model is of type \"{from}\" but the target folder is \"{to}\".\nAre you sure you want to move it there?",
        copy_webui:"Copy for WebUI", copied:"Copied", copy_failed:"Could not copy to clipboard"
      },
      painter: {
        title:"Painter", back:"← AI Hub",
        arch_label:"Architecture",
        btn_load:"Load image", btn_save:"↓ Save", btn_generate:"Generate", btn_inpaint:"Inpaint", btn_outpaint:"Outpaint", btn_upscale:"Upscale",
        preview_ready:"Preview ready — accept?",
        btn_accept:"✓ Accept", btn_reject:"✕ Reject",
        reg_step_label:"Region {n} of {total} — accept?",
        reg_btn_accept:"✓ Accept region", reg_btn_reject:"↺ Regenerate",
        reg_all_done:"All regions generated.",
        btn_cancel_job:"✕ Cancel", btn_clear_mask:"Clear mask",
        tab_generate:"Generate", tab_outpaint:"Outpaint", tab_upscale:"Upscale", tab_controlnet:"ControlNet",
        label_checkpoint:"Checkpoint", label_prompt:"Prompt", label_negative:"Negative",
        label_sampler:"Sampler", label_scheduler:"Scheduler", label_steps:"Steps",
        label_cfg:"CFG", label_seed:"Seed", label_denoise:"Denoise", label_feather:"Feather",
        label_width:"Width", label_height:"Height",
        label_inpaint_model:"Inpaint model", label_inpaint_none:"(basic workflow)",
        label_upscaler:"Upscaler",
        label_cn_model:"ControlNet model", label_cn_preprocessor:"Preprocessor",
        label_cn_strength:"Strength",
        cn_enable:"Enable ControlNet", cn_img_label:"Guide image",
        cn_use_canvas:"Use current image", cn_img_drop:"Drag or click to upload",
        cn_union_type:"Type (Union)",
        btn_randomize:"↺",
        tool_brush:"Brush (B)", tool_rect:"Rect (R)", tool_lasso:"Lasso (L)", tool_eraser:"Eraser (E)",
        status_zoom:"Zoom", status_tool:"Tool",
        setup_checking:"Checking setup…",
        setup_install_comfyui:"ComfyUI is not installed — install it from the hub.",
        setup_start_comfyui:"ComfyUI is not running. Start it from the hub and reload.",
        comfy_gate_not_installed_title:"ComfyUI is not installed",
        comfy_gate_not_installed_desc:"Painter requires ComfyUI to generate images.",
        comfy_gate_install_btn:"Install ComfyUI",
        comfy_gate_offline_title:"ComfyUI is not running",
        comfy_gate_offline_desc:"Start it to use the Painter.",
        comfy_gate_start_btn:"Start ComfyUI",
        comfy_gate_starting:"Starting ComfyUI...",
        comfy_gate_install_done:"Installation complete. Starting ComfyUI...",
        comfy_gate_error:"Error — please try again.",
        setup_installing:"Installing dependencies…",
        setup_restart:"Restart ComfyUI to load new nodes, then reload this page.",
        setup_ready:"Ready",
        setup_warning:"Setup complete with warnings",
        generating:"Generating…", step_of:"step {s} of {t}",
        op_top:"Top", op_bottom:"Bottom", op_left:"Left", op_right:"Right",
        op_note:"Uses prompt, checkpoint and params from the Generate tab.",
        op_no_pad:"Set at least one padding value greater than 0.",
        no_checkpoint:"Select a checkpoint first.",
        no_image:"Load an image first.",
        no_mask:"Paint a mask over the image first.",
        job_error:"Generation error: {msg}",
        accept_ok:"Image accepted.", reject_ok:"Generation rejected.",
        undo_ok:"Undone.", redo_ok:"Redone.", nothing_to_undo:"Nothing to undo.", nothing_to_redo:"Nothing to redo.",
        controlnet_disabled:"No ControlNet models installed.",
        conn_error:"Connection error.",
        empty_title:"Where do we start?",
        empty_subtitle:"Generate a new image from a prompt or load an existing one to edit.",
        empty_btn_generate:"✦ Generate image",
        empty_btn_load:"↑ Load image",
        empty_no_checkpoint:"Select a checkpoint in the panel first.",
        styles_none:"No active style",
        styles_save:"Save",
        styles_name_ph:"Style name",
        styles_saved:"Style saved.",
        styles_deleted:"Style deleted.",
        styles_applied:"Style applied: {name}",
        styles_cleared:"Style deactivated."
      },
      merger: {
        title:"LoRA Merger", back:"← AI Hub", no_loras:"No LoRAs",
        lora_count:"{count} LoRA", lora_count_plural:"{count} LoRAs",
        btn_add:"+ Add", btn_suggest:"✦ Suggest", btn_suggesting:"...",
        empty:"Add .safetensors files\nto start the merge", analyzing:"Analyzing...", impact_title:"Block impact",
        section_method:"Merge method", label_algorithm:"Algorithm",
        method: { weighted_sum:"Weighted sum of deltas. Result = ∑ wi × ΔWi", slerp:"Spherical interpolation. Smooth transition in high-dimensional space. Only 2 LoRAs.", ties:"Removes low-magnitude parameters and resolves sign conflicts by voting.", dare:"Random magnitude-based pruning and rescaling of remaining parameters.", dare_ties:"DARE followed by TIES: pruning + combined conflict resolution." },
        label_slerp_t:"SLERP t", label_dare_density:"DARE density", label_ties_k:"TIES top-k",
        section_weights:"LoRA weights", normalize:"Normalize (sum = 1)",
        section_layers:"Layers and rank", label_layer_filter:"Layer filter",
        layer: { full:"All layers", attn_only:"Attention only", mlp_only:"MLP only", attn_mlp:"Attention + MLP" },
        label_rank:"Target rank", section_output:"Output", label_out_dir:"Directory", label_out_name:"Name", label_precision:"Precision",
        dtype: { bf16:"BF16 (recommended)", f16:"F16", f32:"F32" },
        btn_merge:"Merge LoRAs", merging:"Processing layers...", layer_text:"Layer {cur} / {tot}  —  {layer}",
        initializing:"Initializing...", merge_starting:"Starting merge...", completed:"Completed!",
        merge_ok_title:"✓ Merge complete", error:"Error", merge_error_title:"✕ Merge error",
        btn_cancel:"✕ Cancel merge", canceling:"Canceling...",
        browser_up:"↑ Up", browser_go:"Go", browser_selected:"{count} selected",
        browser_cancel:"Cancel", browser_add:"Add selected",
        browser_empty:"No directories or .safetensors files here.", already_loaded:"Already in list",
        no_output_dir:"Specify an output directory.",
        status_analyzing:"Analyzing...", status_mixed_arch:"Mixed archs — caution", status_ready:"Ready to merge", status_need_more:"Add at least 2 LoRAs", status_errors:"{count} error(s)",
        suggestion_label:"✦ Suggestion:",
        error_browse:"Error querying browser.", error_suggestion:"Error getting suggestion.",
        error_analyze:"Backend connection error", error_status:"Error querying merge status.",
        error_start:"Error starting merge on server.", error_conn:"Server connection error.", unknown_error:"Unknown merge error."
      },
      lang: { toggle:"ES" }
    }
  };

  // ── Estado ────────────────────────────────────────────────────────────────
  let _lang = localStorage.getItem("ai_hub_lang") || "es";

  // ── API pública ───────────────────────────────────────────────────────────

  /**
   * Obtiene un string del locale activo.
   * Soporta claves con punto: t("vault.title") → LOCALES[lang].vault.title
   * Soporta interpolación: t("vault.models_count_plural", {count: 42})
   */
  function t(key, vars) {
    const parts = key.split(".");
    let val = LOCALES[_lang];
    for (const p of parts) {
      if (val == null) break;
      val = val[p];
    }
    if (val == null) {
      // fallback a español
      val = LOCALES["es"];
      for (const p of parts) {
        if (val == null) break;
        val = val[p];
      }
    }
    if (val == null) return key;
    if (vars) {
      return String(val).replace(/\{(\w+)\}/g, (_, k) => (vars[k] !== undefined ? vars[k] : `{${k}}`));
    }
    return String(val);
  }

  /** Cambia el idioma y recarga la página para aplicar todos los textos. */
  function setLocale(lang) {
    if (!LOCALES[lang]) return;
    _lang = lang;
    localStorage.setItem("ai_hub_lang", lang);
    // Actualizar atributo html lang
    document.documentElement.lang = lang;
    // Aplicar data-i18n sin recargar
    applyI18n();
    // Actualizar botón de idioma
    document.querySelectorAll("[data-i18n-lang-btn]").forEach(el => {
      el.textContent = t("lang.toggle");
    });
  }

  /** Aplica atributos data-i18n al DOM. */
  function applyI18n() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
      const key = el.dataset.i18n;
      const text = t(key);
      if (el.dataset.i18nHtml) {
        el.innerHTML = text;
      } else {
        el.textContent = text;
      }
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
      el.placeholder = t(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll("[data-i18n-title]").forEach(el => {
      el.title = t(el.dataset.i18nTitle);
    });
  }

  /** Retorna el idioma activo. */
  function getLang() { return _lang; }

  // ── Exponer globalmente ───────────────────────────────────────────────────
  window.t         = t;
  window.setLocale = setLocale;
  window.getLang   = getLang;

  // ── Aplicar al cargar el DOM ──────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    document.documentElement.lang = _lang;
    applyI18n();
    document.querySelectorAll("[data-i18n-lang-btn]").forEach(el => {
      el.textContent = t("lang.toggle");
      el.addEventListener("click", () => setLocale(_lang === "es" ? "en" : "es"));
    });
  });
})();
