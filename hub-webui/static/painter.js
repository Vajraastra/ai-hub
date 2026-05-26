/* Painter — lógica principal */
'use strict';

const API = '/api/painter';

// ── Estado global ─────────────────────────────────────────────────────────
const S = {
  arch:       'sdxl',
  tool:       'brush',
  brushSize:  30,
  imgW: 0, imgH: 0,       // resolución de la imagen actual
  scale:  1,              // factor CSS→imagen
  hasMask:    false,
  showMask:   true,   // false mientras se muestra un preview (máscara oculta pero no borrada)
  hasImage:   false,
  hasPreview: false,
  activeJobId: null,
  ws:         null,
  models:     { checkpoints:[], controlnet:[], upscale_models:[], samplers:[], schedulers:[] },
  session:    { has_current:false, history_size:0, redo_size:0 },
  regional:   { active: false, activeIdx: 0 },
};

// ── Presets de tamaño de canvas ───────────────────────────────────────────
const SIZE_PRESETS = [
  { w: 512,  h: 512,  label: '512×512',   desc: 'SD 1.5' },
  { w: 768,  h: 512,  label: '768×512',   desc: 'Landscape' },
  { w: 512,  h: 768,  label: '512×768',   desc: 'Portrait' },
  { w: 1024, h: 1024, label: '1024×1024', desc: 'SDXL ★' },
  { w: 1216, h: 832,  label: '1216×832',  desc: 'SDXL Wide' },
  { w: 832,  h: 1216, label: '832×1216',  desc: 'SDXL Tall' },
  { w: 1344, h: 768,  label: '1344×768',  desc: 'Cinematic' },
  { w: 768,  h: 1344, label: '768×1344',  desc: 'Vertical' },
  { w: null, h: null, label: 'Custom',    desc: 'Libre' },
];
let _selectedSizeIdx = 3;  // default: 1024×1024

// ── Regional Conditioning ─────────────────────────────────────────────────
const REGION_COLORS = ['#FF4444', '#4488FF', '#44CC44', '#FFAA00'];
let regionalMasks = [];  // [{canvas, ctx, hasPixels}]

// Estado de la generación regional secuencial
let _regSeq = {
  active:      false,
  queue:       [],    // [{prompt, mask_b64, regionIdx}] — regiones pendientes
  total:       0,     // total de regiones a procesar
  stepIdx:     0,     // 0-based: qué región estamos procesando ahora
  baseB64:     null,  // imagen acumulada (resultado aceptado de la región anterior)
  lastB64:     null,  // resultado del job actual (para capturarlo en onJobDone)
  params:      {},    // params compartidos (checkpoint, neg, seed, steps, cfg, denoise, w, h)
  seed:        0,     // semilla actual (se incrementa al regenerar)
};

// ── Canvas ────────────────────────────────────────────────────────────────
let canvasBg, ctxBg, canvasFg, ctxFg, maskCanvas, ctxMask;
let currentImg  = null;   // HTMLImageElement — imagen aceptada
let previewImg  = null;   // HTMLImageElement — preview pendiente

// ── LoRA token highlight ──────────────────────────────────────────────────
let _allLoras = [];   // lista completa del endpoint /models; usada para validar tokens

// Reconoce cualquier <lora:...> independientemente del formato
const _LORA_RE = /<lora:[^>]+>/gi;

function _loraFileStem(name) {
  // Extrae solo el nombre de archivo sin ruta ni extensión: "a/b/Foo.safetensors" → "foo"
  const base = name.includes('/') ? name.slice(name.lastIndexOf('/') + 1) : name;
  const dot  = base.lastIndexOf('.');
  return (dot > 0 ? base.slice(0, dot) : base).toLowerCase();
}

function _isLoraValid(stem) {
  if (!_allLoras.length) return true;   // lista aún no cargada → neutral
  const s = stem.toLowerCase();
  return _allLoras.some(n =>
    n === stem ||
    n.toLowerCase() === s ||
    _loraFileStem(n) === s
  );
}

function renderLoraChips() {
  const ta      = document.getElementById('inp-prompt');
  const container = document.getElementById('lora-chips');
  if (!ta || !container) return;

  const raw = ta.value;
  container.innerHTML = '';

  _LORA_RE.lastIndex = 0;
  let m;
  while ((m = _LORA_RE.exec(raw)) !== null) {
    const token  = m[0];                          // "<lora:name:strength>"
    const inner  = token.slice(6, -1);            // "name:strength" o "name"
    const parts  = inner.split(':');
    const name   = parts[0].trim();
    const str    = parts[1] ? parseFloat(parts[1]).toFixed(2) : '1.00';
    const valid  = _isLoraValid(name);

    const chip = document.createElement('span');
    chip.className = `lora-chip ${valid ? 'valid' : 'invalid'}`;
    chip.title = token;

    const label = document.createElement('span');
    label.className = 'lora-chip-name';
    label.textContent = _loraFileStem(name) || name;

    const strength = document.createElement('span');
    strength.className = 'lora-chip-str';
    strength.textContent = `×${str}`;

    const rm = document.createElement('button');
    rm.className = 'lora-chip-rm';
    rm.textContent = '✕';
    rm.title = 'Quitar LoRA';
    rm.addEventListener('click', () => {
      ta.value = ta.value.replace(token, '');
      // limpiar comas dobles o espacios sobrantes al borrar
      ta.value = ta.value.replace(/,\s*,/g, ',').replace(/(^[\s,]+|[\s,]+$)/g, '');
      ta.dispatchEvent(new Event('input'));
    });

    chip.appendChild(label);
    chip.appendChild(strength);
    chip.appendChild(rm);
    container.appendChild(chip);
  }
}

// ── Estilos (quality prompts) ─────────────────────────────────────────────
let _styles          = [];       // [{name, prompt}] — lista guardada en disco
let _activeStyleName = '';       // nombre del estilo activo ('' = ninguno)
let _activeStylePrompt = '';     // prompt del estilo activo

// ── ADetailer ─────────────────────────────────────────────────────────────
let _adDetectors      = [];      // [{id, label, filename, available}] del backend
let _adEnabled        = new Set(['face']);  // ids activos por defecto
let _adImpactAvailable = false;  // True si FaceDetailer está cargado en ComfyUI

/** Devuelve el prompt con el estilo activo concatenado al final. */
function withStyle(prompt) {
  const s = _activeStylePrompt.trim();
  if (!s) return prompt || '';
  const p = (prompt || '').trim();
  return p ? `${p}, ${s}` : s;
}

// ── Herramienta rect / lasso ──────────────────────────────────────────────
let drawing = false, rectStart = null, rectEnd = null, lassoPoints = [];

// ── Posición anterior del brush para trazos continuos ────────────────────
let _prevBrushX = null, _prevBrushY = null;

// ── Posición del cursor en espacio-imagen ─────────────────────────────────
let _cursorX = -1, _cursorY = -1, _cursorVisible = false;

// ── Init canvas ───────────────────────────────────────────────────────────
function initCanvas() {
  canvasBg   = document.getElementById('canvas-bg');
  ctxBg      = canvasBg.getContext('2d');
  canvasFg   = document.getElementById('canvas-fg');
  ctxFg      = canvasFg.getContext('2d');
  maskCanvas = document.createElement('canvas');
  ctxMask    = maskCanvas.getContext('2d');

  canvasFg.addEventListener('mousedown',  onMouseDown);
  canvasFg.addEventListener('mousemove',  onMouseMove);
  canvasFg.addEventListener('mouseup',    onMouseUp);
  canvasFg.addEventListener('mouseleave', e => { _cursorVisible = false; onMouseUp(e); });
  canvasFg.addEventListener('mouseenter', () => { _cursorVisible = true; });
  canvasFg.addEventListener('contextmenu', e => e.preventDefault());
  // Ocultar cursor CSS — lo dibujamos nosotros
  canvasFg.style.cursor = 'none';
}

function resizeCanvases(w, h) {
  S.imgW = w; S.imgH = h;
  const wrap  = document.getElementById('p-canvas-wrap');
  const maxW  = wrap.clientWidth  - 20;
  const maxH  = wrap.clientHeight - 20;
  S.scale     = Math.min(maxW / w, maxH / h, 1);
  const cssW  = Math.round(w * S.scale);
  const cssH  = Math.round(h * S.scale);

  [canvasBg, canvasFg].forEach(c => {
    c.width  = w; c.height = h;
    c.style.width  = cssW + 'px';
    c.style.height = cssH + 'px';
  });
  maskCanvas.width = w; maskCanvas.height = h;
  updateStatusDims();
}

// ── Coordenadas CSS → imagen ──────────────────────────────────────────────
function toImg(clientX, clientY) {
  const r = canvasFg.getBoundingClientRect();
  return [
    Math.round((clientX - r.left)  / S.scale),
    Math.round((clientY - r.top)   / S.scale),
  ];
}

// ── Render ────────────────────────────────────────────────────────────────
function render() {
  ctxFg.clearRect(0, 0, S.imgW, S.imgH);
  if (previewImg) ctxFg.drawImage(previewImg, 0, 0);
  if (S.regional.active) {
    drawRegionalOverlay();
  } else if (S.hasMask && S.showMask) {
    drawMaskOverlay();
  }
  if (_wandSel) _renderWandSelection();
  if (S.imgW > 0 && _cursorVisible) drawToolPreview();
  requestAnimationFrame(render);
}

function drawRegionalOverlay() {
  REGION_COLORS.forEach((color, i) => {
    const rm = regionalMasks[i];
    if (!rm || !rm.hasPixels) return;
    const tmp    = document.createElement('canvas');
    tmp.width    = S.imgW; tmp.height = S.imgH;
    const tCtx   = tmp.getContext('2d');
    tCtx.drawImage(rm.canvas, 0, 0);
    tCtx.globalCompositeOperation = 'source-in';
    tCtx.fillStyle = color;
    tCtx.fillRect(0, 0, S.imgW, S.imgH);
    ctxFg.save();
    ctxFg.globalAlpha = i === S.regional.activeIdx ? 0.55 : 0.35;
    ctxFg.drawImage(tmp, 0, 0);
    ctxFg.restore();
  });
}

function drawToolPreview() {
  const x = _cursorX, y = _cursorY;
  ctxFg.save();

  if (S.tool === 'wand' || S.tool === 'fill') {
    const sz = 10;
    const color = S.tool === 'wand' ? 'rgba(84,239,234,0.9)' : 'rgba(236,0,240,0.9)';
    ctxFg.strokeStyle = color;
    ctxFg.lineWidth   = 1.5;
    ctxFg.setLineDash([]);
    ctxFg.beginPath();
    ctxFg.moveTo(x - sz, y); ctxFg.lineTo(x + sz, y);
    ctxFg.moveTo(x, y - sz); ctxFg.lineTo(x, y + sz);
    ctxFg.stroke();
  } else if (S.tool === 'brush' || S.tool === 'eraser') {
    // Círculo que muestra el tamaño real del pincel
    const r = S.brushSize / 2;
    ctxFg.beginPath();
    ctxFg.arc(x, y, r, 0, Math.PI * 2);
    ctxFg.strokeStyle = 'rgba(255,255,255,0.85)';
    ctxFg.lineWidth   = 1;
    ctxFg.setLineDash([4, 3]);
    ctxFg.stroke();
    // Punto central
    ctxFg.beginPath();
    ctxFg.arc(x, y, 1.5, 0, Math.PI * 2);
    ctxFg.fillStyle = 'rgba(255,255,255,0.9)';
    ctxFg.fill();

  } else if (S.tool === 'rect' && drawing && rectStart && rectEnd) {
    const [x0, y0] = rectStart;
    const [x1, y1] = rectEnd;
    // Sombra negra para contraste sobre fondos claros
    ctxFg.strokeStyle = 'rgba(0,0,0,0.6)';
    ctxFg.lineWidth   = 3;
    ctxFg.setLineDash([]);
    ctxFg.strokeRect(x0, y0, x1 - x0, y1 - y0);
    // Línea blanca punteada encima
    ctxFg.strokeStyle = 'rgba(255,255,255,0.95)';
    ctxFg.lineWidth   = 2;
    ctxFg.setLineDash([6, 4]);
    ctxFg.strokeRect(x0, y0, x1 - x0, y1 - y0);
    // Dimensiones
    const w = Math.abs(x1 - x0), h = Math.abs(y1 - y0);
    ctxFg.setLineDash([]);
    ctxFg.font        = 'bold 12px monospace';
    ctxFg.fillStyle   = 'rgba(0,0,0,0.7)';
    ctxFg.fillText(`${w}×${h}`, Math.min(x0, x1) + 4, Math.min(y0, y1) - 5);
    ctxFg.fillStyle   = 'rgba(255,255,255,0.95)';
    ctxFg.fillText(`${w}×${h}`, Math.min(x0, x1) + 3, Math.min(y0, y1) - 6);

  } else if (S.tool === 'lasso' && lassoPoints.length > 1) {
    // Sombra negra
    ctxFg.strokeStyle = 'rgba(0,0,0,0.6)';
    ctxFg.lineWidth   = 3;
    ctxFg.setLineDash([]);
    ctxFg.beginPath();
    ctxFg.moveTo(lassoPoints[0][0], lassoPoints[0][1]);
    lassoPoints.slice(1).forEach(([px, py]) => ctxFg.lineTo(px, py));
    if (drawing) ctxFg.lineTo(x, y);
    ctxFg.stroke();
    // Línea blanca punteada encima
    ctxFg.strokeStyle = 'rgba(255,255,255,0.95)';
    ctxFg.lineWidth   = 2;
    ctxFg.setLineDash([6, 4]);
    ctxFg.beginPath();
    ctxFg.moveTo(lassoPoints[0][0], lassoPoints[0][1]);
    lassoPoints.slice(1).forEach(([px, py]) => ctxFg.lineTo(px, py));
    if (drawing) ctxFg.lineTo(x, y);
    ctxFg.stroke();
    // Punto de inicio (cyan para indicar dónde cerrar)
    ctxFg.setLineDash([]);
    ctxFg.beginPath();
    ctxFg.arc(lassoPoints[0][0], lassoPoints[0][1], 5, 0, Math.PI * 2);
    ctxFg.fillStyle = '#54EFEA';
    ctxFg.fill();
    ctxFg.strokeStyle = '#000';
    ctxFg.lineWidth = 1;
    ctxFg.stroke();

  } else if ((S.tool === 'rect' || S.tool === 'lasso') && !drawing) {
    // Cruz de referencia cuando no está dibujando
    const size = 9;
    ctxFg.setLineDash([]);
    ctxFg.lineWidth = 2;
    ctxFg.strokeStyle = 'rgba(0,0,0,0.5)';
    ctxFg.beginPath();
    ctxFg.moveTo(x - size, y); ctxFg.lineTo(x + size, y);
    ctxFg.moveTo(x, y - size); ctxFg.lineTo(x, y + size);
    ctxFg.stroke();
    ctxFg.strokeStyle = 'rgba(255,255,255,0.9)';
    ctxFg.lineWidth = 1.5;
    ctxFg.beginPath();
    ctxFg.moveTo(x - size, y); ctxFg.lineTo(x + size, y);
    ctxFg.moveTo(x, y - size); ctxFg.lineTo(x, y + size);
    ctxFg.stroke();
  }

  ctxFg.restore();
}

function drawMaskOverlay() {
  const tmp    = document.createElement('canvas');
  tmp.width    = S.imgW; tmp.height = S.imgH;
  const tmpCtx = tmp.getContext('2d');
  tmpCtx.drawImage(maskCanvas, 0, 0);
  tmpCtx.globalCompositeOperation = 'source-in';
  tmpCtx.fillStyle = '#EC00F0';
  tmpCtx.fillRect(0, 0, S.imgW, S.imgH);
  ctxFg.save();
  ctxFg.globalAlpha = 0.45;
  ctxFg.drawImage(tmp, 0, 0);
  ctxFg.restore();
}

// ── Herramientas ──────────────────────────────────────────────────────────
function onMouseDown(e) {
  if (S.hasPreview) return;
  drawing = true;
  _prevBrushX = null; _prevBrushY = null;  // inicio de trazo nuevo
  const [x, y] = toImg(e.clientX, e.clientY);
  if (S.tool === 'brush' || S.tool === 'eraser') {
    paintBrush(x, y);
  } else if (S.tool === 'wand') {
    wandSelect(x, y, e.shiftKey, e.altKey);
    drawing = false;
  } else if (S.tool === 'fill') {
    bucketFill(x, y, e.altKey);
    drawing = false;
  } else if (S.tool === 'rect') {
    rectStart = [x, y];
  } else if (S.tool === 'lasso') {
    lassoPoints = [[x, y]];
  }
}

function onMouseMove(e) {
  const [x, y] = toImg(e.clientX, e.clientY);
  _cursorX = x; _cursorY = y;
  updateStatusCursor(x, y);
  if (!drawing) return;
  if (S.tool === 'brush' || S.tool === 'eraser') {
    paintBrush(x, y);
  } else if (S.tool === 'lasso') {
    lassoPoints.push([x, y]);
  } else if (S.tool === 'rect' && rectStart) {
    rectEnd = [x, y];
  }
}

function onMouseUp(e) {
  if (!drawing) return;
  drawing = false;
  _prevBrushX = null; _prevBrushY = null;
  const [x, y] = toImg(e.clientX, e.clientY);
  if (S.tool === 'rect' && rectStart) {
    commitRect(rectStart[0], rectStart[1], x, y, e.shiftKey, e.altKey);
    rectStart = null; rectEnd = null;
  } else if (S.tool === 'lasso' && lassoPoints.length > 2) {
    commitLasso(e.shiftKey, e.altKey);
    lassoPoints = [];
  }
  updateMaskState();
}

function getActiveMaskCtx() {
  return S.regional.active ? regionalMasks[S.regional.activeIdx].ctx : ctxMask;
}

function paintBrush(x, y) {
  const ctx = getActiveMaskCtx();
  ctx.globalCompositeOperation = S.tool === 'eraser' ? 'destination-out' : 'source-over';
  ctx.lineWidth   = S.brushSize;
  ctx.lineCap     = 'round';
  ctx.lineJoin    = 'round';
  ctx.strokeStyle = 'white';
  ctx.fillStyle   = 'white';

  ctx.beginPath();
  if (_prevBrushX !== null) {
    // Trazo continuo desde el punto anterior al actual
    ctx.moveTo(_prevBrushX, _prevBrushY);
    ctx.lineTo(x, y);
    ctx.stroke();
  } else {
    // Primer punto del trazo: círculo puntual
    ctx.arc(x, y, S.brushSize / 2, 0, Math.PI * 2);
    ctx.fill();
  }
  _prevBrushX = x; _prevBrushY = y;

  if (S.regional.active) {
    regionalMasks[S.regional.activeIdx].hasPixels =
      checkRegionalMaskPixels(S.regional.activeIdx);
    updateRegionCards();
  }
  updateMaskState();
}

function commitRect(x0, y0, x1, y1, add, subtract) {
  const ctx = getActiveMaskCtx();
  ctx.globalCompositeOperation = subtract ? 'destination-out' : 'source-over';
  ctx.fillStyle = 'white';
  ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1),
               Math.abs(x1 - x0), Math.abs(y1 - y0));
  if (S.regional.active) {
    regionalMasks[S.regional.activeIdx].hasPixels =
      checkRegionalMaskPixels(S.regional.activeIdx);
    updateRegionCards();
  }
}

function commitLasso(add, subtract) {
  const ctx = getActiveMaskCtx();
  ctx.globalCompositeOperation = subtract ? 'destination-out' : 'source-over';
  ctx.fillStyle = 'white';
  ctx.beginPath();
  ctx.moveTo(lassoPoints[0][0], lassoPoints[0][1]);
  lassoPoints.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
  ctx.closePath();
  ctx.fill();
  if (S.regional.active) {
    regionalMasks[S.regional.activeIdx].hasPixels =
      checkRegionalMaskPixels(S.regional.activeIdx);
    updateRegionCards();
  }
}

function clearMask() {
  if (S.regional.active) {
    clearRegionalMask(S.regional.activeIdx);
    return;
  }
  ctxMask.clearRect(0, 0, S.imgW, S.imgH);
  updateMaskState();
}

function updateMaskState() {
  if (S.regional.active) return;
  const d = ctxMask.getImageData(0, 0, S.imgW, S.imgH).data;
  S.hasMask = d.some((v, i) => i % 4 === 3 && v > 0);
  document.getElementById('inpaint-model-group').style.display =
    S.hasMask ? '' : 'none';
}

function getMaskB64() {
  return maskCanvas.toDataURL('image/png').split(',')[1];
}

function getCurrentB64() {
  const tmp    = document.createElement('canvas');
  tmp.width    = S.imgW; tmp.height = S.imgH;
  tmp.getContext('2d').drawImage(canvasBg, 0, 0);
  return tmp.toDataURL('image/png').split(',')[1];
}

// ── Diálogo de tamaño de canvas ───────────────────────────────────────────
function buildSizeDialog() {
  const grid = document.getElementById('csm-grid');
  grid.innerHTML = '';
  SIZE_PRESETS.forEach((p, i) => {
    const card = document.createElement('div');
    card.className = 'csm-card' + (i === _selectedSizeIdx ? ' selected' : '');
    card.dataset.sizeIdx = i;
    if (p.w !== null) {
      const maxDim = 36, scale = Math.min(maxDim / p.w, maxDim / p.h);
      const sw = Math.max(4, Math.round(p.w * scale));
      const sh = Math.max(4, Math.round(p.h * scale));
      card.innerHTML = `
        <div style="width:${sw}px;height:${sh}px;background:rgba(96,13,181,.65);border-radius:2px"></div>
        <span class="csm-label">${p.label}</span>
        <span class="csm-desc">${p.desc}</span>`;
    } else {
      card.innerHTML = `
        <div style="width:28px;height:28px;border:1px dashed rgba(96,13,181,.7);border-radius:3px;display:flex;align-items:center;justify-content:center;font-size:16px;color:rgba(96,13,181,.9)">+</div>
        <span class="csm-label">${p.label}</span>
        <span class="csm-desc">${p.desc}</span>`;
    }
    card.addEventListener('click', () => {
      _selectedSizeIdx = i;
      grid.querySelectorAll('.csm-card').forEach((c, ci) =>
        c.classList.toggle('selected', ci === i));
      document.getElementById('csm-custom').style.display =
        SIZE_PRESETS[i].w === null ? '' : 'none';
    });
    grid.appendChild(card);
  });
  document.getElementById('csm-custom').style.display =
    SIZE_PRESETS[_selectedSizeIdx].w === null ? '' : 'none';
}

function showSizeDialog() {
  buildSizeDialog();
  document.getElementById('canvas-size-modal').style.display = 'flex';
}

function confirmSizeDialog() {
  const preset = SIZE_PRESETS[_selectedSizeIdx];
  let w, h;
  if (preset.w === null) {
    w = parseInt(document.getElementById('csm-w').value) || 1024;
    h = parseInt(document.getElementById('csm-h').value) || 1024;
    w = Math.round(w / 8) * 8;
    h = Math.round(h / 8) * 8;
  } else {
    w = preset.w; h = preset.h;
  }
  w = Math.max(64, Math.min(4096, w));
  h = Math.max(64, Math.min(4096, h));

  document.getElementById('canvas-size-modal').style.display = 'none';
  resizeCanvases(w, h);
  document.getElementById('inp-width').value  = w;
  document.getElementById('inp-height').value = h;
  requestAnimationFrame(render);
}

// ── Cargar imagen ─────────────────────────────────────────────────────────
function loadImageFile(file) {
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    currentImg = img;
    let w = img.naturalWidth, h = img.naturalHeight;
    // snap a múltiplo de 8
    w = Math.floor(w / 8) * 8;
    h = Math.floor(h / 8) * 8;
    resizeCanvases(w, h);
    ctxBg.clearRect(0, 0, w, h);
    ctxBg.drawImage(img, 0, 0, w, h);
    clearMask();
    document.getElementById('inp-width').value  = w;
    document.getElementById('inp-height').value = h;
    S.hasImage = true;
    updateButtons();
    URL.revokeObjectURL(url);
  };
  img.src = url;
}

function loadImageFromB64(b64) {
  return new Promise(resolve => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.src    = 'data:image/png;base64,' + b64;
  });
}

// ── API calls ─────────────────────────────────────────────────────────────
async function apiPost(path, body) {
  const r = await fetch(API + path, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || r.statusText);
  }
  return r.json();
}

async function apiGet(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

function getParams() {
  return {
    arch:             document.getElementById('arch-select').value,
    checkpoint:       document.getElementById('sel-checkpoint').value,
    prompt:           withStyle(document.getElementById('inp-prompt').value),
    negative_prompt:  document.getElementById('inp-negative').value,
    steps:            parseInt(document.getElementById('inp-steps').value),
    cfg:              parseFloat(document.getElementById('inp-cfg').value),
    sampler:          document.getElementById('sel-sampler').value,
    scheduler:        document.getElementById('sel-scheduler').value,
    seed:             parseInt(document.getElementById('inp-seed').value),
    denoise:          parseFloat(document.getElementById('inp-denoise').value),
    feather_radius:   parseInt(document.getElementById('inp-feather').value),
  };
}

// ── Generar (txt2img) ─────────────────────────────────────────────────────
async function doGenerate() {
  if (!document.getElementById('sel-checkpoint').value) {
    return toast(t('painter.no_checkpoint'));
  }
  const p = getParams();
  p.width  = parseInt(document.getElementById('inp-width').value);
  p.height = parseInt(document.getElementById('inp-height').value);
  try {
    const { job_id } = await apiPost('/generate', p);
    await trackJob(job_id);
  } catch (_) { toast(t('painter.conn_error')); }
}

// ── Inpaint ───────────────────────────────────────────────────────────────
async function doInpaint() {
  if (!S.hasImage) return toast(t('painter.no_image'));
  if (!S.hasMask)  return toast(t('painter.no_mask'));
  const p = getParams();
  p.image_b64     = getCurrentB64();
  p.mask_b64      = getMaskB64();
  p.inpaint_model = document.getElementById('sel-inpaint-model').value || null;
  try {
    const { job_id } = await apiPost('/inpaint', p);
    await trackJob(job_id);
  } catch (_) { toast(t('painter.conn_error')); }
}

// ── Guardar imagen a disco ────────────────────────────────────────────────
async function doSaveImage() {
  if (!S.hasImage) return;
  try {
    const { filename } = await apiPost('/save', { image_b64: getCurrentB64() });
    toast(`↓ ${filename}`);
  } catch (e) { toast(e.message || t('painter.conn_error')); }
}

// ── Outpaint ──────────────────────────────────────────────────────────────
function updateOpPreview() {
  const vals = { top: 'op-top', bottom: 'op-bottom', left: 'op-left', right: 'op-right' };
  for (const [dir, id] of Object.entries(vals)) {
    const v = parseInt(document.getElementById(id).value) || 0;
    document.getElementById('op-show-' + dir).classList.toggle('op-active', v > 0);
  }
}

async function doOutpaint() {
  if (!S.hasImage) return toast(t('painter.no_image'));
  const ckpt = document.getElementById('sel-checkpoint').value;
  if (!ckpt)    return toast(t('painter.no_checkpoint'));

  const top    = parseInt(document.getElementById('op-top').value)    || 0;
  const bottom = parseInt(document.getElementById('op-bottom').value) || 0;
  const left   = parseInt(document.getElementById('op-left').value)   || 0;
  const right  = parseInt(document.getElementById('op-right').value)  || 0;
  if (top + bottom + left + right === 0)
    return toast(t('painter.op_no_pad'));

  const arch    = document.getElementById('arch-select').value;
  const prompt  = withStyle(document.getElementById('inp-prompt').value);
  const neg     = document.getElementById('inp-negative').value;
  const steps   = parseInt(document.getElementById('inp-steps').value);
  const cfg     = parseFloat(document.getElementById('inp-cfg').value);
  const sampler = document.getElementById('sel-sampler').value;
  const sched   = document.getElementById('sel-scheduler').value;
  const seed    = parseInt(document.getElementById('inp-seed').value);
  const denoise = parseFloat(document.getElementById('op-denoise').value);
  const feather = parseInt(document.getElementById('op-feather').value);

  try {
    const { job_id } = await apiPost('/outpaint', {
      checkpoint: ckpt, arch,
      prompt, negative_prompt: neg,
      seed, steps, cfg, sampler, scheduler: sched,
      image_b64:  getCurrentB64(),
      pad_top: top, pad_bottom: bottom, pad_left: left, pad_right: right,
      denoise, feathering: feather,
    });
    await trackJob(job_id);
  } catch (_) { toast(t('painter.conn_error')); }
}

// ── Upscale ───────────────────────────────────────────────────────────────
async function doUpscale() {
  if (!S.hasImage) return toast(t('painter.no_image'));
  const model = document.getElementById('sel-upscaler').value;
  if (!model)  return toast(t('painter.no_checkpoint'));
  const arch  = document.getElementById('arch-select').value;
  try {
    const { job_id } = await apiPost('/upscale', {
      image_b64: getCurrentB64(), model_name: model, arch,
    });
    await trackJob(job_id);
  } catch (_) { toast(t('painter.conn_error')); }
}

// ── Job tracking via WebSocket ────────────────────────────────────────────
function trackJob(jobId) {
  return new Promise(resolve => {
    S.activeJobId = jobId;
    showProgress(true);

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws    = new WebSocket(`${proto}://${location.host}${API}/progress/${jobId}`);
    S.ws        = ws;

    ws.onmessage = async ({ data }) => {
      const ev = JSON.parse(data);
      if (ev.type === 'progress') {
        setProgress(ev.step, ev.total);
      } else if (ev.type === 'done') {
        ws.close();
        await onJobDone(jobId);
        resolve();
      } else if (ev.type === 'error') {
        ws.close();
        showProgress(false);
        toast(t('painter.job_error').replace('{msg}', ev.msg || ''));
        S.activeJobId = null;
        resolve();
      }
    };
    ws.onerror = () => {
      showProgress(false);
      toast(t('painter.conn_error'));
      resolve();
    };
  });
}

async function onJobDone(jobId) {
  showProgress(false);
  S.activeJobId = null;
  try {
    const r    = await fetch(`${API}/jobs/${jobId}/result`);
    const blob = await r.blob();
    const b64  = await blobToB64(blob);

    previewImg = await loadImageFromB64(b64);
    const pw = previewImg.naturalWidth, ph = previewImg.naturalHeight;
    if (S.imgW !== pw || S.imgH !== ph) resizeCanvases(pw, ph);
    S.hasPreview = true;
    S.showMask   = false;

    if (_regSeq.active) {
      _regSeq.lastB64 = b64;
      _showRegSeqAR();
    } else {
      showAcceptReject(true);
    }
    updateButtons();
  } catch (_) { toast(t('painter.conn_error')); }
}

function blobToB64(blob) {
  return new Promise(resolve => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result.split(',')[1]);
    fr.readAsDataURL(blob);
  });
}

function showProgress(visible) {
  document.getElementById('progress-overlay').classList.toggle('visible', visible);
}

function setProgress(step, total) {
  const pct = total > 0 ? Math.round(step / total * 100) : 0;
  document.getElementById('progress-bar-fill').style.width = pct + '%';
  document.getElementById('progress-label').textContent =
    t('painter.step_of').replace('{s}', step).replace('{t}', total);
}

// ── Accept / Reject ───────────────────────────────────────────────────────
async function doAccept() {
  if (_regSeq.active) { _acceptRegStep(); return; }
  try {
    const r = await apiPost('/session/accept', {});
    if (r.image_b64) {
      const img = await loadImageFromB64(r.image_b64);
      currentImg = img;
      ctxBg.clearRect(0, 0, S.imgW, S.imgH);
      ctxBg.drawImage(img, 0, 0);
    }
    previewImg   = null;
    S.hasPreview = false;
    S.hasImage   = true;
    S.session    = { has_current: r.has_current, history_size: r.history_size, redo_size: r.redo_size };
    clearMask();
    S.showMask   = true;
    showAcceptReject(false);
    updateButtons();
    updateUndoRedo();
    toast(t('painter.accept_ok'));
  } catch (_) { toast(t('painter.conn_error')); }
}

async function doReject() {
  if (_regSeq.active) { _regenRegStep(); return; }
  try {
    await apiPost('/session/reject', {});
    previewImg   = null;
    S.hasPreview = false;
    S.showMask   = true;
    showAcceptReject(false);
    updateButtons();
    toast(t('painter.reject_ok'));
  } catch (_) { toast(t('painter.conn_error')); }
}

// ── Undo / Redo ───────────────────────────────────────────────────────────
async function doUndo() {
  try {
    const r = await apiPost('/session/undo', {});
    if (r.image_b64) {
      const img = await loadImageFromB64(r.image_b64);
      currentImg = img;
      ctxBg.clearRect(0, 0, S.imgW, S.imgH);
      ctxBg.drawImage(img, 0, 0);
    }
    S.session = { has_current: r.has_current, history_size: r.history_size, redo_size: r.redo_size };
    clearMask();
    updateUndoRedo();
    toast(t('painter.undo_ok'));
  } catch (e) {
    if (e.message && e.message.includes('deshacer')) toast(t('painter.nothing_to_undo'));
    else toast(t('painter.conn_error'));
  }
}

async function doRedo() {
  try {
    const r = await apiPost('/session/redo', {});
    if (r.image_b64) {
      const img = await loadImageFromB64(r.image_b64);
      currentImg = img;
      ctxBg.clearRect(0, 0, S.imgW, S.imgH);
      ctxBg.drawImage(img, 0, 0);
    }
    S.session = { has_current: r.has_current, history_size: r.history_size, redo_size: r.redo_size };
    clearMask();
    updateUndoRedo();
    toast(t('painter.redo_ok'));
  } catch (e) {
    if (e.message && e.message.includes('rehacer')) toast(t('painter.nothing_to_redo'));
    else toast(t('painter.conn_error'));
  }
}

// ── Cancelar job ──────────────────────────────────────────────────────────
async function doCancelJob() {
  try {
    await apiPost('/interrupt', {});
    if (S.ws) S.ws.close();
    showProgress(false);
    S.activeJobId = null;
    updateButtons();
  } catch (_) { toast(t('painter.conn_error')); }
}

// ── UI helpers ────────────────────────────────────────────────────────────
function showAcceptReject(visible) {
  const el = document.getElementById('accept-reject');
  if (visible) {
    el.classList.add('visible');
    // Posición inicial: bottom-center del canvas, mismo lugar que la barra de progreso
    const wrap = document.getElementById('p-canvas-wrap');
    const rect = wrap.getBoundingClientRect();
    const elW  = el.offsetWidth || 210;
    el.style.left   = Math.round(rect.left + rect.width / 2 - elW / 2) + 'px';
    el.style.top    = Math.round(rect.bottom - 110) + 'px';
    el.style.right  = 'auto';
    el.style.bottom = 'auto';
  } else {
    el.classList.remove('visible');
  }
}

function initDraggable() {
  const el     = document.getElementById('accept-reject');
  const handle = document.getElementById('ar-handle');
  let dragging = false, ox = 0, oy = 0;

  handle.addEventListener('mousedown', e => {
    dragging = true;
    const r = el.getBoundingClientRect();
    ox = e.clientX - r.left;
    oy = e.clientY - r.top;
    // Fijar desde top/left, eliminar posibles right/bottom
    el.style.right  = 'auto';
    el.style.bottom = 'auto';
    e.preventDefault();
  });

  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    let nx = e.clientX - ox;
    let ny = e.clientY - oy;
    // Clamp dentro del viewport
    nx = Math.max(0, Math.min(window.innerWidth  - el.offsetWidth,  nx));
    ny = Math.max(0, Math.min(window.innerHeight - el.offsetHeight, ny));
    el.style.left = nx + 'px';
    el.style.top  = ny + 'px';
  });

  document.addEventListener('mouseup', () => { dragging = false; });
}

function updateButtons() {
  const busy    = !!S.activeJobId;
  const hasCkpt = !!document.getElementById('sel-checkpoint').value;
  const btnGen  = document.getElementById('btn-generate');
  btnGen.disabled  = busy || !hasCkpt;
  btnGen.textContent = S.hasMask
    ? t('painter.btn_inpaint')
    : t('painter.btn_generate');
  document.getElementById('btn-save-img').disabled  = !S.hasImage;
  document.getElementById('btn-outpaint').disabled  = busy || !S.hasImage;
  document.getElementById('btn-upscale').disabled   = busy || !S.hasImage;
  const hasAdEnabled = _adEnabled.size > 0;
  document.getElementById('btn-adetailer').disabled = busy || !S.hasImage || !hasCkpt || !hasAdEnabled;
  updateUndoRedo();
}

function updateUndoRedo() {
  document.getElementById('btn-undo').disabled = S.session.history_size === 0;
  document.getElementById('btn-redo').disabled = S.session.redo_size    === 0;
}

// ── Status bar ────────────────────────────────────────────────────────────
function updateStatusDims() {
  document.getElementById('st-dims').textContent =
    S.imgW && S.imgH ? `${S.imgW}×${S.imgH}` : '—';
  const pct = S.imgW ? Math.round(S.scale * 100) : null;
  document.getElementById('st-zoom').textContent = pct ? `${pct}%` : '—';
}

function updateStatusCursor(x, y) {
  document.getElementById('st-cursor').textContent =
    `${x}, ${y}`;
}

// ── Toast ─────────────────────────────────────────────────────────────────
let _toastTimer = null;
function toast(msg) {
  const el = document.getElementById('p-toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
}

// ── Regional Conditioning — lógica ────────────────────────────────────────

function initRegionalMasks() {
  regionalMasks = REGION_COLORS.map(() => {
    const canvas = document.createElement('canvas');
    return { canvas, ctx: canvas.getContext('2d'), hasPixels: false };
  });
}

function enterRegionalMode() {
  S.regional.active   = true;
  S.regional.activeIdx = 0;
  const w = Math.max(S.imgW, 1), h = Math.max(S.imgH, 1);
  regionalMasks.forEach(rm => {
    rm.canvas.width  = w;
    rm.canvas.height = h;
    rm.ctx.clearRect(0, 0, w, h);
    rm.hasPixels = false;
  });
  document.getElementById('reg-toolbar').style.display = 'flex';
  updateRegionHighlight();
  updateRegionCards();
}

function exitRegionalMode() {
  const hasMasks = regionalMasks.some(rm => rm.hasPixels);
  if (hasMasks && !confirm('¿Salir del modo Regional? Se perderán las máscaras pintadas.')) {
    return false;
  }
  S.regional.active = false;
  regionalMasks.forEach(rm => {
    rm.ctx.clearRect(0, 0, rm.canvas.width, rm.canvas.height);
    rm.hasPixels = false;
  });
  document.getElementById('reg-toolbar').style.display = 'none';
  return true;
}

function setActiveRegion(idx) {
  S.regional.activeIdx = idx;
  updateRegionHighlight();
  updateRegionCards();
}

function updateRegionHighlight() {
  document.querySelectorAll('.reg-btn').forEach((b, i) => {
    const color = REGION_COLORS[i];
    if (i === S.regional.activeIdx) {
      b.style.background  = color;
      b.style.color       = '#fff';
      b.style.borderColor = color;
    } else {
      b.style.background  = '';
      b.style.color       = color;
      b.style.borderColor = color;
    }
  });
}

function buildRegionCards() {
  const container = document.getElementById('reg-regions');
  container.innerHTML = '';
  REGION_COLORS.forEach((color, i) => {
    const card = document.createElement('div');
    card.className = 'reg-card';
    card.innerHTML = `
      <div class="reg-card-header" data-region="${i}">
        <span class="reg-swatch" style="background:${color}"></span>
        <span class="reg-card-label" style="color:${color}">R${i + 1}</span>
        <span class="reg-mask-status" id="reg-status-${i}">(sin máscara)</span>
        <button class="reg-clear-btn" data-clear-region="${i}" title="Limpiar R${i+1}">✕</button>
      </div>
      <div class="reg-card-body" id="reg-body-${i}" style="display:none">
        <textarea class="ctrl-textarea" id="reg-prompt-${i}"
          style="min-height:52px;margin-top:6px;border-left:2px solid ${color}"
          placeholder="Prompt para R${i + 1}…"></textarea>
      </div>
    `;
    container.appendChild(card);
  });

  // Event: expandir/colapsar al hacer click en el header
  container.querySelectorAll('.reg-card-header').forEach(h => {
    h.addEventListener('click', e => {
      if (e.target.classList.contains('reg-clear-btn')) return;
      setActiveRegion(parseInt(h.dataset.region));
    });
  });

  // Event: limpiar máscara de región
  container.querySelectorAll('.reg-clear-btn').forEach(b => {
    b.addEventListener('click', () => {
      clearRegionalMask(parseInt(b.dataset.clearRegion));
    });
  });
}

function updateRegionCards() {
  REGION_COLORS.forEach((_, i) => {
    const rm     = regionalMasks[i];
    const status = document.getElementById(`reg-status-${i}`);
    const body   = document.getElementById(`reg-body-${i}`);
    if (status) status.textContent = (rm && rm.hasPixels) ? '' : '(sin máscara)';
    if (body)   body.style.display = (i === S.regional.activeIdx) ? '' : 'none';
  });
}

function clearRegionalMask(idx) {
  const rm = regionalMasks[idx];
  if (!rm) return;
  rm.ctx.clearRect(0, 0, rm.canvas.width, rm.canvas.height);
  rm.hasPixels = false;
  updateRegionCards();
}

function checkRegionalMaskPixels(idx) {
  const rm = regionalMasks[idx];
  if (!rm || rm.canvas.width === 0) return false;
  const d = rm.ctx.getImageData(0, 0, rm.canvas.width, rm.canvas.height).data;
  return d.some((v, i) => i % 4 === 3 && v > 0);
}

function getRegionalMaskB64(idx) {
  return regionalMasks[idx].canvas.toDataURL('image/png').split(',')[1];
}

async function doRegional() {
  const checkpoint = document.getElementById('reg-checkpoint').value
                  || document.getElementById('sel-checkpoint').value;
  if (!checkpoint) return toast(t('painter.no_checkpoint'));

  const queue = [];
  for (let i = 0; i < 4; i++) {
    const rm = regionalMasks[i];
    if (!rm || !rm.hasPixels) continue;
    const prompt = withStyle((document.getElementById(`reg-prompt-${i}`) || {}).value || '');
    queue.push({ prompt, mask_b64: getRegionalMaskB64(i), regionIdx: i });
  }
  if (queue.length === 0) {
    return toast('Pinta al menos una región antes de generar.');
  }

  const baseSeed = parseInt(document.getElementById('reg-seed').value);

  _regSeq = {
    active:  true,
    queue,
    total:   queue.length,
    stepIdx: 0,
    baseB64: S.hasImage ? getCurrentB64() : null,
    lastB64: null,
    params: {
      checkpoint,
      negative_prompt: document.getElementById('reg-negative').value,
      steps:     parseInt(document.getElementById('reg-steps').value),
      cfg:       parseFloat(document.getElementById('reg-cfg').value),
      scheduler: document.getElementById('sel-scheduler').value,
      denoise:   parseFloat(document.getElementById('reg-denoise').value),
      width:     S.imgW || parseInt(document.getElementById('inp-width').value),
      height:    S.imgH || parseInt(document.getElementById('inp-height').value),
    },
    seed: baseSeed < 0 ? Math.floor(Math.random() * 2147483647) : baseSeed,
  };

  await _runRegStep();
}

// Envía el paso actual de la cola regional
async function _runRegStep() {
  const step = _regSeq.queue[_regSeq.stepIdx];
  const p    = _regSeq.params;
  const body = {
    checkpoint:      p.checkpoint,
    prompt:          step.prompt,
    negative_prompt: p.negative_prompt,
    image_b64:       _regSeq.baseB64,
    mask_b64:        step.mask_b64,
    width:           p.width,
    height:          p.height,
    seed:            _regSeq.seed,
    steps:           p.steps,
    cfg:             p.cfg,
    scheduler:       p.scheduler,
    denoise:         p.denoise,
  };
  try {
    const { job_id } = await apiPost('/regional_step', body);
    await trackJob(job_id);
  } catch (_) {
    _regSeq.active = false;
    toast(t('painter.conn_error'));
  }
}

// Actualiza el overlay AR con etiquetas propias del modo secuencial
function _showRegSeqAR() {
  const n     = _regSeq.stepIdx + 1;
  const total = _regSeq.total;
  const label = t('painter.reg_step_label')
    .replace('{n}', n).replace('{total}', total);
  document.getElementById('ar-label').textContent          = label;
  document.getElementById('ar-reg-progress').style.display = '';
  document.getElementById('ar-reg-progress').textContent   =
    `R${_regSeq.queue[_regSeq.stepIdx].regionIdx + 1} — paso ${n} de ${total}`;
  document.getElementById('btn-accept').textContent = t('painter.reg_btn_accept');
  document.getElementById('btn-reject').textContent = t('painter.reg_btn_reject');
  showAcceptReject(true);
}

// Resetea el overlay AR a sus etiquetas normales
function _resetAR() {
  document.getElementById('ar-label').textContent          = t('painter.preview_ready');
  document.getElementById('ar-reg-progress').style.display = 'none';
  document.getElementById('btn-accept').textContent        = t('painter.btn_accept');
  document.getElementById('btn-reject').textContent        = t('painter.btn_reject');
}

// El usuario acepta el resultado de la región actual
async function _acceptRegStep() {
  // El resultado ya está en previewImg/lastB64 — lo usamos como nueva base
  _regSeq.baseB64 = _regSeq.lastB64;
  _regSeq.lastB64 = null;

  const hasNext = _regSeq.stepIdx + 1 < _regSeq.total;

  if (hasNext) {
    // Avanzar a la siguiente región — ocultar AR, mostrar nuevo paso
    _regSeq.stepIdx++;
    _regSeq.seed++;  // nueva semilla por defecto para la siguiente región
    previewImg   = null;
    S.hasPreview = false;
    S.showMask   = true;
    showAcceptReject(false);
    _resetAR();
    updateButtons();
    await _runRegStep();
  } else {
    // Última región aceptada → commit a la sesión
    // El servidor ya tiene el preview del último job vía session.set_preview()
    _regSeq.active = false;
    _resetAR();
    try {
      const r   = await apiPost('/session/accept', {});
      const img = await loadImageFromB64(_regSeq.baseB64);
      currentImg = img;
      ctxBg.clearRect(0, 0, S.imgW, S.imgH);
      ctxBg.drawImage(img, 0, 0);
      previewImg   = null;
      S.hasPreview = false;
      S.hasImage   = true;
      S.session    = { has_current: r.has_current, history_size: r.history_size, redo_size: r.redo_size };
      clearMask();
      S.showMask = true;
      showAcceptReject(false);
      updateButtons();
      updateUndoRedo();
      toast(t('painter.reg_all_done'));
    } catch (_) { toast(t('painter.conn_error')); }
  }
}

// El usuario rechaza y quiere regenerar la región actual
async function _regenRegStep() {
  // Incrementar semilla para obtener resultado diferente
  _regSeq.seed++;
  _regSeq.lastB64 = null;
  previewImg   = null;
  S.hasPreview = false;
  S.showMask   = true;
  showAcceptReject(false);
  _resetAR();
  updateButtons();
  await _runRegStep();
}

// ── Clasificación de arquitectura ─────────────────────────────────────────
// _vaultArchMap: {stem_sin_ext → base_model} cargado desde /api/vault/arch-map
let _vaultArchMap = {};

// Convierte base_model del Vault a clave interna del Painter.
// Mapeo explícito primero; regex como fallback para variantes no listadas.
const _VAULT_ARCH_MAP_EXPLICIT = {
  // SDXL y variantes
  'sdxl 1.0': 'sdxl', 'sdxl hyper': 'sdxl', 'sdxl turbo': 'sdxl',
  'pony': 'sdxl', 'illustrious': 'sdxl', 'noobai': 'sdxl',
  'wai': 'sdxl', 'animagine': 'sdxl',
  // Flux
  'flux.1 d': 'flux', 'flux.1 s': 'flux', 'flux.1 kontext': 'flux',
  'flux.1 [dev]': 'flux', 'flux.1 [schnell]': 'flux',
  // SD 1.5
  'sd 1.5': 'sd15',
  // ZImageTurbo — arquitectura propia, NO es SDXL
  'zimageturbo': 'zimage', 'zimage turbo': 'zimage', 'zimage': 'zimage',
  // Wan / video
  'wan video': 'wan', 'wan 2.1': 'wan', 'wan': 'wan',
  // Otros que no son checkpoints de imagen fija
  'qwen': 'other', 'other': 'other',
};

function normalizeVaultArch(baseModel) {
  if (!baseModel) return 'unknown';
  const key = baseModel.toLowerCase().trim();
  if (_VAULT_ARCH_MAP_EXPLICIT[key]) return _VAULT_ARCH_MAP_EXPLICIT[key];
  // Fallback regex para variantes no listadas
  if (/flux/.test(key)) return 'flux';
  if (/sd\s*3|stable.?diffusion.?3/.test(key)) return 'sd3';
  if (/sd\s*1|stable.?diffusion.?1/.test(key)) return 'sd15';
  if (/zimage|zimageturbo/.test(key)) return 'zimage';
  if (/wan\s*video|wan\s*2/.test(key)) return 'wan';
  if (/sdxl|pony|illustrious|noob|wai|animagine|playground|kolors|hunyuan/.test(key)) return 'sdxl';
  if (/xl/.test(key)) return 'sdxl';
  return 'unknown';
}

// Determina la arch de un modelo por su nombre de archivo (stem sin extensión).
// Prioridad: mapa del Vault → heurísticas de nombre/path.
function modelArch(name) {
  const stem = name.replace(/\\/g, '/').split('/').pop().replace(/\.[^.]+$/, '');
  // Búsqueda en el mapa del Vault (case-insensitive)
  const vaultArch = _vaultArchMap[stem] || _vaultArchMap[stem.toLowerCase()];
  if (vaultArch) return normalizeVaultArch(vaultArch);

  // Fallback: heurísticas de nombre/path
  const n = name.toLowerCase().replace(/\\/g, '/');
  if (/\/flux[\/\-_.]|^flux[\/\-_\.]|flux/.test(n)) return 'flux';
  if (/\/sd3[\/\-_.]|^sd3[\/\-_\.]|sd3|stable.?diffusion.?3/.test(n)) return 'sd3';
  if (/\/sdxl[\/\-_.]|^sdxl[\/\-_\.]|sdxl/.test(n)) return 'sdxl';
  if (/pony|illustrious|noob|wai[_\-]?xl|animagine|juggernaut.?xl|dreamshaper.?xl|playground/.test(n)) return 'sdxl';
  if (/[_\-\.]xl[_\-\.\d]|[_\-\.]xl$/.test(n)) return 'sdxl';
  return 'unknown';
}

// Devuelve modelos que coinciden con arch + los no clasificados (siempre visibles).
function filterByArch(names, arch) {
  const matched = names.filter(n => modelArch(n) === arch);
  const unknown = names.filter(n => modelArch(n) === 'unknown');
  return [...matched, ...unknown];
}

// ── Cargar modelos ────────────────────────────────────────────────────────
async function loadModels() {
  try {
    const [m, archMap] = await Promise.all([
      apiGet('/models'),
      fetch('/api/vault/arch-map').then(r => r.ok ? r.json() : {}).catch(() => ({})),
    ]);
    _vaultArchMap = archMap;
    S.models   = m;
    populateModelSelects(m, S.arch);
    updateButtons();
    loadAdDetectors();
  } catch (_) {
    toast(t('painter.conn_error'));
  }
}


function populateModelSelects(m, arch) {
  // ── Checkpoints — filtrados por arquitectura ──────────────────────────
  const checkpoints = filterByArch(m.checkpoints, arch);
  const ckptHtml = '<option value="">— checkpoint —</option>' +
    checkpoints.map(c => `<option value="${c}">${c}</option>`).join('');

  const selCkpt = document.getElementById('sel-checkpoint');
  const prevCkpt = selCkpt.value;
  selCkpt.innerHTML = ckptHtml;
  if (checkpoints.includes(prevCkpt)) selCkpt.value = prevCkpt;

  // Sincronizar selector en tab Regional (mantiene selección previa si sigue disponible)
  const regCkpt = document.getElementById('reg-checkpoint');
  const prevRegCkpt = regCkpt.value;
  regCkpt.innerHTML = ckptHtml;
  if (checkpoints.includes(prevRegCkpt)) regCkpt.value = prevRegCkpt;
  else if (checkpoints.includes(prevCkpt)) regCkpt.value = prevCkpt;

  // ── Samplers / Schedulers — sin arquitectura ──────────────────────────
  const selSampler = document.getElementById('sel-sampler');
  selSampler.innerHTML = m.samplers.map(s =>
    `<option value="${s}"${s==='euler'?' selected':''}>${s}</option>`).join('');

  const selSched = document.getElementById('sel-scheduler');
  selSched.innerHTML = m.schedulers.map(s =>
    `<option value="${s}"${s==='normal'?' selected':''}>${s}</option>`).join('');

  // ── Upscalers — agnósticos de arquitectura ────────────────────────────
  const selUp = document.getElementById('sel-upscaler');
  selUp.innerHTML = '<option value="">— upscaler —</option>' +
    m.upscale_models.map(u => `<option value="${u}">${u}</option>`).join('');

  // ── LoRAs — cache para validación de tokens en el prompt ─────────────
  _allLoras = m.loras || [];

  // ── ControlNet — filtrados por arquitectura ───────────────────────────
  const controlnet = filterByArch(m.controlnet, arch);
  const selCN = document.getElementById('sel-cn-model');
  if (controlnet.length === 0) {
    document.getElementById('cn-disabled-msg').style.display = '';
    document.getElementById('cn-controls').style.display = 'none';
  } else {
    document.getElementById('cn-disabled-msg').style.display = 'none';
    document.getElementById('cn-controls').style.display = '';
    selCN.innerHTML = controlnet.map(c => `<option value="${c}">${c}</option>`).join('');
  }

  // ── ADetailer — disponibilidad de impact-pack ─────────────────────────
  _adImpactAvailable = !!m.adetailer_available;
  document.getElementById('ad-install-panel').style.display = _adImpactAvailable ? 'none' : 'flex';
  document.getElementById('ad-controls').style.display      = _adImpactAvailable ? ''     : 'none';
}

// ── Setup overlay ─────────────────────────────────────────────────────────
// ── Inicialización en background ──────────────────────────────────────────
// No bloquea el canvas. Verifica ComfyUI y, si faltan nodos, los instala
// silenciosamente mostrando progreso en el status bar.

function _showBanner(msg, actionLabel, actionFn) {
  const banner = document.getElementById('comfy-banner');
  document.getElementById('comfy-banner-msg').textContent = msg;
  const btn = document.getElementById('comfy-banner-action');
  if (actionLabel) {
    btn.textContent  = actionLabel;
    btn.style.display = '';
    btn.onclick = actionFn;
  } else {
    btn.style.display = 'none';
  }
  banner.classList.add('visible');
}

function _hideBanner() {
  document.getElementById('comfy-banner').classList.remove('visible');
}

function _setNodesInstalling(active) {
  document.getElementById('st-nodes-installing').style.display = active ? '' : 'none';
}

async function backgroundInit() {
  let status;
  try {
    status = await apiGet('/setup/status');
  } catch (_) {
    _showBanner('No se pudo conectar con el backend del Painter.', 'Recargar', () => location.reload());
    return;
  }

  if (status.comfyui_not_installed) {
    _showBanner('ComfyUI no está instalado. Painter lo requiere para funcionar.',
                '← Ir a Aplicaciones', () => { window.location = '/'; });
    return;
  }

  if (status.comfyui_offline) {
    _showBanner('ComfyUI no está corriendo. Inícialo desde el hub para usar Painter.',
                'Reintentar', () => { _hideBanner(); backgroundInit(); });
    return;
  }

  if (status.ready) {
    // Todo en orden — arrancar normalmente
    loadModels();
    showSizeDialog();
    requestAnimationFrame(render);
    return;
  }

  // Faltan nodos o modelos YOLO — instalar en background
  _setNodesInstalling(true);
  const es = new EventSource(API + '/setup/run');

  es.onmessage = ({ data }) => {
    const ev = JSON.parse(data);
    if (ev.step === 'stream_end') { es.close(); return; }

    if (ev.status === 'error') {
      es.close();
      _setNodesInstalling(false);
      _showBanner(`Error al preparar Painter: ${ev.msg}`, 'Reintentar',
                  () => { _hideBanner(); backgroundInit(); });
      return;
    }

    if (ev.step === 'restart_required') {
      es.close();
      _setNodesInstalling(false);
      _showBanner('Se instalaron nodos nuevos. Reinicia ComfyUI para activarlos.', null, null);
      loadModels();
      showSizeDialog();
      requestAnimationFrame(render);
      return;
    }

    if (ev.step === 'done') {
      es.close();
      _setNodesInstalling(false);
      if (ev.status === 'warning') {
        toast('Painter listo (algunos componentes opcionales no disponibles)');
      }
      loadModels();
      showSizeDialog();
      requestAnimationFrame(render);
    }
  };

  es.onerror = () => {
    es.close();
    _setNodesInstalling(false);
    _showBanner('Error de conexión durante la inicialización.', 'Reintentar',
                () => { _hideBanner(); backgroundInit(); });
  };
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────
function initKeyboard() {
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.ctrlKey && e.key === 'z') { e.preventDefault(); doUndo(); return; }
    if (e.ctrlKey && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); doRedo(); return; }
    if (e.key === 'Escape') { cancelSelection(); return; }
    switch (e.key.toLowerCase()) {
      case 'b': setTool('brush');  break;
      case 'r': setTool('rect');   break;
      case 'l': setTool('lasso');  break;
      case 'w': setTool('wand');   break;
      case 'f': setTool('fill');   break;
      case 'e': setTool('eraser'); break;
      case 'x': toggleBrushEraser(); break;
      case '[': changeBrushSize(-5); break;
      case ']': changeBrushSize(+5); break;
    }
  });
}

// ── Selección temporal (Magic Wand) ───────────────────────────────────────
let _wandSel = null;   // Uint8Array — píxeles seleccionados (null = sin selección)

/** BFS flood fill sobre la imagen; devuelve Uint8Array de píxeles alcanzados. */
function _floodFillImg(ix, iy, tol) {
  const w = S.imgW, h = S.imgH;
  const srcData = ctxBg.getImageData(0, 0, w, h).data;
  const si = (iy * w + ix) * 4;
  const seedR = srcData[si], seedG = srcData[si+1], seedB = srcData[si+2];

  const visited = new Uint8Array(w * h);
  const result  = new Uint8Array(w * h);
  const queue   = [ix + iy * w];
  visited[ix + iy * w] = 1;

  while (queue.length) {
    const idx = queue.pop();
    const px = idx % w, py = (idx / w) | 0;
    const pi = idx * 4;
    const dr = srcData[pi]   - seedR;
    const dg = srcData[pi+1] - seedG;
    const db = srcData[pi+2] - seedB;
    if (Math.sqrt(dr*dr + dg*dg + db*db) > tol) continue;
    result[idx] = 1;
    const nb = [px-1+py*w, px+1+py*w, px+(py-1)*w, px+(py+1)*w];
    for (const n of nb) {
      const nx = n % w, ny = (n / w) | 0;
      if (nx < 0 || nx >= w || ny < 0 || ny >= h || visited[n]) continue;
      visited[n] = 1;
      queue.push(n);
    }
  }
  return result;
}

/** Dibuja la selección activa como overlay cyan en canvasFg. */
function _renderWandSelection() {
  if (!_wandSel) return;
  const w = S.imgW, h = S.imgH;
  const overlay = ctxFg.createImageData(w, h);
  const od = overlay.data;
  for (let i = 0; i < _wandSel.length; i++) {
    if (!_wandSel[i]) continue;
    const mi = i * 4;
    od[mi]   = 84; od[mi+1] = 239; od[mi+2] = 234; od[mi+3] = 100;
  }
  ctxFg.putImageData(overlay, 0, 0);
}

/** Magic Wand: actualiza _wandSel sin tocar la máscara. */
function wandSelect(imgX, imgY, add, subtract) {
  if (!S.hasImage) return;
  const w = S.imgW, h = S.imgH;
  const ix = Math.round(imgX), iy = Math.round(imgY);
  if (ix < 0 || iy < 0 || ix >= w || iy >= h) return;

  const tol    = parseInt(document.getElementById('wand-tolerance').value);
  const pixels = _floodFillImg(ix, iy, tol);

  if (subtract && _wandSel) {
    for (let i = 0; i < pixels.length; i++) if (pixels[i]) _wandSel[i] = 0;
  } else if (add && _wandSel) {
    for (let i = 0; i < pixels.length; i++) if (pixels[i]) _wandSel[i] = 1;
  } else {
    _wandSel = pixels;
  }
  render();
  document.getElementById('btn-wand-apply').style.display = _wandSel ? '' : 'none';
}

/** Aplica la selección wand a la máscara activa. */
function applyWandSelection() {
  if (!_wandSel) return;
  const w = S.imgW, h = S.imgH;
  const ctx  = getActiveMaskCtx();
  const data = ctx.getImageData(0, 0, w, h);
  const md   = data.data;
  for (let i = 0; i < _wandSel.length; i++) {
    if (!_wandSel[i]) continue;
    const mi = i * 4;
    md[mi] = md[mi+1] = md[mi+2] = 255; md[mi+3] = 255;
  }
  ctx.putImageData(data, 0, 0);
  _wandSel = null;
  document.getElementById('btn-wand-apply').style.display = 'none';
  updateMaskState();
}

/** Cancela la selección wand sin aplicar. */
function clearWandSelection() {
  _wandSel = null;
  document.getElementById('btn-wand-apply').style.display = 'none';
  render();
}

// ── Fill / Bucket — flood fill directo a la máscara ──────────────────────
function bucketFill(imgX, imgY, subtract) {
  if (!S.hasImage) return;
  const w = S.imgW, h = S.imgH;
  const ix = Math.round(imgX), iy = Math.round(imgY);
  if (ix < 0 || iy < 0 || ix >= w || iy >= h) return;

  const tol    = parseInt(document.getElementById('wand-tolerance').value);
  const pixels = _floodFillImg(ix, iy, tol);
  const ctx    = getActiveMaskCtx();
  const data   = ctx.getImageData(0, 0, w, h);
  const md     = data.data;
  for (let i = 0; i < pixels.length; i++) {
    if (!pixels[i]) continue;
    const mi = i * 4;
    if (subtract) {
      md[mi] = md[mi+1] = md[mi+2] = 0; md[mi+3] = 0;
    } else {
      md[mi] = md[mi+1] = md[mi+2] = 255; md[mi+3] = 255;
    }
  }
  ctx.putImageData(data, 0, 0);
  updateMaskState();
}

function setTool(name) {
  if (S.tool === 'wand' && name !== 'wand') clearWandSelection();
  // Cancelar lasso/rect pendiente al cambiar de herramienta
  if ((S.tool === 'lasso' || S.tool === 'rect') && name !== S.tool) {
    lassoPoints = []; rectStart = null; rectEnd = null;
  }
  S.tool = name;
  document.querySelectorAll('.tool-btn[data-tool]').forEach(b =>
    b.classList.toggle('active', b.dataset.tool === name));
  document.getElementById('st-tool').textContent = name;
  const showTol  = name === 'wand' || name === 'fill';
  const showSize = name !== 'wand' && name !== 'fill' && name !== 'rect' && name !== 'lasso';
  document.getElementById('brush-size-wrap').style.display = showSize ? '' : 'none';
  document.getElementById('wand-tol-wrap').style.display   = showTol  ? '' : 'none';
}

function toggleBrushEraser() {
  setTool(S.tool === 'eraser' ? 'brush' : 'eraser');
}

function changeBrushSize(delta) {
  S.brushSize = Math.max(4, Math.min(200, S.brushSize + delta));
  document.getElementById('brush-size').value = S.brushSize;
  document.getElementById('brush-size-label').textContent = S.brushSize;
}

// ── Event listeners ───────────────────────────────────────────────────────
function initEvents() {
  // Chips de LoRA — actualizar al escribir en el prompt
  document.getElementById('inp-prompt').addEventListener('input', renderLoraChips);

  // Herramientas
  document.querySelectorAll('.tool-btn[data-tool]').forEach(b =>
    b.addEventListener('click', () => setTool(b.dataset.tool)));

  // Brush size slider
  document.getElementById('brush-size').addEventListener('input', function () {
    S.brushSize = parseInt(this.value);
    document.getElementById('brush-size-label').textContent = S.brushSize;
  });
  document.getElementById('wand-tolerance').addEventListener('input', function () {
    document.getElementById('wand-tol-label').textContent = this.value;
  });

  // Cargar imagen (header + panel)
  const openFilePicker = () => document.getElementById('file-input').click();
  document.getElementById('btn-load-img').addEventListener('click', openFilePicker);
  document.getElementById('btn-load-img-panel').addEventListener('click', openFilePicker);
  document.getElementById('btn-save-img').addEventListener('click', doSaveImage);
  document.getElementById('btn-wand-apply').addEventListener('click', applyWandSelection);

  document.getElementById('file-input').addEventListener('change', function () {
    if (this.files[0]) loadImageFile(this.files[0]);
  });

  // Limpiar máscara
  document.getElementById('btn-clear-mask').addEventListener('click', clearMask);

  // Generar
  document.getElementById('btn-generate').addEventListener('click', () => {
    S.hasMask ? doInpaint() : doGenerate();
  });

  // Upscale
  document.getElementById('btn-outpaint').addEventListener('click', doOutpaint);
  ['op-top','op-bottom','op-left','op-right'].forEach(id =>
    document.getElementById(id).addEventListener('input', updateOpPreview)
  );
  document.getElementById('op-denoise').addEventListener('input', function () {
    document.getElementById('op-denoise-val').textContent = (+this.value).toFixed(2);
  });
  document.getElementById('op-feather').addEventListener('input', function () {
    document.getElementById('op-feather-val').textContent = this.value;
  });
  document.getElementById('btn-upscale').addEventListener('click', doUpscale);

  // Accept / Reject
  document.getElementById('btn-accept').addEventListener('click', doAccept);
  document.getElementById('btn-reject').addEventListener('click', doReject);

  // Undo / Redo
  document.getElementById('btn-undo').addEventListener('click', doUndo);
  document.getElementById('btn-redo').addEventListener('click', doRedo);

  // Cancelar job
  document.getElementById('btn-cancel-job').addEventListener('click', doCancelJob);

  // Randomize seed
  document.getElementById('btn-randomize').addEventListener('click', () => {
    document.getElementById('inp-seed').value = Math.floor(Math.random() * 2 ** 32);
  });

  // Denoise slider
  document.getElementById('inp-denoise').addEventListener('input', function () {
    document.getElementById('denoise-val').textContent = parseFloat(this.value).toFixed(2);
  });

  // Feather slider
  document.getElementById('inp-feather').addEventListener('input', function () {
    document.getElementById('feather-val').textContent = this.value;
  });

  // CN strength slider
  document.getElementById('inp-cn-strength').addEventListener('input', function () {
    document.getElementById('cn-strength-val').textContent = parseFloat(this.value).toFixed(2);
  });

  // Selector de modo del panel
  document.getElementById('panel-mode-select').addEventListener('change', function () {
    const targetTab = this.value;
    if (S.regional.active && targetTab !== 'regional') {
      if (!exitRegionalMode()) { this.value = 'regional'; return; }
    }
    if (targetTab === 'regional') enterRegionalMode();
    document.querySelectorAll('.panel-body').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + targetTab).classList.add('active');
  });

  // Diálogo de tamaño de canvas
  document.getElementById('csm-confirm').addEventListener('click', confirmSizeDialog);
  ['csm-w', 'csm-h'].forEach(id => {
    document.getElementById(id).addEventListener('keydown', e => {
      if (e.key === 'Enter') confirmSizeDialog();
    });
  });

  // Regional: botones R1-R4 en toolbar
  document.querySelectorAll('.reg-btn').forEach(b => {
    b.addEventListener('click', () => setActiveRegion(parseInt(b.dataset.region)));
  });

  // Regional: slider denoise
  document.getElementById('reg-denoise').addEventListener('input', function () {
    document.getElementById('reg-denoise-val').textContent = parseFloat(this.value).toFixed(2);
  });

  // Regional: randomize seed
  document.getElementById('reg-btn-randomize').addEventListener('click', () => {
    document.getElementById('reg-seed').value = Math.floor(Math.random() * 2 ** 32);
  });

  // Regional: botón generar
  document.getElementById('btn-regional').addEventListener('click', doRegional);

  // Arch selector
  document.getElementById('arch-select').addEventListener('change', function () {
    S.arch = this.value;
    loadModels();
  });

  // ADetailer
  document.getElementById('btn-adetailer').addEventListener('click', doADetailer);
  document.getElementById('btn-ad-install').addEventListener('click', _adInstallImpactPack);

  document.getElementById('ad-denoise').addEventListener('input', function () {
    document.getElementById('ad-denoise-val').textContent = parseFloat(this.value).toFixed(2);
  });
  document.getElementById('ad-threshold').addEventListener('input', function () {
    document.getElementById('ad-threshold-val').textContent = parseFloat(this.value).toFixed(2);
  });

  // Collapsibles ADetailer
  document.getElementById('ad-det-toggle').addEventListener('click', function () {
    const open = this.getAttribute('aria-expanded') === 'true';
    this.setAttribute('aria-expanded', String(!open));
    document.getElementById('ad-det-body').style.display = open ? 'none' : '';
  });
  document.getElementById('ad-prompt-toggle').addEventListener('click', function () {
    const open = this.getAttribute('aria-expanded') === 'true';
    this.setAttribute('aria-expanded', String(!open));
    document.getElementById('ad-prompt-body').style.display = open ? 'none' : '';
  });
}

// ── ADetailer — lógica ───────────────────────────────────────────────────

async function loadAdDetectors() {
  try {
    const d = await apiGet('/adetailer/detectors');
    _adDetectors = d.detectors || [];
    _renderAdDetectors();
  } catch (_) {}
}

function _renderAdDetectors() {
  const list = document.getElementById('ad-det-list');
  if (!list) return;
  list.innerHTML = '';
  _adDetectors.forEach(det => {
    const row = document.createElement('div');
    row.className = 'ad-det-row';

    const label = document.createElement('span');
    label.className = 'ad-det-label';
    label.textContent = det.label;

    const file = document.createElement('span');
    file.className = 'ad-det-file';
    file.textContent = det.filename;

    if (det.available) {
      // Switch toggle
      const sw = document.createElement('label');
      sw.className = 'ad-switch';
      const inp = document.createElement('input');
      inp.type    = 'checkbox';
      inp.checked = _adEnabled.has(det.id);
      inp.addEventListener('change', () => {
        if (inp.checked) _adEnabled.add(det.id);
        else             _adEnabled.delete(det.id);
        updateButtons();
      });
      const track = document.createElement('span');
      track.className = 'ad-switch-track';
      const thumb = document.createElement('span');
      thumb.className = 'ad-switch-thumb';
      track.appendChild(thumb);
      sw.appendChild(inp);
      sw.appendChild(track);
      row.append(label, file, sw);
    } else {
      // Botón de descarga
      const btn = document.createElement('button');
      btn.className   = 'ad-dl-btn';
      btn.textContent = '⬇ Descargar';
      btn.id          = `ad-dl-${det.id}`;
      btn.addEventListener('click', () => _adDownload(det.id, btn));
      row.append(label, file, btn);
    }
    list.appendChild(row);
  });
}

function _adDownload(detId, btn) {
  btn.disabled    = true;
  btn.textContent = '…';
  const progEl = document.createElement('span');
  progEl.className = 'ad-dl-progress';
  btn.parentElement.appendChild(progEl);

  const es = new EventSource(`${API}/adetailer/download/${detId}`);
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.status === 'progress') {
      progEl.textContent = `${d.pct}%`;
    } else if (d.status === 'done' || d.status === 'ok') {
      es.close();
      toast(`Detector descargado: ${detId}`);
      loadAdDetectors();
    } else if (d.status === 'error') {
      es.close();
      btn.disabled    = false;
      btn.textContent = '⬇ Reintentar';
      progEl.textContent = '';
      toast(`Error: ${d.msg}`);
    }
  };
  es.onerror = () => {
    es.close();
    btn.disabled    = false;
    btn.textContent = '⬇ Reintentar';
  };
}

async function doADetailer() {
  if (!S.hasImage) return toast('No hay imagen en el canvas');
  if (!_adImpactAvailable) return; // el tab ya muestra el panel de instalación
  const enabled = [..._adEnabled];
  if (!enabled.length) return toast('Habilita al menos un detector');
  const ckpt = document.getElementById('sel-checkpoint').value;
  if (!ckpt) return toast('Selecciona un checkpoint');

  try {
    const basePrompt   = withStyle(document.getElementById('inp-prompt').value);
    const overrideText = document.getElementById('ad-prompt-override').value.trim();
    const body = {
      image_b64:       getCurrentB64(),
      checkpoint:      ckpt,
      prompt:          overrideText || basePrompt,
      negative_prompt: document.getElementById('inp-negative').value,
      seed:            parseInt(document.getElementById('inp-seed').value) || -1,
      steps:           parseInt(document.getElementById('inp-steps').value),
      cfg:             parseFloat(document.getElementById('inp-cfg').value),
      sampler:         document.getElementById('sel-sampler').value,
      scheduler:       document.getElementById('sel-scheduler').value,
      arch:            S.arch,
      detectors:       enabled,
      denoise:         parseFloat(document.getElementById('ad-denoise').value),
      guide_size:      parseInt(document.getElementById('ad-guide-size').value),
      bbox_threshold:  parseFloat(document.getElementById('ad-threshold').value),
      bbox_dilation:   parseInt(document.getElementById('ad-dilation').value),
    };
    const { job_id } = await apiPost('/adetailer', body);
    await trackJob(job_id);
  } catch (e) { toast(e.message || t('painter.conn_error')); }
}

function _adInstallImpactPack() {
  const btn  = document.getElementById('btn-ad-install');
  const log  = document.getElementById('ad-install-log');
  const note = document.getElementById('ad-install-note');
  btn.disabled   = true;
  btn.textContent = 'Instalando…';
  log.style.display = '';
  log.textContent   = '';

  const addLog = (msg, cls) => {
    const span = document.createElement('span');
    span.style.color = cls === 'ok'      ? 'var(--accent-cyan)'
                     : cls === 'warning' ? 'var(--accent-amber)'
                     : cls === 'error'   ? 'var(--accent-red)' : '';
    span.textContent = msg + '\n';
    log.appendChild(span);
    log.scrollTop = log.scrollHeight;
  };

  const es = new EventSource(API + '/setup/run');
  es.onmessage = ({ data }) => {
    const ev = JSON.parse(data);
    if (ev.step === 'stream_end') { es.close(); return; }
    addLog(ev.msg, ev.status);

    if (ev.status === 'error') {
      es.close();
      btn.disabled    = false;
      btn.textContent = 'Reintentar';
      return;
    }
    if (ev.step === 'restart_required') {
      es.close();
      note.textContent = '✓ Instalado. Reinicia ComfyUI para activarlo.';
      btn.disabled    = false;
      btn.textContent = 'Instalar impact-pack';
      return;
    }
    if (ev.step === 'done') {
      es.close();
      loadModels();  // refrescar — ahora adetailer_available debería ser true
      btn.disabled    = false;
      btn.textContent = 'Instalar impact-pack';
    }
  };
  es.onerror = () => {
    es.close();
    addLog('Error de conexión.', 'error');
    btn.disabled    = false;
    btn.textContent = 'Reintentar';
  };
}

// ── Estilos — lógica ─────────────────────────────────────────────────────

async function loadStyles() {
  try {
    const r = await fetch(`${API}/styles`);
    const d = await r.json();
    _styles = d.styles || [];
    renderStyleChips();
  } catch (_) {}
}

function renderStyleChips() {
  const list = document.getElementById('styles-list');
  if (!list) return;
  list.innerHTML = '';
  _styles.forEach(s => {
    const chip = document.createElement('div');
    chip.className = 'style-chip' + (s.name === _activeStyleName ? ' active' : '');
    chip.innerHTML =
      `<span class="style-chip-label">${s.name}</span>` +
      `<button class="style-chip-del" data-del="${s.name}" title="Eliminar">×</button>`;
    chip.querySelector('.style-chip-label').addEventListener('click', () => applyStyle(s));
    chip.querySelector('.style-chip-del').addEventListener('click', e => {
      e.stopPropagation();
      deleteStyle(s.name);
    });
    list.appendChild(chip);
  });
}

function applyStyle(s) {
  _activeStyleName   = s.name;
  _activeStylePrompt = s.prompt;
  document.getElementById('styles-prompt').value    = s.prompt;
  document.getElementById('styles-active-name').textContent = s.name;
  document.getElementById('styles-active-name').classList.add('has-style');
  document.getElementById('styles-clear-btn').style.display = '';
  renderStyleChips();
  toast(t('painter.styles_applied').replace('{name}', s.name));
}

function clearStyle() {
  _activeStyleName   = '';
  _activeStylePrompt = '';
  document.getElementById('styles-prompt').value    = '';
  document.getElementById('styles-active-name').textContent = t('painter.styles_none');
  document.getElementById('styles-active-name').classList.remove('has-style');
  document.getElementById('styles-clear-btn').style.display = 'none';
  renderStyleChips();
  toast(t('painter.styles_cleared'));
}

async function saveStyle() {
  const name   = document.getElementById('styles-name-input').value.trim();
  const prompt = document.getElementById('styles-prompt').value.trim();
  if (!name) return toast(t('painter.styles_name_ph'));
  try {
    await fetch(`${API}/styles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, prompt }),
    });
    await loadStyles();
    // Activar el estilo recién guardado
    applyStyle({ name, prompt });
    document.getElementById('styles-name-input').value = '';
    toast(t('painter.styles_saved'));
  } catch (_) { toast(t('painter.conn_error')); }
}

async function deleteStyle(name) {
  try {
    await fetch(`${API}/styles/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (_activeStyleName === name) clearStyle();
    await loadStyles();
    toast(t('painter.styles_deleted'));
  } catch (_) { toast(t('painter.conn_error')); }
}

function toggleStylesPanel() {
  const panel  = document.getElementById('styles-panel');
  const arrow  = document.getElementById('styles-toggle-arrow');
  const open   = panel.style.display === 'none' || panel.style.display === '';
  panel.style.display = open ? 'flex' : 'none';
  arrow.classList.toggle('open', open);
}

function initStyles() {
  document.getElementById('styles-header').addEventListener('click', toggleStylesPanel);
  document.getElementById('styles-clear-btn').addEventListener('click', e => {
    e.stopPropagation();
    clearStyle();
  });
  document.getElementById('styles-save-btn').addEventListener('click', saveStyle);
  // Actualizar _activeStylePrompt en tiempo real si el usuario edita sin guardar
  document.getElementById('styles-prompt').addEventListener('input', function () {
    if (_activeStyleName) _activeStylePrompt = this.value;
  });
  loadStyles();
}

// ── Autocomplete de tags Danbooru ─────────────────────────────────────────

const _acDrop = (() => {
  const el = document.createElement('div');
  el.id = 'tag-ac-drop';
  el.style.display = 'none';
  document.body.appendChild(el);
  return el;
})();

let _acResults    = [];
let _acSelIdx     = 0;
let _acActiveTA   = null;
let _acTimer      = null;
let _acTagsLoaded = false;

const _AC_DOT_COLORS = {
    0: '#4a9eff', 1: '#ff4d4d', 3: '#b04dff', 4: '#44dd77', 5: '#ff9900',  // danbooru
    7: '#5bb8ff', 8: '#ff6666', 9: '#ffd700', 10: '#cc77ff', 11: '#66ee99', 12: '#ff7755', 14: '#ffbb44', 15: '#44bb88',  // e621
};

function _acFmtCount(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1e3) return Math.round(n / 1e3) + 'k';
  return String(n);
}

function _acShow(results, ta) {
  _acResults = results;
  _acSelIdx  = 0;
  if (!results.length) { _acHide(); return; }

  const rect = ta.getBoundingClientRect();
  _acDrop.style.left    = rect.left + 'px';
  _acDrop.style.top     = (rect.bottom + 2) + 'px';
  _acDrop.style.width   = Math.max(rect.width, 220) + 'px';
  _acDrop.style.display = '';

  _acDrop.innerHTML = results.map((r, i) => {
    const dot      = `<span class="ac-dot" style="background:${_AC_DOT_COLORS[r.category] || '#888'}"></span>`;
    const name     = r.name.replace(/_/g, ' ');
    const alias    = r.matched_alias
      ? `<div class="ac-alias">→ ${r.matched_alias.replace(/_/g, ' ')}</div>` : '';
    return `<div class="ac-item${i === 0 ? ' selected' : ''}" data-idx="${i}">
      <div class="ac-main">${dot}<span class="ac-name">${name}</span>
        <span class="ac-count">${_acFmtCount(r.post_count)}</span>
      </div>${alias}
    </div>`;
  }).join('');

  _acDrop.querySelectorAll('.ac-item').forEach(el => {
    el.addEventListener('mousedown', e => {
      e.preventDefault();
      _acInsert(_acResults[+el.dataset.idx], _acActiveTA);
    });
    el.addEventListener('mouseover', () => {
      _acSelIdx = +el.dataset.idx;
      _acUpdateSel();
    });
  });
}

function _acHide() {
  _acDrop.style.display = 'none';
  _acResults = [];
}

function _acUpdateSel() {
  _acDrop.querySelectorAll('.ac-item').forEach((el, i) =>
    el.classList.toggle('selected', i === _acSelIdx));
}

function _acInsert(result, ta) {
  if (!result || !ta) return;
  const pos    = ta.selectionStart;
  const before = ta.value.slice(0, pos);
  const after  = ta.value.slice(pos);

  // Inicio del token parcial (después del último separador: coma, newline, <, >, (, ))
  let sepIdx = -1;
  for (let i = before.length - 1; i >= 0; i--) {
    if (',\n<>()'.includes(before[i])) { sepIdx = i; break; }
  }
  const prefix     = before.slice(0, sepIdx + 1);
  const lead       = prefix.endsWith(',') ? ' ' : '';
  // Escapar paréntesis literales del nombre del tag
  const escaped    = result.name.replace(/([()])/g, '\\$1');
  // Auto-coma al final salvo que el siguiente char ya sea coma
  const suffix     = after.trimStart().startsWith(',') ? '' : ', ';

  ta.value = prefix + lead + escaped + suffix + after;
  ta.selectionStart = ta.selectionEnd = (prefix + lead + escaped + suffix).length;

  _balanceParens(ta);
  _acHide();
  ta.dispatchEvent(new Event('input'));
  ta.focus();
}

function _balanceParens(ta) {
  const pos        = ta.selectionStart;
  const text       = ta.value;
  const chunkStart = Math.max(text.lastIndexOf(',', pos - 1), -1) + 1;
  const nextComma  = text.indexOf(',', pos);
  const chunkEnd   = nextComma === -1 ? text.length : nextComma;
  const chunk      = text.slice(chunkStart, chunkEnd);

  let open = 0;
  for (let i = 0; i < chunk.length; i++) {
    if (chunk[i] === '\\') { i++; continue; }
    if (chunk[i] === '(')  open++;
    else if (chunk[i] === ')') open = Math.max(0, open - 1);
  }
  if (open > 0) {
    ta.value = text.slice(0, chunkEnd) + ')'.repeat(open) + text.slice(chunkEnd);
    ta.selectionStart = ta.selectionEnd = pos;
  }
}

function _acGetToken(ta) {
  const before = ta.value.slice(0, ta.selectionStart);
  let sep = -1;
  for (let i = before.length - 1; i >= 0; i--) {
    if (',\n<>()'.includes(before[i])) { sep = i; break; }
  }
  return before.slice(sep + 1).trim();
}

// csv_name activo (corresponde al perfil seleccionado)
let _acCsvName = '';

async function _acSearch(token) {
  if (!token || !_acCsvName) { _acHide(); return; }
  // No se bloquea por _acTagsLoaded — si el CSV aún no cargó, el backend devuelve []
  try {
    const { results } = await apiGet(
      `/tags/search?q=${encodeURIComponent(token)}&csv=${encodeURIComponent(_acCsvName)}&limit=8`
    );
    if (_acActiveTA && document.activeElement === _acActiveTA) {
      _acShow(results, _acActiveTA);
    }
  } catch (_) {}
}

function initAutocomplete(ta) {
  ta.addEventListener('input', () => {
    clearTimeout(_acTimer);
    const token = _acGetToken(ta);
    if (!token) { _acHide(); return; }
    _acActiveTA = ta;
    _acTimer = setTimeout(() => _acSearch(token), 50);
  });

  ta.addEventListener('keydown', e => {
    if (_acDrop.style.display === 'none') return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _acSelIdx = Math.min(_acSelIdx + 1, _acResults.length - 1);
      _acUpdateSel();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _acSelIdx = Math.max(_acSelIdx - 1, 0);
      _acUpdateSel();
    } else if (e.key === 'Tab' || e.key === 'Enter') {
      if (_acResults.length) { e.preventDefault(); _acInsert(_acResults[_acSelIdx], ta); }
    } else if (e.key === 'Escape') {
      e.preventDefault(); _acHide();
    } else if (e.key === ',') {
      setTimeout(() => _balanceParens(ta), 0);
    }
  });

  ta.addEventListener('blur',  () => setTimeout(_acHide, 150));
  ta.addEventListener('focus', () => { _acActiveTA = ta; });
}

// ── Model profiles ────────────────────────────────────────────────────────

let _profiles      = [];
let _activeProfile = null;

async function loadProfiles() {
  try {
    const data = await apiGet('/tags/profiles');
    _profiles = data.profiles || [];
    const sel = document.getElementById('sel-profile');
    if (!sel || !_profiles.length) return;
    sel.innerHTML = _profiles.map(p =>
      `<option value="${p.id}">${p.name}</option>`
    ).join('');
    if (data.default) sel.value = data.default;
    // Solo aplica UI inicial — no verifica CSV hasta que el usuario seleccione checkpoint
    _applyProfileUi();
  } catch (_) {}
}

// Aplica defaults + badge + rating al perfil activo. Sin check de CSV.
function _applyProfileUi() {
  const sel = document.getElementById('sel-profile');
  if (!sel) return;
  _activeProfile = _profiles.find(p => p.id === sel.value) || null;
  // Setear CSV activo para que el autocomplete pueda disparar desde el inicio
  _acCsvName = _activeProfile?.tag_csv || '';

  const selRating = document.getElementById('sel-rating');
  if (selRating) {
    const ratings = _activeProfile?.rating_tags || {};
    selRating.innerHTML = '<option value="">Rating…</option>' +
      Object.keys(ratings).map(k => `<option value="${k}">${k}</option>`).join('');
  }

  const d = _activeProfile?.defaults;
  if (d) {
    const elCfg   = document.getElementById('inp-cfg');
    const elSteps = document.getElementById('inp-steps');
    const elSamp  = document.getElementById('sel-sampler');
    const elSched = document.getElementById('sel-scheduler');
    if (elCfg   && d.cfg   != null) elCfg.value   = d.cfg;
    if (elSteps && d.steps != null) elSteps.value  = d.steps;
    if (elSamp  && d.sampler) {
      const opt = [...elSamp.options].find(o => o.value === d.sampler);
      if (opt) elSamp.value = d.sampler;
    }
    if (elSched && d.scheduler) {
      const opt = [...elSched.options].find(o => o.value === d.scheduler);
      if (opt) elSched.value = d.scheduler;
    }
  }

  _updateProfileBadge(_activeProfile);
}

// Aplica UI + verifica CSV. Llamado solo por acción del usuario.
async function _onProfileChange() {
  _applyProfileUi();
  await _updateActiveCsv();
}

// Intenta detectar el perfil correcto a partir del nombre del checkpoint.
function _detectProfileFromCheckpoint(name) {
  if (!name) return null;
  const lower = name.toLowerCase();
  for (const p of _profiles) {
    const kws = p.checkpoint_keywords || [];
    if (kws.some(kw => lower.includes(kw.toLowerCase()))) return p;
  }
  return null;
}

// Disparado al cambiar el checkpoint — auto-detecta perfil y verifica CSV.
async function _onCheckpointChange() {
  const ckpt  = document.getElementById('sel-checkpoint')?.value || '';
  const found = _detectProfileFromCheckpoint(ckpt);
  if (found) {
    const sel = document.getElementById('sel-profile');
    if (sel && sel.value !== found.id) sel.value = found.id;
  }
  updateButtons();
  await _onProfileChange();
}

function _updateProfileBadge(profile) {
  const badge   = document.getElementById('profile-badge');
  const tooltip = document.getElementById('profile-tooltip');
  if (!badge || !tooltip) return;

  // Determinar tipo de arquitectura
  const note = (profile?._note || '').toLowerCase();
  const id   = (profile?.id   || '').toLowerCase();
  let type, label, badgeClass;

  if (!profile || profile.prompt_style === 'natural' && !id.includes('flux')) {
    type = 'natural'; label = 'NAT'; badgeClass = 'badge-natural';
  } else if (id.includes('flux') || note.includes('flow matching')) {
    type = 'flow'; label = 'FLOW'; badgeClass = 'badge-flow';
  } else if (id.includes('vpred') || note.includes('v-pred') || note.includes('v_pred')) {
    type = 'vpred'; label = 'V-Pred'; badgeClass = 'badge-vpred';
  } else {
    type = 'epsilon'; label = 'ε-Pred'; badgeClass = 'badge-epsilon';
  }

  badge.textContent = label;
  badge.className   = badgeClass;
  badge.title       = type === 'vpred'   ? 'V-prediction: CFG bajo, sin Karras' :
                      type === 'flow'    ? 'Flow Matching: CFG ~1, euler+simple' :
                      type === 'natural' ? 'Lenguaje natural — sin tag autocomplete' :
                                          'Epsilon-prediction: dpmpp_2m+karras estándar';

  // Contenido del tooltip flotante
  const d = profile?.defaults;
  if (!d) { tooltip.innerHTML = ''; return; }

  const samplerNote = type === 'vpred'   ? ' <span style="color:#ec00f0">(euler forzado en regional)</span>' :
                      type === 'flow'    ? '' : '';
  tooltip.innerHTML = `
    <div class="pt-row"><span class="pt-key">CFG</span><span class="pt-val">${d.cfg}</span></div>
    <div class="pt-row"><span class="pt-key">Steps</span><span class="pt-val">${d.steps}</span></div>
    <div class="pt-row"><span class="pt-key">Sampler</span><span class="pt-val">${d.sampler}${samplerNote}</span></div>
    <div class="pt-row"><span class="pt-key">Scheduler</span><span class="pt-val">${d.scheduler}</span></div>
    ${profile.danbooru_cutoff ? `<div class="pt-row"><span class="pt-key">DB cutoff</span><span class="pt-val">${profile.danbooru_cutoff}</span></div>` : ''}
  `;

  // Mostrar/ocultar tooltip al hover sobre el profile-row
  const row = document.getElementById('profile-row');
  if (row && !row._tooltipBound) {
    row._tooltipBound = true;
    row.addEventListener('mouseenter', () => tooltip.classList.add('visible'));
    row.addEventListener('mouseleave', () => tooltip.classList.remove('visible'));
  }
}

async function _updateActiveCsv() {
  const csvName = _activeProfile?.tag_csv || null;
  _acCsvName    = csvName || '';
  _acTagsLoaded = false;

  const bar   = document.getElementById('tag-status-bar');
  const label = document.getElementById('tag-count-label');

  if (!csvName) {
    // Perfil sin CSV configurado — no mostrar nada
    if (bar) bar.style.display = 'none';
    return;
  }

  try {
    const { loaded, count, csv_present } = await apiGet(
      `/tags/status?csv=${encodeURIComponent(csvName)}`
    );
    _acTagsLoaded = loaded;

    if (bar && label) {
      if (loaded) {
        label.textContent = count.toLocaleString() + ' tags';
        bar.style.display = '';
      } else if (!csv_present) {
        label.textContent = 'Tags no disponibles';
        bar.style.display = '';
        // Ofrecer descarga si no está suprimido para este perfil
        const skipKey = `tags_skip_${_activeProfile.id}`;
        if (!localStorage.getItem(skipKey)) {
          _showTagsDownloadModal(_activeProfile);
        }
      }
    }
  } catch (_) {}
}

function initProfiles() {
  document.getElementById('sel-profile')
    ?.addEventListener('change', _onProfileChange);

  document.getElementById('sel-checkpoint')
    ?.addEventListener('change', _onCheckpointChange);

  document.getElementById('btn-quality-prefix')
    ?.addEventListener('click', () => {
      if (!_activeProfile?.quality_prefix) return;
      const ta = document.getElementById('inp-prompt');
      const pre = _activeProfile.quality_prefix;
      ta.value  = ta.value.trim() ? `${pre}, ${ta.value.trim()}` : pre;
      ta.focus();
    });

  document.getElementById('sel-rating')
    ?.addEventListener('change', function () {
      if (!this.value || !_activeProfile) return;
      const ta    = document.getElementById('inp-prompt');
      const newTag = _activeProfile.rating_tags[this.value] || '';
      if (!newTag) return;
      const all    = Object.values(_activeProfile.rating_tags);
      const parts  = ta.value.split(',').map(p => p.trim()).filter(Boolean);
      let replaced = false;
      for (let i = 0; i < parts.length; i++) {
        if (all.includes(parts[i])) { parts[i] = newTag; replaced = true; break; }
      }
      if (!replaced) parts.push(newTag);
      ta.value = parts.join(', ');
      this.value = '';
      ta.focus();
    });

  document.getElementById('btn-tags-reload')
    ?.addEventListener('click', async () => {
      if (!_acCsvName) return;
      try {
        const { count } = await fetch(
          `${API}/tags/reload?csv=${encodeURIComponent(_acCsvName)}`,
          { method: 'POST' }
        ).then(r => r.json());
        _acTagsLoaded = count > 0;
        const label = document.getElementById('tag-count-label');
        if (label) label.textContent = count.toLocaleString() + ' tags';
        toast(`Tags recargados: ${count.toLocaleString()}`);
      } catch (_) { toast(t('painter.conn_error')); }
    });

  loadProfiles();
}


// ── Modal de descarga de tags ─────────────────────────────────────────────

let _tdmEs = null;   // EventSource activo de descarga

function _showTagsDownloadModal(profile) {
  const modal = document.getElementById('tags-dl-modal');
  if (!modal) return;
  document.getElementById('tdm-profile-name').textContent = profile.name;
  document.getElementById('tdm-skip-check').checked = false;
  document.getElementById('tdm-progress').classList.remove('visible');
  document.getElementById('tdm-prog-fill').style.width = '0%';
  document.getElementById('tdm-btn-dl').disabled = false;
  modal.classList.add('visible');
}

function _hideTagsDownloadModal() {
  document.getElementById('tags-dl-modal')?.classList.remove('visible');
  if (_tdmEs) { _tdmEs.close(); _tdmEs = null; }
}

function initTagDownloadModal() {
  const btnDl   = document.getElementById('tdm-btn-dl');
  const btnGh   = document.getElementById('tdm-btn-gh');
  const btnSkip = document.getElementById('tdm-btn-skip');

  btnDl?.addEventListener('click', () => {
    if (!_activeProfile?.id) return;
    btnDl.disabled = true;
    const progress = document.getElementById('tdm-progress');
    const fill     = document.getElementById('tdm-prog-fill');
    const label    = document.getElementById('tdm-prog-label');
    progress.classList.add('visible');

    _tdmEs = new EventSource(
      `${API}/tags/download?profile_id=${encodeURIComponent(_activeProfile.id)}`
    );
    _tdmEs.onmessage = ({ data }) => {
      const ev = JSON.parse(data);
      if (ev.type === 'progress') {
        const pct = ev.pct || 0;
        fill.style.width = pct + '%';
        const mb = (ev.downloaded / 1048576).toFixed(1);
        const total = ev.total ? ' / ' + (ev.total / 1048576).toFixed(1) + ' MB' : '';
        label.textContent = `Descargando… ${mb} MB${total} (${pct}%)`;
      } else if (ev.type === 'loading') {
        fill.style.width = '100%';
        label.textContent = ev.msg || 'Indexando…';
      } else if (ev.type === 'done') {
        _tdmEs.close(); _tdmEs = null;
        _acTagsLoaded = ev.count > 0;
        _acCsvName    = ev.csv || _acCsvName;
        const lbl = document.getElementById('tag-count-label');
        if (lbl) { lbl.textContent = ev.count.toLocaleString() + ' tags'; }
        const bar = document.getElementById('tag-status-bar');
        if (bar) bar.style.display = '';
        _hideTagsDownloadModal();
        toast(`Tags cargados: ${ev.count.toLocaleString()}`);
      } else if (ev.type === 'error') {
        _tdmEs.close(); _tdmEs = null;
        label.textContent = 'Error: ' + ev.msg;
        btnDl.disabled = false;
      }
    };
    _tdmEs.onerror = () => {
      label.textContent = 'Error de conexión';
      btnDl.disabled = false;
    };
  });

  btnGh?.addEventListener('click', () => {
    // Abrir el directorio danbooru del repo en una nueva pestaña
    window.open(
      'https://github.com/DraconicDragon/dbr-e621-lists-archive/tree/main/tag-lists/danbooru',
      '_blank'
    );
  });

  btnSkip?.addEventListener('click', () => {
    if (document.getElementById('tdm-skip-check')?.checked && _activeProfile) {
      localStorage.setItem(`tags_skip_${_activeProfile.id}`, '1');
    }
    _hideTagsDownloadModal();
  });
}


async function initTagSystem() {
  // Attach autocomplete a todos los textareas de prompt (estáticos)
  ['inp-prompt', 'inp-negative', 'reg-global-prompt', 'reg-negative'].forEach(id => {
    const el = document.getElementById(id);
    if (el) initAutocomplete(el);
  });
  // Textareas regionales (generados por buildRegionCards)
  for (let i = 0; i < 4; i++) {
    const el = document.getElementById(`reg-prompt-${i}`);
    if (el) initAutocomplete(el);
  }
}


// ── Main ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initCanvas();
  initRegionalMasks();
  buildRegionCards();
  initEvents();
  initKeyboard();
  initDraggable();
  initStyles();
  initTagDownloadModal();
  initProfiles();
  initTagSystem();
  backgroundInit();   // verifica ComfyUI e instala nodos faltantes en background
});
