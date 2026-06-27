# Handoff Sesión 25 → 26

## Estado al cerrar

Todo limpio. Sin commits pendientes (no se modificó código del hub, solo config y scripts).

**Archivos modificados esta sesión:**
- `hub/config/app_registry.json` — entrada `anima-standalone-trainer` restaurada
- `hub/scripts/install_standalone_trainer.sh` — reconstruido desde cero (nuevo archivo)
- `hub/hub_config.json` — entrada anima-standalone-trainer removida (instalación parcial limpiada)
- `apps/anima-standalone-trainer/` — directorio eliminado (instalación parcial)

**Pendiente commitear** cuando el training funcione correctamente.

---

## Anima Standalone Trainer — Estado listo para instalar

El hub necesita **reinicio** para cargar el registry actualizado. Después:
- Ir a Aplicaciones → Anima Standalone Trainer → ↓ Instalar
- La instalación tarda ~10-15 min (npm install + pip deps)
- Puerto: 7880 | auto_open_browser: true

**Modelos confirmados en disco:**
- DiT: `Models/diffusion_models/anima/anima-base-v1.0.safetensors` (4.2 GB)
- TE:  `Models/clip/anima/qwen_3_06b_base.safetensors` (1.2 GB)
- VAE: `Models/vae/anima/qwen_image_vae.safetensors` (254 MB)

**El install script escribe automáticamente** `training-ui/global_config.toml` con estos paths.

---

## Si el training falla — qué revisar primero

1. **torch_compile** — en `config_template.toml` ya está `torch_compile = false`. Si la UI tiene un toggle, verificar que esté desactivado. Triton en Blackwell (sm_120) causa OOM.
2. **VRAM al arrancar training** — cerrar ComfyUI antes. Anima 2B + LoRA puede necesitar 10-12 GB.
3. **AdamW8bit vs Prodigy** — AdamW8bit es más predecible para primera prueba. LR ~1e-4.
4. **Rank/alpha** — default: rank=16, alpha=16. Razonable para primera LoRA.
5. **Resume** — si crashea, `auto_resume_last_state = true` retoma desde último checkpoint.

---

## Próxima sesión — Tema principal: Captioner local con Gemma 4

El usuario quiere evaluar si es viable implementar un captioner usando un modelo como Gemma 4 (u otro modelo local) directamente en el hub, como alternativa o complemento a WD14/Dataset Refiner para datasets de IA generativa.

**Puntos a investigar:**
- ¿Gemma 4 multimodal? ¿Soporta imágenes? (Gemma 3 tiene variante multimodal 4B/12B)
- Viabilidad en 16 GB VRAM con otras apps activas
- Calidad de captions para personajes, estilos artísticos vs WD14 tag-based
- Opciones de integración: standalone app, módulo en Dataset Refiner, o módulo en Painter
- Alternativas: LLaVA, InternVL, Qwen2-VL (ya tenemos el VAE), Florence-2 (ya en TaggerGUI)

**Contexto clave:** Anima tiene corte de dataset más reciente que WD14 EVA02 → los tags de WD14 pueden no cubrir bien personajes/estilos nuevos. Un captioner con descripción natural puede ser más efectivo.

---

## Bugs conocidos activos

| Bug | Estado | Prioridad |
|-----|--------|-----------|
| ControlNet sin probar con modelos reales | Implementado (sesión 23) | Media |
| ComfyUI workflows guardados desaparecen | Conocido | Baja |
| beforeunload dispara en refresh | Conocido | Baja |
