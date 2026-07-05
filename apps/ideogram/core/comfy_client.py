"""
Cliente HTTP + WebSocket para ComfyUI (adaptado del de Forge Lab / Painter).

Solo la parte genérica: encolar workflows, esperar resultado, descargar
imágenes y sondear nodos. El workflow de Ideogram vive como template JSON en
workflows/ideogram4_t2i.json y el prompt JSON se inyecta como STRING.
"""
import json
import uuid
import aiohttp
from pathlib import Path
from typing import Callable

_WORKFLOWS_DIR = Path(__file__).parent.parent / "workflows"


def load_workflow(name: str) -> dict:
    """Carga un template JSON de la carpeta de workflows del módulo."""
    path = _WORKFLOWS_DIR / name
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

    async def probe_node(self, node_name: str) -> bool:
        try:
            info = await self._get(f"/object_info/{node_name}")
            return node_name in info
        except Exception:
            return False

    async def get_models(self, folder: str) -> list[str]:
        """folder: diffusion_models | text_encoders | vae ..."""
        try:
            return await self._get(f"/models/{folder}")
        except Exception:
            return []

    async def free_memory(self, unload_models: bool = True, free_memory: bool = True):
        """Pide a ComfyUI liberar VRAM (descargar modelos). Se usa antes de
        recargar el LLM en LM Studio para no solapar los dos en 16 GB."""
        try:
            await self._post("/free", {"unload_models": unload_models,
                                       "free_memory": free_memory})
        except Exception:
            pass

    async def queue_prompt(self, workflow: dict) -> tuple[str, str]:
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
        """Conecta al WebSocket y espera a que el prompt termine.
        Retorna el output del nodo SaveImage: {"images": [{"filename": ...}]}."""
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
        """Sustituye {{key}} en todos los valores string del workflow.
        Los strings se escapan con json.dumps para que saltos de línea,
        comillas o barras (abundan en el prompt JSON) no rompan el JSON."""
        raw = json.dumps(template)

        for key, val in params.items():
            placeholder = f"{{{{{key}}}}}"
            if isinstance(val, bool):
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
        built    = self.build_workflow(workflow, params)
        pid, cid = await self.queue_prompt(built)
        output   = await self.wait_for_completion(pid, cid, on_progress)
        img_info = output["images"][0]
        return await self.get_image_bytes(
            img_info["filename"],
            img_info.get("subfolder", ""),
            img_info.get("type", "output"),
        )
