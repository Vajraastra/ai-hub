"""
Cliente del server LM Studio (ya lo levanta el usuario; aquí solo se consume).

Dos usos:
  1. Chat con structured output (json_schema) para el paso descripción→JSON.
  2. Orquestación de VRAM: consultar qué modelos están cargados y descargarlos
     (lms unload) antes de renderizar en ComfyUI, para no solapar el LLM y los
     modelos de Ideogram en los 16 GB. LM Studio recarga el LLM solo (JIT) en la
     siguiente petición de chat.

No gestiona/instala/descarga modelos: eso lo hace el usuario en LM Studio.
"""
import os
import json
import shutil
import asyncio
from pathlib import Path

import aiohttp

LMSTUDIO_BASE = os.environ.get("LMSTUDIO_BASE", "http://localhost:1234")


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
                    "name": "ideogram_caption",
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
