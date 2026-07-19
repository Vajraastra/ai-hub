"""
Adaptador Z-Image (S3-DiT 6B, variante de trabajo: ostris/Z-Image-De-Turbo).

Estructura real del DiT (inspección del safetensors, 2026-07-04):
  layers.0 … layers.29   30 bloques transformer single-stream (13 tensores c/u)
  noise_refiner.*        refinador de tokens de imagen (2 bloques)
  context_refiner.*      refinador de tokens de caption (2 bloques)
  x_embedder / t_embedder / cap_embedder / cap_pad_token / final_layer

El text encoder (Qwen3-4B) va en fichero aparte y NUNCA se mergea aquí;
además su interfaz dentro del DiT (cap_embedder) es zona prohibida por
defecto (regla del handoff §4.5).

Reglas duras de la variante:
  - Merge/entrenamiento SIEMPRE sobre De-Turbo; el Turbo destilado colapsa.
  - Inferencia De-Turbo: CFG 2.0–3.0, 20–30 steps (Turbo usaba cfg=1).
"""
from .base import ArchAdapter, BlockGroup, SamplingDefaults

_N_LAYERS = 30


class ZImageAdapter(ArchAdapter):
    name = "zimage"
    label = "Z-Image (De-Turbo)"

    # layout por piezas: DiT suelto en diffusion_models/, claves desnudas
    weights_root = "diffusion_models"
    container_prefix = ""
    multi_base = False

    def file_keys(self) -> dict[str, tuple[str, ...]]:
        return {
            "diffusion_model": ("diffusion_models",),
            "text_encoder": ("clip", "text_encoders"),
            "vae": ("vae",),
        }

    def model_files(self) -> dict[str, str]:
        return {
            "diffusion_model": "diffusion_models/z_image_de_turbo_v1_bf16.safetensors",
            "text_encoder": "clip/qwen_3_4b.safetensors",
            "vae": "vae/ae.safetensors",  # ZImage usa latent_formats.Flux (VAE de Flux)
        }

    def required_nodes(self) -> list[str]:
        return ["ZImageSelectiveLoRALoader", "LoRALoaderWithAnalysis",
                "ScheduledLoRALoader"]

    def list_blocks(self) -> list[str]:
        return (["noise_refiner", "context_refiner"]
                + [f"layers.{i}" for i in range(_N_LAYERS)])

    def block_groups(self) -> list[BlockGroup]:
        # HIPÓTESIS inicial (tendencia DiT: capas tempranas→estructura,
        # centrales→semántica/estilo, finales→textura/detalle). Se corrige
        # con los analyzers y el set fijo; no fiarse de estas etiquetas aún.
        third = _N_LAYERS // 3
        return [
            BlockGroup(
                id="structure", label="Estructura / composición",
                blocks=["noise_refiner"] + [f"layers.{i}" for i in range(third)],
                description="Refinador de imagen y capas tempranas: layout, "
                            "poses, composición global.",
            ),
            BlockGroup(
                id="semantics", label="Semántica / estilo",
                blocks=["context_refiner"] + [f"layers.{i}" for i in range(third, 2 * third)],
                description="Refinador de caption y capas centrales: identidad "
                            "de conceptos, look general.",
            ),
            BlockGroup(
                id="texture", label="Textura / detalle",
                blocks=[f"layers.{i}" for i in range(2 * third, _N_LAYERS)],
                description="Capas finales: acabado, microdetalle, render.",
            ),
        ]

    def forbidden_zones(self) -> list[str]:
        # Interfaz del texto dentro del DiT. El text encoder Qwen ni siquiera
        # entra al flujo de merge (fichero aparte, nunca se toca).
        return ["cap_embedder", "cap_pad_token"]

    def lora_key_map(self) -> dict[str, tuple[str, tuple[int, int, int] | None]]:
        # Dos familias de naming en los LoRAs Z-Image reales (censo s78):
        #  - diffusers (ai-toolkit y cía., 21/22 en disco): q/k/v SEPARADOS;
        #    el checkpoint fusiona qkv [3*H, H] → cada proyección aterriza en
        #    su franja de filas (dim 0). Réplica exacta (solo .weight) de
        #    comfy.utils.z_image_to_diffusers. Este naming pelado es el
        #    canónico: ComfyUI lo carga tal cual (rama Lumina2 de lora.py).
        #  - nativa del checkpoint (zturbo_bimbo_alpha): qkv FUSIONADO y
        #    attention.out. El pelado nativo NO lo carga ComfyUI, pero su
        #    alias kohya (lora_unet_…, generado del state dict en el bloque
        #    genérico de model_lora_keys_unet) SÍ → se registran ambos: el
        #    pelado para que derive/dosis lo resuelvan y el kohya para que
        #    canonical() del fuse_worker emita claves cargables.
        # OJO: NO añadir alias kohya de los nombres diffusers — ComfyUI no
        # los genera para Lumina2 y serían claves muertas en el fusionado.
        H = 3840  # hidden size del S3-DiT 6B
        m: dict[str, tuple[str, tuple[int, int, int] | None]] = {}
        prefixes = ([f"layers.{i}" for i in range(_N_LAYERS)]
                    + [f"context_refiner.{i}" for i in range(2)]
                    + [f"noise_refiner.{i}" for i in range(2)])

        def add_native(path: str, target: tuple):
            m[path] = target                                   # pelado
            m["lora_unet_" + path.replace(".", "_")] = target  # kohya
        for p in prefixes:
            qkv = f"{p}.attention.qkv.weight"
            m[f"{p}.attention.to_q"] = (qkv, (0, 0, H))
            m[f"{p}.attention.to_k"] = (qkv, (0, H, H))
            m[f"{p}.attention.to_v"] = (qkv, (0, 2 * H, H))
            m[f"{p}.attention.to_out.0"] = (f"{p}.attention.out.weight", None)
            for w in ("w1", "w2", "w3"):
                m[f"{p}.feed_forward.{w}"] = (f"{p}.feed_forward.{w}.weight", None)
            m[f"{p}.adaLN_modulation.0"] = (f"{p}.adaLN_modulation.0.weight", None)
            add_native(f"{p}.attention.qkv", (qkv, None))
            add_native(f"{p}.attention.out", (f"{p}.attention.out.weight", None))
        return m

    # ── Exploración (ZImageSelectiveLoRALoader) ───────────────────────────

    def explore_switches(self) -> list[dict]:
        return ([{"id": f"layers.{i}", "label": str(i)} for i in range(_N_LAYERS)]
                + [{"id": "other", "label": "other (refiners+resto)"}])

    def selective_node_inputs(self, config: dict, exponent: int) -> dict:
        out = {}
        for i in range(_N_LAYERS):
            d = config["blocks"].get(f"layers.{i}", 0.0)
            out[f"layer_{i}"] = d > 0
            out[f"layer_{i}_str"] = round(d ** (1 / exponent), 6) if d > 0 else 1.0
        other = config.get("other", 0.0)
        out["other_weights"] = other > 0
        out["other_weights_str"] = (round(other ** (1 / exponent), 6)
                                    if other > 0 else 1.0)
        return out

    def config_to_merge_blocks(self, config: dict) -> dict[str, float]:
        # dosis firmada (s82): las negativas SÍ mergean (restan)
        blocks = {b: d for b, d in config["blocks"].items() if d != 0}
        # "other" del nodo cubre las claves fuera de layers.*: en los LoRAs
        # de Z-Image eso son los refiners (los embedders no se entrenan)
        other = config.get("other", 0.0)
        if other != 0:
            blocks["noise_refiner"] = other
            blocks["context_refiner"] = other
        return dict(sorted(blocks.items()))

    def sampling_defaults(self) -> SamplingDefaults:
        return SamplingDefaults(
            cfg=2.5, steps=25,           # rango De-Turbo: CFG 2.0–3.0, 20–30
            sampler="euler", scheduler="simple",
            width=1024, height=1024,
        )

    def workflow_name(self, task: str) -> str:
        return {"txt2img": "txt2img.json",
                "txt2img_lora_selective": "txt2img_lora_selective.json"}[task]
