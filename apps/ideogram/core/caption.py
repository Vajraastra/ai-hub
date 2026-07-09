"""
Esquema de caption JSON de Ideogram 4 + generación asistida por LLM.

Ideogram 4 se entrenó con captions JSON estructurados (resumen de escena,
bloque de estilo, fondo, y objetos con bounding-box + paleta hex). Prompts en
lenguaje natural plano disparan el filtro de seguridad con mucha más frecuencia;
el JSON con VARIAS cajas (sujeto + entorno) evita el filtro y usa el modelo como
fue diseñado. Este módulo define el esquema, el prompt de sistema para el LLM y
la validación/normalización del JSON que devuelve.

Formato de bbox: [ymin, xmin, ymax, xmax] en grid normalizado 0-1000 (el mismo
que consume el nodo nativo CreateBoundingBoxes de ComfyUI).
"""
import json
import re


class CaptionError(Exception):
    pass


# ── JSON Schema para structured output de LM Studio ──────────────────────────
# Garantiza que el LLM (incluso un 4B) devuelva SIEMPRE JSON válido con la forma
# correcta. LM Studio lo recibe en response_format={"type":"json_schema", ...}.
IDEOGRAM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "high_level_description": {
            "type": "string",
            "description": "Una o dos frases que resumen la imagen completa.",
        },
        "style_description": {
            "type": "object",
            "properties": {
                "aesthetics": {"type": "string"},
                "lighting": {"type": "string"},
                "medium": {"type": "string"},
                "photo": {"type": "string"},
                "art_style": {"type": "string"},
                "color_palette": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                    "maxItems": 16,
                },
            },
            "required": ["aesthetics", "lighting", "medium"],
        },
        "compositional_deconstruction": {
            "type": "object",
            "properties": {
                "background": {"type": "string"},
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["obj", "text"]},
                            "bbox": {
                                "type": "array",
                                "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                            "desc": {"type": "string"},
                            "text": {"type": "string"},
                            "color_palette": {
                                "type": "array",
                                "items": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                                "maxItems": 5,
                            },
                        },
                        "required": ["type", "bbox", "desc"],
                    },
                },
            },
            "required": ["background", "elements"],
        },
    },
    "required": ["high_level_description", "style_description", "compositional_deconstruction"],
}


SYSTEM_PROMPT = """\
Eres un compositor experto de captions JSON para el modelo de imagen Ideogram 4.
Conviertes la descripción libre del usuario en un caption JSON estructurado que
Ideogram entiende de forma nativa. El usuario puede escribir en CUALQUIER idioma;
TODO el texto del caption que produces va SIEMPRE en inglés natural (traducido y
adaptado con fluidez, no palabra por palabra).

REGLAS CLAVE (afectan directamente a la calidad y a que la imagen NO se bloquee):

1. Descompón SIEMPRE la escena en VARIOS elementos con bounding box: al menos el
   sujeto principal MÁS elementos del entorno (suelo, pared, cielo, mobiliario,
   objetos de apoyo). Nunca uses una sola caja. Varias cajas de sujeto + entorno
   es lo que hace que Ideogram renderice de forma fiable y sin falsos positivos.

2. Cada bbox es [ymin, xmin, ymax, xmax] en una rejilla 0-1000 (0=arriba/izquierda,
   1000=abajo/derecha). Colócalas con coherencia espacial real: el cielo arriba,
   el suelo abajo, el sujeto donde corresponda. Pueden solaparse si la escena lo pide.

3. "background" (obligatorio) describe el entorno/fondo global en prosa breve.

4. "style_description": aesthetics, lighting y medium son obligatorios. Si es una
   foto, rellena "photo" (cámara/lente) y pon medium="photograph". Si es ilustración
   u otro medio, usa "art_style". Añade "color_palette" (hex #RRGGBB) cuando ayude.

5. type="text" es EXCLUSIVAMENTE para texto que debe aparecer ESCRITO en la imagen
   (rótulos, carteles, letreros). SIEMPRE lleva el campo "text" con la cadena
   LITERAL a renderizar (no vacío) y "desc" con su estilo tipográfico y posición.
   TODO lo demás —personas, objetos, fondos, mobiliario— es type="obj" SIN campo
   "text". Ante la duda, usa "obj". Nunca marques un objeto como "text".

6. "high_level_description" DEBE empezar CONCRETANDO la toma (nunca la dejes al
   azar): encuadre (extreme close-up | close-up | medium shot | cowboy shot |
   full body | wide shot | establishing shot), ángulo (eye-level | low angle |
   high angle | overhead/top-down | dutch angle), orientación del sujeto (frontal |
   3/4 view | profile/side | from behind) y lente/distancia (macro | 35mm wide |
   85mm portrait | telephoto). Elige lo que mejor sirva a lo que pidió el usuario;
   si no lo especificó, comprométete con una opción coherente, no la omitas.

7. Sé descriptivo pero fiel a lo que pidió el usuario. NO inventes contenido no
   solicitado ni infles el prompt con florituras (el upsampling agresivo dispara
   el filtro). No añadas texto en la imagen salvo que el usuario lo pida.

Devuelve EXCLUSIVAMENTE el objeto JSON, sin explicaciones ni ```.\
"""


# Prompt para el MODO MANUAL: el usuario ya colocó las cajas; el LLM solo
# completa/normaliza el borrador SIN tocar la composición. La geometría se
# refuerza además en el backend (preserve_geometry), así que aunque el LLM
# desobedezca, las cajas del usuario se conservan intactas.
REFINE_SYSTEM_PROMPT = """\
Eres un compositor experto de captions JSON para el modelo de imagen Ideogram 4.
El usuario YA ha colocado a mano las cajas (bounding boxes) de la composición y
ha escrito sus descripciones. Tu tarea es COMPLETAR y NORMALIZAR ese borrador,
NUNCA rehacerlo.

REGLAS ABSOLUTAS:

1. NO añadas, elimines, muevas ni redimensiones ninguna caja. Respeta EXACTAMENTE
   el número, el orden, las coordenadas "bbox", el "type" y el "text" que llegan.

2. Puedes pulir la redacción de cada "desc" para que sea clara y evocadora, sin
   cambiar su significado ni el objeto que describe.

3. Rellena lo global que falte: "high_level_description" (una o dos frases que
   resuman la escena, coherentes con el prompt general del usuario si lo hay),
   "style_description" (aesthetics, lighting y medium obligatorios; photo o
   art_style y color_palette cuando ayuden) y "background" (entorno en prosa breve).

4. Añade "color_palette" (hex #RRGGBB) a los elementos donde aporte, sin inventar
   contenido nuevo ni cajas nuevas.

5. Sé fiel a lo que el usuario compuso; no infles el prompt (el upsampling agresivo
   dispara el filtro de seguridad).

Devuelve EXCLUSIVAMENTE el objeto JSON completo, sin explicaciones ni ```.\
"""


# Prompt para el pase de TRADUCCIÓN/CORRECCIÓN del modo manual. A diferencia de
# REFINE, NO completa ni rellena nada: solo lleva el texto del usuario a inglés
# correcto y literal (traduce + corrige ortografía/gramática) sin embellecer ni
# inventar. La geometría se blinda igual en el backend (preserve_geometry).
TRANSLATE_SYSTEM_PROMPT = """\
Eres un traductor y corrector de captions JSON para el modelo de imagen Ideogram 4.
El usuario YA ha compuesto un borrador (cajas, descripciones, estilo, textos). Tu
ÚNICA tarea es traducir todo su texto a INGLÉS correcto y corregir errores de
ortografía y gramática MANTENIENDO EL SIGNIFICADO LITERAL. No eres un redactor
creativo: no reescribes, no embelleces, no inventas.

REGLAS ABSOLUTAS:

1. NO añadas, elimines, muevas ni redimensiones ninguna caja. Respeta EXACTAMENTE
   el número, el orden, las coordenadas "bbox" y el "type" que llegan.

2. Traduce a inglés y corrige, SIN reescribir ni embellecer, cada campo de texto:
   "high_level_description", "background", el "desc" de CADA caja, los strings de
   "style_description" (aesthetics, lighting, medium, photo, art_style) y el "text"
   de las cajas de tipo texto (el rótulo que se dibuja en la imagen). Mismo
   contenido, solo en inglés correcto.

3. Si un campo ya está en inglés, corrige solo los errores. Si un campo está
   vacío, DÉJALO VACÍO: no inventes contenido ni añadas detalles (el upsampling
   agresivo dispara el filtro de seguridad).

4. NO cambies los códigos de color (color_palette) ni añadas paletas nuevas.

Devuelve EXCLUSIVAMENTE el objeto JSON completo, sin explicaciones ni ```.\
"""


# Prompt para AUTOPROMPT: el usuario carga una imagen de referencia y un VLM la
# analiza. A diferencia de los otros pases, aquí el modelo VE la imagen. Como los
# VLM multimodales de llama.cpp (p.ej. Gemma) CRASHEAN al decodificar bajo
# cualquier gramática JSON tras encodear la imagen, seguimos el patrón de
# panopticon: el VLM devuelve TEXTO LIBRE etiquetado (sin gramática) y el JSON +
# las bboxes los ENSAMBLA nuestro código (parse_vision_freetext). Las cajas salen
# gruesas (por región 3x3); el usuario las retoca sobre la referencia de fondo.
VISION_FREETEXT_PROMPT = """\
Eres un analista visual experto. Miras una IMAGEN de referencia y la describes en
un bloque de TEXTO PLANO etiquetado (NO JSON) que servirá para regenerar una
imagen equivalente con Ideogram 4. TODO el texto que produces va en inglés natural.

Describe SOLO lo que realmente ves; no inventes objetos ni texto, y no infles con
florituras. Devuelve EXACTAMENTE este formato, una etiqueta por línea:

HLD: <una frase que resume la imagen completa>
SHOT: <OBLIGATORIO y CONCRETO — encuadre + ángulo + perspectiva. Elige de verdad:
  encuadre (extreme close-up | close-up | medium shot | cowboy shot | full body |
  wide shot | establishing shot); ángulo de cámara (eye-level | low angle | high angle |
  overhead/top-down | dutch angle); orientación del sujeto (frontal | 3/4 view |
  profile/side | from behind); distancia/lente (macro | 35mm wide | 85mm portrait |
  telephoto). NUNCA lo dejes vago: comprométete con lo que ves en la imagen.>
BACKGROUND: <solo el fondo/entorno global, ignorando los sujetos>
MEDIUM: <photograph | illustration | 3d render | painting | ...>
PHOTO: <cámara/lente aparente — SOLO si es photograph; si no, escribe ->>
ART_STYLE: <estilo artístico — SOLO si NO es photograph; si no, escribe ->>
AESTHETICS: <estética general en pocas palabras>
LIGHTING: <iluminación>
PALETTE: <3-6 colores dominantes en hex, p.ej. #223344, #aabbcc>
OBJECTS:
- <descripción breve del elemento> :: <región>
- <descripción breve del elemento> :: <región>
TEXT:
- "<texto LITERAL escrito en la imagen>" :: <región> :: <estilo tipográfico>

Reglas:
- Descompón la escena en VARIOS objetos: el/los sujeto(s) MÁS elementos del
  entorno (suelo, pared, cielo, mobiliario, objetos de apoyo). Nunca uno solo.
- <región> es UNA de: top-left, top, top-right, left, center, right,
  bottom-left, bottom, bottom-right, full. Elige dónde está de verdad el elemento.
- La sección TEXT es SOLO para texto realmente ESCRITO en la imagen (rótulos,
  carteles). Si no hay texto legible, escribe exactamente "TEXT:" y nada debajo.
- No añadas explicaciones ni ``` fuera del bloque etiquetado.\
"""


# Regiones nombradas → bbox [ymin, xmin, ymax, xmax] en rejilla 0-1000. Cajas
# gruesas sobre una malla 3x3 (el usuario las afina luego). "full" cubre casi todo.
def _region_to_bbox(region: str) -> list[int]:
    r = (region or "").lower()
    if any(k in r for k in ("full", "whole", "entire", "cover", "all", "fills")):
        return [40, 40, 960, 960]
    if any(k in r for k in ("top", "upper", "above", "sky")):
        ys = (60, 430)
    elif any(k in r for k in ("bottom", "lower", "below", "ground", "floor", "foreground")):
        ys = (570, 940)
    else:
        ys = (330, 700)
    if "left" in r:
        xs = (60, 440)
    elif "right" in r:
        xs = (560, 940)
    else:
        xs = (330, 670)
    return [ys[0], xs[0], ys[1], xs[1]]


def parse_vision_freetext(text: str, width: int = 2048, height: int = 2048,
                          general: str = "") -> dict:
    """Convierte el bloque etiquetado del VLM (texto libre) en el dict de caption
    de Ideogram, listo para validate_and_clean. Tolera líneas extra/ausentes: solo
    lee las etiquetas conocidas y las secciones OBJECTS/TEXT. Las bboxes salen de
    la región nombrada de cada línea (malla 3x3)."""
    fields: dict = {}
    objs: list[tuple[str, str]] = []       # (desc, region)
    texts: list[tuple[str, str, str]] = []  # (literal, region, style)
    section = None
    KEYS = ("HLD", "SHOT", "BACKGROUND", "MEDIUM", "PHOTO", "ART_STYLE", "AESTHETICS",
            "LIGHTING", "PALETTE")
    for line in text.splitlines():
        ln = line.strip()
        if not ln or ln.startswith("```"):
            continue
        up = ln.upper()
        if up.startswith("OBJECTS"):
            section = "obj"; continue
        if up.startswith("TEXT"):
            section = "text"; continue
        m = re.match(r"^([A-Z_]+)\s*:\s*(.*)$", ln)
        if m and m.group(1) in KEYS:
            section = None
            fields[m.group(1)] = m.group(2).strip()
            continue
        # Dentro de una sección, cualquier línea es un ítem: tolera bullets
        # (- * •), listas numeradas (1. / 1)) o texto pelado. Separador :: o |.
        if section:
            item = re.sub(r"^[-*•\d.)\s]+", "", ln).strip()
            if not item:
                continue
            parts = [p.strip() for p in re.split(r"\s*(?:::|\|)\s*", item)]
            if section == "obj" and parts and parts[0]:
                objs.append((parts[0], parts[1] if len(parts) > 1 else "center"))
            elif section == "text" and parts and parts[0]:
                lit = parts[0].strip().strip('"').strip("'").strip()
                if lit:
                    texts.append((lit, parts[1] if len(parts) > 1 else "center",
                                  parts[2] if len(parts) > 2 else ""))

    def _blank(v: str) -> bool:
        return not v or v.strip() in ("-", "->", "n/a", "none", "")

    medium = fields.get("MEDIUM", "").strip()
    style: dict = {
        "aesthetics": fields.get("AESTHETICS", ""),
        "lighting": fields.get("LIGHTING", ""),
        "medium": medium or "photograph",
    }
    is_photo = "photo" in medium.lower() or not _blank(fields.get("PHOTO", ""))
    if is_photo and not _blank(fields.get("PHOTO", "")):
        style["photo"] = fields["PHOTO"].strip()
    if not is_photo and not _blank(fields.get("ART_STYLE", "")):
        style["art_style"] = fields["ART_STYLE"].strip()
    pal = re.findall(r"#[0-9A-Fa-f]{6}", fields.get("PALETTE", ""))
    if pal:
        style["color_palette"] = pal[:16]

    elements: list[dict] = []
    for desc, region in objs:
        elements.append({"type": "obj", "bbox": _region_to_bbox(region), "desc": desc})
    for lit, region, tstyle in texts:
        elements.append({"type": "text", "bbox": _region_to_bbox(region),
                         "desc": tstyle or f'text reading "{lit}"', "text": lit})

    # El encuadre/ángulo (SHOT) se antepone al HLD: sin él la generación deja la
    # toma al azar. Va al condicionamiento como parte de la frase resumen.
    hld = fields.get("HLD", "").strip() or general.strip()
    shot = fields.get("SHOT", "").strip()
    if shot and not _blank(shot):
        hld = f"{shot}. {hld}" if hld else shot
    return {
        "high_level_description": hld,
        "style_description": style,
        "compositional_deconstruction": {
            "background": fields.get("BACKGROUND", ""),
            "elements": elements,
        },
    }


def build_messages(description: str, width: int = 1024, height: int = 1024) -> list[dict]:
    """Mensajes para el chat del LLM. La descripción del usuario + el aspecto
    del lienzo (ayuda al LLM a ubicar las cajas)."""
    aspect = _aspect_hint(width, height)
    user = (
        f"Lienzo: {width}x{height} px ({aspect}). Rejilla de bounding boxes 0-1000.\n\n"
        f"Descripción del usuario:\n{description.strip()}\n\n"
        "Genera el caption JSON de Ideogram 4 siguiendo las reglas."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_refine_messages(manual: dict, general: str = "",
                          width: int = 2048, height: int = 2048) -> list[dict]:
    """Mensajes para el MODO MANUAL: se le pasa al LLM el borrador con las cajas
    ya colocadas para que lo complete/normalice sin alterar la composición."""
    aspect = _aspect_hint(width, height)
    payload = json.dumps(manual, ensure_ascii=False, indent=2)
    gp = f"\nPrompt general del usuario:\n{general.strip()}\n" if general.strip() else ""
    user = (
        f"Lienzo: {width}x{height} px ({aspect}). Rejilla de bounding boxes 0-1000.\n{gp}\n"
        f"Borrador manual del usuario (cajas ya colocadas):\n{payload}\n\n"
        "Completa y normaliza este caption siguiendo las reglas. Conserva las cajas intactas."
    )
    return [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_translate_messages(manual: dict, general: str = "",
                             width: int = 2048, height: int = 2048) -> list[dict]:
    """Mensajes para el pase de TRADUCCIÓN/CORRECCIÓN del modo manual: lleva el
    texto del borrador a inglés literal y correcto sin alterar la composición ni
    inflar el prompt."""
    aspect = _aspect_hint(width, height)
    payload = json.dumps(manual, ensure_ascii=False, indent=2)
    gp = f"\nPrompt general del usuario:\n{general.strip()}\n" if general.strip() else ""
    user = (
        f"Lienzo: {width}x{height} px ({aspect}).\n{gp}\n"
        f"Borrador manual del usuario:\n{payload}\n\n"
        "Traduce a inglés correcto y corrige TODO su texto siguiendo las reglas. "
        "Conserva las cajas y su geometría intactas."
    )
    return [
        {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_vision_messages(image_data_uri: str, width: int = 2048, height: int = 2048,
                          general: str = "") -> list[dict]:
    """Mensajes multimodales para el AUTOPROMPT: el VLM ve la imagen de referencia
    y devuelve el BLOQUE ETIQUETADO de texto libre (sin gramática — la parsea
    parse_vision_freetext). `image_data_uri` es un data URI OpenAI-style
    (data:image/...;base64,...). `general` es una nota/instrucción opcional del
    usuario (p.ej. un trigger o un ajuste de estilo)."""
    aspect = _aspect_hint(width, height)
    note = f"\nNota del usuario (tenla en cuenta sin contradecir la imagen): {general.strip()}" if general.strip() else ""
    user = [
        {"type": "text", "text": (
            f"Imagen objetivo: {aspect}.{note}\n"
            "Analiza la imagen y descríbela en el bloque etiquetado siguiendo el formato exacto."
        )},
        {"type": "image_url", "image_url": {"url": image_data_uri}},
    ]
    return [
        {"role": "system", "content": VISION_FREETEXT_PROMPT},
        {"role": "user", "content": user},
    ]


def _aspect_hint(w: int, h: int) -> str:
    if w == h:
        return "cuadrado"
    return "apaisado" if w > h else "vertical"


# ── Parsing / validación del JSON devuelto ───────────────────────────────────

def parse_llm_output(raw: str) -> dict:
    """Extrae el objeto JSON de la respuesta del LLM (tolera ``` y texto extra)."""
    raw = raw.strip()
    # quitar fences ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    else:
        # primer { hasta el último }
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise CaptionError(f"el LLM no devolvió JSON válido: {e}")


_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _clean_palette(pal, limit: int) -> list[str]:
    if not isinstance(pal, list):
        return []
    out = [c for c in pal if isinstance(c, str) and _HEX.match(c)]
    return out[:limit]


def _clamp_bbox(bbox) -> list[int]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise CaptionError(f"bbox inválido: {bbox!r}")
    try:
        ymin, xmin, ymax, xmax = (max(0, min(1000, int(round(float(v))))) for v in bbox)
    except (TypeError, ValueError):
        raise CaptionError(f"bbox no numérico: {bbox!r}")
    if ymin > ymax:
        ymin, ymax = ymax, ymin
    if xmin > xmax:
        xmin, xmax = xmax, xmin
    return [ymin, xmin, ymax, xmax]


def validate_and_clean(obj: dict) -> dict:
    """Normaliza el caption: recorta bboxes al rango, limpia paletas, exige los
    campos mínimos. Devuelve un dict listo para serializar hacia ComfyUI."""
    if not isinstance(obj, dict):
        raise CaptionError("el caption no es un objeto JSON")

    out: dict = {}
    hld = obj.get("high_level_description", "")
    out["high_level_description"] = hld if isinstance(hld, str) else ""

    style = obj.get("style_description")
    if isinstance(style, dict):
        s: dict = {
            "aesthetics": str(style.get("aesthetics", "")),
            "lighting": str(style.get("lighting", "")),
            "medium": str(style.get("medium", "")),
        }
        if style.get("photo"):
            s["photo"] = str(style["photo"])
        if style.get("art_style"):
            s["art_style"] = str(style["art_style"])
        pal = _clean_palette(style.get("color_palette"), 16)
        if pal:
            s["color_palette"] = pal
        out["style_description"] = s

    comp = obj.get("compositional_deconstruction")
    if not isinstance(comp, dict):
        raise CaptionError("falta compositional_deconstruction")
    elements_in = comp.get("elements")
    if not isinstance(elements_in, list) or not elements_in:
        raise CaptionError("compositional_deconstruction.elements vacío")

    elements = []
    for el in elements_in:
        if not isinstance(el, dict):
            continue
        # type="text" SOLO es válido si trae una cadena real a renderizar; los
        # LLM pequeños marcan objetos como "text" con text vacío → se degradan a
        # "obj" (si no, Ideogram intenta escribir glifos donde va un objeto).
        text_val = str(el.get("text", "")).strip()
        etype = "text" if (el.get("type") == "text" and text_val) else "obj"
        e: dict = {
            "type": etype,
            "bbox": _clamp_bbox(el.get("bbox")),
            "desc": str(el.get("desc", "")),
        }
        if etype == "text":
            e["text"] = text_val
        pal = _clean_palette(el.get("color_palette"), 5)
        if pal:
            e["color_palette"] = pal
        elements.append(e)

    if not elements:
        raise CaptionError("ningún elemento válido tras la limpieza")

    out["compositional_deconstruction"] = {
        "background": str(comp.get("background", "")),
        "elements": elements,
    }
    return out


def preserve_geometry(manual: dict, refined: dict, general: str = "",
                      translate_text: bool = False) -> dict:
    """Fusiona el refinado del LLM sobre el borrador manual GARANTIZANDO que la
    geometría del usuario manda: fuerza bbox/type de cada caja manual y solo toma
    del LLM la redacción de "desc", las paletas y los campos globales (estilo,
    fondo, resumen). Blindaje: aunque el LLM mueva/añada/borre cajas, la
    composición del usuario se conserva intacta.

    translate_text=True (pase de traducción): también adopta el "text" (rótulo)
    traducido por el LLM en las cajas de tipo texto; por defecto (refine) el
    rótulo del usuario se conserva literal."""
    man_els = manual.get("compositional_deconstruction", {}).get("elements", [])
    comp = refined.setdefault("compositional_deconstruction", {})
    ref_els = comp.get("elements") if isinstance(comp.get("elements"), list) else []
    merged = []
    for i, m in enumerate(man_els):
        r = ref_els[i] if i < len(ref_els) else {}
        if not isinstance(r, dict):
            r = {}
        e = {"type": m["type"], "bbox": m["bbox"], "desc": m.get("desc", "")}
        if r.get("desc"):
            e["desc"] = str(r["desc"])
        if m["type"] == "text":
            rt = str(r.get("text", "")).strip()
            e["text"] = rt if (translate_text and rt) else m.get("text", "")
        pal = _clean_palette(r.get("color_palette"), 5)
        if pal:
            e["color_palette"] = pal
        merged.append(e)
    comp["elements"] = merged
    if general.strip() and not str(refined.get("high_level_description", "")).strip():
        refined["high_level_description"] = general.strip()
    return refined


def to_prompt_string(caption: dict) -> str:
    """Serializa el caption al STRING que se inyecta en CLIPTextEncode."""
    return json.dumps(caption, ensure_ascii=False, indent=2)
