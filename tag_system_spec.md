# Sistema de Tags para Editor de Prompts (ComfyUI Frontend)

## Contexto

Estoy desarrollando un frontend simplificado para ComfyUI orientado a modelos SDXL que usan tags estilo Danbooru (Illustrious, NoobAI, Pony, etc.). El modelo principal de uso es **WAI-Illustrious-SDXL**, un fine-tune de Illustrious-XL.

Este documento describe el sistema de autocomplete + correctores que necesito implementar. El objetivo es que el usuario escriba tags fluidamente sin tener que memorizar la forma canónica exacta que el modelo conoce.

---

## 1. Fuente de datos

### CSV de tags

Usar la lista **Danbooru-only** del paquete "22.12.2024 Refined" de Civitai (model ID 950325):
- URL: https://civitai.com/models/950325/danboorue621-autocomplete-tag-lists-incl-aliases-krita-ai-support
- Variante: **Danbooru** (NO la merged, NO la e621 — Illustrious solo conoce Danbooru)
- Versión: 22.12.2024 (sigue siendo la recomendada porque el cutoff de Illustrious es ~junio 2024)

### Formato CSV

Cuatro columnas, mismo formato que `a1111-sd-webui-tagcomplete`:

```
tag,category,post_count,aliases
jeans,0,485231,"denim_pants,blue_jeans"
sakura_futaba_(persona_5),4,5200,"futaba_sakura"
```

- `tag`: forma canónica (la que el modelo realmente conoce)
- `category`: int
  - 0 = general (azul)
  - 1 = artista (rojo)
  - 3 = copyright/serie (morado)
  - 4 = personaje (verde)
  - 5 = meta (naranja)
- `post_count`: int, cuántas imágenes en Danbooru tienen este tag (proxy de qué tan bien lo aprendió el modelo)
- `aliases`: lista de strings separados por coma, formas alternativas que mappean a este tag

### Carga

CSV pesa ~50-100MB descomprimido. Cargar a memoria al startup es aceptable. Estructura sugerida:

```python
tags = [
    {
        "name": "jeans",
        "category": 0,
        "post_count": 485231,
        "aliases": ["denim_pants", "blue_jeans"]
    },
    ...
]
```

Construir índices auxiliares al cargar (ver secciones de búsqueda).

---

## 2. Autocomplete: sistema de scoring tiered

El usuario empieza a escribir → mostrar **top 5** resultados ordenados por relevancia. La relevancia se calcula con un sistema de **tiers** donde cada nivel domina al siguiente, y dentro de cada tier `log10(post_count)` desempata.

### Algoritmo de scoring

```
def score(query, tag):
    q = query.lower().strip()
    q_tokens = set(q.replace('_', ' ').split())
    name = tag.name.lower()
    name_tokens = set(name.replace('_', ' ').split())
    # Quitar suffix "_(serie)" para matching de personajes
    name_clean = re.sub(r'_\([^)]+\)$', '', name)
    name_clean_tokens = set(name_clean.replace('_', ' ').split())
    aliases = [a.lower() for a in tag.aliases]
    
    if q == name or q in aliases:
        tier = 1000  # match exacto
    elif q_tokens == name_clean_tokens:
        tier = 900   # token-set exacto (resuelve orden invertido)
    elif name.startswith(q):
        tier = 800   # prefix en nombre
    elif any(a.startswith(q) for a in aliases):
        tier = 700   # prefix en alias
    elif all(any(nt.startswith(qt) for nt in name_clean_tokens) for qt in q_tokens):
        tier = 600   # todos los tokens del query son prefijo de algún token del tag
    elif q in name or any(q in a for a in aliases):
        tier = 500   # substring
    else:
        # Fuzzy (edit distance) solo si los tiers superiores devuelven pocos resultados
        return None
    
    return tier + math.log10(max(tag.post_count, 1))
```

**Por qué funciona:**
- `jean` → tier 800 atrapa `jeans`, `jean_jacket`, etc., ordenados por post_count
- `denim pants` → tier 1000 atrapa `jeans` porque `denim_pants` es alias exacto
- `futaba sakura` → tier 900 atrapa `sakura_futaba_(persona_5)` aunque el orden esté invertido
- `sakura futaba` → mismo resultado, mismo tier
- `pants denim` → tier 900 atrapa `denim_pants` aunque alguien lo prefiera al canónico `jeans` (ajuste fino con post_count)

### Fuzzy match (opcional, segundo paso)

Si los tiers 1000-500 devuelven menos de 5 resultados, correr edit distance contra el resto:
- Pre-filtrar por longitud (±2 caracteres) y primera letra
- Usar `rapidfuzz` (Python) o `fuse.js` (JS)
- Tier resultante: `400 - edit_distance * 50`
- Solo correr este paso después de 200ms de inactividad (debounce diferenciado)

### Top 5

```python
matches = [(t, score(q, t)) for t in all_tags if score(q, t) is not None]
matches.sort(key=lambda x: -x[1])
return matches[:5]
```

---

## 3. Inserción de tags (prevención > corrección)

Cuando el usuario selecciona un tag del autocomplete (Tab/Enter/click), la inserción debe ser "inteligente" para evitar errores comunes.

### Reglas de inserción

1. **Reemplazar el token parcial** que el usuario escribió, no insertar en la posición del cursor cruda
   - Buscar desde el cursor hacia atrás hasta la última coma o salto de línea
   - Reemplazar ese rango con el tag completo

2. **Auto-escape de paréntesis** en el nombre del tag
   - Tags como `sakura_futaba_(persona_5)` tienen paréntesis literales, NO son pesos
   - Al insertar, escapar: `sakura_futaba_\(persona_5\)`
   - Sin esto, el parser del prompt los interpreta como peso y rompe el tag

3. **Auto-coma al final**
   - Añadir `, ` después del tag insertado
   - EXCEPCIÓN: si el siguiente carácter ya es `,`, no añadir otra
   - EXCEPCIÓN: si el cursor está al final del prompt, sí añadir (el usuario probablemente sigue escribiendo)

4. **Mover el cursor** al final de la inserción (después de `, `)

### Pseudocódigo

```javascript
function insertTag(textarea, tagName) {
  const pos = textarea.selectionStart;
  const before = textarea.value.slice(0, pos);
  const after = textarea.value.slice(pos);
  
  // Encontrar inicio del token parcial
  const tokenStart = Math.max(
    before.lastIndexOf(','),
    before.lastIndexOf('\n'),
    -1
  ) + 1;
  
  const prefix = before.slice(0, tokenStart);
  const leadingSpace = needsLeadingSpace(prefix) ? ' ' : '';
  
  // Escapar paréntesis del nombre del tag
  const escaped = tagName.replace(/([()])/g, '\\$1');
  
  // Decidir si añadir coma
  const trimmedAfter = after.trimStart();
  const needsComma = !trimmedAfter.startsWith(',');
  const suffix = needsComma ? ', ' : '';
  
  // Aplicar
  textarea.value = prefix + leadingSpace + escaped + suffix + after;
  textarea.selectionStart = textarea.selectionEnd = 
    (prefix + leadingSpace + escaped + suffix).length;
}
```

---

## 4. Auto-balance de paréntesis

Los paréntesis en prompts SDXL son **multiplicadores de atención**, no agrupación sintáctica:
- `(tag)` = peso 1.1
- `((tag))` = 1.21
- `(tag:1.3)` = peso explícito 1.3

A veces el usuario abre paréntesis y se distrae sin cerrarlos. El sistema debe auto-balancear silenciosamente.

### Cuándo correr el balanceador

- Después de insertar un tag desde el autocomplete
- Después de que el usuario teclea `,`
- NO en cada keystroke (interfiere con escribir pesos como `(tag:1.3)`)

### Algoritmo

Analizar solo el **chunk actual** (texto entre las comas circundantes), no todo el prompt:

```javascript
function balanceChunk(text, cursorPos) {
  const chunkStart = Math.max(text.lastIndexOf(',', cursorPos - 1), -1) + 1;
  const nextComma = text.indexOf(',', cursorPos);
  const chunkEnd = nextComma === -1 ? text.length : nextComma;
  const chunk = text.slice(chunkStart, chunkEnd);
  
  let open = 0;
  for (let i = 0; i < chunk.length; i++) {
    if (chunk[i] === '\\') { i++; continue; }  // skip escaped
    if (chunk[i] === '(') open++;
    else if (chunk[i] === ')') open = Math.max(0, open - 1);
  }
  
  if (open > 0) {
    return text.slice(0, chunkEnd) + ')'.repeat(open) + text.slice(chunkEnd);
  }
  return text;
}
```

### Reglas importantes

- **Ignorar `\(` y `\)`** — son literales, no cuentan para el balance
- **No tocar pesos numéricos** — `(tag:1.3)` está balanceado, no es error
- **Anidamiento legítimo** — `((tag))` es válido (peso 1.21), no "simplificar"
- **Solo cerrar abiertos, no insertar abiertos** — si hay `)` sobrante, mejor eliminarlo o ignorarlo que insertar un `(` (cambiaría el peso del tag anterior)

### Anti-pattern: paréntesis anidados sin `:`

Cuando un usuario escribe algo como `(clothes(shoes(red))tshirt(gray))` está pensando en sintaxis jerárquica de programación, pero el parser lo interpreta como multiplicadores acumulativos. Esto produce pesos exagerados (`red` termina con peso 1.331) y la "agrupación" que el usuario imagina no existe.

**Opcional pero recomendado:** detectar el patrón de paréntesis anidados sin `:` numérico y mostrar un warning sutil tipo *"este patrón aplica pesos compuestos altos, ¿quisiste usar `(tag:1.3)`?"*. No autocorregir.

---

## 5. Configuración por modelo (preset WAI-Illustrious)

Crear una estructura de "model profile" en JSON, editable por el usuario, con presets predefinidos. Profile inicial:

```json
{
  "name": "WAI-Illustrious-SDXL",
  "family": "illustrious",
  "tag_csv": "danbooru_22122024",
  "quality_prefix": "masterpiece, best quality, amazing quality, very aesthetic, absurdres",
  "negative_base": "worst quality, bad quality, bad anatomy, lowres, bad hands, deformed",
  "rating_tags": ["general", "sensitive", "nsfw", "explicit"],
  "default_rating_negative": "nsfw",
  "name_order": "japanese",
  "danbooru_cutoff": "2024-06"
}
```

### Comportamientos asociados al profile

1. **Quality prefix** se puede insertar con un botón "Add quality tags" al inicio del prompt
2. **Selector de rating** (dropdown) que añade el tag correspondiente al positivo y opcionalmente "nsfw" al negativo
3. **Normalización de nombres de personaje**: si el usuario escribe nombres en orden occidental ("Misato Katsuragi"), el sistema sugiere el orden Danbooru ("katsuragi_misato") porque el modelo así los aprendió
4. **Warning de cutoff**: si el usuario inserta un tag con fecha de creación posterior a `danbooru_cutoff`, mostrar warning de que el modelo probablemente no lo conoce (requiere campo extra en el CSV o lookup a la API de Danbooru)

---

## 6. UX mínimo

El editor es minimalista. El autocomplete debe ser invisible cuando no se necesita.

### Interacción

- Mientras el usuario escribe dentro de un tag (después de la última coma), mostrar dropdown con **top 5** resultados
- Primera opción **pre-seleccionada** visualmente
- **Tab** o **Enter**: inserta la opción seleccionada
- **Flechas ↑/↓**: navegan la lista
- **Esc**: cierra el dropdown sin insertar
- **Click**: inserta esa opción

### Visualización de cada item

```
[color] tag_name              post_count
        ← alias (si match vino por alias)
```

- `[color]` codifica la categoría (azul=general, rojo=artista, morado=serie, verde=personaje, naranja=meta)
- `tag_name` mostrado con underscores reemplazados por espacios para legibilidad (pero insertado con underscores)
- `post_count` formateado: 485231 → "485k", 1200000 → "1.2M"
- Si el match vino por alias, mostrar el alias debajo en gris pequeño

### Performance

- **Debounce de 50ms** para los tiers 1000-500 (búsqueda barata)
- **Debounce de 200ms** para fuzzy match (solo si los tiers superiores devuelven <5)
- Búsqueda en **Web Worker** si es web, o thread separado en desktop, para no bloquear el input
- Pre-construir un **índice por primera letra** para reducir el universo de búsqueda de ~1M a ~30k de entrada

---

## 7. Casos borde y reglas finales

### Normalización

- Underscores ↔ espacios son intercambiables en input/output
- Internamente todo se compara en lowercase
- Diacríticos: normalizar con NFKD antes de tokenizar

### Tags multi-personaje

Tags como `sakura_futaba_and_takamaki_ann` existen. No incluirlos en el índice de personajes (category 4) porque rompen el token-set matching cuando el usuario solo escribe un nombre. Dejarlos solo en el autocomplete general.

### Personajes mononímicos

Personajes como "Rin", "Saber" con un solo nombre. Sin suffix de serie generan muchos falsos positivos. Para estos, mostrar siempre el suffix entre paréntesis al usuario para desambiguar (`saber_(fate)`, `saber_(fate/extra)`, etc.).

### Persistencia

- El profile activo se guarda en config local
- El CSV se descarga una vez y se cachea localmente
- Verificación periódica (semanal) de si hay versión más nueva del CSV en la fuente

---

## Orden recomendado de implementación

1. **Carga e indexación del CSV** (parse, estructura, índices auxiliares)
2. **Scoring tiered + top 5** (núcleo del autocomplete)
3. **Inserción inteligente** (auto-coma, auto-escape de paréntesis en nombres)
4. **Auto-balance de paréntesis** (al insertar y al teclear coma)
5. **Model profile + preset WAI** (selector, quality prefix, rating)
6. **Fuzzy match opcional** (segundo paso si los tiers superiores son insuficientes)
7. **Warning de anti-pattern** de paréntesis anidados (opcional, baja prioridad)

---

## Notas técnicas finales

- El sistema es **prevención > corrección**: insertar bien desde el principio elimina la necesidad de correctores complejos
- El token-set matching resuelve órdenes invertidos sin código de "autocorrector" explícito
- El `log10(post_count)` dentro de cada tier es lo que produce el efecto "lo más probable arriba" sin necesidad de heurísticas adicionales
- Todos los aliases del CSV ya cubren los sinónimos comunes (`denim_pants` → `jeans`, `futaba_sakura` → `sakura_futaba`, etc.), no hay que mantener una lista propia

Cualquier funcionalidad más sofisticada (corrector de comas faltantes por segmentación DP, etc.) puede agregarse en v2 si la experiencia de uso real lo justifica. La base descrita arriba cubre ~95% del uso real.
