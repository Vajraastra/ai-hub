// Forge Lab — frontend Fase 2: sets de validación fijos + grid de runs.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mark = (ok) => ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>';

let runsCache  = [];     // conservado solo para updateTabBadges (badges viejos)
// true tras la primera vez que el KSampler recibe valores (defaults o de
// sesión). No usar "¿está vacío el campo?" como señal de virginidad: el
// navegador a veces restaura el valor de UN input number en un reload
// (bfcache) y no otros, dejando steps/width/height vacíos para siempre.
let ksPopulated = false;

// arquitectura activa: gobierna qué checkpoints/LoRAs/sets se ven y con qué
// switches se explora. Persistida entre recargas.
let currentArch = localStorage.getItem('forge-arch') || 'zimage';
let archDefs = [];       // defs completas de /architectures
const archDefByName = (n) => archDefs.find(a => a.name === n);
const archDef = () => archDefByName(currentArch) || archDefs[0] || null;
// arquitectura vigente en el laboratorio: la de la sesión de exploración
// activa (que puede ser de otra arch), o la seleccionada si no hay sesión
const labArch = () =>
  (exploreSession && archDefByName(exploreSession.arch)) || archDef();
// añade ?arch= (o &arch=) a las rutas que filtran por arquitectura
const withArch = (path) =>
  path + (path.includes('?') ? '&' : '?') + 'arch=' + encodeURIComponent(currentArch);

// ── Modo de trabajo (F2): derivar checkpoint / fusionar LoRAs ──────────────
// El modo elegido se persiste entre recargas; con sesión activa manda el de
// la sesión (congelado en el lock, como base/LoRA/prompt).
let currentMode = localStorage.getItem('fusion-mode') || 'derive';
const labMode = () =>
  (exploreSession && (exploreSession.mode || 'derive')) || currentMode;

function applyModeUI() {
  const mode = labMode();
  const fuse = mode === 'fuse';
  document.querySelectorAll('#mode-switch button').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
    b.disabled = !!exploreSession;   // el modo queda congelado con la sesión
  });
  $('field-lora2').style.display = fuse ? '' : 'none';   // arrastra su fuerza anidada
  $('lbl-lora').textContent = fuse ? 'LoRA A' : 'LoRA';
  $('blocks-title-a').textContent = fuse ? 'Bloques · LoRA A' : 'Bloques';
  $('blocks-head-b').style.display = fuse ? '' : 'none';
  $('switch-grid-b').style.display = fuse ? '' : 'none';
  $('blocks-hint').textContent = fuse
    ? 'cada panel dosifica su LoRA por separado (A arriba · B abajo)'
    : 'clic en un bloque → slider · valor 1.0 = efecto completo (lineal)';
  const mbtn = $('btn-merge');
  if (mbtn) mbtn.textContent = fuse
    ? '⚗ Fusionar → LoRA A⊕B con la config afinada'
    : '⚒ Merge → checkpoint con la config afinada';
}

document.querySelectorAll('#mode-switch button').forEach(b => b.onclick = () => {
  if (exploreSession) return;
  currentMode = b.dataset.mode;
  localStorage.setItem('fusion-mode', currentMode);
  applyModeUI();
  refreshTriggers();
});

async function api(path, opts = {}) {
  if (opts.body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch('/api/fusion' + path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

// ── Diagnóstico ─────────────────────────────────────────────────────────────

const MF_LABELS = {
  diffusion_model: 'Diffusion model (base oficial)',
  text_encoder: 'Text encoder',
  vae: 'VAE',
};

async function loadArchitectures() {
  const data = await api('/architectures');
  archDefs = data.architectures;
  if (!archDefByName(currentArch)) currentArch = archDefs[0]?.name || 'zimage';
  const sel = $('arch-select');
  sel.innerHTML = '';
  for (const a of archDefs) sel.add(new Option(a.label, a.name));
  sel.value = currentArch;
  sel.onchange = () => switchArch(sel.value);
}

async function switchArch(name) {
  if (name === currentArch) return;
  currentArch = name;
  localStorage.setItem('forge-arch', name);
  // resetear todo lo dependiente de arquitectura (checkpoints, LoRAs, sets y
  // el grid de switches son distintos por arch)
  $('switch-grid').innerHTML = '';
  await Promise.all([loadStatus(), refreshCheckpoints(), refreshLoras(),
                     refreshBatteries(false)]);
  await refreshExplore();
  // cambiar de arch SÍ resetea el KSampler a los defaults de la nueva
  if (!exploreSession) populateKSampler(archDefaultsSampling());
}

async function loadStatus() {
  try {
    const [s, mf] = await Promise.all([api(withArch('/status')), api(withArch('/model-files'))]);
    // El pill lo pinta el poller mientras hay una transición en curso.
    if (!comfyBusy) {
      setComfyPill(s.comfyui ? 'online' : 'offline');
      // Si ComfyUI está arriba, reclamar la propiedad: habilita el auto-cierre
      // al soltar esta página y cancela un cierre pendiente tras un F5.
      if (s.comfyui) claimComfy();
    }
    let html = `<div class="status-row"><span>ComfyUI (API :8188)</span>${mark(s.comfyui)}</div>`;
    for (const [node, ok] of Object.entries(s.nodes))
      html += `<div class="status-row"><span>nodo ${esc(node)}</span>${mark(ok)}</div>`;
    if (!Object.keys(s.nodes).length)
      html += `<div class="status-row"><span>nodos Realtime-Lora</span><span class="bad">sin comprobar (ComfyUI apagado)</span></div>`;

    // ficheros de modelo: seleccionables sobre el almacén global del Hub
    html += `<div class="cat-title" style="margin-top:14px">Ficheros de modelo — ${esc(s.arch)}</div>`;
    for (const [k, m] of Object.entries(s.models)) {
      const opts = (mf.options[k] || []).map(o =>
        `<option value="${esc(o.path)}"${o.path === mf.files[k] ? ' selected' : ''}>${esc(o.path)}</option>`
      ).join('');
      const isDefault = mf.files[k] === mf.defaults[k];
      html += `<div class="status-row"><span>${esc(MF_LABELS[k] || k)}` +
        (isDefault ? '' : ' <span class="badge locked">override</span>') +
        `<br><select class="mf-select" data-key="${esc(k)}">` +
        (opts || `<option selected>${esc(m.path)}</option>`) +
        `</select></span>${mark(m.present)}</div>`;
    }
    html += `<div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button class="btn btn-outline" id="btn-save-paths">Guardar paths</button>
      <button class="back-btn" id="btn-default-paths">volver a defaults</button>
      <span class="dim" style="font-size:11px">paths relativos al almacén global de modelos del Hub</span></div>`;
    $('status-content').innerHTML = html;
    $('btn-save-paths').onclick = () => saveModelPaths(false);
    $('btn-default-paths').onclick = () => saveModelPaths(true);
  } catch (e) {
    $('status-content').innerHTML = `<span class="bad">Error: ${esc(e.message)}</span>`;
  }
}

async function saveModelPaths(reset) {
  const files = {};
  document.querySelectorAll('.mf-select').forEach(sel => {
    files[sel.dataset.key] = reset ? '' : sel.value;
  });
  try {
    await api('/model-files', { method: 'PUT', body: { arch: currentArch, files } });
    await loadStatus();
    await refreshCheckpoints();   // la base oficial puede haber cambiado
  } catch (e) { alert('Error guardando paths: ' + e.message); }
}

$('btn-status-toggle').onclick = () => {
  const el = $('status-card');
  el.style.display = el.style.display === 'none' ? '' : 'none';
};

// ── ComfyUI como backend headless (arranque/parada desde el pill) ────────────
// El pill de estado es también el botón: offline→arranca, online→detiene.
// ComfyUI corre sin UI (solo :8188) y su log sale en el monitor del dashboard.
// Al cerrar/recargar esta página se suelta el backend (con gracia para el F5).
const COMFY_OWNER = 'fusion';
let comfyBusy = false;   // hay un arranque/parada en curso

function setComfyPill(state) {
  const pill = $('comfy-pill');
  const map = {
    online:   ['ComfyUI: online',      'badge comfy-toggle draft'],
    offline:  ['ComfyUI: offline',     'badge comfy-toggle'],
    starting: ['ComfyUI: arrancando…', 'badge comfy-toggle busy locked'],
    stopping: ['ComfyUI: deteniendo…', 'badge comfy-toggle busy locked'],
  };
  const [txt, cls] = map[state] || map.offline;
  pill.textContent = txt;
  pill.className = cls;
}

async function hubPost(path) {
  const r = await fetch(path, { method: 'POST' });
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

// Reclama la propiedad del backend si ya está arriba (idempotente, barato).
async function claimComfy() {
  try { await hubPost(`/api/apps/comfyui/launch-backend?owner=${COMFY_OWNER}`); }
  catch (_) {}
}

// Sondea /status hasta que comfyui alcance el estado deseado (o timeout).
async function pollComfyUntil(wantUp, timeoutMs = 90000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    await new Promise(r => setTimeout(r, 1500));
    let up = false;
    try { up = (await api(withArch('/status'))).comfyui; } catch (_) {}
    if (up === wantUp) return true;
  }
  return false;
}

async function toggleComfy() {
  if (comfyBusy) return;
  const online = $('comfy-pill').textContent.includes('online');
  comfyBusy = true;
  try {
    if (online) {
      setComfyPill('stopping');
      await hubPost(`/api/apps/comfyui/stop-backend?owner=${COMFY_OWNER}`);
      await pollComfyUntil(false, 30000);
    } else {
      setComfyPill('starting');
      await hubPost(`/api/apps/comfyui/launch-backend?owner=${COMFY_OWNER}`);
      await pollComfyUntil(true, 120000);
    }
  } catch (e) {
    alert('ComfyUI: ' + e.message);
  } finally {
    comfyBusy = false;
    await loadStatus();   // refresca pill + nodos + diagnóstico
  }
}

$('comfy-pill').onclick = toggleComfy;
$('comfy-pill').onkeydown = (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleComfy(); }
};

// Al cerrar/recargar la página, soltar el backend (sendBeacon sobrevive al
// unload). El servidor lo cierra con gracia solo si lo arrancamos nosotros.
window.addEventListener('pagehide', () => {
  try {
    navigator.sendBeacon(
      `/api/apps/comfyui/release-backend?owner=${COMFY_OWNER}`);
  } catch (_) {}
});

// ── Batería de prompts (Fase 4) ─────────────────────────────────────────────

let batteriesCache = [];      // resúmenes de /batteries
let currentBattery = null;    // dict completo de la batería seleccionada
const LT_LABELS = { character: 'Personaje', style: 'Estilo', concept: 'Concepto' };

async function refreshBatteries(keepSel = true) {
  const data = await api('/batteries');
  batteriesCache = data.batteries;
  const sel = $('battery-select');
  const prev = keepSel && currentBattery ? currentBattery.id : sel.value;
  sel.innerHTML = '';
  for (const b of batteriesCache) {
    const lt = LT_LABELS[b.lora_type] || b.lora_type;
    sel.add(new Option(`${lt} · ${b.style} — ${b.label}${b.modified ? ' ✎' : ''}`, b.id));
  }
  if (prev && batteriesCache.some(b => b.id === prev)) sel.value = prev;
  await selectBattery(sel.value);
}

async function selectBattery(id) {
  if (!id) { currentBattery = null; return; }
  currentBattery = await api('/batteries/' + encodeURIComponent(id));
  renderBattery();
}

function renderBattery() {
  const b = currentBattery;
  if (!b) return;
  $('battery-hint').textContent = b.hint || '';
  const lt = LT_LABELS[b.lora_type] || b.lora_type;
  const origin = !b.is_default ? 'batería propia'
    : b.is_custom ? 'default modificado' : 'default de fábrica';
  $('battery-meta').textContent = `${lt} · ${b.style} · ${b.prompts.length} prompts · ${origin}`;
  const pe = $('battery-prompts');
  pe.innerHTML = '';
  for (const p of b.prompts) pe.appendChild(batteryPromptRow(p));
  $('btn-battery-reset').style.display = (b.is_default && b.is_custom) ? '' : 'none';
  $('btn-battery-delete').style.display = (!b.is_default) ? '' : 'none';
}

function batteryPromptRow(p) {
  const row = document.createElement('div');
  row.className = 'prompt-row';
  const id = document.createElement('input');
  id.value = p.id; id.placeholder = 'id'; id.dataset.f = 'id';
  const seed = document.createElement('input');
  seed.value = p.seed; seed.placeholder = 'seed'; seed.dataset.f = 'seed';
  const txt = document.createElement('textarea');
  txt.value = p.text; txt.placeholder = 'prompt'; txt.dataset.f = 'text';
  const neg = document.createElement('input');
  neg.value = p.negative || ''; neg.placeholder = 'negative (opcional)'; neg.dataset.f = 'negative';
  const del = document.createElement('button');
  del.className = 'prompt-del'; del.textContent = '✕'; del.title = 'Quitar prompt';
  del.onclick = () => row.remove();
  row.append(id, seed, txt, neg, del);
  return row;
}

function collectBattery() {
  const b = currentBattery;
  const prompts = [];
  for (const row of $('battery-prompts').querySelectorAll('.prompt-row')) {
    const get = (f) => row.querySelector(`[data-f="${f}"]`).value;
    prompts.push({ id: get('id').trim(), seed: get('seed').trim(),
                   text: get('text'), negative: get('negative') });
  }
  return { id: b.id, label: b.label, lora_type: b.lora_type,
           style: b.style, hint: b.hint, prompts };
}

$('battery-select').onchange = () => selectBattery($('battery-select').value);

$('btn-battery-add').onclick = () => {
  if (!currentBattery) return;
  const n = $('battery-prompts').querySelectorAll('.prompt-row').length + 1;
  $('battery-prompts').appendChild(batteryPromptRow(
    { id: `prompt-${n}`, seed: Math.floor(Math.random() * 1e9), text: '', negative: '' }));
};

$('btn-battery-save').onclick = async () => {
  if (!currentBattery) return;
  try {
    await api('/batteries', { method: 'POST', body: collectBattery() });
    await refreshBatteries();
  } catch (e) { alert('Error guardando: ' + e.message); }
};

$('btn-battery-reset').onclick = async () => {
  if (!currentBattery) return;
  if (!confirm(`Restaurar "${currentBattery.label}" a la plantilla de fábrica? Se pierden tus cambios.`)) return;
  try {
    await api('/batteries/' + encodeURIComponent(currentBattery.id), { method: 'DELETE' });
    await refreshBatteries();
  } catch (e) { alert('Error: ' + e.message); }
};

$('btn-battery-delete').onclick = async () => {
  if (!currentBattery) return;
  if (!confirm(`Eliminar la batería "${currentBattery.label}"?`)) return;
  try {
    await api('/batteries/' + encodeURIComponent(currentBattery.id), { method: 'DELETE' });
    currentBattery = null;
    await refreshBatteries(false);
  } catch (e) { alert('Error: ' + e.message); }
};

$('btn-battery-new').onclick = async () => {
  const id = prompt('ID de la batería nueva (minúsculas, dígitos, guiones):');
  if (!id) return;
  const lora_type = prompt('Tipo de LoRA (character / style / concept):', 'character');
  if (!['character', 'style', 'concept'].includes(lora_type)) {
    if (lora_type) alert('tipo inválido'); return; }
  const style = prompt('Estilo (prosa / tags):', 'prosa');
  if (!['prosa', 'tags'].includes(style)) { if (style) alert('estilo inválido'); return; }
  const label = prompt('Etiqueta:', id) || id;
  try {
    await api('/batteries', { method: 'POST', body: {
      id: id.trim(), label, lora_type, style, hint: '',
      prompts: [{ id: 'prompt-1', text: 'edítame', negative: '',
                  seed: Math.floor(Math.random() * 1e9) }] } });
    await refreshBatteries(false);
    $('battery-select').value = id.trim();
    await selectBattery(id.trim());
  } catch (e) { alert('Error: ' + e.message); }
};

$('btn-battery-run').onclick = async () => {
  if (!exploreSession) return alert('Inicia una sesión de exploración primero (define base/LoRA/prompt y config).');
  if (!currentBattery) return alert('Elige una batería.');
  if (!confirm(`Correr la batería "${currentBattery.label}" (${currentBattery.prompts.length} prompts) contra la config actual de bloques?\nCada prompt cae al historial como un trabajo.`)) return;
  try {
    const { job_id } = await api('/explore/battery', { method: 'POST',
      body: { battery_id: currentBattery.id, config: collectConfig() } });
    $('btn-battery-run').disabled = true;
    $('battery-progress').style.display = '';
    const timer = setInterval(async () => {
      let j;
      try { j = await api('/jobs/' + job_id); } catch (e) { return; }
      const done = j.item_index + (j.step / Math.max(j.steps_total, 1));
      const pct = Math.round(100 * done / Math.max(j.total, 1));
      $('battery-progress-fill').style.width = pct + '%';
      $('battery-progress-label').textContent =
        `[${j.item_index + 1}/${j.total}] ${j.prompt_id} — paso ${j.step}/${j.steps_total} (${pct}%)`;
      if (j.status !== 'running') {
        clearInterval(timer);
        $('btn-battery-run').disabled = false;
        $('battery-progress').style.display = 'none';
        if (j.status === 'error') alert('Batería fallida: ' + j.error);
        await refreshExplore();
      }
    }, 1000);
  } catch (e) { alert('Error lanzando la batería: ' + e.message); }
};

// (Sets de validación retirados de la UI — el backend Python sigue intacto.)

// ── Checkpoints + merge (Fase 3) ────────────────────────────────────────────

let checkpointsCache = [];

const fmtGB = (b) => b == null ? '' : (b / 1024 ** 3).toFixed(2) + ' GB';

// Los bloques de un checkpoint derivado se guardan como dict {bloque: peso}
// (p. ej. {"middle_block.1": 1.0}), NO como array. Antes se hacía
// c.blocks.join(', ') directo → "join is not a function" en cuanto había un
// derivado con dict, lo que reventaba init() entero y dejaba el KSampler vacío.
function fmtDerivedBlocks(blocks) {
  if (Array.isArray(blocks)) return blocks.join(', ');
  if (blocks && typeof blocks === 'object')
    return Object.entries(blocks).map(([k, v]) => v === 1 ? k : `${k}@${v}`).join(', ');
  return '';
}

async function refreshCheckpoints() {
  const data = await api(withArch('/checkpoints'));
  checkpointsCache = data.checkpoints;
  const el = $('ckpt-list');
  el.innerHTML = '';
  for (const c of checkpointsCache) {
    const row = document.createElement('div');
    row.className = 'ckpt-row';
    const lineage = c.kind === 'derived'
      ? `${esc(c.base.name)} ← ${esc(c.lora.file)} @ ${c.lora.strength}` +
        (c.blocks ? ` · bloques: ${esc(fmtDerivedBlocks(c.blocks))}` : '') +
        ` · ${esc((c.created_at || '').slice(0, 16).replace('T', ' '))}`
      : '';
    row.innerHTML =
      `<span class="mono">${esc(c.name)}</span>` +
      `<span class="badge ${c.kind === 'official' ? 'locked' : 'draft'}">${c.kind === 'official' ? 'oficial' : 'derivado'}</span>` +
      (c.present ? '' : '<span class="bad">✘ falta el fichero</span>') +
      `<span>${esc(c.label || '')}</span>` +
      (lineage ? `<span class="lineage">${lineage}</span>` : '') +
      `<span style="flex:1"></span>` +
      `<span class="dim">${fmtGB(c.size_bytes)}</span>`;
    if (c.kind === 'derived') {
      const del = document.createElement('button');
      del.className = 'prompt-del'; del.textContent = '🗑';
      del.title = 'Borrar checkpoint derivado (el fichero, ~12 GB, y su registro)';
      del.onclick = async () => {
        if (!confirm(`Borrar el checkpoint derivado "${c.name}" (${fmtGB(c.size_bytes)})?\nSus runs quedan como histórico; el merge es reproducible desde el registro.`)) return;
        try {
          await api('/checkpoints/' + encodeURIComponent(c.name), { method: 'DELETE' });
          await refreshCheckpoints();
        } catch (e) { alert('Error: ' + e.message); }
      };
      row.appendChild(del);
    }
    el.appendChild(row);
  }
  // pick buttons visuales de checkpoint (base del merge + checkpoint de la
  // exploración): default = primer checkpoint presente de la arquitectura
  const present = checkpointsCache.filter(c => c.present);
  for (const [inp, btn] of [['exp-checkpoint', 'btn-pick-exp-checkpoint']]) {
    // con sesión activa manda la sesión: el default pisaría el checkpoint
    // congelado al recargar la página (bug 2026-07-17)
    if (exploreSession) $(inp).value = exploreSession.checkpoint;
    else if (!present.find(c => c.name === $(inp).value))
      $(inp).value = present[0]?.name || '';
    renderCkptPickBtn(inp, btn);
  }
  updateTabBadges();
}

const ckptByName = (n) => checkpointsCache.find(c => c.name === n);
const ckptThumbUrl = (c) => c.has_preview
  ? `/api/fusion/checkpoint-preview?arch=${encodeURIComponent(currentArch)}&name=${encodeURIComponent(c.name)}`
  : '';

function renderCkptPickBtn(inputId, btnId) {
  const btn = $(btnId);
  const c = ckptByName($(inputId).value);
  if (!c) {
    // sesión de otra arch: su checkpoint no está en la cache de la arch
    // visible, pero el nombre congelado debe verse igual (sin thumbnail)
    if (exploreSession && $(inputId).value === exploreSession.checkpoint) {
      const n = exploreSession.checkpoint;
      btn.innerHTML = `<span class="ph">🧩</span><span class="nm" title="${esc(n)}">${esc(n)}</span>`;
      return;
    }
    btn.innerHTML = '<span class="ph">🧩</span><span class="nm dim">— elegir checkpoint —</span>';
    return;
  }
  const url = ckptThumbUrl(c);
  btn.innerHTML = (url ? `<img src="${url}">` : '<span class="ph">🧩</span>') +
    `<span class="nm" title="${esc(c.name)}">${esc(c.name)}</span>`;
}

// ── Picker visual genérico (thumbnails estilo Vault) ───────────────────────
// Reutilizado para LoRAs y checkpoints: mismo modal, distinta config. Los
// LoRAs se listan solo si son de la arquitectura activa; los checkpoints ya
// vienen filtrados por arquitectura del backend.

let lorasCache = [];        // solo arch_match
let picker = null;          // config del picker abierto (o null)

const loraByFile = (f) => lorasCache.find(l => l.file === f);

// LoRA congelado en la sesión para cada picker (A o B); se muestra el
// ORIGINAL de la colección (sus sidecars/thumbnail viven junto a él)
const sessLoraFor = (id) => exploreSession &&
  (id === 'exp-lora2'
    ? (exploreSession.lora2_source || exploreSession.lora2 || '')
    : (exploreSession.lora_source || exploreSession.lora));

async function refreshLoras() {
  const data = await api(withArch('/loras'));
  lorasCache = data.loras.filter(l => l.arch_match);
  for (const id of ['exp-lora', 'exp-lora2']) {
    // con sesión activa manda la sesión (mismo motivo que en checkpoints)
    if (exploreSession) $(id).value = sessLoraFor(id) || '';
    else if ($(id).value && !loraByFile($(id).value)) $(id).value = '';
    renderLoraPickBtn(id);
  }
}

function renderLoraPickBtn(id) {
  const btn = $('btn-pick-' + id);
  const l = loraByFile($(id).value);
  refreshTriggers();
  if (!l) {
    // sesión de otra arch: mismo fallback que en checkpoints
    const sessLora = sessLoraFor(id);
    if (sessLora && $(id).value === sessLora) {
      const n = sessLora.split('/').pop().replace(/\.safetensors$/i, '');
      btn.innerHTML = `<span class="ph">🧬</span><span class="nm" title="${esc(sessLora)}">${esc(n)}</span>`;
      return;
    }
    btn.innerHTML = '<span class="ph">🧬</span><span class="nm dim">— elegir LoRA —</span>';
    return;
  }
  const thumb = l.has_preview
    ? `<img src="/api/fusion/lora-preview?file=${encodeURIComponent(l.file)}">`
    : '<span class="ph">🧬</span>';
  btn.innerHTML = thumb + `<span class="nm" title="${esc(l.file)}">${esc(l.name)}</span>`;
}

// ── Trigger words del LoRA (chips sobre el prompt) ──────────────────────────
// Fuente (backend /lora/triggers): sidecars de Civitai/Lora-Manager o los tags
// más frecuentes del dataset (kohya). Clic = añade/quita la palabra del
// prompt; con sesión activa el prompt está congelado y los chips solo
// informan (marcan cuáles ya están dentro). En modo fusión se pintan los de
// AMBOS LoRAs, etiquetados A y B.
let triggerKey = null;      // clave de la última respuesta pintada

// la sesión puede correr sobre una copia alias: los sidecars viven junto al
// LoRA ORIGINAL (lora_source / lora2_source)
function activeTriggerLoras() {
  const fuse = labMode() === 'fuse';
  const s = exploreSession;
  const a = s ? (s.lora_source || s.lora) : $('exp-lora').value;
  const b = fuse
    ? (s ? (s.lora2_source || s.lora2 || '') : $('exp-lora2').value)
    : '';
  const out = [];
  if (a) out.push({ file: a, tag: fuse ? 'A' : '' });
  if (b) out.push({ file: b, tag: 'B' });
  return out;
}

async function refreshTriggers() {
  const loras = activeTriggerLoras();
  const strip = $('trigger-strip');
  const key = loras.map(l => l.tag + ':' + l.file).join('|');
  if (!loras.length) {
    triggerKey = null;
    strip.style.display = 'none';
    strip.innerHTML = '';
    return;
  }
  if (key === triggerKey) { paintTriggerState(); return; }
  triggerKey = key;
  const results = await Promise.all(loras.map(async (l) => {
    try {
      return { ...l, ...(await api('/lora/triggers?file=' + encodeURIComponent(l.file))) };
    } catch (_) { return { ...l, words: [], source: null }; }
  }));
  if (triggerKey !== key) return;   // respuesta tardía: ya se pidió otra cosa
  strip.innerHTML = '';
  let any = false;
  for (const r of results) {
    if (!r.words || !r.words.length) continue;
    any = true;
    const srcLabel = { civitai: 'Civitai', 'lora-manager': 'Civitai',
                       dataset: 'dataset' }[r.source] || r.source;
    const src = document.createElement('span');
    src.className = 'trig-src';
    src.textContent = `⚡${r.tag ? ' ' + r.tag : ''} triggers · ${srcLabel}`;
    src.title = 'Palabras de activación del LoRA — clic para añadir/quitar del prompt';
    strip.appendChild(src);
    for (const w of r.words) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'trig-chip';
      b.textContent = w;
      b.onclick = () => toggleTrigger(w);
      strip.appendChild(b);
    }
  }
  strip.style.display = any ? '' : 'none';
  paintTriggerState();
}

const promptHasWord = (text, w) => text.toLowerCase().includes(w.toLowerCase());

function paintTriggerState() {
  const text = $('exp-prompt').value;
  const frozen = !!exploreSession;
  for (const b of $('trigger-strip').querySelectorAll('.trig-chip')) {
    b.classList.toggle('in-prompt', promptHasWord(text, b.textContent));
    b.disabled = frozen;
    b.title = frozen ? 'prompt congelado durante la sesión' : b.textContent;
  }
}

function toggleTrigger(w) {
  if (exploreSession) return;
  const ta = $('exp-prompt');
  let t = ta.value;
  if (promptHasWord(t, w)) {
    // quitar la aparición literal comiéndose la coma/espacio de delante
    const re = new RegExp('(,\\s*)?' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
    t = t.replace(re, '').replace(/^\s*,\s*/, '').replace(/ {2,}/g, ' ').trim();
  } else {
    // tags (sdxl) se encadenan con coma; la prosa (zimage) con espacio
    const sep = (labArch() && labArch().name === 'zimage') ? ' ' : ', ';
    t = t.trim() ? t.replace(/[,\s]+$/, '') + sep + w : w;
  }
  ta.value = t;
  paintTriggerState();
}

$('exp-prompt').addEventListener('input', paintTriggerState);

// ── Modal genérico (paginado + carga progresiva) ──
// Un solo componente para LoRAs y checkpoints. Con 1300+ LoRAs, renderizar de
// golpe colapsaba el webview: aquí se pinta en lotes (LP_BATCH) con un
// IntersectionObserver sobre el centinela del final, y el grid usa
// grid-auto-rows fijo (altura de fila a prueba de colapso).
const LP_BATCH = 60;
let lpObserver = null;

function lpEnsureObserver() {
  if (lpObserver) return;
  lpObserver = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) lpAppendBatch();
  }, { root: $('lp-scroll'), rootMargin: '400px' });
  lpObserver.observe($('lp-sentinel'));
}

function openPicker(cfg) {
  picker = cfg;
  picker.favOnly = false;
  picker.sort = cfg.sorts[0].key;
  $('lp-search').value = '';
  $('lp-fav-only').classList.remove('on');
  const sortSel = $('lp-sort');
  sortSel.innerHTML = '';
  for (const s of cfg.sorts) sortSel.add(new Option(s.label, s.key));
  sortSel.value = picker.sort;
  lpEnsureObserver();
  renderPickerGrid();
  $('lora-picker-overlay').classList.add('open');
  $('lp-scroll').scrollTop = 0;
  $('lp-search').focus();
}

function closePicker() {
  $('lora-picker-overlay').classList.remove('open');
  picker = null;
}

// filtrado (búsqueda + solo-favoritas) + orden (favoritas primero, luego el
// criterio elegido)
function lpComputeList() {
  const q = $('lp-search').value.trim().toLowerCase();
  const cmp = picker.sorts.find(s => s.key === picker.sort).cmp;
  const list = picker.items.filter(it =>
    (!q || picker.search(it).includes(q)) &&
    (!picker.favOnly || it.is_favorite));
  list.sort((a, b) => ((b.is_favorite ? 1 : 0) - (a.is_favorite ? 1 : 0)) || cmp(a, b));
  return list;
}

function lpCard(it) {
  const url = picker.thumbUrl(it);
  const card = document.createElement('div');
  card.className = 'lp-card' + (picker.value(it) === picker.current ? ' selected' : '');
  card.title = picker.title(it);
  card.innerHTML =
    (url ? `<img class="lp-thumb" loading="lazy" src="${url}">`
         : `<div class="lp-thumb-ph">${picker.icon}</div>`) +
    `<div class="lp-body"><div class="lp-name">${esc(picker.title(it))}</div>` +
    `<div class="lp-meta">${esc(picker.meta(it))}</div></div>`;
  const star = document.createElement('button');
  star.className = 'lp-star' + (it.is_favorite ? ' on' : '');
  star.textContent = it.is_favorite ? '★' : '☆';
  star.title = 'favorita';
  star.onclick = (e) => { e.stopPropagation(); lpToggleFav(it, star); };
  card.appendChild(star);
  card.onclick = () => { picker.onPick(it); closePicker(); };
  return card;
}

function lpAppendBatch() {
  if (!picker) return;
  const end = Math.min(picker.shown + LP_BATCH, picker.list.length);
  const frag = document.createDocumentFragment();
  for (let i = picker.shown; i < end; i++) frag.appendChild(lpCard(picker.list[i]));
  $('lp-grid').appendChild(frag);
  picker.shown = end;
  // si el centinela sigue a la vista tras el lote (lista corta), forzar
  // re-evaluación del observer para encadenar el siguiente lote
  if (picker.shown < picker.list.length && lpObserver) {
    lpObserver.unobserve($('lp-sentinel'));
    lpObserver.observe($('lp-sentinel'));
  }
}

function renderPickerGrid() {
  if (!picker) return;
  picker.list = lpComputeList();
  picker.shown = 0;
  const grid = $('lp-grid');
  grid.innerHTML = '';
  $('lp-count').textContent = `${picker.list.length} ${picker.noun}`;
  if (!picker.list.length) {
    grid.innerHTML =
      `<span class="dim" style="font-size:12px;grid-column:1/-1">${esc(picker.empty)}` +
      `${picker.favOnly ? ' (favoritas)' : ($('lp-search').value.trim() ? ' (con ese filtro)' : '')}</span>`;
    return;
  }
  lpAppendBatch();
}

async function lpToggleFav(it, star) {
  const want = !it.is_favorite;
  it.is_favorite = want;
  star.textContent = want ? '★' : '☆';
  star.classList.toggle('on', want);
  try {
    await api('/favorites/toggle',
      { method: 'POST', body: { kind: picker.kind, id: picker.favId(it), on: want } });
  } catch (e) {
    it.is_favorite = !want;              // revertir si el backend falla
    star.textContent = it.is_favorite ? '★' : '☆';
    star.classList.toggle('on', it.is_favorite);
    return alert('Error guardando favorita: ' + e.message);
  }
  // si estamos filtrando por favoritas y se desmarcó, quitarla de la vista
  if (picker.favOnly && !want) renderPickerGrid();
}

const LP_SORT_NAME = { key: 'name', label: 'A → Z',
                       cmp: (a, b) => a.name.localeCompare(b.name) };

function openLoraPicker(targetId) {
  openPicker({
    kind: 'loras', noun: 'compatibles', icon: '🧬', current: $(targetId).value,
    empty: 'No hay LoRAs de esta arquitectura en el almacén',
    items: lorasCache,
    thumbUrl: (l) => l.has_preview
      ? `/api/fusion/lora-preview?file=${encodeURIComponent(l.file)}` : '',
    title: (l) => l.name,
    meta: (l) => (l.subfolder || '') + (l.rank ? (l.subfolder ? ' · ' : '') + 'r' + l.rank : ''),
    value: (l) => l.file,
    favId: (l) => l.file,
    search: (l) => (l.name + ' ' + (l.subfolder || '')).toLowerCase(),
    sorts: [
      LP_SORT_NAME,
      { key: 'recent', label: 'recientes', cmp: (a, b) => (b.mtime || 0) - (a.mtime || 0) },
      { key: 'rank',   label: 'rank',      cmp: (a, b) => (b.rank || 0) - (a.rank || 0) },
    ],
    onPick: (l) => { $(targetId).value = l.file; renderLoraPickBtn(targetId); },
  });
}

function openCheckpointPicker(inputId, btnId) {
  openPicker({
    kind: 'checkpoints', noun: 'checkpoints', icon: '🧩', current: $(inputId).value,
    empty: 'No hay checkpoints presentes para esta arquitectura',
    items: checkpointsCache.filter(c => c.present),
    thumbUrl: ckptThumbUrl,
    title: (c) => c.name,
    meta: (c) => (c.kind === 'official' ? 'oficial' : 'derivado') + (c.label ? ' · ' + c.label : ''),
    value: (c) => c.name,
    favId: (c) => c.name,
    search: (c) => (c.name + ' ' + (c.label || '')).toLowerCase(),
    sorts: [LP_SORT_NAME],
    onPick: (c) => { $(inputId).value = c.name; renderCkptPickBtn(inputId, btnId); },
  });
}

$('lp-close').onclick = closePicker;
$('lora-picker-overlay').onclick = (e) => {
  if (e.target.id === 'lora-picker-overlay') closePicker();
};
let lpSearchTimer;
$('lp-search').oninput = () => {
  clearTimeout(lpSearchTimer);
  lpSearchTimer = setTimeout(renderPickerGrid, 150);
};
$('lp-sort').onchange = () => { picker.sort = $('lp-sort').value; renderPickerGrid(); };
$('lp-fav-only').onclick = () => {
  picker.favOnly = !picker.favOnly;
  $('lp-fav-only').classList.toggle('on', picker.favOnly);
  renderPickerGrid();
};
$('btn-pick-exp-lora').onclick = () => openLoraPicker('exp-lora');
$('btn-pick-exp-lora2').onclick = () => openLoraPicker('exp-lora2');
$('btn-pick-exp-checkpoint').onclick = () => openCheckpointPicker('exp-checkpoint', 'btn-pick-exp-checkpoint');

// Footer de merge (Fase 5): fusiona la config de la generación SELECCIONADA
// (base+LoRA+strength de la sesión, dosis de bloques del trabajo) → checkpoint.
$('btn-merge').onclick = async () => {
  if (!exploreSession || !selectedGen) return;
  const g = genById(selectedGen);
  const s = exploreSession;
  const fuse = (s.mode || 'derive') === 'fuse';
  const name = ($('merge-name').value || '').trim();
  const label = ($('merge-label').value || '').trim();
  if (!name) return alert('Pon nombre al ' + (fuse ? 'LoRA fusionado' : 'checkpoint derivado')
    + ' (minúsculas, dígitos, guiones).');
  const msg = fuse
    ? `Fusión A⊕B con la config afinada [${g.summary}]:\n\n  ${sessLoraName(s)} @ ${s.strength}\n  + ${sessLora2Name(s)} @ ${s.strength2}\n  → loras/forge_fusion/${name}.safetensors\n\nProducto: un LoRA comprimido por SVD (energía 99%). Corre en CPU/RAM. ¿Adelante?`
    : `Merge con la config afinada [${g.summary}]:\n\n  ${s.checkpoint}  ←  ${sessLoraName(s)}  @ ${s.strength}\n  →  forge_lab/${name}.safetensors (~12 GB)\n\nCorre en CPU/RAM (unos minutos). ¿Adelante?`;
  if (!confirm(msg)) return;
  try {
    const { job_id } = await api('/explore/merge',
      { method: 'POST', body: { gen_id: selectedGen, name, label } });
    $('btn-merge').disabled = true;
    $('merge-progress').style.display = '';
    pollMergeJob(job_id);
  } catch (e) { alert('Error lanzando ' + (fuse ? 'fusión' : 'merge') + ': ' + e.message); }
};

// Habilita el footer según haya un trabajo seleccionado y refleja su config.
function updateMergeFooter() {
  const sel = (exploreSession && selectedGen) ? genById(selectedGen) : null;
  const sum = $('mf-summary');
  const btn = $('btn-merge');
  if (!btn) return;
  const fuse = exploreSession && (exploreSession.mode || 'derive') === 'fuse';
  if (sel) {
    const s = exploreSession;
    sum.classList.add('ready');
    sum.innerHTML = fuse
      ? `LoRA fusionado A⊕B: <span class="mono">${esc(sel.summary)}</span><br>` +
        `<span class="dim">${esc(sessLoraName(s))} @ ${s.strength} + ${esc(sessLora2Name(s))} @ ${s.strength2} → loras/forge_fusion/</span>`
      : `Config afinada: <span class="mono">${esc(sel.summary)}</span><br>` +
        `<span class="dim">${esc(s.checkpoint)} ← ${esc(sessLoraName(s))} @ ${s.strength}</span>`;
    btn.disabled = false;
  } else {
    sum.classList.remove('ready');
    sum.textContent = exploreSession
      ? 'Selecciona un trabajo del historial para ' +
        (fuse ? 'fusionar A⊕B con su config de bloques.' : 'mergear su config de bloques.')
      : 'Inicia una sesión y genera al menos una vez; selecciona ese trabajo para el bake.';
    btn.disabled = true;
  }
}

const MERGE_PHASES = { map: 'mapeando LoRA', merge: 'mergeando tensores',
                       fuse: 'fusionando rangos A⊕B',
                       write: 'escribiendo fichero', hash: 'SHA256 de verificación' };

function pollMergeJob(jobId) {
  const timer = setInterval(async () => {
    let j;
    try { j = await api('/jobs/' + jobId); } catch (e) { return; }
    const pct = j.total ? Math.round(100 * j.done / j.total) : 0;
    const bar = (j.phase === 'merge' || j.phase === 'fuse');
    $('merge-progress-fill').style.width = (bar ? pct : (j.status === 'running' ? 100 : pct)) + '%';
    $('merge-progress-label').textContent =
      `${MERGE_PHASES[j.phase] || j.phase || 'preparando'} — ${j.done}/${j.total}`;
    if (j.status !== 'running') {
      clearInterval(timer);
      $('btn-merge').disabled = false;
      $('merge-progress').style.display = 'none';
      if (j.status === 'error') alert((j.fused ? 'Fusión' : 'Merge') + ' fallida: ' + j.error);
      else {
        $('merge-name').value = ''; $('merge-label').value = '';
        const c = j.checkpoint;
        if (j.fused) {
          const w = c.worker || {};
          const rk = (w.rank_pre && w.rank_pre !== w.rank_max)
            ? `rank ${w.rank_pre}→${w.rank_max} (SVD)` : `rank ${w.rank_max ?? '?'}`;
          alert(`LoRA fusionado "${c.name}" creado (${fmtGB(c.size_bytes)}, ${rk}).\nEn el catálogo de LoRAs, subcarpeta forge_fusion/.`);
        } else
          alert(`Checkpoint "${c.name}" creado (${fmtGB(c.size_bytes)}).\nQueda listado abajo, en "Checkpoints — base y derivados".`);
      }
      await (j.fused ? refreshLoras() : refreshCheckpoints());
    }
  }, 1500);
}

// ── Laboratorio de bloques (Fase 4) ─────────────────────────────────────────

let exploreSession = null;
let selectedGen = null;
let draftTs = null;   // mtime del último draft de calibración (pre-lock)
let hasBaseImage = false;   // ¿existe base.png (checkpoint sin LoRA) de la sesión?
let lockedCompareGen = null;   // id fijado en el slider derecho (null = modo "últimas 2")

// Pane del draft de calibración: imagen efímera, fuera del historial
function draftPane() {
  const url = '/api/fusion/explore/draft-image?ts=' + draftTs;
  return `<div class="compare-pane">
    <h3>Calibración (no se guarda)</h3>
    <img src="${url}" onclick="window.open('${url}','_blank')">
    <div class="cfg">pre-lock · LoRA(s) al 100% en todos los bloques</div>
  </div>`;
}

async function refreshExplore() {
  const wasActive = !!exploreSession;
  const data = await api('/explore/session');
  exploreSession = data.session;
  draftTs = data.draft ? data.draft.ts : null;
  hasBaseImage = !!data.has_base;
  updateTabBadges();
  const active = !!exploreSession;
  // entradas + config se BLOQUEAN mientras hay sesión (congeladas al arrancar)
  setInputsLocked(active);
  applyModeUI();   // con sesión manda su modo; sin ella, el elegido
  $('btn-inputs-toggle').style.display = active ? '' : 'none';
  if (active && !wasActive) {
    // al bloquear la sesión: colapsar base/LoRA y prompt (ya congelados, redundantes
    // con el resumen de #run-bar) — se pueden reabrir con sus controles para verificar
    $('inputs-bar').classList.add('collapsed');
    $('btn-inputs-toggle').textContent = '▸ Ver base/LoRA';
    $('prompt-fold').classList.remove('open');
  } else if (!active) {
    $('inputs-bar').classList.remove('collapsed');
    $('prompt-fold').classList.add('open');
  }
  $('btn-explore-start').style.display = active ? 'none' : '';
  $('btn-explore-close').style.display = active ? '' : 'none';
  $('btn-explore-clear').style.display =
    (active && exploreSession.generations.length) ? '' : 'none';
  const gb = $('btn-explore-gen');
  gb.textContent = active ? '▶ Generar' : '▶ Generar (prueba)';
  gb.title = active ? ''
    : 'Calibración pre-lock: la imagen NO se guarda en el historial';
  refreshTriggers();   // el LoRA vigente puede venir de la sesión o del picker

  if (!active) {
    selectedGen = null;
    lockedCompareGen = null;
    closeChipPop();
    $('switch-grid').innerHTML = '';
    $('preset-row').innerHTML = '';
    $('explore-info').textContent = '';
    $('explore-actions').style.display = 'none';
    // defaults del KSampler solo si está virgen: un refresh tras cada draft
    // no debe pisar los valores que el usuario está calibrando
    if (!ksPopulated) populateKSampler(archDefaultsSampling());
    $('compare-wrap').innerHTML = draftTs ? draftPane() :
      '<div class="viewport-empty">Elige <b>base</b> + <b>LoRA</b> arriba y calibra libremente prompt y KSampler con «▶ Generar (prueba)» — esas imágenes NO se guardan.<br>Cuando estés convencido, pulsa «🔒 Lock sesión»: la config se congela y cada generación pasa al historial (la 1ª queda como referencia).</div>';
    $('hist-strip').innerHTML = '<div class="hist-empty">sin lock — nada se guarda</div>';
    updateMergeFooter();
    return;
  }

  const s = exploreSession;
  // restaurar el workbench desde la sesión: al recargar página/hub los pickers
  // arrancan con defaults y quedarían congelados mostrando modelos que NO son
  // los de la sesión (bug 2026-07-17); prompt y KSampler ya se restauraban
  $('exp-checkpoint').value = s.checkpoint;
  renderCkptPickBtn('exp-checkpoint', 'btn-pick-exp-checkpoint');
  $('exp-lora').value = s.lora_source || s.lora;
  renderLoraPickBtn('exp-lora');
  $('exp-lora2').value = s.lora2_source || s.lora2 || '';
  renderLoraPickBtn('exp-lora2');
  $('exp-strength').value = s.strength;
  $('exp-strength2').value = s.strength2 ?? 1.0;
  $('exp-seed').value = s.prompt.seed;
  const fuse = (s.mode || 'derive') === 'fuse';
  $('explore-info').innerHTML =
    (fuse ? '<span class="badge draft">⚗ fusión</span> ' : '') +
    `<span class="mono">${esc(s.checkpoint)}</span> + ` +
    `<span class="mono">${esc(sessLoraName(s))}</span> @ ${s.strength}` +
    (fuse ? ` + <span class="mono">${esc(sessLora2Name(s))}</span> @ ${s.strength2}` : '') +
    ` <span class="dim">seed ${s.prompt.seed} · ${s.sampling.steps} steps · cfg ${s.sampling.cfg}</span>`;
  populateKSampler(s.sampling);
  $('exp-prompt').value = s.prompt.text;
  paintTriggerState();   // el prompt congelado acaba de fijarse

  if (!$('switch-grid').children.length) buildSwitchGrid();
  if (selectedGen && !s.generations.find(g => g.id === selectedGen))
    selectedGen = null;
  if (!selectedGen && s.generations.length)
    selectedGen = s.generations[s.generations.length - 1].id;
  renderCompare();
  renderHistory();
}

// Cada bloque es un CHIP compacto: muestra su valor numérico encima (distingue
// "dosis 0.5" de simple on/off) y el slider vive en un POPUP al hacer clic.
function switchCell(label, key) {
  const chip = document.createElement('div');
  chip.className = 'chip';
  chip.dataset.key = key;
  chip.dataset.dose = '1';
  chip.innerHTML = `<span class="chip-val">1.00</span><span class="chip-lbl">${esc(label)}</span>`;
  chip.onclick = () => openChipPop(chip);
  return chip;
}

const chipOn = (chip) => !chip.classList.contains('off');

function paintChip(chip) {
  const d = parseFloat(chip.dataset.dose) || 0;
  chip.querySelector('.chip-val').textContent = chipOn(chip) ? d.toFixed(2) : 'off';
}

function setCell(chip, on) {
  chip.classList.toggle('off', !on);
  paintChip(chip);
}

// Construye un grid de chips + su fila de presets. En fuse hay dos:
// A (#switch-grid/#preset-row) y B (#switch-grid-b/#preset-row-b).
function buildGrid(gridId, presetId) {
  const grid = $(gridId);
  grid.innerHTML = '';
  const def = labArch();
  if (!def) return;
  for (const sw of def.explore_switches) grid.appendChild(switchCell(sw.label, sw.id));
  for (const chip of grid.children) paintChip(chip);
  buildPresetRow(def, gridId, presetId);
}

function buildSwitchGrid() {
  buildGrid('switch-grid', 'preset-row');
  buildGrid('switch-grid-b', 'preset-row-b');
}

// presets derivados de la arquitectura: genéricos (todo/invertir) + uno por
// grupo macro de block_groups (enciende solo los switches del grupo)
function buildPresetRow(def, gridId, presetId) {
  const row = $(presetId);
  row.innerHTML = '';
  const cells = () => [...$(gridId).children];
  const mk = (label, title, fn) => {
    const b = document.createElement('button');
    b.className = 'back-btn'; b.textContent = label;
    if (title) b.title = title;
    b.onclick = fn;
    row.appendChild(b);
  };
  mk('todo ON', '', () => cells().forEach(c => setCell(c, true)));
  mk('todo OFF', '', () => cells().forEach(c => setCell(c, false)));
  mk('invertir', '', () => cells().forEach(c => setCell(c, !chipOn(c))));
  for (const g of def.groups || []) {
    const ids = new Set(g.blocks);
    const short = g.label.split(/[\s/]/)[0].toLowerCase();
    mk('solo ' + short, g.description || g.label,
       () => cells().forEach(c => setCell(c, ids.has(c.dataset.key))));
  }
}

function collectConfig(gridId = 'switch-grid') {
  const blocks = {};
  let other = 0;
  for (const chip of $(gridId).children) {
    const d = chipOn(chip) ? (parseFloat(chip.dataset.dose) || 0) : 0;
    if (chip.dataset.key === 'other') other = d;
    else blocks[chip.dataset.key] = d;
  }
  return { blocks, other };
}

function applyConfig(cfg, gridId = 'switch-grid') {
  const blocks = cfg.blocks || {};
  for (const chip of $(gridId).children) {
    const d = chip.dataset.key === 'other' ? (cfg.other || 0) : (blocks[chip.dataset.key] ?? 0);
    chip.dataset.dose = d > 0 ? String(d) : '1';
    chip.classList.toggle('off', !(d > 0));
    paintChip(chip);
  }
}

// ── Popup del slider de un chip (compartido) ────────────────────────────
let popChip = null;
function openChipPop(chip) {
  popChip = chip;
  document.querySelectorAll('.chip.editing').forEach(c => c.classList.remove('editing'));
  chip.classList.add('editing');
  const d = parseFloat(chip.dataset.dose) || 1;
  $('pop-title').textContent = chip.querySelector('.chip-lbl').textContent;
  $('pop-on').checked = chipOn(chip);
  $('pop-range').value = d;
  $('pop-num').value = d;
  $('pop-val').textContent = d.toFixed(2);
  const pop = $('chip-pop');
  pop.classList.add('open');
  const r = chip.getBoundingClientRect();
  const pw = 230, ph = pop.offsetHeight || 160;
  let left = r.left, top = r.bottom + 6;
  if (left + pw > window.innerWidth - 8) left = window.innerWidth - pw - 8;
  if (top + ph > window.innerHeight - 8) top = r.top - ph - 6;
  pop.style.left = Math.max(8, left) + 'px';
  pop.style.top = Math.max(8, top) + 'px';
}
function closeChipPop() {
  $('chip-pop').classList.remove('open');
  if (popChip) popChip.classList.remove('editing');
  popChip = null;
}
function popApply(dose, on) {
  if (!popChip) return;
  dose = Math.min(2, Math.max(0, dose || 0));
  popChip.dataset.dose = String(dose);
  popChip.classList.toggle('off', !on);
  $('pop-val').textContent = dose.toFixed(2);
  paintChip(popChip);
}

function genById(id) { return exploreSession.generations.find(g => g.id === id); }

// Nombre legible del LoRA de la sesión: el original de la colección si la
// sesión corre sobre una copia alias (conversión diffusers→kohya).
function sessLoraName(s) { return (s.lora_source || s.lora).split('/').pop(); }
function sessLora2Name(s) { return (s.lora2_source || s.lora2 || '').split('/').pop(); }

function comparePane(title, gen, extraClass = '') {
  if (!gen) return '';
  const url = '/api/fusion/explore/image/' + encodeURIComponent(gen.id);
  return `<div class="compare-pane ${extraClass}">
    <h3>${title}</h3>
    <div class="ba-zoom single"><img class="ba-img" src="${url}"></div>
    <div class="cfg">${esc(gen.summary)} · ${Math.round(gen.seconds)}s</div>
  </div>`;
}

// Markup genérico del slider antes/después (2 imágenes + divisor + labels) —
// reusado por la Referencia (sin/con LoRA) y por el comparador de la derecha
// (últimas 2 generaciones / fijada vs última). Ver initBeforeAfter().
function imageCompareSlider(leftUrl, leftLabel, rightUrl, rightLabel) {
  return `<div class="ba-slider">
    <div class="ba-zoom">
      <img class="ba-img ba-base" src="${leftUrl}">
      <img class="ba-img ba-over" src="${rightUrl}">
    </div>
    <div class="ba-handle"></div>
    <span class="ba-label ba-label-l">${esc(leftLabel)}</span>
    <span class="ba-label ba-label-r">${esc(rightLabel)}</span>
  </div>`;
}

// Referencia con base.png (checkpoint sin LoRA, misma sesión).
function compareSlider(gen) {
  if (!gen) return '';
  const url = '/api/fusion/explore/image/' + encodeURIComponent(gen.id);
  return `<div class="compare-pane">
    <h3>Referencia 📌</h3>
    ${imageCompareSlider('/api/fusion/explore/base-image', 'Sin LoRA', url, 'Con LoRA')}
    <div class="cfg">${esc(gen.summary)} · ${Math.round(gen.seconds)}s</div>
  </div>`;
}

// Viewer derecho: por defecto compara las últimas 2 generaciones (para ver
// diferencias finas entre iteraciones consecutivas). Con "🔒 Fijar" se
// congela la imagen de la izquierda y la derecha sigue mostrando siempre la
// última generación (para comparar contra un punto de referencia fijo
// mientras se siguen probando variantes); "🔓 Quitar fijado" vuelve al modo
// por defecto.
function rightComparePane() {
  const gens = (exploreSession && exploreSession.generations) || [];
  if (lockedCompareGen && !genById(lockedCompareGen)) lockedCompareGen = null;
  if (!gens.length || (gens.length < 2 && !lockedCompareGen)) return '';
  const last = gens[gens.length - 1];
  const lastUrl = '/api/fusion/explore/image/' + encodeURIComponent(last.id);

  if (lockedCompareGen) {
    const locked = genById(lockedCompareGen);
    const lockedUrl = '/api/fusion/explore/image/' + encodeURIComponent(locked.id);
    return `<div class="compare-pane">
      <div class="compare-pane-head">
        <h3>Fijada vs última</h3>
        <button class="btn-icon" id="btn-compare-lock" title="Quitar fijado (vuelve a comparar las últimas 2)">🔓 Quitar fijado</button>
      </div>
      ${imageCompareSlider(lockedUrl, 'Fijada', lastUrl, 'Última')}
      <div class="cfg">${esc(locked.summary)} → ${esc(last.summary)}</div>
    </div>`;
  }

  const prev = gens[gens.length - 2];
  const prevUrl = '/api/fusion/explore/image/' + encodeURIComponent(prev.id);
  return `<div class="compare-pane">
    <div class="compare-pane-head">
      <h3>Últimas 2</h3>
      <button class="btn-icon" id="btn-compare-lock" title="Fijar la izquierda y comparar contra las próximas generaciones">🔒 Fijar</button>
    </div>
    ${imageCompareSlider(prevUrl, 'Penúltima', lastUrl, 'Última')}
    <div class="cfg">${esc(prev.summary)} → ${esc(last.summary)}</div>
  </div>`;
}

function renderCompare() {
  const s = exploreSession;
  const ref = s.reference ? genById(s.reference) : null;
  const sel = selectedGen ? genById(selectedGen) : null;
  let html = ref ? (hasBaseImage ? compareSlider(ref) : comparePane('Referencia 📌', ref)) : '';
  html += rightComparePane();
  $('compare-wrap').innerHTML = html ||
    '<span class="dim" style="font-size:12px">Sin generaciones todavía. Configura los bloques y pulsa "Generar variante" — la primera será la referencia.</span>';
  $('explore-actions').style.display = sel ? '' : 'none';
  updateMergeFooter();
  initCompareInteractions();
}

// ── Slider antes/después + zoom/pan estilo Photoshop ────────────────────────

function initCompareInteractions() {
  const wrap = $('compare-wrap');
  wrap.querySelectorAll('.ba-slider').forEach(initBeforeAfter);
  wrap.querySelectorAll('.ba-zoom.single').forEach(el => initZoomPan(el));
  const lockBtn = wrap.querySelector('#btn-compare-lock');
  if (lockBtn) lockBtn.onclick = () => {
    if (lockedCompareGen) {
      lockedCompareGen = null;
    } else {
      const gens = exploreSession.generations;
      const prev = gens[gens.length - 2];
      lockedCompareGen = prev ? prev.id : null;
    }
    renderCompare();
  };
}

function initBeforeAfter(slider) {
  const zoom = slider.querySelector('.ba-zoom');
  const over = slider.querySelector('.ba-over');
  const handle = slider.querySelector('.ba-handle');
  const setPct = (pct) => {
    pct = Math.min(100, Math.max(0, pct));
    over.style.clipPath = `inset(0 0 0 ${pct}%)`;
    handle.style.left = pct + '%';
  };
  setPct(50);
  let dragging = false;
  const move = (e) => {
    if (!dragging) return;
    const r = zoom.getBoundingClientRect();
    setPct(100 * (e.clientX - r.left) / r.width);
  };
  slider.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    dragging = true;
    move(e);
    e.preventDefault();
  });
  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', () => { dragging = false; });
  // solo zoom por rueda (centrado al cursor); el arrastre mueve el divisor,
  // no la imagen.
  initZoomPan(zoom, { pannable: false });
}

function initZoomPan(el, opts = {}) {
  const pannable = opts.pannable !== false;
  const state = { scale: 1, tx: 0, ty: 0 };
  const apply = () => {
    el.style.transformOrigin = '0 0';
    el.style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`;
    el.classList.toggle('zoomed', state.scale > 1);
  };
  el.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = el.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const prev = state.scale;
    const next = Math.min(6, Math.max(1, prev * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    state.tx = mx - (mx - state.tx) * (next / prev);
    state.ty = my - (my - state.ty) * (next / prev);
    state.scale = next;
    if (next === 1) { state.tx = 0; state.ty = 0; }
    apply();
  }, { passive: false });
  el.addEventListener('dblclick', () => {
    state.scale = 1; state.tx = 0; state.ty = 0; apply();
  });
  if (pannable) {
    let panning = false, moved = false, startX, startY, startTx, startTy;
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      panning = true; moved = false;
      startX = e.clientX; startY = e.clientY;
      startTx = state.tx; startTy = state.ty;
      el.classList.add('panning');
      el.setPointerCapture(e.pointerId);
    });
    el.addEventListener('pointermove', (e) => {
      if (!panning) return;
      const dx = e.clientX - startX, dy = e.clientY - startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
      if (state.scale > 1) {
        state.tx = startTx + dx; state.ty = startTy + dy;
        apply();
      }
    });
    const endPan = () => {
      if (!panning) return;
      panning = false;
      el.classList.remove('panning');
      if (!moved && state.scale === 1) {
        const img = el.querySelector('img');
        if (img) window.open(img.src, '_blank');
      }
    };
    el.addEventListener('pointerup', endPan);
    el.addEventListener('pointerleave', endPan);
  }
  apply();
}

function renderHistory() {
  const strip = $('hist-strip');
  strip.innerHTML = '';
  if (!exploreSession.generations.length) {
    strip.innerHTML = '<div class="hist-empty">sin trabajos aún</div>';
    return;
  }
  for (const g of exploreSession.generations) {
    const item = document.createElement('div');
    item.className = 'hist-item' +
      (g.id === selectedGen ? ' selected' : '') +
      (g.id === exploreSession.reference ? ' is-ref' : '');
    item.innerHTML =
      `<img src="/api/fusion/explore/image/${encodeURIComponent(g.id)}" loading="lazy">` +
      `<div class="tag" title="${esc(g.summary)}">${g.id === exploreSession.reference ? '📌 ' : ''}${esc(g.summary)}</div>`;
    item.onclick = () => {
      selectedGen = g.id;
      applyConfig(g.config);   // cargar su config en los switches para iterar
      if (labMode() === 'fuse') applyConfig(g.config2 || {}, 'switch-grid-b');
      renderCompare();
      renderHistory();
    };
    const del = document.createElement('button');
    del.className = 'hist-del';
    del.textContent = '🗑';
    del.title = 'Borrar este trabajo';
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Borrar el trabajo [${g.summary}] (imagen incluida)?`)) return;
      try {
        await api('/explore/generation/' + encodeURIComponent(g.id), { method: 'DELETE' });
        if (selectedGen === g.id) selectedGen = null;
        await refreshExplore();
      } catch (err) { alert('Error: ' + err.message); }
    };
    item.appendChild(del);
    strip.appendChild(item);
  }
}

// Entradas actuales del workbench (compartidas por el lock y la calibración
// pre-lock). Devuelve null (con alert) si falta algo.
function collectSessionBody() {
  const body = {
    arch: currentArch,
    mode: labMode(),
    checkpoint: $('exp-checkpoint').value,
    lora: $('exp-lora').value,
    strength: parseFloat($('exp-strength').value),
    prompt: $('exp-prompt').value,
    negative: '',
    seed: parseInt($('exp-seed').value, 10) || 424242,
    sampling: collectKSampler(),   // KSampler del panel izquierdo (editable pre-lock)
  };
  if (!body.checkpoint) { alert('No hay checkpoint base seleccionado.'); return null; }
  if (!body.lora) { alert('No hay LoRA seleccionado.'); return null; }
  if (body.mode === 'fuse') {
    body.lora2 = $('exp-lora2').value;
    body.strength2 = parseFloat($('exp-strength2').value) || 1.0;
    if (!body.lora2) { alert('Modo fusión: falta el LoRA B.'); return null; }
    if (body.lora2 === body.lora) { alert('Modo fusión: elige dos LoRAs distintos.'); return null; }
  }
  if (!body.prompt.trim()) { alert('Escribe un prompt.'); return null; }
  const s = body.sampling;
  if (s.steps < 1) { alert('KSampler: Steps vacío o inválido.'); return null; }
  if (s.width < 16 || s.height < 16) { alert('KSampler: Ancho/Alto vacíos o inválidos.'); return null; }
  return body;
}

$('btn-explore-start').onclick = async () => {
  const body = collectSessionBody();
  if (!body) return;
  try {
    await api('/explore/session', { method: 'POST', body });
    $('switch-grid').innerHTML = '';   // reconstruir con todo ON
    await refreshExplore();
  } catch (e) { alert('Error: ' + e.message); }
};

// Nueva sesión: descarta la actual (config + trabajos) para reconfigurar
// base/LoRA/prompt. Los trabajos persisten hasta aquí; se pregunta antes.
$('btn-explore-close').onclick = async () => {
  const n = exploreSession ? exploreSession.generations.length : 0;
  if (!confirm(n
    ? `Iniciar una sesión NUEVA descarta la actual y borra sus ${n} trabajo(s) (imágenes incluidas).\nLa config ganadora solo sobrevive si hiciste merge o confirmación con set.\n\n¿Continuar?`
    : '¿Descartar la sesión actual para reconfigurar?')) return;
  try {
    await api('/explore/session', { method: 'DELETE' });
    selectedGen = null;
    lockedCompareGen = null;
    await refreshExplore();
  } catch (e) { alert('Error: ' + e.message); }
};

// Vaciar historial: borra todos los trabajos pero CONSERVA la sesión/config
$('btn-explore-clear').onclick = async () => {
  const n = exploreSession ? exploreSession.generations.length : 0;
  if (!n) return;
  if (!confirm(`Vaciar el historial: se borran los ${n} trabajo(s) de esta sesión (imágenes incluidas).\nLa sesión y su config (base/LoRA/prompt) se conservan para seguir generando. ¿Seguir?`)) return;
  try {
    await api('/explore/clear', { method: 'POST' });
    selectedGen = null;
    lockedCompareGen = null;
    await refreshExplore();
  } catch (e) { alert('Error: ' + e.message); }
};

// Generar: con sesión → trabajo del historial; sin sesión → draft de
// calibración (misma tubería, imagen efímera que no entra al historial)
$('btn-explore-gen').onclick = async () => {
  try {
    let job_id;
    if (exploreSession) {
      const body = { config: collectConfig() };
      if (labMode() === 'fuse') body.config2 = collectConfig('switch-grid-b');
      ({ job_id } = await api('/explore/generate', { method: 'POST', body }));
    } else {
      const body = collectSessionBody();
      if (!body) return;
      ({ job_id } = await api('/explore/draft', { method: 'POST', body }));
    }
    $('btn-explore-gen').disabled = true;
    $('explore-progress').style.display = '';
    exploreJobId = job_id;
    const timer = setInterval(async () => {
      let j;
      try { j = await api('/jobs/' + job_id); } catch (e) { return; }
      const pct = Math.round(100 * j.step / Math.max(j.steps_total, 1));
      $('explore-progress-fill').style.width = pct + '%';
      $('explore-progress-label').textContent = `paso ${j.step}/${j.steps_total} (${pct}%)`;
      if (j.status !== 'running') {
        clearInterval(timer);
        exploreJobId = null;
        $('btn-explore-gen').disabled = false;
        $('explore-progress').style.display = 'none';
        if (j.status === 'error' && j.error !== 'cancelado por el usuario')
          alert('Generación fallida: ' + j.error);
        else if (j.status === 'done' && j.gen) selectedGen = j.gen.id;
        await refreshExplore();
      }
    }, 1000);
  } catch (e) { alert('Error: ' + e.message); }
};

// Failsafe: cancela el job en curso (p.ej. contador clavado en 0/N) y libera
// el candado del backend para poder relanzar. El poll ve el status != running
// y restaura la UI solo.
let exploreJobId = null;
$('btn-explore-cancel').onclick = async () => {
  if (!exploreJobId) return;
  try { await api('/jobs/' + exploreJobId + '/cancel', { method: 'POST' }); }
  catch (e) { alert('Error al cancelar: ' + e.message); }
};

$('btn-explore-ref').onclick = async () => {
  if (!selectedGen) return;
  try {
    await api('/explore/reference', { method: 'POST', body: { gen_id: selectedGen } });
    await refreshExplore();
  } catch (e) { alert('Error: ' + e.message); }
};

// ── Pestañas por fase ───────────────────────────────────────────────────────

let setsCache = [];

function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach(p =>
    p.classList.toggle('active', p.id === 'tab-' + name));
  localStorage.setItem('forge-tab', name);
}

document.querySelectorAll('.tab-btn').forEach(b =>
  b.onclick = () => switchTab(b.dataset.tab));

// Badges de las viejas pestañas: ya no existen en el layout de workbench;
// tolerante a elementos ausentes (se mantiene por si vuelven como indicadores).
function updateTabBadges() {
  const set = (id, txt, title) => {
    const el = $(id); if (!el) return;
    el.textContent = txt; if (title !== undefined) el.title = title;
  };
  const active = setsCache.filter(s => !s.archived);
  const baselineReady = active.some(s => s.locked && s.n_runs > 0);
  set('tb-sets', baselineReady ? '✔' : (active.length ? '…' : ''),
      baselineReady ? 'hay set bloqueado con baseline' : 'falta bloquear un set o generar su baseline');
  set('tb-lab', exploreSession ? '●' : '', exploreSession ? 'sesión de exploración activa' : '');
  const nDeriv = checkpointsCache.filter(c => c.kind === 'derived' && c.present).length;
  set('tb-merge', nDeriv ? String(nDeriv) : '', nDeriv ? `${nDeriv} checkpoint(s) derivado(s)` : '');
  set('tb-compare', runsCache.length ? String(runsCache.length) : '',
      runsCache.length ? `${runsCache.length} run(s) del set seleccionado` : '');
}

// ── KSampler + plegables + popup de chips (rediseño) ────────────────────────
const KS_FIELDS = ['cfg', 'steps', 'sampler', 'scheduler', 'width', 'height'];

function archDefaultsSampling() {
  return ((labArch() || archDef() || {}).sampling_defaults) || {};
}
async function loadSamplingOptions() {
  const opts = await api('/sampling_options');
  fillKsSelect($('ks-sampler'), opts.samplers);
  fillKsSelect($('ks-scheduler'), opts.schedulers);
}
function fillKsSelect(sel, values) {
  const prev = sel.value;
  sel.innerHTML = values.map(v => `<option>${esc(v)}</option>`).join('');
  if (prev) setKsSelect(sel, prev);
}
// Fija el valor aunque no esté en la lista (sesión guardada con un sampler
// que esta instalación ya no ofrece, o fallback con ComfyUI caído).
function setKsSelect(sel, value) {
  if (![...sel.options].some(o => o.value === value)) {
    const o = document.createElement('option');
    o.textContent = value;
    sel.appendChild(o);
  }
  sel.value = value;
}
function populateKSampler(s) {
  s = s || {};
  for (const k of KS_FIELDS) {
    const el = $('ks-' + k);
    if (!el || s[k] === undefined || s[k] === null) continue;
    if (el.tagName === 'SELECT') setKsSelect(el, String(s[k]));
    else el.value = s[k];
  }
  ksPopulated = true;
}
function collectKSampler() {
  return {
    cfg: parseFloat($('ks-cfg').value) || 0,
    steps: parseInt($('ks-steps').value, 10) || 0,
    sampler: $('ks-sampler').value.trim(),
    scheduler: $('ks-scheduler').value.trim(),
    width: parseInt($('ks-width').value, 10) || 0,
    height: parseInt($('ks-height').value, 10) || 0,
  };
}
function setInputsLocked(locked) {
  for (const id of ['exp-strength', 'exp-strength2', 'exp-seed', 'exp-from-set',
                    'exp-prompt', ...KS_FIELDS.map(k => 'ks-' + k)]) {
    const el = $(id); if (el) el.disabled = locked;
  }
  $('btn-pick-exp-checkpoint').disabled = locked;
  $('btn-pick-exp-lora').disabled = locked;
  $('btn-pick-exp-lora2').disabled = locked;
  const lb = $('lock-badge'); if (lb) lb.style.display = locked ? '' : 'none';
}

$('ks-toggle').onclick = () => $('ksampler-panel').classList.toggle('collapsed');
$('btn-inputs-toggle').onclick = () => {
  const collapsed = $('inputs-bar').classList.toggle('collapsed');
  $('btn-inputs-toggle').textContent = collapsed ? '▸ Ver base/LoRA' : '▾ Ocultar base/LoRA';
};
document.querySelectorAll('.fold-head').forEach(h =>
  h.onclick = () => $(h.dataset.fold).classList.toggle('open'));

$('pop-range').oninput = () => {
  const v = parseFloat($('pop-range').value);
  $('pop-num').value = v; $('pop-on').checked = true; popApply(v, true);
};
$('pop-num').oninput = () => {
  const v = parseFloat($('pop-num').value) || 0;
  $('pop-range').value = v; $('pop-on').checked = true; popApply(v, true);
};
$('pop-on').onchange = () => popApply(parseFloat($('pop-num').value) || 0, $('pop-on').checked);
document.addEventListener('mousedown', (e) => {
  if (!$('chip-pop').classList.contains('open')) return;
  if ($('chip-pop').contains(e.target) || e.target.closest('.chip')) return;
  closeChipPop();
});

// ── Init ────────────────────────────────────────────────────────────────────

// Abrir la carpeta de los merges generados (mode-aware: derived checkpoints o
// LoRAs fusionados). El botón vive dentro del <summary>, así que frenamos la
// propagación para no colapsar/expandir el fold al pulsarlo.
$('btn-open-output').onclick = async (e) => {
  e.preventDefault();
  e.stopPropagation();
  const btn = $('btn-open-output');
  btn.disabled = true;
  try {
    await api(`/open-output-folder?arch=${encodeURIComponent(currentArch)}` +
              `&mode=${encodeURIComponent(labMode())}`, { method: 'POST' });
  } catch (err) {
    alert('No se pudo abrir la carpeta: ' + err.message);
  } finally {
    btn.disabled = false;
  }
};

async function init() {
  await loadArchitectures();   // debe ir primero: fija currentArch y el selector
  // allSettled, no all: un fallo al pintar un panel secundario (checkpoints,
  // loras, status, baterías) NO debe abortar init y dejar sin ejecutar
  // refreshExplore(), que es quien restaura la sesión y puebla el KSampler
  // (bug 2026-07-17: c.blocks.join reventaba y el KSampler quedaba vacío).
  const res = await Promise.allSettled([loadStatus(), loadSamplingOptions(),
    refreshBatteries(false), refreshCheckpoints(), refreshLoras()]);
  for (const r of res)
    if (r.status === 'rejected') console.error('init: panel secundario falló:', r.reason);
  await refreshExplore();
}
init();
