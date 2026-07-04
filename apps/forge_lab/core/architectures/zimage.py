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
from .base import ArchAdapter, BlockGroup, ModelFiles, SamplingDefaults

_N_LAYERS = 30


class ZImageAdapter(ArchAdapter):
    name = "zimage"
    label = "Z-Image (De-Turbo)"

    def model_files(self) -> ModelFiles:
        return ModelFiles(
            diffusion_model="diffusion_models/z_image_de_turbo_v1_bf16.safetensors",
            text_encoder="clip/qwen_3_4b.safetensors",
            vae="vae/ae.safetensors",   # ZImage usa latent_formats.Flux (VAE de Flux)
        )

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

    def sampling_defaults(self) -> SamplingDefaults:
        return SamplingDefaults(
            cfg=2.5, steps=25,           # rango De-Turbo: CFG 2.0–3.0, 20–30
            sampler="euler", scheduler="simple",
            width=1024, height=1024,
        )

    def workflow_name(self, task: str) -> str:
        return {"txt2img": "txt2img.json"}[task]
