"""
Cliente del server LM Studio (ya lo levanta el usuario; aquí solo se consume).

Portado de `apps/ideogram/core/lmstudio.py` sin cambios de fondo. Se copia en
vez de importarse para que un módulo no dependa del otro (si mañana aparece un
tercer consumidor, ahí sí se factoriza a un sitio compartido).

Dos usos:
  1. Chat (texto libre o structured output) para escribir el prompt de H3.
  2. Orquestación de VRAM: el LLM escritor y H3 no caben a la vez —el stock de
     H3 ronda 30 GB entre encoder, DiT y VAEs—, pero tampoco hacen falta a la
     vez: se escribe el prompt, se descarga el LLM y recién ahí se encola en
     ComfyUI. LM Studio lo recarga solo (JIT) en la siguiente petición.

No gestiona/instala/descarga modelos: eso lo hace el usuario en LM Studio.
"""
import os
import json
import shutil
import asyncio
from pathlib import Path

import re
import aiohttp

LMSTUDIO_BASE = os.environ.get("LMSTUDIO_BASE", "http://localhost:1234")

# Tokens especiales que los VLM rotos vomitan en bucle (<unused24>, <pad>, <eos>…).
_SPECIAL_TOK = re.compile(r"<(?:unused\d+|pad|eos|bos|end_of_turn|start_of_turn)>", re.I)


class LMStudioError(Exception):
    pass


def _find_lms() -> str | None:
    """Localiza el CLI lms.exe (para unload). PATH → ~/.lmstudio/bin."""
    exe = shutil.which("lms")
    if exe:
        return exe
    cand = Path.home() / ".lmstudio" / "bin" / ("lms.exe" if os.name == "nt" else "lms")
    return str(cand) if cand.exists() else None


class LMStudio:
    def __init__(self, base: str = LMSTUDIO_BASE):
        self.base = base.rstrip("/")

    # ── Estado ───────────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.base}/api/v0/models",
                                 timeout=aiohttp.ClientTimeout(total=4)) as r:
                    return r.status == 200
        except Exception:
            return False

    async def list_models(self) -> list[dict]:
        """Modelos del server con su estado (loaded/not-loaded). Usa el REST
        nativo /api/v0/models que sí expone 'state' (el /v1/models no)."""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.base}/api/v0/models",
                                 timeout=aiohttp.ClientTimeout(total=6)) as r:
                    r.raise_for_status()
                    data = await r.json()
        except Exception as e:
            raise LMStudioError(f"LM Studio no responde en {self.base}: {e}")
        out = []
        for m in data.get("data", []):
            out.append({
                "id": m.get("id"),
                "type": m.get("type"),          # llm | vlm | embeddings
                "state": m.get("state"),        # loaded | not-loaded
                "arch": m.get("arch"),
                "quantization": m.get("quantization"),
                "max_context_length": m.get("max_context_length"),
            })
        return out

    async def loaded_llms(self) -> list[str]:
        models = await self.list_models()
        return [m["id"] for m in models
                if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm")]

    # ── Chat con structured output ───────────────────────────────────────────

    async def chat_text(self, model: str, messages: list[dict],
                        temperature: float = 0.2, max_tokens: int = 900,
                        timeout: float = 180.0, extra: dict | None = None) -> str:
        """Chat de TEXTO LIBRE (sin response_format ni gramática). Para VLM
        multimodales de llama.cpp —p.ej. Gemma— que CRASHEAN al decodificar bajo
        cualquier gramática tras encodear la imagen ("Channel Error"): pedimos
        texto plano y lo parseamos en código (como hace panopticon). max_tokens
        acotado + temperatura baja evitan que divague. Devuelve el content crudo."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # `extra` lleva parámetros que no todo backend acepta — sobre todo
        # `reasoning_effort`, la palanca que apaga el razonamiento de los
        # modelos que lo traen. Si el servidor lo rechaza, se reintenta sin él
        # antes de dar la petición por perdida.
        payload.update(extra or {})

        async def _post(body: dict):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(f"{self.base}/v1/chat/completions", json=body,
                                      timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                        if r.status != 200:
                            return r.status, await r.text()
                        return 200, await r.json()
            except asyncio.TimeoutError:
                raise LMStudioError("timeout esperando al VLM de LM Studio")
            except aiohttp.ClientError as e:
                raise LMStudioError(f"error de red con LM Studio: {e}")

        status, data = await _post(payload)
        if status == 400 and extra:
            base = {k: v for k, v in payload.items() if k not in extra}
            status, data = await _post(base)
        if status != 200:
            raise LMStudioError(f"chat/completions {status}: {str(data)[:400]}")
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError):
            raise LMStudioError(f"respuesta inesperada de LM Studio: {str(data)[:400]}")
        # Guard de degeneración: los VLM abliterated/uncensored de Gemma colapsan
        # al procesar imágenes repitiendo tokens especiales (<unused24>, <pad>…)
        # hasta agotar max_tokens. Los limpiamos; si no queda texto real, el modelo
        # está roto para visión y avisamos en vez de fallar como "elements vacío".
        clean = _SPECIAL_TOK.sub("", content or "")
        if not clean.strip():
            # Antes de culpar al modelo: los razonadores (Gemma 4, Qwen3.x…)
            # devuelven el razonamiento en `reasoning_content` y COBRAN esos
            # tokens contra max_tokens. Con un system prompt largo se lo gastan
            # entero pensando y `content` llega vacío sin que nada esté roto.
            used = (data.get("usage") or {}).get("completion_tokens_details") or {}
            reasoning = used.get("reasoning_tokens") or 0
            if reasoning or choice["message"].get("reasoning_content"):
                raise LMStudioError(
                    f"'{model}' agotó el presupuesto razonando ({reasoning} tokens de "
                    f"razonamiento, finish_reason={choice.get('finish_reason')}) y no llegó a "
                    f"escribir la respuesta: sube max_tokens o usa un modelo sin razonamiento")
            raise LMStudioError(
                f"el VLM '{model}' degeneró en tokens basura (probablemente un modelo "
                "abliterated roto para visión); prueba un VLM distinto, p.ej. un Qwen-VL")
        return clean

    async def chat_json(self, model: str, messages: list[dict], schema: dict,
                        temperature: float = 0.6, max_tokens: int = 4096,
                        timeout: float = 180.0) -> dict:
        """Chat que fuerza salida JSON válida vía response_format json_schema.
        Devuelve el dict ya parseado."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "h3_prompt",
                    "schema": schema,
                    "strict": True,
                },
            },
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{self.base}/v1/chat/completions", json=payload,
                                  timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                    if r.status != 200:
                        body = await r.text()
                        raise LMStudioError(f"chat/completions {r.status}: {body[:400]}")
                    data = await r.json()
        except asyncio.TimeoutError:
            raise LMStudioError("timeout esperando al LLM de LM Studio")
        except aiohttp.ClientError as e:
            raise LMStudioError(f"error de red con LM Studio: {e}")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise LMStudioError(f"respuesta inesperada de LM Studio: {str(data)[:400]}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # con strict schema no debería pasar; se devuelve crudo para el
            # parser tolerante de caption.py
            return {"__raw__": content}

    # ── Orquestación de VRAM ─────────────────────────────────────────────────

    async def unload_all(self) -> dict:
        """Descarga todos los modelos cargados en LM Studio (libera VRAM).
        Vía el CLI lms; si no está, se informa para que el usuario lo haga."""
        lms = _find_lms()
        if not lms:
            return {"ok": False, "reason": "cli-lms-no-encontrado",
                    "hint": "instala 'lms' o descarga el modelo a mano en LM Studio"}
        try:
            proc = await asyncio.create_subprocess_exec(
                lms, "unload", "--all",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            return {"ok": proc.returncode == 0,
                    "output": (out or b"").decode("utf-8", "replace").strip()}
        except asyncio.TimeoutError:
            return {"ok": False, "reason": "timeout-unload"}
        except Exception as e:
            return {"ok": False, "reason": str(e)}
