"""
Cliente HTTP + WebSocket para ComfyUI (adaptado del de Painter).

Solo la parte genérica: encolar workflows, esperar resultado, descargar
imágenes, sondear nodos. Los workflows específicos de Forge Lab viven como
templates JSON en workflows/<arch>/ y los nombra el ArchAdapter.
"""
import json
import uuid
import aiohttp
from pathlib import Path
from typing import Callable

_WORKFLOWS_DIR = Path(__file__).parent.parent / "workflows"


def load_workflow(name: str, arch: str) -> dict:
    """Carga un template JSON de la carpeta de la arquitectura indicada."""
    path = _WORKFLOWS_DIR / arch / name
    with open(path, encoding="utf-8") as f:
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
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self.base_url}{path}") as r:
                r.raise_for_status()
                return await r.json()

    async def _post(self, path: str, payload: dict) -> dict:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.base_url}{path}", json=payload) as r:
                r.raise_for_status()
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
            info = await self._get(f"/object_info/{node_name}")
            return node_name in info
        except Exception:
            return False

    async def get_sampler_options(self) -> dict:
        """Listas de samplers/schedulers del KSampler instalado (fallback si
        ComfyUI está caído: mínimos universales, la UI los completa luego)."""
        try:
            info = await self._get("/object_info/KSampler")
            req = info["KSampler"]["input"]["required"]
            return {"samplers": req["sampler_name"][0],
                    "schedulers": req["scheduler"][0]}
        except Exception:
            return {"samplers": ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_sde"],
                    "schedulers": ["normal", "karras", "exponential", "simple"]}

    async def get_models(self, folder: str) -> list[str]:
        """folder: diffusion_models | loras | vae | text_encoders ..."""
        try:
            return await self._get(f"/models/{folder}")
        except Exception:
            return []

    async def queue_prompt(self, workflow: dict) -> tuple[str, str]:
        """Envía el workflow a ComfyUI. Retorna (prompt_id, client_id)."""
        client_id = str(uuid.uuid4())
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

    async def wait_for_completion(
        self,
        prompt_id: str,
        client_id: str,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """
        Conecta al WebSocket y espera a que el prompt termine.
        Llama on_progress(step, total) en cada avance.
        Retorna el output del nodo SaveImage: {"images": [{"filename": ...}]}.
        """
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"{self.ws_url}?clientId={client_id}"
            ) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        mtype = data.get("type")

                        if mtype == "progress":
                            d = data.get("data", {})
                            if d.get("prompt_id") == prompt_id and on_progress:
                                on_progress(d.get("value", 0), d.get("max", 1))

                        elif mtype == "executed":
                            d = data.get("data", {})
                            if d.get("prompt_id") == prompt_id:
                                output = d.get("output") or {}
                                if "images" in output:
                                    return output
                                # nodo sin imágenes, seguir esperando

                        elif mtype == "execution_error":
                            d = data.get("data", {})
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

    # ── Sustitución de placeholders ────────────────────────────────────────

    @staticmethod
    def build_workflow(template: dict, params: dict) -> dict:
        """
        Sustituye {{key}} en todos los valores string del workflow.
        Los strings se escapan con json.dumps para que saltos de línea,
        comillas o barras no rompan el JSON resultante.
        """
        raw = json.dumps(template)

        for key, val in params.items():
            placeholder = f"{{{{{key}}}}}"
            if isinstance(val, bool):
                # bool antes que int porque bool es subclase de int
                raw = raw.replace(f'"{placeholder}"', str(val).lower())
            elif isinstance(val, (int, float)):
                raw = raw.replace(f'"{placeholder}"', str(val))
                raw = raw.replace(placeholder, str(val))
            else:
                escaped = json.dumps(str(val))[1:-1]
                raw = raw.replace(placeholder, escaped)

        return json.loads(raw)

    # ── Flujo completo ─────────────────────────────────────────────────────

    async def run_workflow(
        self,
        workflow: dict,
        params: dict,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bytes:
        """Sustituye params, encola, espera y devuelve los bytes PNG."""
        built     = self.build_workflow(workflow, params)
        pid, cid  = await self.queue_prompt(built)
        output    = await self.wait_for_completion(pid, cid, on_progress)
        img_info  = output["images"][0]
        return await self.get_image_bytes(
            img_info["filename"],
            img_info.get("subfolder", ""),
            img_info.get("type", "output"),
        )
