"""
Modelos Pydantic para la API del Painter.
"""
from pydantic import BaseModel, Field
from typing import Optional


# ── Parámetros de muestreo compartidos ─────────────────────────────────────

class LoraEntry(BaseModel):
    name:     str
    strength: float = Field(1.0, ge=-2.0, le=2.0)


class SamplingParams(BaseModel):
    checkpoint:       str
    prompt:           str   = ""
    negative_prompt:  str   = ""
    seed:             int   = -1          # -1 = aleatorio
    steps:            int   = Field(20, ge=1, le=150)
    cfg:              float = Field(7.0, ge=1.0, le=30.0)
    sampler:          str   = "euler"
    scheduler:        str   = "normal"
    arch:             str   = "sdxl"
    # ── ControlNet (opcionales) ────────────────────────────────────────────
    cn_image_b64:  Optional[str]   = None   # imagen de guía en base64
    cn_model:      Optional[str]   = None   # nombre del modelo .safetensors
    cn_strength:   float           = Field(1.0, ge=0.0, le=2.0)
    cn_type:       Optional[str]   = None   # None = tradicional; string SetUnionControlNetType ("openpose", "depth", …) = Union
    cn_preprocess: Optional[str]   = None   # AIO_Preprocessor; None = sin preprocesar


# ── Requests ────────────────────────────────────────────────────────────────

class GenerateRequest(SamplingParams):
    width:  int = Field(1024, ge=512, le=2048, multiple_of=8)
    height: int = Field(1024, ge=512, le=2048, multiple_of=8)


class InpaintRequest(SamplingParams):
    image_b64:      str
    mask_b64:       str
    denoise:        float = Field(0.85, ge=0.0, le=1.0)
    feather_radius: int   = Field(4,    ge=0,   le=64)
    inpaint_model:  Optional[str] = None   # None → workflow básico


class OutpaintRequest(SamplingParams):
    image_b64: str
    pad_left:  int = Field(0, ge=0, le=1024, multiple_of=8)
    pad_right: int = Field(0, ge=0, le=1024, multiple_of=8)
    pad_top:   int = Field(0, ge=0, le=1024, multiple_of=8)
    pad_bottom:int = Field(0, ge=0, le=1024, multiple_of=8)
    denoise:   float = Field(0.85, ge=0.0, le=1.0)
    feathering:int = Field(40, ge=0, le=200)


class UpscaleRequest(BaseModel):
    image_b64:    str
    model_name:   str
    arch:         str = "sdxl"


class RegionDef(BaseModel):
    prompt:   str
    mask_b64: str  # PNG base64 — blanco = área de la región


class RegionalRequest(SamplingParams):
    image_b64: Optional[str] = None  # None = txt2img puro
    regions:   list[RegionDef]       # 1-4 regiones con máscara
    width:     int   = Field(1024, ge=512, le=2048, multiple_of=8)
    height:    int   = Field(1024, ge=512, le=2048, multiple_of=8)
    denoise:   float = Field(0.85, ge=0.0, le=1.0)
    # sampler forzado internamente a euler; scheduler viene del perfil


class RegionalStepRequest(BaseModel):
    """Una sola región — txt2img si image_b64 es None, inpainting si tiene valor."""
    checkpoint:      str
    prompt:          str           = ""
    negative_prompt: str           = ""
    image_b64:       Optional[str] = None   # None → txt2img (EmptyLatentImage)
    mask_b64:        str                    # máscara de esta región (PNG b64, blanco = modificar)
    width:           int   = Field(1024, ge=512, le=2048, multiple_of=8)
    height:          int   = Field(1024, ge=512, le=2048, multiple_of=8)
    seed:            int   = -1
    steps:           int   = Field(20, ge=1, le=150)
    cfg:             float = Field(7.0, ge=1.0, le=30.0)
    # euler forzado como sampler; scheduler viene del perfil (exponential para V-Pred)
    scheduler:       str   = "normal"
    denoise:         float = Field(0.85, ge=0.0, le=1.0)


class AcceptRequest(BaseModel):
    """Frontend envía la imagen de resultado que quiere aceptar como current."""
    result_b64: str


# ── Responses ───────────────────────────────────────────────────────────────

class JobResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str          # queued | running | done | error
    progress: int = 0
    total:    int = 0
    error:    Optional[str] = None


class SessionStateResponse(BaseModel):
    has_current:  bool
    history_size: int
    redo_size:    int


class ADetailerDetector(BaseModel):
    id:        str   # "face" | "hand" | "person"
    label:     str
    filename:  str   # nombre del .pt en ultralytics/bbox/
    available: bool  # True si el archivo existe en disco


class ADetailerRequest(BaseModel):
    image_b64:       str
    checkpoint:      str
    prompt:          str   = ""
    negative_prompt: str   = ""
    seed:            int   = -1
    steps:           int   = Field(20, ge=1, le=150)
    cfg:             float = Field(7.0, ge=1.0, le=30.0)
    sampler:         str   = "euler"
    scheduler:       str   = "normal"
    arch:            str   = "sdxl"
    detectors:       list[str]         # ids habilitados: ["face", "hand", "person"]
    denoise:         float = Field(0.4, ge=0.0, le=1.0)
    guide_size:      int   = Field(512, ge=256, le=1024)
    bbox_threshold:  float = Field(0.5, ge=0.1, le=1.0)
    bbox_dilation:   int   = Field(10,  ge=0,   le=100)



class ModelsResponse(BaseModel):
    checkpoints:     list[str]
    controlnet:      list[str]
    upscale_models:  list[str]
    loras:           list[str]
    samplers:        list[str]
    schedulers:      list[str]
    architectures:   list[str]
    adetailer_available: bool = False   # True si FaceDetailer está cargado en ComfyUI
