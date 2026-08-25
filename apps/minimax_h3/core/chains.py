"""
Encadenado de tramos (v2, s96) -- une clips cortos de H3 en una escena mas
larga via I2V: cada tramo nuevo (2do en adelante) arranca desde el ultimo
frame del tramo ACEPTADO anterior. `pipeline.py` ya soporta esto
(`first_frame_b64` -> `MiniMaxH3ImageToVideo`). Se probo en vivo (s96,
`tests/proto_continuity.py`) si la imagen de referencia de Ref2V daba la
misma continuidad literal: NO (MAE de pixeles ~20x peor, el encuadre ni
coincide) -- por eso Ref2V NUNCA se usa para continuar la cadena.

Ref2V SI tiene un rol: como MODO DE INICIO opcional del tramo 1 (`start.mode
== "ref2v"`, ver `create_chain`) -- varias imagenes de referencia dan control
de identidad/estilo que T2V/I2V no tiene. Desde el tramo 2 la cadena siempre
usa fl2va+I2V sin excepcion, sea cual sea como arranco el tramo 1 (la
continuidad solo necesita el ultimo frame como imagen, es agnostica a que
checkpoint la produjo).

Un tramo puede tener varias VERSIONES (una por cada generar/regenerar); el
usuario elige cual ACEPTAR antes de poder generar el siguiente (su imagen
inicial depende del ultimo frame del tramo aceptado anterior). La cadena es
append-only: editar o borrar un tramo en medio invalida la continuidad de
todo lo que viene despues, asi que se borra en cascada en vez de permitir
reordenamiento libre.

Los videos en si NO se duplican -- cada version es un run_id del historial
normal de `pipeline.py` (`data/history/<run_id>.{mp4,json}`); este modulo
solo guarda la estructura de tramos + que run_id esta aceptado en cada uno.
"""
import json
import time
import uuid
from pathlib import Path

from . import pipeline
from .pipeline import GenParams
from .video_utils import last_frame_b64, concat_videos, VideoUtilError

_DATA = Path(__file__).parent.parent / "data"
_CHAINS_DIR = _DATA / "chains"


class ChainError(Exception):
    pass


def _chain_path(chain_id: str) -> Path:
    p = (_CHAINS_DIR / f"{chain_id}.json").resolve()
    if not p.is_relative_to(_CHAINS_DIR.resolve()):
        raise ChainError("cadena inválida")
    return p


def create_chain(name: str, base_params: dict, start: dict | None = None) -> dict:
    """`start`: cómo se genera el TRAMO 1 (el resto de la cadena siempre usa
    `base_params` + fl2va/I2V, sin excepción — ver `generate_segment_version`).
    `{"mode": "t2v_i2v"}` (default) usa `base_params` igual que cualquier otro
    tramo (T2V puro, o I2V si `base_params.first_frame_b64` trae una imagen).
    `{"mode": "ref2v", "unet_name", "turbo_lora_name", "turbo_strength",
    "turbo_steps", "ref_images_b64"}` usa el checkpoint Ref2V con varias
    imágenes de referencia — ver `pipeline.Ref2VParams`."""
    _CHAINS_DIR.mkdir(parents=True, exist_ok=True)
    chain_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    chain = {
        "id": chain_id,
        "name": name or "Cadena sin título",
        "created": time.time(),
        "base_params": base_params,   # modelos/turbo/loras/geometría comunes al tramo 2+
        "start": start or {"mode": "t2v_i2v"},
        "segments": [],                # [{id, prompt, duration_s, seed, versions:[run_id], accepted}]
    }
    _save(chain)
    return chain


def _save(chain: dict):
    _chain_path(chain["id"]).write_text(
        json.dumps(chain, ensure_ascii=False, indent=2), encoding="utf-8")


def load_chain(chain_id: str) -> dict:
    p = _chain_path(chain_id)
    if not p.is_file():
        raise ChainError("cadena no encontrada")
    return json.loads(p.read_text(encoding="utf-8"))


def list_chains() -> list[dict]:
    if not _CHAINS_DIR.exists():
        return []
    out = []
    for f in sorted(_CHAINS_DIR.glob("*.json"), reverse=True):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "id": c["id"], "name": c.get("name", ""), "created": c.get("created"),
            "segments": len(c.get("segments", [])),
            "accepted": sum(1 for s in c.get("segments", []) if s.get("accepted")),
        })
    return out


def delete_chain(chain_id: str):
    _chain_path(chain_id).unlink(missing_ok=True)


def add_segment(chain_id: str, prompt: str, duration_s: float, seed: int = -1) -> dict:
    if not prompt.strip():
        raise ChainError("hace falta un prompt para el tramo")
    chain = load_chain(chain_id)
    segs = chain["segments"]
    if segs and not segs[-1].get("accepted"):
        raise ChainError("el tramo anterior no tiene versión aceptada todavía")
    seg = {"id": uuid.uuid4().hex[:10], "prompt": prompt, "duration_s": duration_s,
          "seed": seed, "versions": [], "accepted": None}
    segs.append(seg)
    _save(chain)
    return seg


def update_segment(chain_id: str, segment_id: str, prompt: str | None = None,
                   duration_s: float | None = None, seed: int | None = None):
    """Edita prompt/duración/seed de un tramo. Seguro en cualquier momento: no
    toca versiones ya generadas, solo cambia con qué se genera la PRÓXIMA."""
    chain = load_chain(chain_id)
    idx, seg = _segment(chain, segment_id)
    if prompt is not None:
        if not prompt.strip():
            raise ChainError("hace falta un prompt para el tramo")
        seg["prompt"] = prompt
    if duration_s is not None:
        seg["duration_s"] = duration_s
    if seed is not None:
        seg["seed"] = seed
    _save(chain)


def _segment(chain: dict, segment_id: str) -> tuple[int, dict]:
    for i, s in enumerate(chain["segments"]):
        if s["id"] == segment_id:
            return i, s
    raise ChainError("tramo no encontrado")


def _first_frame_for(chain: dict, index: int) -> str | None:
    """b64 del último frame del tramo aceptado anterior. En el primer tramo,
    None (T2V puro) salvo que la cadena traiga una imagen inicial explícita
    en base_params (arrancar la cadena entera desde una imagen dada)."""
    if index == 0:
        return chain["base_params"].get("first_frame_b64")
    prev = chain["segments"][index - 1]
    if not prev.get("accepted"):
        raise ChainError("el tramo anterior no tiene versión aceptada")
    video = pipeline.history_video(prev["accepted"])
    return last_frame_b64(video)


async def generate_segment_version(comfy, chain_id: str, segment_id: str,
                                   on_phase=None, on_step=None) -> dict:
    chain = load_chain(chain_id)
    idx, seg = _segment(chain, segment_id)
    bp = chain["base_params"]

    if idx == 0 and chain.get("start", {}).get("mode") == "ref2v":
        st = chain["start"]
        params = pipeline.Ref2VParams(
            prompt=seg["prompt"], duration_s=seg["duration_s"], seed=seg["seed"],
            unet_name=st["unet_name"], clip_name=bp["clip_name"],
            video_vae_name=bp["video_vae_name"], audio_vae_name=bp["audio_vae_name"],
            width=bp.get("width", 1344), height=bp.get("height", 768),
            turbo_lora_name=st["turbo_lora_name"],
            turbo_strength=st.get("turbo_strength", 1.0),
            turbo_steps=st.get("turbo_steps", 4),
            ref_images_b64=st["ref_images_b64"],
        )
        manifest = await pipeline.generate_ref2v(comfy, params, on_phase=on_phase, on_step=on_step)
    else:
        first_frame = _first_frame_for(chain, idx)
        params = GenParams(
            prompt=seg["prompt"], first_frame_b64=first_frame,
            duration_s=seg["duration_s"], seed=seg["seed"],
            unet_name=bp["unet_name"], clip_name=bp["clip_name"],
            video_vae_name=bp["video_vae_name"], audio_vae_name=bp["audio_vae_name"],
            width=bp.get("width", 1344), height=bp.get("height", 768),
            turbo=bp.get("turbo", True), turbo_lora_name=bp.get("turbo_lora_name", ""),
            turbo_strength=bp.get("turbo_strength", 1.0), turbo_steps=bp.get("turbo_steps", 8),
            steps=bp.get("steps", 20), loras=bp.get("loras", []),
        )
        manifest = await pipeline.generate(comfy, params, on_phase=on_phase, on_step=on_step)

    # Re-leer del disco: el archivo pudo haber sido tocado por otra request
    # mientras esta generación (varios minutos) estaba en curso.
    chain = load_chain(chain_id)
    idx, seg = _segment(chain, segment_id)
    seg["versions"].append(manifest["id"])
    _save(chain)
    return manifest


def accept_version(chain_id: str, segment_id: str, run_id: str):
    chain = load_chain(chain_id)
    idx, seg = _segment(chain, segment_id)
    if run_id not in seg["versions"]:
        raise ChainError("esa versión no pertenece a este tramo")
    seg["accepted"] = run_id
    _save(chain)


def delete_segment_cascade(chain_id: str, segment_id: str):
    """Borra este tramo y todos los que vienen después (su continuidad ya no
    es válida al perder el tramo del que dependían). Los archivos de video en
    sí quedan en el historial general -- no se borran, solo se desvinculan
    de la cadena."""
    chain = load_chain(chain_id)
    idx, _ = _segment(chain, segment_id)
    chain["segments"] = chain["segments"][:idx]
    _save(chain)


def delete_version(chain_id: str, segment_id: str, run_id: str):
    chain = load_chain(chain_id)
    idx, seg = _segment(chain, segment_id)
    if seg.get("accepted") == run_id:
        raise ChainError("no se puede borrar la versión aceptada — acepta otra primero")
    seg["versions"] = [v for v in seg["versions"] if v != run_id]
    _save(chain)


def export_final(chain_id: str) -> str:
    """Concatena los tramos aceptados en un clip final y lo registra en el
    historial general (mismo run_id/manifest que cualquier generación, así
    aparece también en la pestaña Historial)."""
    chain = load_chain(chain_id)
    accepted_ids = [s["accepted"] for s in chain["segments"] if s.get("accepted")]
    if not accepted_ids:
        raise ChainError("no hay tramos aceptados para exportar")

    metas = [pipeline.history_meta(r) for r in accepted_ids]
    videos = [pipeline.history_video(r) for r in accepted_ids]

    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-chain-{chain_id[-6:]}"
    pipeline.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = pipeline.HISTORY_DIR / f"{run_id}.mp4"
    try:
        concat_videos(videos, out_path)
    except VideoUtilError as e:
        raise ChainError(f"error concatenando los tramos: {e}")

    manifest = {
        "id": run_id, "ext": "mp4", "created": time.time(),
        "prompt": f'[Cadena "{chain["name"]}"] ' + " → ".join(
            (m.get("prompt") or "")[:40] for m in metas),
        "seed": None,
        "length_frames": sum(m.get("length_frames", 0) for m in metas),
        "duration_s": round(sum(m.get("duration_s", 0) for m in metas), 2),
        "render": {"chain_id": chain_id, "segments": accepted_ids},
    }
    (pipeline.HISTORY_DIR / f"{run_id}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_id
