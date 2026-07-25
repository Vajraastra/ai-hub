"""
Cliente HTTP + WebSocket para ComfyUI.
Gestiona: envío de workflows, recepción de progreso, descarga de imágenes.
"""
import json
import uuid
import aiohttp
from pathlib import Path
from typing import Callable

_WORKFLOWS_DIR = Path(__file__).parent.parent / "workflows"

SUPPORTED_ARCHITECTURES = ["sdxl"]   # añadir "flux" cuando esté listo

# Timeout de las llamadas HTTP de control (encolar, interrumpir, listar).
# NO aplica al WebSocket, que espera lo que dure la generación.
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)


def load_workflow(name: str, arch: str = "sdxl") -> dict:
    """Carga un workflow JSON de la carpeta de la arquitectura indicada."""
    path = _WORKFLOWS_DIR / arch / name
    with open(path) as f:
        raw = json.load(f)
    raw.pop("_comment", None)
    return raw


class ComfyError(Exception):
    pass


class ComfyClient:
    def __init__(self, host: str = "localhost", port: int = 8188):
        self.base_url = f"http://{host}:{port}"
        self.ws_url   = f"ws://{host}:{port}/ws"

    # ── Helpers ────────────────────────────────────────────────────────────

    async def _get(self, path: str) -> dict:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as s:
            async with s.get(f"{self.base_url}{path}") as r:
                r.raise_for_status()
                return await r.json()

    async def _post(self, path: str, payload: dict) -> dict:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as s:
            async with s.post(f"{self.base_url}{path}", json=payload) as r:
                if r.status >= 400:
                    # ComfyUI devuelve el detalle de validación en el body
                    # (error.message + node_errors); no perderlo en un "Bad Request".
                    try:
                        body = await r.json()
                    except Exception:
                        r.raise_for_status()
                        raise
                    msg = (body.get("error") or {}).get("message", f"HTTP {r.status}")
                    detalles = []
                    for nid, nerr in (body.get("node_errors") or {}).items():
                        for e in nerr.get("errors", []):
                            detalles.append(f"{nid}: {e.get('message', '')}")
                    if detalles:
                        msg += " — " + "; ".join(detalles[:3])
                    raise ComfyError(msg)
                return await r.json()

    # ── API pública ────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        try:
            await self._get("/system_stats")
            return True
        except Exception:
            return False

    async def get_object_info(self) -> dict:
        return await self._get("/object_info")

    async def probe_node(self, node_name: str) -> bool:
        try:
            info = await self.get_object_info()
            return node_name in info
        except Exception:
            return False

    async def get_models(self, folder: str) -> list[str]:
        """folder: checkpoints | controlnet | upscale_models | loras ..."""
        try:
            return await self._get(f"/models/{folder}")
        except Exception:
            return []

    async def get_samplers(self) -> list[str]:
        try:
            info = await self._get("/object_info/KSampler")
            return info["KSampler"]["input"]["required"]["sampler_name"][0]
        except Exception:
            return ["euler", "dpmpp_2m", "dpmpp_sde"]

    async def get_schedulers(self) -> list[str]:
        try:
            info = await self._get("/object_info/KSampler")
            return info["KSampler"]["input"]["required"]["scheduler"][0]
        except Exception:
            return ["normal", "karras", "exponential"]

    async def queue_prompt(self, workflow: dict, client_id: str | None = None) -> tuple[str, str]:
        """Envía el workflow a ComfyUI. Retorna (prompt_id, client_id)."""
        client_id = client_id or str(uuid.uuid4())
        payload   = {"prompt": workflow, "client_id": client_id}
        resp      = await self._post("/prompt", payload)
        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            raise ComfyError(f"ComfyUI no retornó prompt_id: {resp}")
        return prompt_id, client_id

    async def interrupt(self):
        try:
            await self._post("/interrupt", {})
        except Exception:
            pass

    async def history_output(self, prompt_id: str) -> dict | None:
        """
        Busca en /history el primer output con imágenes del prompt.

        Es la única vía cuando ComfyUI resuelve el grafo entero desde su caché
        (parámetros idénticos a una ejecución previa): en ese caso no re-ejecuta
        ningún nodo y por tanto NUNCA emite el mensaje `executed`.
        """
        try:
            hist = await self._get(f"/history/{prompt_id}")
        except Exception:
            return None
        entry = hist.get(prompt_id)
        if not entry:
            return None
        for output in (entry.get("outputs") or {}).values():
            if output.get("images"):
                return output
        return None

    async def wait_for_completion(
        self,
        prompt_id: str,
        client_id: str,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """
        Conecta al WebSocket y espera a que el prompt termine.
        Llama on_progress(step, total) en cada avance.
        Retorna el output del nodo SaveImage: {"images": [{"filename": ..., ...}]}.
        """
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"{self.ws_url}?clientId={client_id}"
            ) as ws:
                return await self._consume_ws(ws, prompt_id, on_progress)

    async def _consume_ws(
        self,
        ws,
        prompt_id: str,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Consume los mensajes del WS hasta que el prompt termine."""
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                mtype = data.get("type")
                d     = data.get("data", {})

                if mtype == "progress":
                    if d.get("prompt_id") == prompt_id and on_progress:
                        on_progress(d.get("value", 0), d.get("max", 1))

                elif mtype == "executed":
                    if d.get("prompt_id") == prompt_id:
                        output = d.get("output") or {}
                        if "images" in output:
                            return output
                        # nodo sin imágenes (ej. VAE encode), seguir esperando

                elif mtype == "execution_success":
                    # El prompt terminó sin habernos dado un `executed` con
                    # imágenes: pasó por caché. El resultado está en /history.
                    if d.get("prompt_id") == prompt_id:
                        output = await self.history_output(prompt_id)
                        if output:
                            return output
                        raise ComfyError(
                            "ComfyUI terminó el prompt sin producir imágenes"
                        )

                elif mtype == "execution_error":
                    if d.get("prompt_id") == prompt_id:
                        raise ComfyError(
                            d.get("exception_message", "Error desconocido en ComfyUI")
                        )

                elif mtype == "execution_interrupted":
                    raise ComfyError("Generación cancelada por el usuario")

            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                raise ComfyError("WebSocket cerrado inesperadamente")

        raise ComfyError("WebSocket cerrado sin resultado")

    async def get_image_bytes(
        self, filename: str, subfolder: str = "", folder_type: str = "output"
    ) -> bytes:
        url = (
            f"{self.base_url}/view"
            f"?filename={filename}&subfolder={subfolder}&type={folder_type}"
        )
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                r.raise_for_status()
                return await r.read()

    # ── Inyección de LoRAs ────────────────────────────────────────────────

    @staticmethod
    def inject_loras(workflow: dict, loras: list[dict]) -> dict:
        """
        Inserta nodos LoraLoader encadenados entre CheckpointLoaderSimple y el
        resto del workflow. Funciona sobre templates (antes de build_workflow) y
        sobre workflows ya construidos.

        Sólo redirige referencias de modelo (slot 0) y clip (slot 1) del nodo
        checkpoint; la referencia al VAE (slot 2) no se toca.

        loras: [{"name": "foo.safetensors", "strength": 1.0}, ...]
        """
        if not loras:
            return workflow

        ckpt_id = next(
            (nid for nid, n in workflow.items()
             if isinstance(n, dict) and n.get("class_type") == "CheckpointLoaderSimple"),
            None,
        )
        if ckpt_id is None:
            return workflow

        last_lora_id = f"lora_{len(loras) - 1}"

        # Redirigir todas las refs de modelo y clip del checkpoint al último LoraLoader.
        # Se opera sobre el JSON serializado para capturar refs anidadas en cualquier nodo.
        raw = json.dumps(workflow)
        raw = raw.replace(f'["{ckpt_id}", 0]', f'["{last_lora_id}", 0]')
        raw = raw.replace(f'["{ckpt_id}", 1]', f'["{last_lora_id}", 1]')
        workflow = json.loads(raw)

        # Construir cadena de LoraLoaders; cada uno toma model/clip del anterior
        prev_model: list = [ckpt_id, 0]
        prev_clip:  list = [ckpt_id, 1]
        for i, lora in enumerate(loras):
            lora_id = f"lora_{i}"
            workflow[lora_id] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "model":          prev_model,
                    "clip":           prev_clip,
                    "lora_name":      lora["name"],
                    "strength_model": float(lora.get("strength", 1.0)),
                    "strength_clip":  float(lora.get("strength", 1.0)),
                },
            }
            prev_model = [lora_id, 0]
            prev_clip  = [lora_id, 1]

        return workflow

    # ── Inyección de ControlNet ───────────────────────────────────────────

    @staticmethod
    def inject_controlnet(
        workflow:      dict,
        cn_image_b64:  str,
        cn_model:      str,
        cn_strength:   float       = 1.0,
        cn_type:       str | None  = None,   # None = tradicional; string SetUnionControlNetType ("openpose", …) = Union
        cn_preprocess: str | None  = None,   # AIO_Preprocessor; None = sin preprocesar
    ) -> dict:
        """
        Inyecta nodos ControlNet en un workflow template (antes de build_workflow).
        Busca el KSampler dinámicamente y redirige su conditioning positivo.

        Requiere: ETN_LoadImageBase64 (comfyui-tooling-nodes).
        Preprocesador opcional: AIO_Preprocessor (comfyui_controlnet_aux).
        """
        ksampler_id = next(
            (nid for nid, n in workflow.items()
             if isinstance(n, dict) and n.get("class_type") == "KSampler"),
            None,
        )
        if ksampler_id is None:
            return workflow

        wf = json.loads(json.dumps(workflow))   # deep copy

        # Imagen de guía
        wf["cn_img"] = {
            "class_type": "ETN_LoadImageBase64",
            "inputs": {"image": cn_image_b64},
        }
        img_ref = ["cn_img", 0]

        # Preprocesador opcional
        if cn_preprocess and cn_preprocess != "none":
            wf["cn_prep"] = {
                "class_type": "AIO_Preprocessor",
                "inputs": {
                    "preprocessor": cn_preprocess,
                    "image": img_ref,
                    "resolution": 512,
                },
            }
            img_ref = ["cn_prep", 0]

        # Modelo ControlNet
        wf["cn_loader"] = {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": cn_model},
        }
        cn_ref = ["cn_loader", 0]

        # Union: tipo via SetUnionControlNetType
        if cn_type is not None:
            wf["cn_union"] = {
                "class_type": "SetUnionControlNetType",
                "inputs": {"control_net": cn_ref, "type": cn_type},
            }
            cn_ref = ["cn_union", 0]

        # Capturar conditioning positivo actual del KSampler
        pos_cond = wf[ksampler_id]["inputs"]["positive"]

        # ControlNetApply
        wf["cn_apply"] = {
            "class_type": "ControlNetApply",
            "inputs": {
                "conditioning": pos_cond,
                "control_net":  cn_ref,
                "image":        img_ref,
                "strength":     float(cn_strength),
            },
        }

        # Redirigir KSampler positive → salida de ControlNetApply
        wf[ksampler_id]["inputs"]["positive"] = ["cn_apply", 0]

        return wf

    # ── Sustitución de placeholders ────────────────────────────────────────

    @staticmethod
    def build_workflow(template: dict, params: dict) -> dict:
        """
        Sustituye {{key}} en todos los valores string del workflow.
        Los strings se escapan con json.dumps para que caracteres como
        saltos de línea, comillas o barras no rompan el JSON resultante.
        """
        raw = json.dumps(template)

        for key, val in params.items():
            placeholder = f"{{{{{key}}}}}"
            if isinstance(val, bool):
                # bool antes que int porque bool es subclase de int
                raw = raw.replace(f'"{placeholder}"', str(val).lower())
            elif isinstance(val, (int, float)):
                # el placeholder aparece entre comillas en el template JSON
                # ("{{seed}}") — las quitamos al sustituir el valor numérico
                raw = raw.replace(f'"{placeholder}"', str(val))
                raw = raw.replace(placeholder, str(val))
            else:
                # String: escapar para JSON (json.dumps agrega comillas externas,
                # las quitamos con [1:-1] para sustituir dentro de las existentes)
                escaped = json.dumps(str(val))[1:-1]
                raw = raw.replace(placeholder, escaped)

        return json.loads(raw)

    # ── Workflow regional dinámico ─────────────────────────────────────────

    @staticmethod
    def build_regional_workflow(
        checkpoint:      str,
        global_prompt:   str,
        negative_prompt: str,
        regions:         list[dict],   # [{prompt, mask_b64, strength}]
        width:           int,
        height:          int,
        seed:            int,
        steps:           int,
        cfg:             float,
        denoise:         float,
        image_b64:       str | None = None,
        scheduler:       str = "normal",
    ) -> dict:
        """
        Construye el workflow de regional conditioning dinámicamente.
        Nodos core de ComfyUI + ETN_LoadMaskBase64 (comfyui-tooling-nodes).
        Sampler forzado a euler (no-ancestral, evita bleeding entre máscaras).
        Scheduler viene del perfil: normal (epsilon-pred) o exponential (V-Pred).
        """
        wf: dict = {}

        # ── Checkpoint ────────────────────────────────────────────────────
        wf["1"] = {"class_type": "CheckpointLoaderSimple",
                   "inputs": {"ckpt_name": checkpoint}}

        # ── Condicionings base ────────────────────────────────────────────
        wf["2"] = {"class_type": "CLIPTextEncode",
                   "inputs": {"text": global_prompt, "clip": ["1", 1]}}
        wf["3"] = {"class_type": "CLIPTextEncode",
                   "inputs": {"text": negative_prompt, "clip": ["1", 1]}}

        # ── Nodos por región ──────────────────────────────────────────────
        # IDs: máscara="1i0", clip_encode="1i1", set_mask="1i2"  (i=0..3)
        for i, reg in enumerate(regions):
            mask_id   = f"1{i}0"
            clip_id   = f"1{i}1"
            csmask_id = f"1{i}2"

            wf[mask_id] = {
                "class_type": "ETN_LoadMaskBase64",
                "inputs": {"mask": reg["mask_b64"]},
            }
            wf[clip_id] = {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": reg["prompt"], "clip": ["1", 1]},
            }
            wf[csmask_id] = {
                "class_type": "ConditioningSetMask",
                "inputs": {
                    "conditioning":  [clip_id, 0],
                    "mask":          [mask_id, 0],
                    "strength":      reg.get("strength", 1.0),
                    "set_cond_area": "mask bounds",
                },
            }

        # ── Combinar condicionings (cadena de ConditioningCombine) ─────────
        # Partimos del global "2" y vamos añadiendo cada región.
        prev_comb = "2"
        for i in range(len(regions)):
            comb_id = f"20{i}"
            wf[comb_id] = {
                "class_type": "ConditioningCombine",
                "inputs": {
                    "conditioning_1": [prev_comb, 0],
                    "conditioning_2": [f"1{i}2", 0],
                },
            }
            prev_comb = comb_id
        final_positive = prev_comb  # "2" si no hay regiones, "20{n-1}" si hay

        # ── Latent — img2img o txt2img ────────────────────────────────────
        if image_b64:
            wf["300"] = {"class_type": "ETN_LoadImageBase64",
                         "inputs": {"image": image_b64}}
            wf["301"] = {"class_type": "VAEEncode",
                         "inputs": {"pixels": ["300", 0], "vae": ["1", 2]}}
            latent_src = ["301", 0]
        else:
            wf["300"] = {"class_type": "EmptyLatentImage",
                         "inputs": {"width": width, "height": height, "batch_size": 1}}
            latent_src = ["300", 0]

        # ── KSampler — euler forzado; scheduler del perfil ────────────────
        wf["400"] = {
            "class_type": "KSampler",
            "inputs": {
                "model":         ["1", 0],
                "positive":      [final_positive, 0],
                "negative":      ["3", 0],
                "latent_image":  latent_src,
                "seed":          seed,
                "steps":         steps,
                "cfg":           cfg,
                "sampler_name":  "euler",
                "scheduler":     scheduler,
                "denoise":       denoise,
            },
        }

        # ── Decode + Save ─────────────────────────────────────────────────
        wf["401"] = {"class_type": "VAEDecode",
                     "inputs": {"samples": ["400", 0], "vae": ["1", 2]}}
        wf["402"] = {"class_type": "SaveImage",
                     "inputs": {"images": ["401", 0], "filename_prefix": "painter_regional"}}

        return wf

    # ── Workflow de un solo paso regional (inpainting real) ───────────────

    @staticmethod
    def build_regional_step_workflow(
        checkpoint:      str,
        prompt:          str,
        negative_prompt: str,
        mask_b64:        str,            # máscara procesada (con blur)
        seed:            int,
        steps:           int,
        cfg:             float,
        denoise:         float,
        image_b64:       str | None = None,  # None → txt2img (EmptyLatentImage)
        width:           int = 1024,
        height:          int = 1024,
        scheduler:       str = "normal",
    ) -> dict:
        """
        Paso regional único:
        - Sin imagen base (txt2img): EmptyLatentImage, ConditioningSetMask.
          La máscara solo restringe el conditioning; el denoising es completo.
        - Con imagen base (img2img): VAEEncode + SetLatentNoiseMask.
          Solo los píxeles dentro de la máscara se modifican.
        Sampler forzado a euler (no-ancestral). Scheduler del perfil.
        """
        wf: dict = {}

        # Checkpoint
        wf["1"] = {"class_type": "CheckpointLoaderSimple",
                   "inputs": {"ckpt_name": checkpoint}}

        # Máscara siempre presente
        wf["11"] = {"class_type": "ETN_LoadMaskBase64",
                    "inputs": {"mask": mask_b64}}

        if image_b64:
            # Imagen base → inpainting restringido a la máscara
            wf["10"] = {"class_type": "ETN_LoadImageBase64",
                        "inputs": {"image": image_b64}}
            wf["12"] = {"class_type": "VAEEncode",
                        "inputs": {"pixels": ["10", 0], "vae": ["1", 2]}}
            wf["13"] = {"class_type": "SetLatentNoiseMask",
                        "inputs": {"samples": ["12", 0], "mask": ["11", 0]}}
            latent_src = ["13", 0]
        else:
            # Sin imagen base → generar desde cero (txt2img)
            wf["10"] = {"class_type": "EmptyLatentImage",
                        "inputs": {"width": width, "height": height, "batch_size": 1}}
            latent_src = ["10", 0]

        # Condicionings con máscara de área
        wf["20"] = {"class_type": "CLIPTextEncode",
                    "inputs": {"text": prompt, "clip": ["1", 1]}}
        wf["21"] = {"class_type": "ConditioningSetMask",
                    "inputs": {
                        "conditioning":  ["20", 0],
                        "mask":          ["11", 0],
                        "strength":      1.0,
                        "set_cond_area": "mask bounds",
                    }}
        wf["22"] = {"class_type": "CLIPTextEncode",
                    "inputs": {"text": negative_prompt, "clip": ["1", 1]}}

        # KSampler — euler forzado; scheduler del perfil
        wf["30"] = {
            "class_type": "KSampler",
            "inputs": {
                "model":         ["1", 0],
                "positive":      ["21", 0],
                "negative":      ["22", 0],
                "latent_image":  latent_src,
                "seed":          seed,
                "steps":         steps,
                "cfg":           cfg,
                "sampler_name":  "euler",
                "scheduler":     scheduler,
                "denoise":       denoise,
            },
        }

        # Decode + Save
        wf["31"] = {"class_type": "VAEDecode",
                    "inputs": {"samples": ["30", 0], "vae": ["1", 2]}}
        wf["32"] = {"class_type": "SaveImage",
                    "inputs": {"images": ["31", 0], "filename_prefix": "painter_reg_step"}}

        return wf

    # ── Flujo completo: generate ───────────────────────────────────────────

    async def run_workflow(
        self,
        workflow: dict,
        params: dict,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bytes:
        """
        Sustituye params, encola, espera resultado y devuelve los bytes PNG.

        El WebSocket se abre ANTES de encolar: si se encolara primero, un prompt
        muy corto (o resuelto por caché) podría terminar antes de que estuviéramos
        escuchando y perderíamos los mensajes de fin.
        """
        built     = self.build_workflow(workflow, params)
        client_id = str(uuid.uuid4())
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"{self.ws_url}?clientId={client_id}"
            ) as ws:
                pid, _ = await self.queue_prompt(built, client_id)
                output = await self._consume_ws(ws, pid, on_progress)
        img_info  = output["images"][0]
        return await self.get_image_bytes(
            img_info["filename"],
            img_info.get("subfolder", ""),
            img_info.get("type", "output"),
        )
