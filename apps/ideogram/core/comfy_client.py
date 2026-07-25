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

# Timeout de las llamadas HTTP de control (encolar, interrumpir, listar).
# NO aplica al WebSocket, que espera lo que dure la generación.
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)


def load_workflow(name: str) -> dict:
    """Carga un template JSON de la carpeta de workflows del módulo."""
    path = _WORKFLOWS_DIR / name
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw.pop("_comment", None)
    return raw


class ComfyError(Exception):
    pass


def _format_exec_error(d: dict) -> str:
    """Construye un mensaje útil desde el evento execution_error de ComfyUI.
    ComfyUI manda node_type/node_id/exception_type/exception_message/traceback;
    el cliente antes solo pasaba exception_message, dejando 'expected 4, got 1'
    sin decir QUÉ nodo lo lanzó. Ahora se identifica el nodo y el tipo."""
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
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as s:
            async with s.get(f"{self.base_url}{path}") as r:
                r.raise_for_status()
                return await r.json()

    async def _post(self, path: str, payload: dict) -> dict:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as s:
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

    async def queue_prompt(self, workflow: dict, client_id: str | None = None
                           ) -> tuple[str, str]:
        """Envía el workflow a ComfyUI. Retorna (prompt_id, client_id).
        Acepta un client_id para poder suscribirse al WS ANTES de encolar."""
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
        ningún nodo y el `executed` que emite lleva `output: None`
        (`execution.py:431`, `_send_cached_ui`).
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
        """Conecta al WebSocket y espera a que el prompt termine. Solo válido
        si el prompt se encoló DESPUÉS de abrir esta conexión; `run_workflow`
        se encarga de ese orden."""
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"{self.ws_url}?clientId={client_id}"
            ) as ws:
                return await self._consume_ws(ws, prompt_id, on_progress)

    async def _consume_ws(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        prompt_id: str,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Lee mensajes de un WebSocket YA conectado hasta que `prompt_id`
        termine. Retorna el output del SaveImage: {"images": [{...}]}."""
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                mtype = data.get("type")
                d     = data.get("data", {}) or {}

                if mtype == "progress":
                    if d.get("prompt_id") == prompt_id and on_progress:
                        on_progress(d.get("value", 0), d.get("max", 1))

                elif mtype == "executed":
                    if d.get("prompt_id") == prompt_id:
                        output = d.get("output") or {}
                        if "images" in output:
                            return output
                        # nodo sin imágenes, seguir esperando

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
        """Sustituye params, encola, espera y devuelve los bytes PNG.

        El WebSocket se abre ANTES de encolar: si se encolara primero, un
        prompt muy corto (o resuelto por caché) podría terminar antes de que
        estuviéramos escuchando y perderíamos los mensajes de fin."""
        built     = self.build_workflow(workflow, params)
        client_id = str(uuid.uuid4())
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"{self.ws_url}?clientId={client_id}"
            ) as ws:
                pid, _ = await self.queue_prompt(built, client_id)
                output = await self._consume_ws(ws, pid, on_progress)
        img_info = output["images"][0]
        return await self.get_image_bytes(
            img_info["filename"],
            img_info.get("subfolder", ""),
            img_info.get("type", "output"),
        )
