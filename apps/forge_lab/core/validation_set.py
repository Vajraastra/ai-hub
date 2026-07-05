"""
Sets de validación fijos — el cimiento no negociable (handoff §5).

Un set = lista de prompts (cada uno con categoría y seed propia) + parámetros
de sampling comunes. Todo es editable SOLO mientras el set está en borrador;
al bloquearlo (`lock()`) queda inmutable para siempre — a partir de ahí solo
se puede clonar a un set nuevo. Toda comparación antes/después de un merge se
hace regenerando el MISMO set bloqueado.

Categorías de prompt requeridas para poder bloquear:
  - "target"  : dominio objetivo (render 3D multiestilo)
  - "general" : fuera de dominio (medir pérdida de generalidad)
  - "stress"  : casos que rompen modelos (manos, multi-personaje, texto, poses)

Persistencia:
  data/validation_sets/<name>.json          el set (se versiona en git)
  data/validation_runs/<name>/<run_id>/     imágenes + run.json (NO se versiona)

Cada run guarda el `fingerprint` (SHA256 del contenido efectivo del set) para
poder verificar que dos runs comparados regeneraron exactamente lo mismo.
Los runs sobre sets en borrador se marcan `draft: true` — sirven para calibrar
los prompts antes de bloquear, nunca como evidencia de comparación.
"""
import asyncio
import hashlib
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

DATA_DIR = Path(__file__).parent.parent / "data" / "validation_sets"
RUNS_DIR = Path(__file__).parent.parent / "data" / "validation_runs"

CATEGORIES = ["target", "general", "stress"]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Campos de sampling que todo set debe fijar (coinciden con SamplingDefaults)
_SAMPLING_FIELDS = ["cfg", "steps", "sampler", "scheduler", "width", "height"]


class ValidationSetError(Exception):
    pass


class LockedError(ValidationSetError):
    """Intento de mutar un set ya bloqueado."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _validate_prompts(prompts: list[dict]) -> list[dict]:
    """Normaliza y valida la lista de prompts (vacía = borrador recién creado)."""
    clean, seen = [], set()
    for i, p in enumerate(prompts):
        pid = str(p.get("id", "")).strip()
        if not _SLUG_RE.match(pid):
            raise ValidationSetError(
                f"prompt[{i}]: id inválido {pid!r} (minúsculas/dígitos/guiones)")
        if pid in seen:
            raise ValidationSetError(f"prompt id duplicado: {pid!r}")
        seen.add(pid)
        cat = p.get("category")
        if cat not in CATEGORIES:
            raise ValidationSetError(
                f"prompt {pid!r}: categoría {cat!r} no es una de {CATEGORIES}")
        text = str(p.get("text", "")).strip()
        if not text:
            raise ValidationSetError(f"prompt {pid!r}: texto vacío")
        try:
            seed = int(p["seed"])
        except (KeyError, TypeError, ValueError):
            raise ValidationSetError(f"prompt {pid!r}: seed debe ser entero")
        clean.append({"id": pid, "category": cat, "text": text,
                      "negative": str(p.get("negative", "")).strip(),
                      "seed": seed})
    return clean


def _validate_sampling(sampling: dict) -> dict:
    missing = [f for f in _SAMPLING_FIELDS if f not in sampling]
    if missing:
        raise ValidationSetError(f"sampling incompleto, faltan: {missing}")
    s = {f: sampling[f] for f in _SAMPLING_FIELDS}
    s["cfg"] = float(s["cfg"])
    for f in ("steps", "width", "height"):
        s[f] = int(s[f])
    for f in ("sampler", "scheduler"):
        s[f] = str(s[f]).strip()
        if not s[f]:
            raise ValidationSetError(f"sampling.{f} vacío")
    return s


class ValidationSet:
    def __init__(self, name: str, arch: str, sampling: dict, prompts: list[dict],
                 created_at: str | None = None, locked_at: str | None = None,
                 archived_at: str | None = None):
        if not _SLUG_RE.match(name):
            raise ValidationSetError(
                f"nombre inválido {name!r} (minúsculas/dígitos/guiones, sin espacios)")
        self.name = name
        self.arch = arch
        self.sampling = _validate_sampling(sampling)
        self.prompts = _validate_prompts(prompts)
        self.created_at = created_at or _now()
        self.locked_at = locked_at
        self.archived_at = archived_at

    # ── Persistencia ───────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return DATA_DIR / f"{self.name}.json"

    @property
    def locked(self) -> bool:
        return self.locked_at is not None

    @property
    def archived(self) -> bool:
        return self.archived_at is not None

    def to_dict(self) -> dict:
        return {"name": self.name, "arch": self.arch,
                "created_at": self.created_at, "locked_at": self.locked_at,
                "archived_at": self.archived_at,
                "sampling": self.sampling, "prompts": self.prompts,
                "fingerprint": self.fingerprint()}

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

    @classmethod
    def create(cls, name: str, arch: str, sampling: dict,
               prompts: list[dict]) -> "ValidationSet":
        vs = cls(name, arch, sampling, prompts)
        if vs.path.exists():
            raise ValidationSetError(f"ya existe un set llamado {name!r}")
        vs.save()
        return vs

    @classmethod
    def load(cls, name: str) -> "ValidationSet":
        path = DATA_DIR / f"{name}.json"
        if not path.exists():
            raise ValidationSetError(f"no existe el set {name!r}")
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(d["name"], d["arch"], d["sampling"], d["prompts"],
                   d.get("created_at"), d.get("locked_at"), d.get("archived_at"))

    def delete(self):
        """Solo borradores. Un set bloqueado es un activo de reproducibilidad."""
        if self.locked:
            raise LockedError(f"{self.name!r} está bloqueado; no se borra")
        self.path.unlink(missing_ok=True)
        runs = RUNS_DIR / self.name
        if runs.exists():
            shutil.rmtree(runs)

    # ── Ciclo de vida ──────────────────────────────────────────────────────

    def update(self, sampling: dict | None = None,
               prompts: list[dict] | None = None):
        if self.locked:
            raise LockedError(
                f"{self.name!r} está bloqueado; clónalo para variar algo")
        if sampling is not None:
            self.sampling = _validate_sampling(sampling)
        if prompts is not None:
            self.prompts = _validate_prompts(prompts)
        self.save()

    def lock(self):
        """Congela el set. Exige las tres categorías presentes (handoff §5)."""
        if self.locked:
            raise LockedError(f"{self.name!r} ya estaba bloqueado")
        have = {p["category"] for p in self.prompts}
        missing = [c for c in CATEGORIES if c not in have]
        if missing:
            raise ValidationSetError(
                f"no se puede bloquear: faltan prompts de categoría {missing}")
        self.locked_at = _now()
        self.save()

    def clone(self, new_name: str) -> "ValidationSet":
        """Copia editable (borrador) — la única vía de cambio tras un lock."""
        return ValidationSet.create(new_name, self.arch,
                                    dict(self.sampling),
                                    [dict(p) for p in self.prompts])

    def archive(self):
        """Oculta el set de la lista sin destruirlo (para sets temáticos ya
        cerrados). El JSON pesa KBs y es lo único que permite reinterpretar
        los experimentos viejos — se conserva siempre; borrar de verdad solo
        se permite en borradores."""
        self.archived_at = _now()
        self.save()

    def unarchive(self):
        self.archived_at = None
        self.save()

    def fingerprint(self) -> str:
        """SHA256 del contenido efectivo (arch+sampling+prompts, orden canónico)."""
        payload = json.dumps(
            {"arch": self.arch, "sampling": self.sampling, "prompts": self.prompts},
            sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ── Regeneración ───────────────────────────────────────────────────────

    async def run(self, comfy, model: str | None = None, label: str = "",
                  on_progress: Callable[[dict], None] | None = None,
                  template: dict | None = None,
                  extra_params: dict | None = None,
                  model_desc: str | None = None,
                  extra_manifest: dict | None = None) -> dict:
        """
        Regenera el set completo contra un modelo. Secuencial (una imagen a la
        vez: la 5060 Ti no da para más y el orden hace el progreso legible).

        template/extra_params permiten regenerar con un workflow distinto al
        txt2img básico (Fase 4: confirmación runtime con LoRA selectivo);
        model_desc es el texto que aparece como "model" en el manifiesto y
        extra_manifest añade campos (p. ej. la config de bloques usada).

        on_progress recibe {prompt_index, total, prompt_id, step, steps_total}.
        Devuelve el manifiesto del run (también escrito en run.json).
        """
        from .architectures import get_adapter
        from .comfy_client import load_workflow
        from .model_config import model_files, loader_name

        if not self.prompts:
            raise ValidationSetError(f"el set {self.name!r} no tiene prompts")
        adapter = get_adapter(self.arch)
        files = model_files(self.arch)     # paths efectivos (config del usuario)
        if model is None:
            model = loader_name(files["diffusion_model"])
        if template is None:
            template = load_workflow(adapter.workflow_name("txt2img"), self.arch)

        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = RUNS_DIR / self.name / run_id
        while out_dir.exists():   # colisión de segundo exacto
            run_id += "b"
            out_dir = RUNS_DIR / self.name / run_id
        out_dir.mkdir(parents=True)

        started, results = _now(), []
        t_run = time.time()
        for i, p in enumerate(self.prompts):
            def _cb(step, total, _i=i, _pid=p["id"]):
                if on_progress:
                    on_progress({"prompt_index": _i, "total": len(self.prompts),
                                 "prompt_id": _pid, "step": step,
                                 "steps_total": total})
            _cb(0, self.sampling["steps"])
            params = {"model": model, "prompt": p["text"],
                      "negative": p["negative"], "seed": p["seed"],
                      "text_encoder": loader_name(files["text_encoder"]),
                      "vae": loader_name(files["vae"]),
                      **self.sampling, **(extra_params or {})}
            t0 = time.time()
            png = await comfy.run_workflow(template, params, on_progress=_cb)
            fname = f"{p['id']}.png"
            (out_dir / fname).write_bytes(png)
            results.append({"prompt_id": p["id"], "category": p["category"],
                            "seed": p["seed"], "image": fname,
                            "seconds": round(time.time() - t0, 1)})

        manifest = {
            "run_id": run_id, "set": self.name, "arch": self.arch,
            "fingerprint": self.fingerprint(), "draft": not self.locked,
            "model": model_desc or model, "label": label,
            # trazabilidad: con qué TE/VAE se generó (si cambian, los runs
            # anteriores dejan de ser comparables y esto es la evidencia)
            "model_files": {"text_encoder": files["text_encoder"],
                            "vae": files["vae"]},
            "sampling": dict(self.sampling),
            "started_at": started, "finished_at": _now(),
            "seconds": round(time.time() - t_run, 1),
            "results": results,
            **(extra_manifest or {}),
        }
        (out_dir / "run.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        return manifest


# ── Consultas de módulo ────────────────────────────────────────────────────

def list_sets() -> list[dict]:
    """Resumen de todos los sets, borradores primero, luego por nombre."""
    out = []
    if DATA_DIR.exists():
        for f in sorted(DATA_DIR.glob("*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            counts = {c: 0 for c in CATEGORIES}
            for p in d.get("prompts", []):
                if p.get("category") in counts:
                    counts[p["category"]] += 1
            out.append({"name": d["name"], "arch": d["arch"],
                        "locked": d.get("locked_at") is not None,
                        "locked_at": d.get("locked_at"),
                        "archived": d.get("archived_at") is not None,
                        "created_at": d.get("created_at"),
                        "n_prompts": len(d.get("prompts", [])),
                        "categories": counts,
                        "n_runs": len(list_runs(d["name"]))})
    out.sort(key=lambda s: (s["archived"], s["locked"], s["name"]))
    return out


def list_runs(set_name: str) -> list[dict]:
    """Manifiestos de runs de un set, del más reciente al más antiguo."""
    base = RUNS_DIR / set_name
    out = []
    if base.exists():
        for d in sorted(base.iterdir(), reverse=True):
            mf = d / "run.json"
            if mf.exists():
                out.append(json.loads(mf.read_text(encoding="utf-8")))
    return out


def delete_run(set_name: str, run_id: str):
    """Borra un run (imágenes + manifiesto). Los runs son la parte pesada;
    el set en sí nunca se toca desde aquí."""
    base = RUNS_DIR.resolve()
    path = (base / set_name / run_id).resolve()
    if not path.is_relative_to(base) or not (path / "run.json").exists():
        raise ValidationSetError(f"no existe el run {run_id!r} del set {set_name!r}")
    shutil.rmtree(path)


# ── Set inicial de arranque (borrador editable) ────────────────────────────

def starter_prompts() -> list[dict]:
    """
    Borrador de partida para zimage — 3 prompts por categoría, seeds fijas.
    Es un punto de arranque para que el usuario edite ANTES de bloquear;
    no pretende ser el set definitivo. Todo SFW a propósito (handoff §11:
    medir drift de calibración con prompts explícitamente SFW).
    """
    return [
        # target — dominio objetivo: render 3D multiestilo
        {"id": "target-pixar", "category": "target", "seed": 101101,
         "text": "3d render of a cheerful young explorer girl with a backpack "
                 "standing on a mossy forest rock, pixar animation style, soft "
                 "studio lighting, subsurface scattering, high quality",
         "negative": ""},
        {"id": "target-clay", "category": "target", "seed": 202202,
         "text": "claymation style 3d render of a small friendly dragon cooking "
                 "soup in a rustic kitchen, stop motion look, handcrafted "
                 "textures, warm lighting", "negative": ""},
        {"id": "target-lowpoly", "category": "target", "seed": 303303,
         "text": "low poly 3d render of a lighthouse on a coastal cliff at "
                 "sunset, isometric view, flat shading, vibrant colors, clean "
                 "geometry", "negative": ""},
        # general — fuera de dominio: lo que NO hay que romper
        {"id": "general-photo", "category": "general", "seed": 404404,
         "text": "documentary photograph of an elderly fisherman repairing a "
                 "net at dawn, natural light, 35mm film grain, shallow depth "
                 "of field", "negative": ""},
        {"id": "general-oil", "category": "general", "seed": 505505,
         "text": "oil painting of a stormy sea with a small sailboat, "
                 "impressionist brushstrokes, dramatic sky, muted palette",
         "negative": ""},
        {"id": "general-anime", "category": "general", "seed": 606606,
         "text": "2d anime illustration of a student reading under a cherry "
                 "blossom tree, clean lineart, cel shading, spring afternoon",
         "negative": ""},
        # stress — donde primero asoma la degradación
        {"id": "stress-hands", "category": "stress", "seed": 707707,
         "text": "close-up of two hands shuffling playing cards, interlaced "
                 "fingers clearly visible, photorealistic, sharp focus",
         "negative": ""},
        {"id": "stress-crowd", "category": "stress", "seed": 808808,
         "text": "three chefs arguing in a busy restaurant kitchen, one "
                 "pointing at a burnt dish, another holding a frying pan, "
                 "dynamic poses, full bodies visible", "negative": ""},
        {"id": "stress-text", "category": "stress", "seed": 909909,
         "text": "storefront of a small bakery with a wooden sign that reads "
                 "'PAN DE ORO', chalkboard menu with prices by the door, "
                 "morning light", "negative": ""},
    ]
