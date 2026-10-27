"""ComfyUI transport following the Ideogram client's proven queue/WS order."""
import json
import re
import uuid
from pathlib import Path

import aiohttp

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)
_WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"


class ComfyError(Exception):
    pass


def load_workflow(name):
    if name not in {"txt2img.json", "expression.json"}:
        raise ValueError("Workflow desconocido")
    workflow = json.loads((_WORKFLOWS / name).read_text(encoding="utf-8"))
    workflow.pop("_comment", None)
    return workflow


def _format_exec_error(data):
    return (f"Nodo {data.get('node_type', '?')} (#{data.get('node_id', '?')}): "
            f"{data.get('exception_type', '')} {data.get('exception_message', 'Error de ComfyUI')}")


class ComfyClient:
    def __init__(self, host="localhost", port=8188):
        self.base_url = f"http://{host}:{port}"
        self.ws_url = f"ws://{host}:{port}/ws"

    async def _get(self, path):
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.get(self.base_url + path) as response:
                return await self._json(response)

    async def _post(self, path, payload):
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.post(self.base_url + path, json=payload) as response:
                return await self._json(response)

    @staticmethod
    async def _json(response):
        if response.status >= 400:
            detail = (await response.text())[:6000]
            raise ComfyError(f"ComfyUI HTTP {response.status}: {detail}")
        return await response.json()

    async def health_check(self):
        try:
            await self._get("/system_stats")
            return True
        except (aiohttp.ClientError, ComfyError, TimeoutError):
            return False

    async def probe_node(self, name):
        try:
            return name in await self._get("/object_info/" + name)
        except (aiohttp.ClientError, ComfyError, TimeoutError):
            return False

    async def get_models(self, folder):
        return await self._get("/models/" + folder)

    async def sampling_options(self):
        info = await self._get("/object_info/KSampler")
        required = info["KSampler"]["input"]["required"]
        return {"samplers": required["sampler_name"][0],
                "schedulers": required["scheduler"][0]}

    async def free_memory(self):
        await self._post("/free", {"unload_models": True, "free_memory": True})

    async def interrupt(self):
        await self._post("/interrupt", {})

    async def queue_prompt(self, workflow, client_id=None):
        client_id = client_id or uuid.uuid4().hex
        response = await self._post("/prompt", {"prompt": workflow, "client_id": client_id})
        prompt_id = response.get("prompt_id")
        if not prompt_id:
            raise ComfyError(f"ComfyUI no devolvió prompt_id: {response}")
        return prompt_id, client_id

    async def history_output(self, prompt_id):
        history = await self._get("/history/" + prompt_id)
        outputs = (history.get(prompt_id) or {}).get("outputs", {})
        return next((output for output in outputs.values() if output.get("images")), None)

    async def _consume_ws(self, ws, prompt_id, on_progress=None):
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                event = json.loads(message.data)
                data = event.get("data") or {}
                if data.get("prompt_id") != prompt_id:
                    continue
                kind = event.get("type")
                if kind == "progress" and on_progress:
                    on_progress(data.get("value", 0), data.get("max", 1))
                elif kind == "execution_error":
                    raise ComfyError(_format_exec_error(data))
                elif kind == "execution_interrupted":
                    raise ComfyError("Generación cancelada")
                elif kind == "executed" and (data.get("output") or {}).get("images"):
                    return data["output"]
                elif kind == "execution_success":
                    output = await self.history_output(prompt_id)
                    if output:
                        return output
                    raise ComfyError("ComfyUI terminó sin producir una imagen")
            elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                raise ComfyError("Se perdió la conexión con ComfyUI")
        raise ComfyError("ComfyUI cerró el WebSocket sin resultado")

    async def get_image_bytes(self, filename, subfolder="", folder_type="output"):
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.get(self.base_url + "/view", params={
                    "filename": filename, "subfolder": subfolder, "type": folder_type}) as response:
                if response.status >= 400:
                    raise ComfyError(f"No se pudo descargar la imagen: HTTP {response.status}")
                return await response.read()

    async def upload_image(self, image_bytes, name=None):
        # Every upload has its own filename, including repeated expression jobs.
        name = f"anima-studio-{uuid.uuid4().hex}.png"
        form = aiohttp.FormData()
        form.add_field("image", image_bytes, filename=name, content_type="image/png")
        form.add_field("type", "input")
        form.add_field("overwrite", "false")
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.post(self.base_url + "/upload/image", data=form) as response:
                uploaded = await self._json(response)
        subfolder = uploaded.get("subfolder", "")
        return (subfolder.rstrip("/\\") + "/" if subfolder else "") + uploaded["name"]

    @staticmethod
    def build_workflow(template, params):
        """Typed placeholder expansion without replacing graph links or prompt text."""
        if isinstance(template, dict):
            return {key: ComfyClient.build_workflow(value, params) for key, value in template.items()}
        if isinstance(template, list):
            return [ComfyClient.build_workflow(value, params) for value in template]
        if isinstance(template, str):
            match = re.fullmatch(r"\{\{([a-z_]+)\}\}", template)
            if match:
                if match[1] not in params:
                    raise ValueError(f"Falta parámetro de workflow: {match[1]}")
                return params[match[1]]
        return template

    async def run_workflow(self, workflow, params=None, on_progress=None):
        built = self.build_workflow(workflow, params or {})
        client_id = uuid.uuid4().hex
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self.ws_url, params={"clientId": client_id}) as ws:
                prompt_id, _ = await self.queue_prompt(built, client_id)
                output = await self._consume_ws(ws, prompt_id, on_progress)
        info = output["images"][0]
        return await self.get_image_bytes(info["filename"], info.get("subfolder", ""),
                                          info.get("type", "output"))
