# Ideogram 4.0 — Investigación y plan de herramienta (AI Hub)

> Documento de referencia consolidado. Reúne TODA la información recabada sobre
> Ideogram 4.0 open-weight y su filtro de seguridad, para arrancar el build sin
> volver a investigar. Fecha de recopilación: **2026-07-05** (sesión 35d).
>
> ⚠️ **AVISO DE CORTE DE DATOS:** el modelo Fable 5 tiene corte enero-2026 y por
> eso asumió (dos veces) que Ideogram era un servicio cerrado en la nube. **ES
> FALSO desde jun-2026: Ideogram 4.0 es OPEN-WEIGHT y corre local.** No repetir
> el error. Toda la info de abajo está verificada por búsqueda web + los dos
> hilos de Reddit que el usuario pegó completos.

---

## 1. Qué es Ideogram 4.0 (hechos verificados)

- **Lanzamiento:** 3 de junio de 2026, **primer modelo open-weight de Ideogram**.
- **Tamaño:** 9.3B parámetros. Mejor renderizado de texto de cualquier
  open-weight comparado (por delante de Qwen-Image 20B, FLUX.2 dev 32B,
  HunyuanImage 3.0 80B MoE).
- **Fortalezas reales (por lo que lo queremos):** tipografía nítida, layout
  controlado, coherencia de prompt largo, diseño comercial, estética.
- **Dónde:**
  - Código: `github.com/ideogram-oss/ideogram4`
  - Pesos (gated, hay que aceptar el gate): `huggingface.co/ideogram-ai/ideogram-4-fp8`, `-nf4`
  - Empaquetado ComfyUI: `huggingface.co/Comfy-Org/Ideogram-4`
  - Quant int8 alternativa (comunidad): `huggingface.co/silveroxides/ideogram4-dequant-and-int8-quant`
- **Hardware:** build nf4 corre en 24 GB (RTX 4090). 16 GB va justo con
  offload (más lento). 32 GB+ cómodo.
- **Licencia:** *Ideogram Non-Commercial Model Agreement* — gratis uso personal
  y research; comercial requiere licencia de pago aparte. **Prohíbe NSFW** y la
  empresa pide reportes de deployments que quiten mitigaciones a
  `safety@ideogram.ai`. Para uso local/privado con contenido benigno es
  irrelevante. **Nunca ayudar con material ilegal (menores / no consentido).**

## 2. Ficheros del modelo (5) para ComfyUI

| Fichero | Rol | Tamaño aprox |
|---|---|---|
| `ideogram4_fp8_scaled.safetensors` | Modelo de difusión (condicional) | ~13.8 GB |
| `ideogram4_unconditional_fp8_scaled.safetensors` | Modelo incondicional (gemelo) | ~13.8 GB |
| `qwen3vl_8b_fp8_scaled.safetensors` | Text encoder | ~8 GB |
| `gemma4_e4b_it_fp8_scaled.safetensors` | Text encoder | ~2 GB |
| `flux2-vae.safetensors` | VAE | ~335 MB |

- Cada uno va en su subcarpeta de `ComfyUI/models/`.
- Quantización alterna: nvfp4 para el lado incondicional (su calidad no es
  crítica) + fp8 en el normal = buen balance de VRAM. Sin caída notable.

## 3. Arquitectura en ComfyUI (PR de integración: Comfy-Org/ComfyUI #14259, kijai)

- **DualModelGuider / DualModelCFGGuider:** el pase **condicional** corre en
  `ideogram4_fp8_scaled`; el **incondicional** en el segundo fichero. Al lado
  incondicional NO se le pasa prompt vacío: recibe **solo imagen, sin tokens de
  texto**.
- **Scheduler de sigmas logit-normal**, afinado al proceso de difusión de
  Ideogram 4. (Este es el "schedule de sigmas" que se puede desplazar.)
- **Importante para la calidad:** usar el modelo incondicional sube MUCHO la
  calidad visual, pero **duplica el tiempo de generación**. Sin él, la calidad
  "cae un montón" (Hoodfu). El VAE es flux2-vae.

## 4. El filtro de censura — cómo funciona REALMENTE

Esto es lo más importante y lo más malentendido:

1. **En LOCAL no hay filtro externo.** El `docs/safety.md` del repo describe 3
   capas: (a) filtrado del dataset en pre-training, (b) post-training para
   reducir prob. de generar NSFW, (c) **filtros externos Hive AI en
   inference — pero SOLO en el servicio de nube de Ideogram.** En tu
   instalación local Hive NO existe.
2. **Lo que bloquea en local es conducta aprendida, quemada en los pesos**
   (la capa b). No es un nodo separado que puedas borrar.
3. **El modelo GENERA el cuadro gris** (con texto de aviso deforme, distinto
   cada vez) — NO hace swap de una imagen guardada. Es un "atractor" en la
   trayectoria de denoising: cuando los primeros pasos arrancan "en vacío", la
   inferencia se encamina hacia esa salida gris. (Aclarado por Sixhaunt.)
4. **El filtro SOLO actúa en los primeros pasos.** Si esos pasos reciben
   CUALQUIER entrada coherente, la inferencia se encamina hacia la imagen real
   y el rechazo nunca se dispara. (Hallazgo clave de ChickyGolfy / qdr1en.)
5. **Da muchos falsos positivos sobre contenido benigno** — ejemplos reales
   del issue oficial #5: *"a 1950s oil painting of a fully clothed woman"*,
   *"a cute cat"*, *"a deckchair in the sun"* → bloqueados en texto plano.

## 5. Métodos de la comunidad (todos, rankeados)

### ⭐ PRINCIPAL — JSON + bounding boxes (Ideogram puro, 0 pérdida de calidad)
- Consenso de la gente con experiencia (Different_Fix_2217, kemb0,
  Murky-Relation481): estructurar el prompt en JSON con **una caja para el
  sujeto + varias cajas del entorno** (suelo, muebles, paredes) da **100% sin
  bloqueo**.
- **No es un workaround que degrade nada: es usar Ideogram como fue diseñado**
  (se entrenó con captions JSON). Y de paso mata los falsos positivos.
- Los que sufren censura suelen tener UNA sola bbox + lenguaje natural.
- **Esta es la vía elegida** porque responde exactamente a lo que el usuario
  quiere: potencia del formato JSON, bounding boxes, calidad de Ideogram, con
  Ideogram como generador único (no un retocador).

### Esquema JSON de caption (con el que se entrenó el modelo)
Campos: resumen de escena (scene summary), bloque de estilo (style block),
fondo (background), objetos con **bounding-box en coordenadas normalizadas** +
**paleta de hasta 16 colores hex**, y elementos de texto tipados (strings
literales a renderizar). Prompts de texto plano tienen tasa de falso positivo
mucho más alta que el JSON.

### RESPALDO avanzado — split-sigmas con 2 samplers (100% Ideogram, NO mete otro modelo)
Fuente: 2º hilo (qdr1en) + mejora de Hoodfu.
- Idea: el filtro solo actúa en los primeros pasos → dos
  `SamplerCustomAdvanced` en cadena con un nodo **SplitSigmas**.
- **Corte tras el paso 1** (si no funciona, probar 2, 3 o 4 — el paso 4 fue
  donde a Hoodfu dejó de aparecer el aviso siempre).
- Primer sampler: siembra los 1–4 pasos iniciales con un **JSON "limpio"**
  (versión benigna); segundo sampler: render completo con el JSON + bboxes.
- **CRÍTICO para calidad:** ambos samplers deben usar los DOS modelos
  (condicional + incondicional) vía **dos nodos `DualModelCFGGuider`**.
- Ideogram hace el 100% del render; el split solo cruza la zona minada.
- Caveat: el split puro puede dañar la composición en algunas semillas
  (a Hoodfu le pasó perdiendo los 4 primeros pasos).
- Workflows de ejemplo: `pastes.io/t1aw64As`, `pastebin.com/EYR21KvP`.

### Variante — sigma-shift / ruido x2 con sampler LCM (1er hilo, TRlG0N)
- **Método 1:** desplazar SOLO el primer paso de sigma **+0.005** (a veces
  +0.01). Los demás sigmas iguales.
- **Método 2 (preferido por el autor):** multiplicar el **ruido inicial x2**
  (a veces x3).
- **SOLO funciona con sampler LCM** (`SamplerLCMCustom`): LCM corrige la
  trayectoria tras la desviación; otros samplers rompen la imagen.
- Nodos: Noise Math (More Math), SamplerLCMCustom (Extra Samplers), Custom
  Sigmas (KJ Nodes), Sigmas2 Mult (RES4LYF). SplitSigmas corte 1–3.
- **Caveat:** degrada bastante la calidad (comparativas del propio hilo), y con
  prompts muy cortos el modelo sigue sin obedecer. Menos preferible que el
  split con DualModelCFGGuider.

### Variante — LoRA en el primer paso (ChickyGolfy)
- Entrenar cualquier LoRA (hasta uno malo sirve) y aplicarlo **solo en el 1er
  paso a strength 0.25** → mejor forma inicial que ruido puro. El punto real:
  cualquier entrada coherente en el 1er paso evita el rechazo.

### DESCARTADO — img2img Z-Image → Ideogram (denoise 0.94)
- Lo propuso Hoodfu; **el usuario lo descartó con razón**: convierte a Z-Image
  en el generador y a Ideogram en un simple retocador, justo lo contrario de
  querer a Ideogram de protagonista.
- Matiz técnico: a 0.94 denoise Ideogram rehace el 94%; Z-Image solo sembraba
  un ~6% de composición gruesa. Y el puente NO es un latent compartido
  (VAEs distintos: Ideogram usa flux2-vae) sino la imagen RGB re-codificada.
- **No usar. Z-Image se queda en Forge Lab.**

### Utilidad complementaria — nodo BlockGuard (INSANEF00L, TOS-safe)
- `github.com/Dragon7108/ComfyUI-BlockGuard`: **no evade nada**; detecta que
  salió el gris y **aborta la generación** para no gastar GPU renderizándolo.
- Añadir al workflow para no desperdiciar cómputo en el bloqueo residual.

### Solución permanente (fuera de scope por ahora) — fine-tune / LoKR
- TRlG0N / Timely-Perception-26: un LoKR o fine-tune pequeño (incluso sin
  captions) elimina el banner gris a strength 0.15. Es la vía definitiva pero
  la de mayor exposición a la licencia. No es el plan inmediato.

## 6. Decisiones tomadas con el usuario (cerradas)

1. **Ideogram como generador protagonista**, no retocador. → img2img descartado.
2. **Vía principal: JSON + bounding boxes** (usa el potencial de Ideogram y de
   paso evita el filtro; 0 pérdida de calidad).
3. **Respaldo: split-sigmas 100% Ideogram** con doble DualModelCFGGuider, solo
   si algún caso benigno se resiste al JSON.
4. **BlockGuard** para abortar el gris residual.
5. Objetivo = **falsos positivos sobre contenido benigno**. No se trabaja
   contenido ilegal.

## 7. Plan del build (próxima sesión)

Herramienta especializada Ideogram 4 dentro del hub (tipo módulo, como
Painter/Forge). Componentes:

1. **Generador de JSON estructurado:** el usuario escribe una descripción
   normal → un LLM (¿cuál? ver preguntas abiertas) la convierte al esquema de
   caption de Ideogram (scene / style / background / objetos con bbox + paleta
   hex / text elements). Debe generar **bounding boxes de sujeto + entorno**
   automáticamente (es lo que mata el filtro).
2. **Workflow ComfyUI base:** Ideogram4 con los 5 ficheros, DualModelCFGGuider
   (condicional + incondicional), **sin prompt upsampling** (el upsampling por
   defecto de AI Toolkit dispara falsos positivos — Ostris), scheduler
   logit-normal.
3. **Rama de respaldo (opcional/documentada):** split-sigmas 2 samplers, corte
   1–4, doble DualModelCFGGuider.
4. **BlockGuard** conectado.
5. **Integración en el hub-webui** siguiendo el patrón de los módulos
   existentes (routes + UI por pestañas). Calibrar en la instalación real
   (fp8 vs nf4 cambia el punto de corte del split).

## 8. Preguntas abiertas (resolver al arrancar)

- [ ] **¿Están descargados los 5 ficheros de Ideogram4?** (descarga gated en HF,
      grande — condiciona todo el arranque.)
- [ ] **¿El ComfyUI objetivo es `apps/comfyui`?** (la misma instalación que usa
      Forge Lab — user dir único confirmado en sesión 35c.)
- [ ] **¿Qué LLM local para el paso descripción→JSON?** ¿Hay uno corriendo en el
      hub o se hace con plantilla + relleno sin LLM?
- [ ] ¿Los nodos custom del respaldo (SplitSigmas, More Math, Extra Samplers,
      KJ Nodes, RES4LYF, BlockGuard) están instalados en ese ComfyUI?

## 9. Referencias (URLs verificadas)

- Tutorial oficial ComfyUI: `docs.comfy.org/tutorials/image/ideogram/ideogram-v4`
- Blog ComfyUI (day-0): `blog.comfy.org/p/ideogram-4-day-0-support-in-comfyui`
- PR integración (kijai): `github.com/Comfy-Org/ComfyUI/pull/14259`
- safety.md oficial: `github.com/ideogram-oss/ideogram4/blob/main/docs/safety.md`
- Issue #5 falsos positivos: `github.com/ideogram-oss/ideogram4/issues/5`
- HF discusión Safety Filter: `huggingface.co/ideogram-ai/ideogram-4-fp8/discussions/15`
- HF "bypassing safety filter": `huggingface.co/Comfy-Org/Ideogram-4/discussions/7`
- Nomadoor workflow base: `comfyui.nomadoor.net/en/basic-workflows/ideogram-4/`
- Reddit hilo 1 (sigma-shift/LCM): `reddit.com/r/StableDiffusion/comments/1tz4fnf`
- Reddit hilo 2 (split-sigmas, el mejor): `reddit.com/r/comfyui/comments/1txurpt`
- BlockGuard: `github.com/Dragon7108/ComfyUI-BlockGuard`

> Nota: Reddit no se puede fetchear desde el entorno (bloqueado). El contenido
> de los dos hilos está transcrito arriba porque el usuario los pegó completos.
