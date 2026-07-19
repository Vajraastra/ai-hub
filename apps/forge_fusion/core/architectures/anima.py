"""
Adaptador Anima (CircleStone Labs + Comfy Org; DiT Cosmos-Predict2 2B).

Estructura real del DiT (inspección de anima-base-v1.0.safetensors, 2026-07-16,
prefijo de fichero `net.`):
  blocks.0 … blocks.27   28 bloques transformer (self_attn + cross_attn con
                         q/k/v/output_proj SEPARADOS, mlp.layer1/2 y 3 pares
                         adaLN — todo lineal 2D, sin qkv fusionado ni conv)
  llm_adapter.*          adaptador del texto DENTRO del DiT (6 bloques que
                         proyectan el Qwen3-0.6B al cross-attn de 1024)
  final_layer / t_embedder / x_embedder / t_embedding_norm

El text encoder (Qwen3-0.6B) y el VAE (Qwen-Image) van en ficheros aparte y
nunca se mergean. A diferencia de zimage (cap_embedder = zona prohibida), el
llm_adapter SÍ lo entrenan LoRAs de la comunidad y el runtime de ComfyUI lo
aplica — así que aquí es un switch propio ("LLM"), no una zona prohibida:
la garantía preview ≡ merge manda.

Reglas duras de la variante:
  - Base de merge/entrenamiento: Anima-Base (o Aesthetic, mismo mapa); el
    Turbo destilado queda fuera (misma lección que el Z-Image Turbo).
  - Inferencia: CFG 4-6, 30-50 steps, er_sde/euler_a, shift 3.0 ya embebido
    en el sampling de ComfyUI (sin nodo ModelSampling extra).
"""
from .base import ArchAdapter, BlockGroup, SamplingDefaults

_N_BLOCKS = 28
_N_LLM_BLOCKS = 6

# Módulos LoRA-entrenables por bloque del DiT (censo de los 16 LoRAs reales:
# 8 proyecciones de atención + 2 mlp siempre; los 6 adaLN solo en algunos)
_BLOCK_MODULES = (
    "self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj",
    "self_attn.output_proj",
    "cross_attn.q_proj", "cross_attn.k_proj", "cross_attn.v_proj",
    "cross_attn.output_proj",
    "mlp.layer1", "mlp.layer2",
    "adaln_modulation_self_attn.1", "adaln_modulation_self_attn.2",
    "adaln_modulation_cross_attn.1", "adaln_modulation_cross_attn.2",
    "adaln_modulation_mlp.1", "adaln_modulation_mlp.2",
)

_LLM_MODULES = (
    "self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj",
    "self_attn.o_proj",
    "cross_attn.q_proj", "cross_attn.k_proj", "cross_attn.v_proj",
    "cross_attn.o_proj",
    "mlp.0", "mlp.2",
)

# Resto de lineales del DiT (solo los entrenan LoKr/full-finetunes raros,
# pero el mapa los cubre para que "other" sea coherente nodo↔merge)
_OTHER_MODULES = (
    "final_layer.linear",
    "final_layer.adaln_modulation.1", "final_layer.adaln_modulation.2",
    "t_embedder.1.linear_1", "t_embedder.1.linear_2",
    "x_embedder.proj.1",
    "llm_adapter.out_proj",
)


class AnimaAdapter(ArchAdapter):
    name = "anima"
    label = "Anima (Cosmos 2B)"

    # layout por piezas (DiT + TE + VAE separados) pero multi_base: cualquier
    # DiT anima del almacén (base/aesthetic/derivados de la comunidad) sirve
    # de base — se filtran por firma de header (net.llm_adapter.*)
    weights_root = "diffusion_models"
    container_prefix = "net."
    # la base oficial v1.0 va con "net."; aesthetic-v1.1 (HF) empaqueta los
    # MISMOS 685 tensores bajo "model.diffusion_model." — el merge worker
    # detecta la variante del fichero y re-apunta los targets del key_map
    container_prefix_variants = ("model.diffusion_model.",)
    multi_base = True

    # claves de text encoder de los LoRAs (kohya single-TE y PEFT): se saltan
    # por política, nunca se aplican ni mergean (preview ≡ merge)
    ignored_lora_prefixes = ("lora_te_", "text_encoder.")

    def file_keys(self) -> dict[str, tuple[str, ...]]:
        return {
            "diffusion_model": ("diffusion_models",),
            "text_encoder": ("clip", "text_encoders"),
            "vae": ("vae",),
        }

    def model_files(self) -> dict[str, str]:
        return {
            "diffusion_model": "diffusion_models/anima/anima-base-v1.0.safetensors",
            "text_encoder": "clip/anima/qwen_3_06b_base.safetensors",
            "vae": "vae/qwen_image_vae.safetensors",
        }

    def required_nodes(self) -> list[str]:
        return ["AnimaSelectiveLoRALoader"]

    def list_blocks(self) -> list[str]:
        return ["llm_adapter"] + [f"blocks.{i}" for i in range(_N_BLOCKS)]

    def block_groups(self) -> list[BlockGroup]:
        # HIPÓTESIS inicial (misma heurística DiT que zimage: tempranas →
        # estructura, centrales → semántica/estilo, finales → detalle). Se
        # corrige con la batería y el set fijo.
        third = _N_BLOCKS // 3
        return [
            BlockGroup(
                id="structure", label="Estructura / composición",
                blocks=[f"blocks.{i}" for i in range(third)],
                description="Capas tempranas: layout, poses, composición "
                            "global.",
            ),
            BlockGroup(
                id="semantics", label="Semántica / estilo",
                blocks=["llm_adapter"]
                       + [f"blocks.{i}" for i in range(third, 2 * third)],
                description="Adaptador del texto y capas centrales: identidad "
                            "de conceptos, look general.",
            ),
            BlockGroup(
                id="texture", label="Textura / detalle",
                blocks=[f"blocks.{i}" for i in range(2 * third, _N_BLOCKS)],
                description="Capas finales: acabado, microdetalle, render.",
            ),
        ]

    def forbidden_zones(self) -> list[str]:
        # Sin zonas prohibidas: el TE y el VAE van en ficheros aparte (nunca
        # entran al merge) y el llm_adapter es un switch explícito.
        return []

    def lora_key_map(self) -> dict[str, tuple[str, tuple[int, int, int] | None]]:
        """Réplica del mapeo runtime de ComfyUI (model_lora_keys_unet genera
        el alias kohya desde las claves del modelo): cada módulo aterriza
        entero en su tensor `net.<path>.weight` — sin franjas ni conv."""
        m: dict[str, tuple[str, tuple[int, int, int] | None]] = {}

        def add(path: str):
            target = (f"net.{path}.weight", None)
            m[path] = target                                   # PEFT
            m["lora_unet_" + path.replace(".", "_")] = target  # kohya

        for i in range(_N_BLOCKS):
            for mod in _BLOCK_MODULES:
                add(f"blocks.{i}.{mod}")
        for j in range(_N_LLM_BLOCKS):
            for mod in _LLM_MODULES:
                add(f"llm_adapter.blocks.{j}.{mod}")
        for path in _OTHER_MODULES:
            add(path)
        return m

    # ── Exploración (AnimaSelectiveLoRALoader) ────────────────────────────

    def explore_switches(self) -> list[dict]:
        return ([{"id": f"blocks.{i}", "label": str(i)}
                 for i in range(_N_BLOCKS)]
                + [{"id": "llm_adapter", "label": "LLM"},
                   {"id": "other", "label": "other (final+embed)"}])

    def selective_node_inputs(self, config: dict, exponent: int) -> dict:
        out = {}
        for i in range(_N_BLOCKS):
            d = config["blocks"].get(f"blocks.{i}", 0.0)
            out[f"block_{i}"] = d > 0
            out[f"block_{i}_str"] = round(d ** (1 / exponent), 6) if d > 0 else 1.0
        llm = config["blocks"].get("llm_adapter", 0.0)
        out["llm_adapter"] = llm > 0
        out["llm_adapter_str"] = round(llm ** (1 / exponent), 6) if llm > 0 else 1.0
        other = config.get("other", 0.0)
        out["other_weights"] = other > 0
        out["other_weights_str"] = (round(other ** (1 / exponent), 6)
                                    if other > 0 else 1.0)
        return out

    def config_to_merge_blocks(self, config: dict) -> dict[str, float]:
        # dosis firmada (s82): las negativas SÍ mergean (restan)
        blocks = {b: d for b, d in config["blocks"].items() if d != 0}
        # "other" del nodo cubre las claves fuera de blocks/llm_adapter/TE:
        # final_layer y embedders (solo los entrenan LoRAs raros)
        other = config.get("other", 0.0)
        if other != 0:
            for b in ("final_layer", "t_embedder", "x_embedder"):
                blocks[b] = other
        return dict(sorted(blocks.items()))

    def sampling_defaults(self) -> SamplingDefaults:
        return SamplingDefaults(
            cfg=4.5, steps=30,           # oficial: CFG 4-6, 30-50 steps
            sampler="er_sde", scheduler="simple",
            width=1024, height=1024,
        )

    def workflow_name(self, task: str) -> str:
        return {"txt2img": "txt2img.json",
                "txt2img_lora_selective": "txt2img_lora_selective.json"}[task]
