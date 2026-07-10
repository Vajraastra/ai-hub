"""
Cliente HTTP + WebSocket para ComfyUI (adaptado del de Ideogram).

Diferencias con el de Ideogram:
- upload_image(): sube las fotos de entrada (escena/donante) a la carpeta
  input de ComfyUI vía /upload/image (los LoadImage las referencian por nombre).
- wait_for_completion() recoge los outputs de TODOS los SaveImage del grafo
  (resultado final + intermedio de LivePortrait) en vez de cortar en el primero;
  termina cuando ComfyUI señala fin de ejecución (executing con node=None).
- get_object_info(): inspecciona un nodo para leer sus opciones (p.ej. la lista
  de swap_models que ReActor ve en disco).
"""
import json
import uuid
import aiohttp
from typing import Callable


class ComfyError(Exception):
    pass


def _format_exec_error(d: dict) -> str:
    """Mensaje útil desde el evento execution_error de ComfyUI (identifica el
    nodo y el tipo de excepción, no solo el mensaje)."""
    node_type = d.get("node_type") or "?"
    node_id = d.get("node_id") or "?"
    exc_type = d.get("exception_type") or ""
    msg = d.get("exception_message") or "Error desconocido en ComfyUI"
    tb = d.get("traceback")
    tail = ""
    if isinstance(tb, list) and tb:
        tail = "\n" + "".join(tb[-4:]).rstrip()
    elif isinstance(tb, str) and tb.strip():
        tail = "\n" + "\n".join(tb.strip().splitlines()[-4:])
    exc = f"{exc_type}: " if exc_type else ""
    return f"nodo {node_type} (#{node_id}) — {exc}{msg}{tail}"


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

    async def get_object_info(self, node_name: str) -> dict:
        """Definición completa del nodo ({} si no existe). Sirve para leer las
        opciones de sus combos, p.ej. qué swap_models ve ReActor en disco."""
        try:
            info = await self._get(f"/object_info/{node_name}")
            return info.get(node_name, {})
        except Exception:
            return {}

    async def upload_image(self, name: str, data: bytes,
                           subfolder: str = "faceswap") -> str:
        """Sube una imagen a la carpeta input de ComfyUI. Devuelve la referencia
        ('subfolder/nombre') que espera el nodo LoadImage."""
        form = aiohttp.FormData()
        form.add_field("image", data, filename=name,
                       content_type="application/octet-stream")
        form.add_field("overwrite", "true")
        form.add_field("type", "input")
        if subfolder:
            form.add_field("subfolder", subfolder)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.base_url}/upload/image", data=form) as r:
                r.raise_for_status()
                resp = await r.json()
        saved = resp.get("name") or name
        sub = resp.get("subfolder") or ""
        return f"{sub}/{saved}" if sub else saved

    async def free_memory(self, unload_models: bool = True, free_memory: bool = True):
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
    ) -> dict[str, dict]:
        """Conecta al WebSocket y espera el fin de la ejecución. Devuelve los
        outputs con imágenes por node_id: {"20": {"images": [...]}, ...}."""
        outputs: dict[str, dict] = {}
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"{self.ws_url}?clientId={client_id}"
            ) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        mtype = data.get("type")
                        d = data.get("data", {}) or {}

                        if d.get("prompt_id") not in (None, prompt_id):
                            continue

                        if mtype == "progress":
                            if on_progress:
                                on_progress(d.get("value", 0), d.get("max", 1))

                        elif mtype == "executed":
                            output = d.get("output") or {}
                            node = d.get("node")
                            if node and "images" in output:
                                outputs[str(node)] = output

                        elif mtype == "executing":
                            # node=None con nuestro prompt_id = ejecución terminada
                            if d.get("node") is None and d.get("prompt_id") == prompt_id:
                                return outputs

                        elif mtype == "execution_success":
                            return outputs

                        elif mtype == "execution_error":
                            raise ComfyError(_format_exec_error(d))

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

    async def get_output_image(self, output: dict) -> bytes:
        """Descarga la primera imagen de un output de SaveImage."""
        img_info = output["images"][0]
        return await self.get_image_bytes(
            img_info["filename"],
            img_info.get("subfolder", ""),
            img_info.get("type", "output"),
        )
