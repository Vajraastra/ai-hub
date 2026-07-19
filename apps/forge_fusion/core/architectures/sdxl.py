"""
Adaptador SDXL (checkpoint completo: UNet + CLIP-L + CLIP-G + VAE en un solo
safetensors bajo checkpoints/ — el formato de todo el ecosistema Civitai:
Juggernaut, Pony, Illustrious, NoobAI…).

A diferencia de Z-Image no hay UNA base oficial: cualquier checkpoint del
almacén es base de merge válida (multi_base). Los tensores del UNet van
prefijados con `model.diffusion_model.`; los text encoders viven DENTRO del
fichero (`conditioner.embedders.*`) igual que el VAE (`first_stage_model.*`)
y ambos son zona prohibida: la cirugía de Forge Lab es solo sobre el UNet.
Coherencia runtime↔fichero: el workflow selectivo apaga los toggles de TE
del nodo, así el preview tampoco aplica esas claves.

Mapa de bloques del UNet SDXL (documentado públicamente; solo los 11 bloques
con capas de atención, que son los que entrena un LoRA estándar):

  input_blocks.4.1 / 5.1     SpatialTransformer 640,  depth 2
  input_blocks.7.1 / 8.1     SpatialTransformer 1280, depth 10
  middle_block.1             SpatialTransformer 1280, depth 10
  output_blocks.0.1–2.1      SpatialTransformer 1280, depth 10
  output_blocks.3.1–5.1      SpatialTransformer 640,  depth 2

(input_blocks 0–3/6 y output_blocks 6–8 son ResNet/updown sin atención: un
LoRA estándar no los toca.)  Cada SpatialTransformer (use_linear=True en
SDXL, todo Linear): proj_in, proj_out y por cada transformer_block attn1 y
attn2 (to_q/to_k/to_v/to_out.0) + ff.net.0.proj / ff.net.2.

Además de la atención, un LoCon (LyCORIS con conv_dim>0) entrena las capas
conv/ResNet y los embeddings globales (72 módulos, censados contra el
checkpoint real 2026-07-16): in_layers.2 / emb_layers.1 / out_layers.3 /
skip_connection de cada ResNet, los down/upsamplers, input_blocks.0.0,
out.2 y time_embed / label_emb. En la exploración se agrupan en 4
pseudo-bloques (conv_input / conv_mid / conv_output / embeddings) porque
la comunidad no dosifica conv por bloque individual; para el merge cada
grupo expande a sus prefijos de tensor reales y los bloques de atención se
refinan a su SpatialTransformer (.1) para que su dosis no sangre a los
ResNets vecinos (.0/.2) ahora que también están mapeados.

LoRAs soportados: formato kohya (lora_unet_*.lora_down/lora_up + alpha), el
de prácticamente todo Civitai, atención sola o con conv (LoCon). Las claves
de text encoder (lora_te1_/lora_te2_) se SALTAN por política
(ignored_lora_prefixes) — nunca se mergean; el nodo selectivo las apaga
también en runtime.
"""
from .base import ArchAdapter, BlockGroup, SamplingDefaults

# (bloque, índice del SpatialTransformer dentro del bloque, depth)
_TRANSFORMERS: list[tuple[str, int, int]] = (
    [("input_blocks.4", 1, 2), ("input_blocks.5", 1, 2),
     ("input_blocks.7", 1, 10), ("input_blocks.8", 1, 10),
     ("middle_block", 1, 10)]
    + [(f"output_blocks.{i}", 1, 10) for i in range(3)]
    + [(f"output_blocks.{i}", 1, 2) for i in range(3, 6)]
)

_BLOCKS = [b for b, _, _ in _TRANSFORMERS]

# id de bloque → nombre de input del nodo SDXLSelectiveLoRALoader
_NODE_NAMES = {
    "input_blocks.4": "input_4", "input_blocks.5": "input_5",
    "input_blocks.7": "input_7", "input_blocks.8": "input_8",
    "middle_block": "unet_mid",
    **{f"output_blocks.{i}": f"output_{i}" for i in range(6)},
}

# Pseudo-bloques conv/emb (LoCon) → prefijos de tensor reales que cubre cada
# uno (sin container_prefix; los entiende el merge worker). El id coincide
# con el nombre de input del nodo selectivo.
_CONV_GROUPS: dict[str, tuple[str, ...]] = {
    "conv_input": tuple(f"input_blocks.{n}.0" for n in range(9)),
    "conv_mid": ("middle_block.0", "middle_block.2"),
    "conv_output": tuple(f"output_blocks.{n}.0" for n in range(9))
                   + ("output_blocks.2.2", "output_blocks.5.2", "out.2"),
    "embeddings": ("time_embed", "label_emb"),
}
_CONV_LABELS = {"conv_input": "CONV-IN", "conv_mid": "CONV-MID",
                "conv_output": "CONV-OUT", "embeddings": "EMB"}


def _conv_module_paths() -> list[str]:
    """Los 72 módulos conv/ResNet/emb del UNet SDXL que entrena un LoCon
    (solo pesos con par lora down/up posible: convs y linears, nunca norms).
    skip_connection existe solo donde cambia el canal."""
    paths = ["input_blocks.0.0"]                       # conv de entrada
    for n in (1, 2, 4, 5, 7, 8):                       # ResNets del encoder
        base = f"input_blocks.{n}.0"
        paths += [f"{base}.in_layers.2", f"{base}.emb_layers.1",
                  f"{base}.out_layers.3"]
        if n in (4, 7):
            paths.append(f"{base}.skip_connection")
    paths += ["input_blocks.3.0.op", "input_blocks.6.0.op"]  # downsamplers
    for m in (0, 2):                                   # ResNets del bottleneck
        base = f"middle_block.{m}"
        paths += [f"{base}.in_layers.2", f"{base}.emb_layers.1",
                  f"{base}.out_layers.3"]
    for n in range(9):                                 # ResNets del decoder
        base = f"output_blocks.{n}.0"
        paths += [f"{base}.in_layers.2", f"{base}.emb_layers.1",
                  f"{base}.out_layers.3", f"{base}.skip_connection"]
    paths += ["output_blocks.2.2.conv", "output_blocks.5.2.conv",  # upsamplers
              "out.2", "time_embed.0", "time_embed.2",
              "label_emb.0.0", "label_emb.0.2"]
    return paths


class SdxlAdapter(ArchAdapter):
    name = "sdxl"
    label = "SDXL (checkpoint completo)"

    weights_root = "checkpoints"
    container_prefix = "model.diffusion_model."
    multi_base = True

    ignored_lora_prefixes = ("lora_te1_", "lora_te2_", "lora_te_")

    def file_keys(self) -> dict[str, tuple[str, ...]]:
        # un solo fichero: el checkpoint de trabajo por defecto (con
        # multi_base cualquier otro checkpoint también sirve de base)
        return {"diffusion_model": ("checkpoints",)}

    def model_files(self) -> dict[str, str]:
        # nombre canónico del base de Stability; el usuario elige su
        # checkpoint de trabajo real desde la UI (override persistido)
        return {"diffusion_model": "checkpoints/sd_xl_base_1.0.safetensors"}

    def required_nodes(self) -> list[str]:
        return ["SDXLSelectiveLoRALoader", "LoRALoaderWithAnalysis"]

    def list_blocks(self) -> list[str]:
        return list(_BLOCKS)

    def block_groups(self) -> list[BlockGroup]:
        # HIPÓTESIS de partida (conocimiento comunitario de LoRA Block
        # Weight + docs del nodo selectivo: output_1 domina estilo/color,
        # input_8/output_0 composición, output_3 caras). Verificar con el
        # set fijo antes de fiarse de las etiquetas.
        return [
            BlockGroup(
                id="encoder", label="Encoder / composición",
                blocks=["input_blocks.4", "input_blocks.5",
                        "input_blocks.7", "input_blocks.8"],
                description="Bloques de bajada: input 7/8 son los de mayor "
                            "impacto en composición y estructura.",
            ),
            BlockGroup(
                id="mid", label="Bottleneck",
                blocks=["middle_block"],
                description="Cuello de botella: semántica global, impacto "
                            "medio-alto.",
            ),
            BlockGroup(
                id="decoder", label="Decoder / estilo y detalle",
                blocks=[f"output_blocks.{i}" for i in range(6)],
                description="Bloques de subida: output_0 composición, "
                            "output_1 el más fuerte para estilo/color, "
                            "output_3 caras; 4–5 detalle fino.",
            ),
            BlockGroup(
                id="conv", label="Conv / ResNet (LoCon)",
                blocks=list(_CONV_GROUPS),
                description="Capas conv/ResNet y embeddings que solo "
                            "entrenan los LoCon (LyCORIS). En LoRAs de "
                            "atención pura no tienen efecto: textura y "
                            "detalle de bajo nivel cuando existen.",
            ),
        ]

    def forbidden_zones(self) -> list[str]:
        # dentro del checkpoint completo: text encoders y VAE
        return ["conditioner.", "first_stage_model."]

    def lora_key_map(self) -> dict[str, tuple[str, tuple[int, int, int] | None]]:
        # Enumeración estática del UNet SDXL (idéntico en todos los
        # checkpoints de la arquitectura, Pony/Illustrious incluidos).
        # Módulo kohya = "lora_unet_" + ruta con puntos→underscores;
        # target = clave completa del checkpoint. Sin franjas: SDXL no
        # fusiona proyecciones (q/k/v son tensores separados).
        m: dict[str, tuple[str, tuple[int, int, int] | None]] = {}

        def add(path: str):
            m["lora_unet_" + path.replace(".", "_")] = (
                f"model.diffusion_model.{path}.weight", None)

        for block, st_idx, depth in _TRANSFORMERS:
            base = f"{block}.{st_idx}"
            add(f"{base}.proj_in")
            add(f"{base}.proj_out")
            for t in range(depth):
                tb = f"{base}.transformer_blocks.{t}"
                for attn in ("attn1", "attn2"):
                    for proj in ("to_q", "to_k", "to_v", "to_out.0"):
                        add(f"{tb}.{attn}.{proj}")
                add(f"{tb}.ff.net.0.proj")
                add(f"{tb}.ff.net.2")
        for path in _conv_module_paths():   # conv/ResNet/emb (LoCon)
            add(path)
        return m

    # ── Exploración (SDXLSelectiveLoRALoader) ─────────────────────────────

    def explore_switches(self) -> list[dict]:
        labels = {"input_blocks.4": "IN4", "input_blocks.5": "IN5",
                  "input_blocks.7": "IN7", "input_blocks.8": "IN8",
                  "middle_block": "MID",
                  **{f"output_blocks.{i}": f"OUT{i}" for i in range(6)}}
        # sin "other": los TE se apagan por política y el resto de claves
        # no tendría equivalente en el merge (preview ≠ fichero)
        return ([{"id": b, "label": labels[b]} for b in _BLOCKS]
                + [{"id": g, "label": _CONV_LABELS[g]} for g in _CONV_GROUPS])

    def selective_node_inputs(self, config: dict, exponent: int) -> dict:
        out = {
            # política: text encoders y resto SIEMPRE apagados, para que el
            # preview runtime sea equivalente al merge (que nunca los toca)
            "text_encoder_1": False, "text_encoder_1_str": 1.0,
            "text_encoder_2": False, "text_encoder_2_str": 1.0,
            "other_weights": False, "other_weights_str": 1.0,
        }
        for b in _BLOCKS:
            d = config["blocks"].get(b, 0.0)
            name = _NODE_NAMES[b]
            out[name] = d > 0
            out[f"{name}_str"] = round(d ** (1 / exponent), 6) if d > 0 else 1.0
        for g in _CONV_GROUPS:      # los pseudo-bloques conv/emb del nodo
            d = config["blocks"].get(g, 0.0)
            out[g] = d > 0
            out[f"{g}_str"] = round(d ** (1 / exponent), 6) if d > 0 else 1.0
        return out

    def config_to_merge_blocks(self, config: dict) -> dict[str, float]:
        # atención → su SpatialTransformer (.1): con los ResNets (.0/.2) ya
        # mapeados, el prefijo de bloque entero mezclaría dosis de atención
        # y conv. Los grupos conv expanden a sus prefijos reales.
        out: dict[str, float] = {}
        for b, d in config["blocks"].items():
            if d == 0:      # dosis firmada (s82): las negativas SÍ mergean
                continue
            for prefix in _CONV_GROUPS.get(b, (f"{b}.1",)):
                out[prefix] = d
        return dict(sorted(out.items()))

    def sampling_defaults(self) -> SamplingDefaults:
        return SamplingDefaults(
            cfg=6.0, steps=28,
            sampler="dpmpp_2m", scheduler="karras",
            width=1024, height=1024,
        )

    def workflow_name(self, task: str) -> str:
        return {"txt2img": "txt2img.json",
                "txt2img_lora_selective": "txt2img_lora_selective.json"}[task]
