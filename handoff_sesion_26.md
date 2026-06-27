# Handoff Sesión 26 → 27

## Estado al cerrar

Sin commits nuevos. Cambios pendientes de confirmar en vivo antes de commitear.

**Archivos modificados esta sesión:**
- `hub/scripts/install_standalone_trainer.sh` — bloque de symlink outputs agregado
- `training-ui/jobs/` — convertido a symlink (ya en disco, no es un archivo del repo)

---

## Symlink outputs — Qué se hizo y por qué

**Problema:** safetensors e imágenes de muestra quedaban enterradas en:
```
apps/anima-standalone-trainer/training-ui/jobs/<job>/output/
apps/anima-standalone-trainer/training-ui/jobs/<job>/output/sample/
```

**Solución:** symlink que redirige el directorio `jobs/` del trainer al hub outputs centralizado.

```
training-ui/jobs/  →  outputs/anima-standalone/
```

El trainer escribe normalmente en `jobs/` sin modificación. Todo aterriza en:
```
outputs/anima-standalone/
└── <nombre-del-job>/
    ├── output/
    │   ├── mi_lora-000001.safetensors
    │   ├── mi_lora-000001-state/        ← optimizer state (solo para resume)
    │   └── sample/
    │       └── mi_lora_e000001_00_....png
    └── logs/
```

**Smoke tests pasados:**
- Escritura a través del symlink llega a `outputs/anima-standalone/`
- Mismo inode (no copia)
- `find -L` desde el path del trainer también los ve
- Install script cubre 3 casos: symlink correcto (skip), symlink incorrecto (reemplazar), directorio real (migrar + reemplazar)

**Symlink activo en disco:**
```
apps/anima-standalone-trainer/training-ui/jobs -> /run/media/system/Kilaya/ai-hub/outputs/anima-standalone
```

---

## Pendiente confirmar en pruebas en vivo

1. Lanzar un training job desde la UI
2. Verificar que los safetensors aparecen en `outputs/anima-standalone/<job>/output/`
3. Verificar que las imágenes de muestra aparecen en `outputs/anima-standalone/<job>/output/sample/`
4. Si todo OK → commit de sesiones 25+26

---

## Por qué 2 carpetas de safetensors (recordatorio)

- `mi_lora-000001.safetensors` — el LoRA usable en ComfyUI
- `mi_lora-000001-state/` — **carpeta** con estado del optimizer (AdamW momentum, LR scheduler, RNG) para `auto_resume`. Pesa mucho más que el LoRA. Con `save_last_n_epochs_state: 1` solo conserva el último.

---

## Próxima sesión — Temas posibles

1. **Confirmar training en vivo** y hacer commit si pasó
2. **Captioner local** — investigar Gemma 4 / LLaVA / InternVL / Qwen2-VL para captions de dataset
   - Gemma 3 tiene variante multimodal 4B/12B
   - Viabilidad en 16 GB VRAM con otras apps activas
   - Alternativas ya en disco: Qwen (tenemos el VAE de Anima), Florence-2 (en TaggerGUI)

---

## Bugs conocidos activos

| Bug | Estado | Prioridad |
|-----|--------|-----------|
| ControlNet sin probar con modelos reales | Implementado (sesión 23) | Media |
| ComfyUI workflows guardados desaparecen | Conocido | Baja |
| beforeunload dispara en refresh | Conocido | Baja |
