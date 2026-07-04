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

async function loadStatus() {
  try {
    const s = await api('/status');
    $('arch-label').textContent = s.arch;
    const pill = $('comfy-pill');
    pill.textContent = 'ComfyUI: ' + (s.comfyui ? 'online' : 'offline');
    pill.className = 'badge ' + (s.comfyui ? 'draft' : '');
    let html = `<div class="status-row"><span>ComfyUI (API :8188)</span>${mark(s.comfyui)}</div>`;
    for (const [kind, m] of Object.entries(s.models))
      html += `<div class="status-row"><span>${esc(kind)}<br><span class="path">${esc(m.path)}</span></span>${mark(m.present)}</div>`;
    for (const [node, ok] of Object.entries(s.nodes))
      html += `<div class="status-row"><span>nodo ${esc(node)}</span>${mark(ok)}</div>`;
    if (!Object.keys(s.nodes).length)
      html += `<div class="status-row"><span>nodos Realtime-Lora</span><span class="bad">sin comprobar (ComfyUI apagado)</span></div>`;
    $('status-content').innerHTML = html;
  } catch (e) {
    $('status-content').innerHTML = `<span class="bad">Error: ${esc(e.message)}</span>`;
  }
}

$('btn-status-toggle').onclick = () => {
  const el = $('status-content');
  const show = el.style.display === 'none';
  el.style.display = show ? '' : 'none';
  $('btn-status-toggle').textContent = show ? 'ocultar' : 'mostrar';
};

// ── Lista de sets ───────────────────────────────────────────────────────────

async function refreshSets(keepSelection = true) {
  const data = await api('/sets');
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

// ── Regeneración ────────────────────────────────────────────────────────────

$('btn-run-set').onclick = async () => {
  const draft = !currentSet.locked_at;
  const label = prompt(
    (draft ? 'AVISO: el set es un BORRADOR — el run se marcará como prueba de calibración, no sirve para comparar merges.\n\n' : '') +
    'Etiqueta del run (ej. "baseline de-turbo"):', '');
  if (label === null) return;
  try {
    const { job_id } = await api('/sets/' + encodeURIComponent(currentSet.name) + '/run',
                                 { method: 'POST', body: { label } });
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

// ── Init ────────────────────────────────────────────────────────────────────

loadStatus();
refreshSets(false);
