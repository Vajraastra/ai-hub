// Forge Lab — frontend Fase 2: sets de validación fijos + grid de runs.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mark = (ok) => ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>';

const CATEGORIES = ['target', 'general', 'stress'];
const CAT_LABELS = {
  target:  'Target — dominio objetivo (render 3D multiestilo)',
  general: 'General — fuera de dominio (no romper)',
  stress:  'Stress — manos, multi-personaje, texto, poses',
};
const SAMPLING_FIELDS = [
  ['cfg', 'CFG'], ['steps', 'Steps'], ['sampler', 'Sampler'],
  ['scheduler', 'Scheduler'], ['width', 'Ancho'], ['height', 'Alto'],
];

let currentSet = null;   // dict completo del set seleccionado
let runsCache  = [];     // runs del set seleccionado
let selectedRuns = new Set();
let jobTimer = null;
let showArchived = false;

async function api(path, opts = {}) {
  if (opts.body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch('/api/forge' + path, opts);
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

async function loadStatus() {
  try {
    const [s, mf] = await Promise.all([api('/status'), api('/model-files')]);
    $('arch-label').textContent = s.arch;
    const pill = $('comfy-pill');
    pill.textContent = 'ComfyUI: ' + (s.comfyui ? 'online' : 'offline');
    pill.className = 'badge ' + (s.comfyui ? 'draft' : '');
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
    await api('/model-files', { method: 'PUT', body: { files } });
    await loadStatus();
    await refreshCheckpoints();   // la base oficial puede haber cambiado
  } catch (e) { alert('Error guardando paths: ' + e.message); }
}

$('btn-status-toggle').onclick = () => {
  const el = $('status-card');
  el.style.display = el.style.display === 'none' ? '' : 'none';
};

// ── Lista de sets ───────────────────────────────────────────────────────────

async function refreshSets(keepSelection = true) {
  const data = await api('/sets');
  setsCache = data.sets;
  // selector de la pestaña Comparar
  const cs = $('compare-set');
  cs.innerHTML = '<option value="">— elige un set —</option>';
  for (const s of data.sets.filter(s => !s.archived))
    cs.add(new Option(`${s.name} (${s.n_runs} runs)`, s.name));
  if (currentSet) cs.value = currentSet.name;
  updateTabBadges();
  const el = $('sets-list');
  const visible = data.sets.filter(s => showArchived || !s.archived);
  const nArchived = data.sets.length - data.sets.filter(s => !s.archived).length;
  $('btn-show-archived').textContent = showArchived
    ? 'ocultar archivados' : `mostrar archivados (${nArchived})`;
  $('btn-show-archived').style.display = nArchived || showArchived ? '' : 'none';
  if (!visible.length) {
    el.innerHTML = '<span class="dim">No hay sets todavía. Crea uno — arranca como borrador editable y se bloquea cuando estés conforme.</span>';
    return;
  }
  el.innerHTML = '';
  for (const s of visible) {
    const row = document.createElement('div');
    row.className = 'set-row' + (currentSet && s.name === currentSet.name ? ' active' : '');
    const cats = CATEGORIES.map(c => `${c[0].toUpperCase()}:${s.categories[c]}`).join(' ');
    row.innerHTML =
      `<span class="mono">${esc(s.name)}</span>` +
      `<span class="badge ${s.locked ? 'locked' : 'draft'}">${s.locked ? '🔒 bloqueado' : 'borrador'}</span>` +
      (s.archived ? '<span class="badge">📦 archivado</span>' : '') +
      `<span class="dim">${esc(s.arch)}</span>` +
      `<span class="dim">${s.n_prompts} prompts (${cats})</span>` +
      `<span style="flex:1"></span>` +
      `<span class="dim">${s.n_runs} runs</span>`;
    row.onclick = () => selectSet(s.name);
    el.appendChild(row);
  }
  if (keepSelection && currentSet) selectSet(currentSet.name, false);
}

$('btn-show-archived').onclick = () => {
  showArchived = !showArchived;
  refreshSets();
};

$('btn-new-set').onclick = async () => {
  const name = prompt('Nombre del nuevo set (minúsculas, dígitos, guiones):');
  if (!name) return;
  try {
    await api('/sets', { method: 'POST', body: { name: name.trim(), starter: true } });
    await refreshSets(false);
    selectSet(name.trim());
  } catch (e) { alert('Error: ' + e.message); }
};

// ── Editor ──────────────────────────────────────────────────────────────────

async function selectSet(name, refetch = true) {
  if (refetch) {
    currentSet = await api('/sets/' + encodeURIComponent(name));
    selectedRuns = new Set();
  }
  document.querySelectorAll('.set-row').forEach(r => {
    r.classList.toggle('active', r.querySelector('.mono').textContent === name);
  });
  renderEditor();
  $('compare-set').value = name;
  // ofrecer los prompts del set en el setup de exploración
  const fs = $('exp-from-set');
  fs.innerHTML = '<option value="">— libre —</option>';
  for (const p of currentSet.prompts)
    fs.add(new Option(`${currentSet.name} / ${p.id}`, p.id));
  await refreshRuns();
}

function renderEditor() {
  const s = currentSet;
  $('editor-card').style.display = '';
  $('editor-title').textContent = s.name;
  $('editor-badge').innerHTML = s.locked_at
    ? `<span class="badge locked">🔒 bloqueado ${esc(s.locked_at.slice(0, 10))}</span>`
    : '<span class="badge draft">borrador — editable</span>';

  const sg = $('sampling-grid');
  sg.innerHTML = '';
  for (const [key, label] of SAMPLING_FIELDS) {
    const lab = document.createElement('label');
    lab.textContent = label;
    const inp = document.createElement('input');
    inp.dataset.key = key;
    inp.value = s.sampling[key];
    inp.disabled = !!s.locked_at;
    lab.appendChild(inp);
    sg.appendChild(lab);
  }

  const pe = $('prompts-editor');
  pe.innerHTML = '';
  for (const cat of CATEGORIES) {
    const title = document.createElement('div');
    title.className = 'cat-title';
    title.textContent = CAT_LABELS[cat];
    pe.appendChild(title);
    for (const p of s.prompts.filter(p => p.category === cat))
      pe.appendChild(promptRow(p, !!s.locked_at));
  }

  const locked = !!s.locked_at;
  $('btn-save-set').style.display = locked ? 'none' : '';
  $('btn-lock-set').style.display = locked ? 'none' : '';
  $('btn-add-prompt').style.display = locked ? 'none' : '';
  $('btn-delete-set').style.display = locked ? 'none' : '';
  // archivar: solo tiene sentido en sets bloqueados (los borradores se eliminan)
  $('btn-archive-set').style.display = locked ? '' : 'none';
  $('btn-archive-set').textContent = s.archived_at ? 'Desarchivar' : 'Archivar';
}

$('btn-archive-set').onclick = async () => {
  const action = currentSet.archived_at ? 'unarchive' : 'archive';
  try {
    currentSet = await api(`/sets/${encodeURIComponent(currentSet.name)}/${action}`,
                           { method: 'POST' });
    renderEditor();
    await refreshSets();
  } catch (e) { alert('Error: ' + e.message); }
};

function promptRow(p, locked) {
  const row = document.createElement('div');
  row.className = 'prompt-row';

  const id = document.createElement('input');
  id.value = p.id; id.placeholder = 'id'; id.dataset.f = 'id';

  const seed = document.createElement('input');
  seed.value = p.seed; seed.placeholder = 'seed'; seed.dataset.f = 'seed';

  const txt = document.createElement('textarea');
  txt.value = p.text; txt.placeholder = 'prompt'; txt.dataset.f = 'text';

  const del = document.createElement('button');
  del.className = 'prompt-del'; del.textContent = '✕'; del.title = 'Quitar prompt';
  del.onclick = () => row.remove();

  row.dataset.category = p.category;
  row.dataset.negative = p.negative || '';
  for (const el of [id, seed, txt]) el.disabled = locked;
  if (locked) del.style.display = 'none';

  row.append(id, seed, txt, del);
  return row;
}

function collectEditor() {
  const sampling = {};
  for (const inp of $('sampling-grid').querySelectorAll('input'))
    sampling[inp.dataset.key] = inp.value.trim();
  const prompts = [];
  for (const row of $('prompts-editor').querySelectorAll('.prompt-row')) {
    const get = (f) => row.querySelector(`[data-f="${f}"]`).value;
    prompts.push({
      id: get('id').trim(), seed: get('seed').trim(), text: get('text'),
      category: row.dataset.category, negative: row.dataset.negative,
    });
  }
  return { sampling, prompts };
}

$('btn-add-prompt').onclick = () => {
  const cat = prompt('Categoría (target / general / stress):', 'target');
  if (!CATEGORIES.includes(cat)) { if (cat) alert('Categoría inválida'); return; }
  const rows = [...$('prompts-editor').querySelectorAll('.prompt-row')]
    .filter(r => r.dataset.category === cat);
  const n = rows.length + 1;
  const row = promptRow({
    id: `${cat}-${String(n).padStart(2, '0')}`, category: cat,
    seed: Math.floor(Math.random() * 1e9), text: '', negative: '',
  }, false);
  if (rows.length) rows[rows.length - 1].after(row);
  else $('prompts-editor').appendChild(row);
};

$('btn-save-set').onclick = async () => {
  try {
    currentSet = await api('/sets/' + encodeURIComponent(currentSet.name),
                           { method: 'PUT', body: collectEditor() });
    renderEditor();
    await refreshSets();
  } catch (e) { alert('Error guardando: ' + e.message); }
};

$('btn-lock-set').onclick = async () => {
  if (!confirm(`Bloquear "${currentSet.name}"?\n\nDespués de esto el set es INMUTABLE para siempre (solo clonable). Asegúrate antes: es la vara de medir de todos los merges.`)) return;
  try {
    const body = collectEditor();
    await api('/sets/' + encodeURIComponent(currentSet.name), { method: 'PUT', body });
    currentSet = await api('/sets/' + encodeURIComponent(currentSet.name) + '/lock',
                           { method: 'POST' });
    renderEditor();
    await refreshSets();
  } catch (e) { alert('Error bloqueando: ' + e.message); }
};

$('btn-clone-set').onclick = async () => {
  const name = prompt('Nombre del clon (nuevo borrador editable):',
                      currentSet.name + '-v2');
  if (!name) return;
  try {
    await api('/sets/' + encodeURIComponent(currentSet.name) + '/clone',
              { method: 'POST', body: { new_name: name.trim() } });
    await refreshSets(false);
    selectSet(name.trim());
  } catch (e) { alert('Error clonando: ' + e.message); }
};

$('btn-delete-set').onclick = async () => {
  const nRuns = runsCache.length;
  if (nRuns) {
    // borrar un borrador arrastra sus runs: exigir el nombre, no un simple OK
    const typed = prompt(
      `CUIDADO: esto elimina el set "${currentSet.name}" Y sus ${nRuns} run(s) con todas sus imágenes.\n` +
      `Si solo quieres borrar un run, usa el 🗑 de su fila.\n\n` +
      `Para confirmar, escribe el nombre exacto del set:`);
    if (typed !== currentSet.name) {
      if (typed !== null) alert('Nombre no coincide — no se borra nada.');
      return;
    }
  } else if (!confirm(`Eliminar el borrador "${currentSet.name}"?`)) return;
  try {
    await api('/sets/' + encodeURIComponent(currentSet.name), { method: 'DELETE' });
    currentSet = null;
    $('editor-card').style.display = 'none';
    $('runs-card').style.display = 'none';
    await refreshSets(false);
  } catch (e) { alert('Error eliminando: ' + e.message); }
};

// ── Checkpoints + merge (Fase 3) ────────────────────────────────────────────

let checkpointsCache = [];

const fmtGB = (b) => b == null ? '' : (b / 1024 ** 3).toFixed(2) + ' GB';

async function refreshCheckpoints() {
  const data = await api('/checkpoints');
  checkpointsCache = data.checkpoints;
  const el = $('ckpt-list');
  el.innerHTML = '';
  for (const c of checkpointsCache) {
    const row = document.createElement('div');
    row.className = 'ckpt-row';
    const lineage = c.kind === 'derived'
      ? `${esc(c.base.name)} ← ${esc(c.lora.file)} @ ${c.lora.strength}` +
        (c.blocks ? ` · bloques: ${esc(c.blocks.join(', '))}` : '') +
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
  // selects: base del merge + modelo del run
  const baseSel = $('merge-base');
  baseSel.innerHTML = '';
  for (const c of checkpointsCache.filter(c => c.present))
    baseSel.add(new Option(`${c.name}${c.kind === 'official' ? ' (oficial)' : ''}`, c.name));
  const runSel = $('run-model');
  runSel.innerHTML = '';
  for (const c of checkpointsCache.filter(c => c.present))
    runSel.add(new Option(`contra: ${c.name}`, c.kind === 'official' ? '' : c.unet_name));
  const expSel = $('exp-checkpoint');
  expSel.innerHTML = '';
  for (const c of checkpointsCache.filter(c => c.present))
    expSel.add(new Option(`${c.name}${c.kind === 'official' ? ' (oficial)' : ''}`, c.name));
  updateTabBadges();
}

// ── Picker visual de LoRAs (thumbnails estilo Vault) ───────────────────────
// Solo se listan LoRAs de la arquitectura activa (header del safetensors +
// clasificación del Model Vault); sin preview → placeholder genérico.

let lorasCache = [];        // solo arch_match
let loraPickerTarget = null;

const loraByFile = (f) => lorasCache.find(l => l.file === f);
const loraThumb = (l, cls) => l.has_preview
  ? `<img class="${cls}" loading="lazy" src="/api/forge/lora-preview?file=${encodeURIComponent(l.file)}">`
  : (cls === 'lp-thumb' ? '<div class="lp-thumb-ph">🧬</div>' : '<span class="ph">🧬</span>');

async function refreshLoras() {
  const data = await api('/loras');
  lorasCache = data.loras.filter(l => l.arch_match);
  for (const id of ['merge-lora', 'exp-lora']) {
    if ($(id).value && !loraByFile($(id).value)) $(id).value = '';
    renderLoraPickBtn(id);
  }
}

function renderLoraPickBtn(id) {
  const btn = $('btn-pick-' + id);
  const l = loraByFile($(id).value);
  btn.innerHTML = l
    ? loraThumb(l, '') + `<span class="nm" title="${esc(l.file)}">${esc(l.name)}</span>`
    : '<span class="ph">🧬</span><span class="nm dim">— elegir LoRA —</span>';
}

function openLoraPicker(targetId) {
  loraPickerTarget = targetId;
  $('lp-search').value = '';
  renderLoraGrid();
  $('lora-picker-overlay').classList.add('open');
  $('lp-search').focus();
}

function closeLoraPicker() { $('lora-picker-overlay').classList.remove('open'); }

function renderLoraGrid() {
  const q = $('lp-search').value.trim().toLowerCase();
  const cur = loraPickerTarget ? $(loraPickerTarget).value : '';
  const list = lorasCache.filter(l => !q ||
    l.name.toLowerCase().includes(q) ||
    (l.subfolder || '').toLowerCase().includes(q));
  $('lp-count').textContent = `${list.length} compatibles`;
  const grid = $('lp-grid');
  grid.innerHTML = '';
  if (!list.length) {
    grid.innerHTML = `<span class="dim" style="font-size:12px">No hay LoRAs de esta arquitectura${q ? ' que casen con la búsqueda' : ' en el almacén'}.</span>`;
    return;
  }
  for (const l of list) {
    const card = document.createElement('div');
    card.className = 'lp-card' + (l.file === cur ? ' selected' : '');
    card.innerHTML = loraThumb(l, 'lp-thumb') +
      `<div class="lp-body"><div class="lp-name">${esc(l.name)}</div>` +
      `<div class="lp-meta">${esc(l.subfolder || '')}${l.rank ? (l.subfolder ? ' · ' : '') + 'r' + l.rank : ''}</div></div>`;
    card.title = l.file;
    card.onclick = () => {
      $(loraPickerTarget).value = l.file;
      renderLoraPickBtn(loraPickerTarget);
      closeLoraPicker();
    };
    grid.appendChild(card);
  }
}

$('lp-close').onclick = closeLoraPicker;
$('lora-picker-overlay').onclick = (e) => {
  if (e.target.id === 'lora-picker-overlay') closeLoraPicker();
};
$('lp-search').oninput = renderLoraGrid;
$('btn-pick-merge-lora').onclick = () => openLoraPicker('merge-lora');
$('btn-pick-exp-lora').onclick = () => openLoraPicker('exp-lora');

$('btn-merge').onclick = async () => {
  const body = {
    base: $('merge-base').value,
    lora: $('merge-lora').value,
    strength: parseFloat($('merge-strength').value),
    name: $('merge-name').value.trim(),
    label: $('merge-label').value.trim(),
  };
  if (!body.lora) return alert('No hay LoRA seleccionado.');
  if (!body.name) return alert('Pon nombre al checkpoint derivado (minúsculas, dígitos, guiones).');
  if (!(body.strength > 0)) return alert('Strength inválido.');
  if (!confirm(`Merge completo:\n\n  ${body.base}  ←  ${body.lora}  @ ${body.strength}\n  →  forge_lab/${body.name}.safetensors (~12 GB)\n\nCorre en CPU/RAM (unos minutos). ¿Adelante?`)) return;
  try {
    const { job_id } = await api('/merge', { method: 'POST', body });
    $('btn-merge').disabled = true;
    $('merge-progress').style.display = '';
    pollMergeJob(job_id);
  } catch (e) { alert('Error lanzando merge: ' + e.message); }
};

const MERGE_PHASES = { map: 'mapeando LoRA', merge: 'mergeando tensores',
                       write: 'escribiendo checkpoint', hash: 'SHA256 de verificación' };

function pollMergeJob(jobId) {
  const timer = setInterval(async () => {
    let j;
    try { j = await api('/jobs/' + jobId); } catch (e) { return; }
    const pct = j.total ? Math.round(100 * j.done / j.total) : 0;
    $('merge-progress-fill').style.width = (j.phase === 'merge' ? pct : (j.status === 'running' ? 100 : pct)) + '%';
    $('merge-progress-label').textContent =
      `${MERGE_PHASES[j.phase] || j.phase || 'preparando'} — ${j.done}/${j.total}`;
    if (j.status !== 'running') {
      clearInterval(timer);
      $('btn-merge').disabled = false;
      $('merge-progress').style.display = 'none';
      if (j.status === 'error') alert('Merge fallido: ' + j.error);
      else {
        $('merge-name').value = ''; $('merge-label').value = '';
        alert(`Checkpoint "${j.checkpoint.name}" creado (${fmtGB(j.checkpoint.size_bytes)}).\nAhora regenera el set contra él para compararlo con el baseline.`);
      }
      await refreshCheckpoints();
    }
  }, 1500);
}

// ── Laboratorio de bloques (Fase 4) ─────────────────────────────────────────

const N_LAYERS = 30;
let exploreSession = null;
let selectedGen = null;

async function refreshExplore() {
  const data = await api('/explore/session');
  exploreSession = data.session;
  updateTabBadges();
  const active = !!exploreSession;
  $('explore-setup').style.display = active ? 'none' : '';
  $('explore-session').style.display = active ? '' : 'none';
  $('btn-explore-close').style.display = active ? '' : 'none';
  if (!active) { selectedGen = null; return; }

  const s = exploreSession;
  $('explore-info').innerHTML =
    `<span class="mono">${esc(s.checkpoint)}</span> + ` +
    `<span class="mono">${esc(s.lora.split('/').pop())}</span> @ ${s.strength}` +
    ` <span class="dim">seed ${s.prompt.seed} · ${s.sampling.steps} steps · cfg ${s.sampling.cfg}</span>` +
    ` <span class="dim" title="${esc(s.prompt.text)}">"${esc(s.prompt.text.slice(0, 90))}${s.prompt.text.length > 90 ? '…' : ''}"</span>`;

  if (!$('switch-grid').children.length) buildSwitchGrid();
  if (selectedGen && !s.generations.find(g => g.id === selectedGen))
    selectedGen = null;
  if (!selectedGen && s.generations.length)
    selectedGen = s.generations[s.generations.length - 1].id;
  renderCompare();
  renderHistory();
}

function switchCell(label, key) {
  const cell = document.createElement('div');
  cell.className = 'switch-cell';
  cell.dataset.key = key;
  const cb = document.createElement('input');
  cb.type = 'checkbox'; cb.checked = true;
  const lbl = document.createElement('span');
  lbl.className = 'lbl'; lbl.textContent = label;
  const dose = document.createElement('input');
  dose.type = 'number'; dose.min = '0'; dose.max = '2'; dose.step = '0.05';
  dose.value = '1';
  cb.onchange = () => cell.classList.toggle('off', !cb.checked);
  cell.append(cb, lbl, dose);
  return cell;
}

function buildSwitchGrid() {
  const grid = $('switch-grid');
  grid.innerHTML = '';
  for (let i = 0; i < N_LAYERS; i++) grid.appendChild(switchCell(String(i), String(i)));
  grid.appendChild(switchCell('refiners', 'other'));
}

function collectConfig() {
  const layers = {};
  let other = 0;
  for (const cell of $('switch-grid').children) {
    const on = cell.querySelector('input[type=checkbox]').checked;
    const d = on ? parseFloat(cell.querySelector('input[type=number]').value) || 0 : 0;
    if (cell.dataset.key === 'other') other = d;
    else layers[cell.dataset.key] = d;
  }
  return { layers, other };
}

function applyConfig(cfg) {
  for (const cell of $('switch-grid').children) {
    const d = cell.dataset.key === 'other' ? cfg.other : (cfg.layers[cell.dataset.key] ?? 0);
    const cb = cell.querySelector('input[type=checkbox]');
    cb.checked = d > 0;
    cell.querySelector('input[type=number]').value = d > 0 ? d : 1;
    cell.classList.toggle('off', !(d > 0));
  }
}

document.querySelectorAll('#explore-session .preset-row button').forEach(btn => {
  btn.onclick = () => {
    for (const cell of $('switch-grid').children) {
      const key = cell.dataset.key;
      const i = key === 'other' ? -1 : parseInt(key, 10);
      const cb = cell.querySelector('input[type=checkbox]');
      let on;
      switch (btn.dataset.preset) {
        case 'all-on':  on = true; break;
        case 'all-off': on = false; break;
        case 'early':   on = i >= 0 && i <= 9; break;
        case 'mid':     on = i >= 10 && i <= 19; break;
        case 'late':    on = i >= 20 && i <= 29; break;
        case 'invert':  on = !cb.checked; break;
      }
      cb.checked = on;
      cell.classList.toggle('off', !on);
    }
  };
});

function genById(id) { return exploreSession.generations.find(g => g.id === id); }

function comparePane(title, gen, extraClass = '') {
  if (!gen) return '';
  const url = '/api/forge/explore/image/' + encodeURIComponent(gen.id);
  return `<div class="compare-pane ${extraClass}">
    <h3>${title}</h3>
    <img src="${url}" onclick="window.open('${url}','_blank')">
    <div class="cfg">${esc(gen.summary)} · ${Math.round(gen.seconds)}s</div>
  </div>`;
}

function renderCompare() {
  const s = exploreSession;
  const ref = s.reference ? genById(s.reference) : null;
  const sel = selectedGen ? genById(selectedGen) : null;
  let html = comparePane('Referencia 📌', ref);
  if (sel && (!ref || sel.id !== ref.id)) html += comparePane('Seleccionada', sel);
  $('compare-wrap').innerHTML = html ||
    '<span class="dim" style="font-size:12px">Sin generaciones todavía. Configura los bloques y pulsa "Generar variante" — la primera será la referencia.</span>';
  $('explore-actions').style.display = sel ? '' : 'none';
}

function renderHistory() {
  const strip = $('hist-strip');
  strip.innerHTML = '';
  for (const g of exploreSession.generations) {
    const item = document.createElement('div');
    item.className = 'hist-item' +
      (g.id === selectedGen ? ' selected' : '') +
      (g.id === exploreSession.reference ? ' is-ref' : '');
    item.innerHTML =
      `<img src="/api/forge/explore/image/${encodeURIComponent(g.id)}" loading="lazy">` +
      `<div class="tag" title="${esc(g.summary)}">${g.id === exploreSession.reference ? '📌 ' : ''}${esc(g.summary)}</div>`;
    item.onclick = () => {
      selectedGen = g.id;
      applyConfig(g.config);   // cargar su config en los switches para iterar
      renderCompare();
      renderHistory();
    };
    strip.appendChild(item);
  }
}

$('btn-explore-start').onclick = async () => {
  const body = {
    checkpoint: $('exp-checkpoint').value,
    lora: $('exp-lora').value,
    strength: parseFloat($('exp-strength').value),
    prompt: $('exp-prompt').value,
    negative: '',
    seed: parseInt($('exp-seed').value, 10) || 424242,
  };
  if (!body.lora) return alert('No hay LoRA seleccionado.');
  if (!body.prompt.trim()) return alert('Escribe un prompt (o elige uno del set).');
  try {
    await api('/explore/session', { method: 'POST', body });
    $('switch-grid').innerHTML = '';   // reconstruir con todo ON
    await refreshExplore();
  } catch (e) { alert('Error: ' + e.message); }
};

$('btn-explore-close').onclick = async () => {
  const n = exploreSession ? exploreSession.generations.length : 0;
  if (!confirm(`Cerrar la sesión de exploración?\nSe borran sus ${n} imagen(es) temporales. La config ganadora solo sobrevive si hiciste merge o confirmación con set.`)) return;
  try {
    await api('/explore/session', { method: 'DELETE' });
    await refreshExplore();
  } catch (e) { alert('Error: ' + e.message); }
};

$('exp-from-set').onchange = () => {
  const pid = $('exp-from-set').value;
  if (!pid || !currentSet) return;
  const p = currentSet.prompts.find(x => x.id === pid);
  if (p) { $('exp-prompt').value = p.text; $('exp-seed').value = p.seed; }
};

$('btn-explore-gen').onclick = async () => {
  try {
    const { job_id } = await api('/explore/generate',
                                 { method: 'POST', body: { config: collectConfig() } });
    $('btn-explore-gen').disabled = true;
    $('explore-progress').style.display = '';
    const timer = setInterval(async () => {
      let j;
      try { j = await api('/jobs/' + job_id); } catch (e) { return; }
      const pct = Math.round(100 * j.step / Math.max(j.steps_total, 1));
      $('explore-progress-fill').style.width = pct + '%';
      $('explore-progress-label').textContent = `paso ${j.step}/${j.steps_total} (${pct}%)`;
      if (j.status !== 'running') {
        clearInterval(timer);
        $('btn-explore-gen').disabled = false;
        $('explore-progress').style.display = 'none';
        if (j.status === 'error') alert('Generación fallida: ' + j.error);
        else selectedGen = j.gen.id;
        await refreshExplore();
      }
    }, 1000);
  } catch (e) { alert('Error: ' + e.message); }
};

$('btn-explore-ref').onclick = async () => {
  if (!selectedGen) return;
  try {
    await api('/explore/reference', { method: 'POST', body: { gen_id: selectedGen } });
    await refreshExplore();
  } catch (e) { alert('Error: ' + e.message); }
};

$('btn-explore-confirm').onclick = async () => {
  if (!selectedGen) return;
  const setName = currentSet ? currentSet.name : 'zimage-base';
  const g = genById(selectedGen);
  const label = prompt(
    `Doble confirmación: regenerar el set "${setName}" completo en runtime con la config\n[${g.summary}]\n\nEl run SÍ se guarda (evidencia). Etiqueta del run:`,
    `explore ${g.summary.slice(0, 40)}`);
  if (label === null) return;
  try {
    const { job_id } = await api('/explore/confirm',
      { method: 'POST', body: { set_name: setName, gen_id: selectedGen, label } });
    $('btn-run-set').disabled = true;
    $('run-progress').style.display = '';
    pollJob(job_id);
  } catch (e) { alert('Error: ' + e.message); }
};

$('btn-explore-merge').onclick = async () => {
  if (!selectedGen) return;
  const g = genById(selectedGen);
  const name = prompt(
    `Merge final con la config [${g.summary}]\n(misma matemática que el preview — dosis lineal)\n\nNombre del checkpoint derivado (minúsculas, dígitos, guiones):`);
  if (!name) return;
  const label = prompt('Etiqueta:', g.summary) || '';
  try {
    const { job_id } = await api('/explore/merge',
      { method: 'POST', body: { gen_id: selectedGen, name: name.trim(), label } });
    $('btn-merge').disabled = true;
    $('merge-progress').style.display = '';
    pollMergeJob(job_id);
  } catch (e) { alert('Error lanzando merge: ' + e.message); }
};

// ── Regeneración ────────────────────────────────────────────────────────────

$('btn-run-set').onclick = async () => {
  const draft = !currentSet.locked_at;
  const model = $('run-model').value || null;
  const against = model ? model.split(/[\\/]/).pop().replace(/\.safetensors$/, '')
                        : 'base oficial';
  const label = prompt(
    (draft ? 'AVISO: el set es un BORRADOR — el run se marcará como prueba de calibración, no sirve para comparar merges.\n\n' : '') +
    `Regenerar contra: ${against}\n\nEtiqueta del run (ej. "baseline de-turbo"):`,
    model ? against : '');
  if (label === null) return;
  try {
    const { job_id } = await api('/sets/' + encodeURIComponent(currentSet.name) + '/run',
                                 { method: 'POST', body: { label, model } });
    $('btn-run-set').disabled = true;
    $('run-progress').style.display = '';
    pollJob(job_id);
  } catch (e) { alert('Error lanzando run: ' + e.message); }
};

function pollJob(jobId) {
  clearInterval(jobTimer);
  jobTimer = setInterval(async () => {
    let j;
    try { j = await api('/jobs/' + jobId); }
    catch (e) { return; }
    const done = j.prompt_index + (j.step / Math.max(j.steps_total, 1));
    const pct = Math.round(100 * done / Math.max(j.total, 1));
    $('run-progress-fill').style.width = pct + '%';
    $('run-progress-label').textContent =
      `[${j.prompt_index + 1}/${j.total}] ${j.prompt_id}  —  paso ${j.step}/${j.steps_total}  (${pct}%)`;
    if (j.status !== 'running') {
      clearInterval(jobTimer);
      $('btn-run-set').disabled = false;
      $('run-progress').style.display = 'none';
      if (j.status === 'error') alert('Run fallido: ' + j.error);
      else selectedRuns.add(j.run_id);
      await refreshSets();
      await refreshRuns();
    }
  }, 1500);
}

// ── Runs + grid comparativo ─────────────────────────────────────────────────

async function refreshRuns() {
  if (!currentSet) return;
  const data = await api('/sets/' + encodeURIComponent(currentSet.name) + '/runs');
  runsCache = data.runs;
  updateTabBadges();
  $('runs-card').style.display = '';
  const el = $('runs-list');
  if (!runsCache.length) {
    el.innerHTML = '<span class="dim" style="font-size:12px">Sin runs todavía. "Regenerar set" genera todas las imágenes contra el modelo actual.</span>';
    $('grid-wrap').innerHTML = '';
    return;
  }
  el.innerHTML = '';
  for (const r of runsCache) {
    const row = document.createElement('div');
    row.className = 'run-row';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selectedRuns.has(r.run_id);
    cb.onchange = () => {
      cb.checked ? selectedRuns.add(r.run_id) : selectedRuns.delete(r.run_id);
      renderGrid();
    };
    row.appendChild(cb);
    const fpOk = r.fingerprint === currentSet.fingerprint;
    row.insertAdjacentHTML('beforeend',
      `<span class="mono">${esc(r.run_id)}</span>` +
      (r.draft ? '<span class="badge">calibración</span>'
               : `<span class="badge locked" title="fingerprint ${fpOk ? 'coincide con el set' : 'NO coincide — set distinto'}">${fpOk ? 'válido' : '⚠ otro fingerprint'}</span>`) +
      `<span>${esc(r.label || '')}</span>` +
      `<span class="dim mono">${esc(r.model)}</span>` +
      `<span style="flex:1"></span>` +
      `<span class="dim">${r.results.length} imgs · ${Math.round(r.seconds)}s</span>`);
    const del = document.createElement('button');
    del.className = 'prompt-del';
    del.textContent = '🗑';
    del.title = 'Borrar este run (imágenes incluidas)';
    del.onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm(`Borrar el run ${r.run_id} (${r.results.length} imágenes)?\nEl set no se toca; solo se libera el espacio de este run.`)) return;
      try {
        await api(`/runs/${encodeURIComponent(currentSet.name)}/${encodeURIComponent(r.run_id)}`,
                  { method: 'DELETE' });
        selectedRuns.delete(r.run_id);
        await refreshSets();
        await refreshRuns();
      } catch (e) { alert('Error borrando run: ' + e.message); }
    };
    row.appendChild(del);
    el.appendChild(row);
  }
  renderGrid();
}

function renderGrid() {
  const wrap = $('grid-wrap');
  const runs = runsCache.filter(r => selectedRuns.has(r.run_id));
  if (!runs.length) { wrap.innerHTML = ''; return; }

  // filas = unión de prompt_ids en orden del set (o del run si el set cambió)
  const ids = currentSet.prompts.map(p => p.id);
  for (const r of runs)
    for (const res of r.results)
      if (!ids.includes(res.prompt_id)) ids.push(res.prompt_id);

  let html = '<table class="result-grid"><tr><th class="rowh">prompt</th>';
  for (const r of runs)
    html += `<th><span class="mono">${esc(r.run_id)}</span><br>${esc(r.label || r.model)}</th>`;
  html += '</tr>';
  for (const pid of ids) {
    const p = currentSet.prompts.find(x => x.id === pid);
    html += `<tr><th class="rowh"><span class="mono">${esc(pid)}</span>` +
            (p ? `<br><span class="dim">seed ${p.seed}</span><br><span class="dim" style="font-weight:400">${esc(p.text.slice(0, 130))}${p.text.length > 130 ? '…' : ''}</span>` : '') +
            '</th>';
    for (const r of runs) {
      const res = r.results.find(x => x.prompt_id === pid);
      if (res) {
        const url = `/api/forge/runs/${encodeURIComponent(currentSet.name)}/${encodeURIComponent(r.run_id)}/${encodeURIComponent(res.image)}`;
        html += `<td><img loading="lazy" src="${url}" onclick="window.open('${url}','_blank')"></td>`;
      } else {
        html += '<td><span class="cell-missing">—</span></td>';
      }
    }
    html += '</tr>';
  }
  html += '</table>';
  wrap.innerHTML = html;
}

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

function updateTabBadges() {
  const active = setsCache.filter(s => !s.archived);
  const baselineReady = active.some(s => s.locked && s.n_runs > 0);
  $('tb-sets').textContent = baselineReady ? '✔' : (active.length ? '…' : '');
  $('tb-sets').title = baselineReady
    ? 'hay set bloqueado con baseline' : 'falta bloquear un set o generar su baseline';
  $('tb-lab').textContent = exploreSession ? '●' : '';
  $('tb-lab').title = exploreSession ? 'sesión de exploración activa' : '';
  const nDeriv = checkpointsCache.filter(c => c.kind === 'derived' && c.present).length;
  $('tb-merge').textContent = nDeriv ? String(nDeriv) : '';
  $('tb-merge').title = nDeriv ? `${nDeriv} checkpoint(s) derivado(s)` : '';
  $('tb-compare').textContent = runsCache.length ? String(runsCache.length) : '';
  $('tb-compare').title = runsCache.length ? `${runsCache.length} run(s) del set seleccionado` : '';
}

$('compare-set').onchange = () => {
  if ($('compare-set').value) selectSet($('compare-set').value);
};

// ── Init ────────────────────────────────────────────────────────────────────

switchTab(localStorage.getItem('forge-tab') || 'sets');
loadStatus();
refreshSets(false);
refreshCheckpoints();
refreshLoras();
refreshExplore();
