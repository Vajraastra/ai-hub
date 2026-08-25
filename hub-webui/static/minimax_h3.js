"use strict";
// MiniMax H3 — lógica del módulo.
const $ = (id) => document.getElementById(id);
const API = "/api/minimax-h3";

const state = {
  jobTimer: null,
  ffB64: null,           // imagen inicial (I2V), sin el prefijo data:
  modelsReady: false,
  loraPresets: [],        // del endpoint /lora-presets, en orden fijo de apilado
  loraPresetState: {},    // id -> {on, strength}  (strength = valor del slider, escala el resto de archivos del preset proporcionalmente)
  loraExtra: [],           // archivos de loras/h3/ sin preset curado (candidatos a slot)
  // Hasta 3 LoRAs propios del usuario, cada uno con su intensidad (s98).
  loraExtraSlots: [
    { name:"", strength:1.0 }, { name:"", strength:1.0 }, { name:"", strength:1.0 },
  ],
  chain: null,             // cadena cargada actualmente (o null)
  refStartB64: [],         // imágenes de referencia (sin prefijo data:) para el inicio Ref2V
  ref2v: null,             // cache de /models/status/ref2v ({ready, missing, names, ...})
  chainJobTimer: null,
  chainJobSeg: null,       // segment_id del tramo que está generando ahora mismo
};

// ── Tabs ─────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".page").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("page-" + t.dataset.page).classList.add("active");
  if (t.dataset.page === "hist") loadHistory();
  if (t.dataset.page === "diag") loadDiag();
  if (t.dataset.page === "chain"){ loadChainsList(); loadRef2vStatus(); }
});

// ── Helpers ──────────────────────────────────────────────────────────────────
async function jget(p){ const r = await fetch(API+p); if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||r.status); return r.json(); }
async function jpost(p,b){ const r = await fetch(API+p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})});
  if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||r.status); return r.json(); }
async function jdel(p){ const r = await fetch(API+p,{method:"DELETE"}); if(!r.ok) throw new Error(r.status); return r.json(); }
function banner(el,msg,kind){ el.innerHTML = msg ? `<div class="banner ${kind||'warn'}">${msg}</div>` : ""; }
function fillSelect(sel,arr,cur){ sel.innerHTML=""; (arr||[]).forEach(v=>{ const o=document.createElement("option");
  o.value=v; o.textContent=v; sel.appendChild(o); });
  if(cur) sel.value=cur; }
function fmtGB(bytes){ return (bytes/1e9).toFixed(1)+" GB"; }

// ── Init ─────────────────────────────────────────────────────────────────────
async function init(){
  try {
    const st = await jget("/status");
    $("hdrStatus").innerHTML = `ComfyUI ${st.comfyui?'<span class="ok">●</span>':'<span class="bad">●</span>'}`;
    if(!st.comfyui) banner($("genBanner"),"ComfyUI no responde en :8188 — arráncalo desde el Hub.","err");
    else if(!st.nodes_ok) banner($("genBanner"),"Faltan nodos nativos de MiniMax H3. Actualiza ComfyUI a 0.33+.","err");
  } catch(e){ $("hdrStatus").innerHTML=`<span class="bad">sin conexión</span>`; }

  await checkModelsGate();
  if (state.modelsReady) await loadModelsAndLoras();
}

// ── Gate: descarga de modelos base ────────────────────────────────────────────
async function checkModelsGate(){
  let ms;
  try { ms = await jget("/models/status"); }
  catch(e){ banner($("genBanner"),"No se pudo consultar el estado de los modelos: "+e.message,"err"); return; }

  state.modelsReady = ms.ready;
  $("gate").classList.toggle("show", !ms.ready);
  $("genArea").classList.toggle("show", ms.ready);

  if (!ms.ready){
    $("gateList").innerHTML = ms.missing.map(m =>
      `<div class="gate-item"><span>${m.label}</span><span class="mono">${fmtGB(m.size_bytes)}</span></div>`
    ).join("") + `<div class="gate-item"><b>Total</b><b class="mono">${fmtGB(ms.total_missing_bytes)}</b></div>`;
  }
}

$("btnDownloadModels").onclick = () => {
  $("btnDownloadModels").disabled = true;
  $("gateProgress").style.display = "block";
  const es = new EventSource(API + "/models/download");
  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.type === "file_start"){
      $("gateFileLabel").textContent = `[${d.index}/${d.total_files}] ${d.label}`;
      $("gateBar").style.width = "0%";
      $("gateStats").textContent = "";
    } else if (d.type === "progress"){
      $("gateBar").style.width = d.pct + "%";
      $("gateStats").textContent = `${fmtGB(d.bytes_done)} / ${fmtGB(d.bytes_total)} — ${d.pct}%`;
    } else if (d.type === "file_done"){
      $("gateBar").style.width = "100%";
    } else if (d.type === "all_done"){
      es.close();
      $("gateProgress").style.display = "none";
      $("btnDownloadModels").disabled = false;
      checkModelsGate().then(() => { if (state.modelsReady) loadModelsAndLoras(); });
    } else if (d.type === "error"){
      es.close();
      $("btnDownloadModels").disabled = false;
      banner($("genBanner"), "Descarga fallida: " + d.msg, "err");
    }
  };
  es.onerror = () => {
    // El navegador reintenta EventSource solo; si el server ya cerró el
    // stream (all_done/error) esto no dispara porque cerramos con es.close().
  };
};

// ── Modelos + LoRAs ────────────────────────────────────────────────────────
async function loadModelsAndLoras(){
  try {
    const [unet, clip, vae, loras, presetData] = await Promise.all([
      jget("/catalog/diffusion_models"),
      jget("/catalog/text_encoders"),
      jget("/catalog/vae"),
      jget("/loras"),
      jget("/lora-presets"),
    ]);
    fillSelect($("unetName"), unet);
    fillSelect($("clipName"), clip);
    fillSelect($("videoVae"), vae.filter(v=>/video/i.test(v)).length ? vae.filter(v=>/video/i.test(v)) : vae);
    fillSelect($("audioVae"), vae.filter(v=>/audio/i.test(v)).length ? vae.filter(v=>/audio/i.test(v)) : vae);
    const turboLoras = loras.filter(l => /turbo/i.test(l));
    fillSelect($("turboLora"), turboLoras.length ? turboLoras : loras);

    state.loraPresets = presetData.presets;
    state.loraExtra = presetData.extra;
    state.loraPresetState = {};
    presetData.presets.forEach(p => { state.loraPresetState[p.id] = { on:false, strength:p.files[0].default_strength }; });
    // Si un slot apunta a un archivo que ya no existe en disco, se vacía.
    state.loraExtraSlots.forEach(s => { if (s.name && !presetData.extra.includes(s.name)) s.name = ""; });
    renderLoraPresets();
    renderLoraExtraSlots();
    updateLorasSummary();
  } catch(e){ banner($("genBanner"),"No se pudieron cargar los modelos: "+e.message,"err"); }
}

function renderLoraPresets(){
  let html = "";
  let lastStage = null;
  for (const p of state.loraPresets){
    if (p.stage !== lastStage){
      html += `<div class="lp-stage-hdr">${p.stage === "anatomy" ? "Anatomía" : "Acción"}</div>`;
      lastStage = p.stage;
    }
    const st = state.loraPresetState[p.id];
    html += `
      <div class="lp-row stage-${p.stage}" data-preset="${p.id}">
        <div class="lp-top">
          <button class="lp-toggle" data-preset="${p.id}">${st.on ? "Activo" : "Activar"}</button>
          <span class="lp-label">${p.label}</span>
          <span class="lp-info" title="${p.description.replace(/"/g,'&quot;')}">i</span>
        </div>
        <div class="lp-strength" data-preset="${p.id}">
          <input type="range" min="0" max="2" step="0.05" value="${st.strength}" data-preset="${p.id}" class="lp-slider">
          <input type="number" min="0" max="2" step="0.01" value="${st.strength}" data-preset="${p.id}" class="lp-num">
        </div>
      </div>`;
  }
  $("loraPresets").innerHTML = html || `<div class="dim">Ningún preset curado detectado — ¿faltan los LoRAs de loras/h3/?</div>`;

  document.querySelectorAll(".lp-toggle").forEach(btn => btn.onclick = () => toggleLoraPreset(btn.dataset.preset));
  document.querySelectorAll(".lp-slider").forEach(el => el.oninput = () => syncLoraStrength(el.dataset.preset, +el.value));
  document.querySelectorAll(".lp-num").forEach(el => el.onchange = () => syncLoraStrength(el.dataset.preset, +el.value));
}

// Hasta 3 LoRAs propios (archivos de loras/h3/ sin preset curado), cada uno con
// su selector + intensidad.
function renderLoraExtraSlots(){
  const box = $("loraExtraSlots");
  if (!state.loraExtra.length){
    box.innerHTML = `<div class="dim" style="font-size:11px">No se encontraron LoRAs propios.
      Copia tus archivos .safetensors a la subcarpeta <span class="mono">loras/h3/</span> del
      almacén de modelos y recarga la página.</div>`;
    return;
  }
  const opts = state.loraExtra.map(f =>
    `<option value="${f}">${f.split(/[\\/]/).pop()}</option>`).join("");
  box.innerHTML = state.loraExtraSlots.map((s, i) => `
    <div class="lx-slot">
      <select class="lx-sel" data-slot="${i}">
        <option value="">— ninguno —</option>${opts}
      </select>
      <input type="number" class="lx-str" data-slot="${i}" value="${s.strength}" step="0.05"
        min="0" max="2" title="Intensidad del efecto (1.0 = completo)" ${s.name ? "" : "disabled"}>
    </div>`).join("");
  state.loraExtraSlots.forEach((s, i) => {
    if (s.name) document.querySelector(`.lx-sel[data-slot="${i}"]`).value = s.name;
  });
  document.querySelectorAll(".lx-sel").forEach(sel => sel.onchange = () => {
    const i = +sel.dataset.slot;
    state.loraExtraSlots[i].name = sel.value;
    document.querySelector(`.lx-str[data-slot="${i}"]`).disabled = !sel.value;
    updateLorasSummary();
  });
  document.querySelectorAll(".lx-str").forEach(inp => inp.onchange = () => {
    state.loraExtraSlots[+inp.dataset.slot].strength = +inp.value;
  });
}

function toggleLoraPreset(id){
  const st = state.loraPresetState[id];
  st.on = !st.on;
  const row = document.querySelector(`.lp-row[data-preset="${id}"]`);
  row.querySelector(".lp-toggle").classList.toggle("on", st.on);
  row.querySelector(".lp-toggle").textContent = st.on ? "Activo" : "Activar";
  row.querySelector(".lp-strength").classList.toggle("show", st.on);

  if (st.on){
    const preset = state.loraPresets.find(p => p.id === id);
    // Fuerza sugerida: si es turbo y el preset tiene una variante turbo, arranca ahí.
    const suggested = ($("turboOn").checked && preset.default_strength_turbo != null)
      ? preset.default_strength_turbo : preset.files[0].default_strength;
    st.strength = suggested;
    row.querySelector(".lp-slider").value = suggested;
    row.querySelector(".lp-num").value = suggested;
    // Trigger: se agrega al final del prompt si todavía no está (no se retira al desactivar).
    const box = $("prompt");
    if (!box.value.toLowerCase().includes(preset.trigger.toLowerCase())){
      box.value = box.value.trim() ? box.value.trim() + ", " + preset.trigger : preset.trigger;
    }
  }
  updateLorasSummary();
}

function syncLoraStrength(id, value){
  state.loraPresetState[id].strength = value;
  const row = document.querySelector(`.lp-row[data-preset="${id}"]`);
  row.querySelector(".lp-slider").value = value;
  row.querySelector(".lp-num").value = value;
}

function updateLorasSummary(){
  const n = Object.values(state.loraPresetState).filter(s=>s.on).length
          + state.loraExtraSlots.filter(s=>s.name).length;
  $("lorasSummary").textContent = n ? `${n} activo(s)` : "";
}

function buildLorasPayload(){
  // Orden fijo del registry (anatomía -> acción), NUNCA el orden de activación del usuario.
  const out = [];
  for (const p of state.loraPresets){
    const st = state.loraPresetState[p.id];
    if (!st.on) continue;
    const base = p.files[0].default_strength || 1;
    for (const f of p.files){
      const ratio = base ? f.default_strength / base : 1;
      out.push({ name: f.filename, strength: +(st.strength * ratio).toFixed(3) });
    }
  }
  const seen = new Set();
  for (const s of state.loraExtraSlots){
    if (s.name && !seen.has(s.name)){
      seen.add(s.name);
      out.push({ name: s.name, strength: s.strength });
    }
  }
  return out;
}

// ── Formato (medidas estándar de monitor / teléfono) ────────────────────────
// Medidas múltiplo de 32 en torno a ~1MP, el rango donde el modelo rinde bien.
const FORMATS = [
  { id:"tv",     ico:"🖥️", label:"Monitor / TV",  w:1280, h:720  },
  { id:"wide",   ico:"🎞️", label:"Panorámico",    w:1344, h:768  },
  { id:"phone",  ico:"📱", label:"Teléfono",       w:720,  h:1280 },
  { id:"phoneL", ico:"📱", label:"Vertical alto",  w:768,  h:1344 },
  { id:"square", ico:"◻️", label:"Cuadrado",       w:1024, h:1024 },
  { id:"custom", ico:"⚙️", label:"Personalizado",  w:null, h:null },
];

function renderFmtChips(){
  $("fmtChips").innerHTML = FORMATS.map(f => `
    <button type="button" class="fmt-chip" data-fmt="${f.id}">
      <span class="fc-ico">${f.ico}</span><span>${f.label}</span>
      <span class="fc-dims">${f.w ? f.w + "×" + f.h : "a mano"}</span>
    </button>`).join("");
  document.querySelectorAll(".fmt-chip").forEach(b => b.onclick = () => applyFormat(b.dataset.fmt));
  syncFmtSelection();
}

function applyFormat(id){
  const f = FORMATS.find(x => x.id === id);
  if (f.w){ $("pw").value = f.w; $("ph").value = f.h; }
  else $("pw").focus();
  syncFmtSelection();
}

// El chip marcado sigue a los campos ancho/alto: si el usuario los edita a mano
// y coinciden con un preset se marca ese, si no se marca "Personalizado".
function syncFmtSelection(){
  const w = +$("pw").value, h = +$("ph").value;
  const match = FORMATS.find(f => f.w === w && f.h === h);
  document.querySelectorAll(".fmt-chip").forEach(b =>
    b.classList.toggle("on", b.dataset.fmt === (match ? match.id : "custom")));
}
$("pw").oninput = syncFmtSelection;
$("ph").oninput = syncFmtSelection;
renderFmtChips();

// ── Turbo toggle ─────────────────────────────────────────────────────────────
function syncTurboUI(){
  const on = $("turboOn").checked;
  $("turboOpts").style.display = on ? "block" : "none";
  $("fullOpts").style.display = on ? "none" : "block";
}
$("turboOn").onchange = syncTurboUI;
syncTurboUI();

// ── Imagen inicial (I2V) ──────────────────────────────────────────────────────
$("btnFfLoad").onclick = () => $("ffFile").click();
$("ffFile").onchange = () => {
  const f = $("ffFile").files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    const dataUri = reader.result;
    state.ffB64 = dataUri.split(",")[1];
    $("ffPreview").src = dataUri;
    $("ffPreviewWrap").style.display = "block";
    $("btnFfClear").style.display = "inline-block";
  };
  reader.readAsDataURL(f);
};
$("btnFfClear").onclick = () => {
  state.ffB64 = null;
  $("ffFile").value = "";
  $("ffPreviewWrap").style.display = "none";
  $("btnFfClear").style.display = "none";
};

// ── Generar ──────────────────────────────────────────────────────────────────
const PHASE = { queued:"En cola", build:"Armando workflow", render:"Renderizando", save:"Guardando", done:"Listo" };

$("btnGenerate").onclick = async () => {
  const prompt = $("prompt").value.trim();
  if (!prompt){ banner($("genBanner"),"Escribe un prompt primero.","warn"); return; }
  const turbo = $("turboOn").checked;
  const loras = buildLorasPayload();

  const body = {
    prompt,
    unet_name: $("unetName").value,
    clip_name: $("clipName").value,
    video_vae_name: $("videoVae").value,
    audio_vae_name: $("audioVae").value,
    first_frame_b64: state.ffB64,
    width: +$("pw").value,
    height: +$("ph").value,
    duration_s: +$("pdur").value,
    turbo,
    turbo_lora_name: turbo ? $("turboLora").value : "",
    turbo_strength: +$("turboStrength").value,
    turbo_steps: +$("turboSteps").value,
    steps: +$("pstepsFull").value,
    seed: +$("pseed").value,
    loras,
  };

  $("btnGenerate").disabled = true;
  banner($("genBanner"),"","");
  try{
    const r = await jpost("/generate", body);
    pollJob(r.job_id);
  } catch(e){
    banner($("genBanner"),"Error: "+e.message,"err");
    $("btnGenerate").disabled = false;
  }
};

function pollJob(id){
  $("jobStatus").style.display="block"; $("resultBox").innerHTML="";
  clearInterval(state.jobTimer);
  state.jobTimer=setInterval(async ()=>{
    let j; try{ j=await jget("/jobs/"+id); }catch(e){ return; }
    $("jobPhase").textContent=PHASE[j.phase]||j.phase;
    if(j.phase==="render"&&j.steps_total){ $("jobStep").textContent=`${j.step||0}/${j.steps_total}`;
      $("jobBar").style.width=(100*(j.step||0)/j.steps_total)+"%"; }
    if(j.status==="done"){ clearInterval(state.jobTimer); $("btnGenerate").disabled=false;
      $("jobBar").style.width="100%"; $("jobPhase").textContent="Listo"; $("jobStep").textContent="";
      showResult(j.run_id); }
    if(j.status==="error"){ clearInterval(state.jobTimer); $("btnGenerate").disabled=false;
      $("jobStatus").style.display="none"; banner($("genBanner"),"Error: "+j.error,"err"); }
  },800);
}
function showResult(runId){
  $("resultBox").innerHTML = `<video class="result" controls autoplay loop src="${API}/history/${runId}/video?t=${Date.now()}"></video>`;
}

// ── Historial ─────────────────────────────────────────────────────────────────
$("btnReloadHist").onclick=loadHistory;
async function loadHistory(){
  try{
    const {runs}=await jget("/history");
    $("gallery").innerHTML = runs.map(r => `
      <div class="gcard">
        <video controls preload="metadata" src="${API}/history/${r.id}/video"></video>
        <div class="prompt">${(r.prompt||"").slice(0,140)}</div>
        <div class="meta">${new Date(r.created*1000).toLocaleString()} · ${r.duration_s}s · seed ${r.seed}</div>
        <div class="actions">
          <button class="ghost" onclick="revealRun('${r.id}')">Abrir carpeta</button>
          <button class="ghost" onclick="deleteRun('${r.id}')">Borrar</button>
        </div>
      </div>`).join("") || `<div class="dim">Sin generaciones todavía.</div>`;
  } catch(e){ $("gallery").innerHTML = `<div class="dim">Error cargando historial: ${e.message}</div>`; }
}
async function revealRun(id){ try{ await jpost(`/history/${id}/reveal`); }catch(e){} }
async function deleteRun(id){ if(!confirm("¿Borrar esta generación?")) return; try{ await jdel(`/history/${id}`); loadHistory(); }catch(e){} }

// ── Diagnóstico ───────────────────────────────────────────────────────────────
$("btnReloadDiag").onclick=loadDiag;
async function loadDiag(){
  try{
    const st = await jget("/status");
    $("diagServices").innerHTML = `<div class="status-row"><span>ComfyUI</span><span class="${st.comfyui?'ok':'bad'}">${st.comfyui?'en línea':'sin conexión'}</span></div>`;
    $("diagNodes").innerHTML = Object.entries(st.nodes||{}).map(([n,ok]) =>
      `<div class="status-row"><span class="mono">${n}</span><span class="${ok?'ok':'bad'}">${ok?'✓':'✗'}</span></div>`
    ).join("") || `<div class="dim">ComfyUI sin conexión.</div>`;
  } catch(e){ $("diagServices").innerHTML = `<div class="bad">Error: ${e.message}</div>`; }

  try{
    const ms = await jget("/models/status");
    $("diagModels").innerHTML = ms.ready
      ? `<div class="banner ok">Todos los modelos base están en disco.</div>`
      : `<div class="banner warn">Faltan ${ms.missing.length} archivo(s), ${fmtGB(ms.total_missing_bytes)} — usa la pestaña Generar para descargarlos.</div>`;
  } catch(e){ $("diagModels").innerHTML = `<div class="bad">Error: ${e.message}</div>`; }
}

// ── Cadena (encadenado de tramos) ───────────────────────────────────────────
async function loadChainsList(){
  let data;
  try { data = await jget("/chains"); }
  catch(e){ banner($("chainBanner"),"No se pudo cargar la lista de cadenas: "+e.message,"err"); return; }
  const sel = $("chainSelect");
  const cur = state.chain?.id || "";
  sel.innerHTML = `<option value="">— Sin cadena seleccionada —</option>` + data.chains.map(c =>
    `<option value="${c.id}">${c.name} (${c.accepted}/${c.segments} tramos)</option>`).join("");
  sel.value = cur;
}

$("chainSelect").onchange = () => {
  const id = $("chainSelect").value;
  if (!id){ state.chain = null; renderTimeline(); updateChainStartBadge();
    $("btnDeleteChain").style.display="none"; $("btnExportChain").style.display="none"; return; }
  loadChain(id);
};

async function loadChain(id){
  try {
    state.chain = await jget("/chains/"+id);
    $("btnDeleteChain").style.display = "inline-block";
    $("btnExportChain").style.display = "inline-block";
    renderTimeline();
    updateChainStartBadge();
  } catch(e){ banner($("chainBanner"),"No se pudo cargar la cadena: "+e.message,"err"); }
}

// ── Inicio con referencias (Ref2V) ──────────────────────────────────────────
function updateChainStartBadge(){
  const st = state.chain?.start;
  const badge = $("chainStartBadge");
  if (st?.mode === "ref2v"){
    badge.textContent = `esta cadena arranca en Ref2V (${(st.ref_images_b64||[]).length} refs)`;
    badge.style.display = "inline-block";
  } else badge.style.display = "none";
}

async function loadRef2vStatus(){
  try { state.ref2v = await jget("/models/status/ref2v"); }
  catch(e){ $("refStartStatus").textContent = "estado Ref2V no disponible"; return; }
  if (state.ref2v.ready){
    $("refStartStatus").textContent = "modelos Ref2V listos";
    $("btnRefDownload").style.display = "none";
  } else {
    $("refStartStatus").textContent =
      `faltan modelos Ref2V (${fmtGB(state.ref2v.total_missing_bytes)})`;
    $("btnRefDownload").style.display = "inline-block";
  }
}

function renderRefThumbs(){
  $("refThumbs").innerHTML = state.refStartB64.map((b64, i) =>
    `<img src="data:image/png;base64,${b64}" title="Picture ${i+1} — clic para quitar" data-i="${i}">`).join("");
  $("refThumbs").querySelectorAll("img").forEach(img => img.onclick = () => {
    state.refStartB64.splice(+img.dataset.i, 1);
    renderRefThumbs();
  });
  $("btnRefClear").style.display = state.refStartB64.length ? "inline-block" : "none";
}

$("btnRefAdd").onclick = () => $("refFiles").click();
$("refFiles").onchange = () => {
  const files = [...$("refFiles").files].slice(0, 9 - state.refStartB64.length);
  if (!files.length) return;
  let pending = files.length;
  files.forEach(f => {
    const reader = new FileReader();
    reader.onload = () => {
      state.refStartB64.push(reader.result.split(",")[1]);
      if (--pending === 0) renderRefThumbs();
    };
    reader.readAsDataURL(f);
  });
  $("refFiles").value = "";
};
$("btnRefClear").onclick = () => { state.refStartB64 = []; renderRefThumbs(); };

$("btnRefDownload").onclick = () => {
  $("btnRefDownload").disabled = true;
  const es = new EventSource(API + "/models/download/ref2v");
  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.type === "file_start")
      $("refStartStatus").textContent = `[${d.index}/${d.total_files}] ${d.label}…`;
    else if (d.type === "progress")
      $("refStartStatus").textContent = `${fmtGB(d.bytes_done)} / ${fmtGB(d.bytes_total)} — ${d.pct}%`;
    else if (d.type === "all_done"){
      es.close(); $("btnRefDownload").disabled = false; loadRef2vStatus();
    } else if (d.type === "error"){
      es.close(); $("btnRefDownload").disabled = false;
      banner($("chainBanner"), "Descarga Ref2V fallida: " + d.msg, "err");
    }
  };
};

$("btnNewChain").onclick = async () => {
  if (!state.modelsReady){ banner($("chainBanner"),"Descarga los modelos primero (pestaña Generar).","warn"); return; }
  let refStart = null;
  if (state.refStartB64.length){
    if (!state.ref2v?.ready){
      banner($("chainBanner"),"Hay imágenes de referencia cargadas pero faltan los modelos Ref2V — descárgalos o quita las imágenes.","warn");
      return;
    }
    refStart = {
      unet_name: state.ref2v.names.ref2v_dit,
      turbo_lora_name: state.ref2v.names.ref2v_turbo,
      turbo_strength: 1.0,
      turbo_steps: 4,
      ref_images_b64: state.refStartB64,
    };
  }
  const name = prompt("Nombre de la cadena:", "Escena nueva");
  if (name === null) return;
  const turbo = $("turboOn").checked;
  const body = {
    name,
    unet_name: $("unetName").value,
    clip_name: $("clipName").value,
    video_vae_name: $("videoVae").value,
    audio_vae_name: $("audioVae").value,
    width: +$("pw").value,
    height: +$("ph").value,
    turbo,
    turbo_lora_name: turbo ? $("turboLora").value : "",
    turbo_strength: +$("turboStrength").value,
    turbo_steps: +$("turboSteps").value,
    steps: +$("pstepsFull").value,
    loras: buildLorasPayload(),
    first_frame_b64: refStart ? null : state.ffB64,
    ref_start: refStart,
  };
  try {
    const chain = await jpost("/chains", body);
    state.refStartB64 = []; renderRefThumbs();
    await loadChainsList();
    $("chainSelect").value = chain.id;
    await loadChain(chain.id);
    banner($("chainBanner"),"","");
  } catch(e){ banner($("chainBanner"),"Error creando la cadena: "+e.message,"err"); }
};

$("btnDeleteChain").onclick = async () => {
  if (!state.chain) return;
  if (!confirm(`¿Borrar la cadena "${state.chain.name}"? Los clips ya generados quedan en el Historial.`)) return;
  try {
    await jdel("/chains/"+state.chain.id);
    state.chain = null;
    await loadChainsList();
    renderTimeline();
    $("btnDeleteChain").style.display="none"; $("btnExportChain").style.display="none";
  } catch(e){ banner($("chainBanner"),"Error: "+e.message,"err"); }
};

$("btnExportChain").onclick = async () => {
  if (!state.chain) return;
  $("btnExportChain").disabled = true;
  try {
    const r = await jpost(`/chains/${state.chain.id}/export`, {});
    $("exportResult").innerHTML = `<video class="result" controls autoplay src="${API}/history/${r.run_id}/video?t=${Date.now()}"></video>`;
  } catch(e){ banner($("chainBanner"),"Error exportando: "+e.message,"err"); }
  $("btnExportChain").disabled = false;
};

function renderTimeline(){
  const tl = $("timeline");
  if (!state.chain){ tl.innerHTML = `<div class="dim">Elige o crea una cadena para empezar.</div>`; return; }
  const segs = state.chain.segments;
  let html = segs.map((seg, i) => segCardHtml(seg, i, segs)).join("");

  const canAdd = segs.length === 0 || !!segs[segs.length-1].accepted;
  html += `
    <div class="seg-add-card">
      <div class="seg-hdr">Tramo ${segs.length+1}</div>
      <textarea id="newSegPrompt" placeholder="Prompt del nuevo tramo (continúa la escena)..." ${canAdd?"":"disabled"}></textarea>
      <div class="seg-row">
        <div><label class="fld">Duración (s)</label><input type="number" id="newSegDur" value="5" step="0.5" min="0.5" max="15" ${canAdd?"":"disabled"}></div>
        <div><label class="fld">Seed</label><input type="number" id="newSegSeed" value="-1" ${canAdd?"":"disabled"}></div>
      </div>
      <button class="act" id="btnAddSeg" ${canAdd?"":"disabled"}>+ Agregar tramo</button>
      ${canAdd?"":`<div class="dim" style="font-size:11px">Acepta una versión del último tramo antes de agregar el siguiente.</div>`}
    </div>`;
  tl.innerHTML = html;

  document.querySelectorAll(".btn-gen-seg").forEach(b => b.onclick = () => generateSegmentVersion(b.dataset.seg));
  document.querySelectorAll(".btn-del-seg").forEach(b => b.onclick = () => deleteSegmentCascade(b.dataset.seg));
  document.querySelectorAll(".btn-accept-ver").forEach(b => b.onclick = () => acceptVersion(b.dataset.seg, b.dataset.run));
  document.querySelectorAll(".btn-del-ver").forEach(b => b.onclick = () => deleteVersion(b.dataset.seg, b.dataset.run));
  const btnAdd = $("btnAddSeg");
  if (btnAdd) btnAdd.onclick = addSegment;
}

function segCardHtml(seg, i, segs){
  const pending = !seg.accepted;
  const versions = seg.versions.slice().reverse().map(runId => `
    <div class="ver-item ${runId===seg.accepted?'accepted':''}">
      <video controls preload="metadata" src="${API}/history/${runId}/video"></video>
      <div class="dim" style="font-size:10px; margin-top:2px">${runId}${runId===seg.accepted?' · <span class="ok">aceptada</span>':''}</div>
      <div class="ver-actions">
        ${runId===seg.accepted ? "" : `<button class="ghost btn-accept-ver" data-seg="${seg.id}" data-run="${runId}">Aceptar</button>
        <button class="ghost btn-del-ver" data-seg="${seg.id}" data-run="${runId}">Borrar</button>`}
      </div>
    </div>`).join("") || `<div class="dim" style="font-size:11px">Sin versiones todavía — genera la primera.</div>`;

  const isLast = i === segs.length - 1;
  const genDisabled = state.chainJobSeg ? "disabled" : "";
  return `
    <div class="seg-card ${pending?'pending':''}">
      <div class="seg-hdr">
        <span>Tramo ${i+1}${seg.accepted?'':' · pendiente'}</span>
        ${isLast ? `<button class="ghost btn-del-seg" data-seg="${seg.id}" style="padding:2px 8px">Borrar desde aquí</button>` : ""}
      </div>
      <textarea data-prompt-for="${seg.id}">${seg.prompt}</textarea>
      <div class="seg-row">
        <div><label class="fld">Duración (s)</label><input type="number" value="${seg.duration_s}" step="0.5" min="0.5" max="15" data-dur-for="${seg.id}"></div>
        <div><label class="fld">Seed</label><input type="number" value="${seg.seed}" data-seed-for="${seg.id}"></div>
      </div>
      <button class="act btn-gen-seg" data-seg="${seg.id}" ${genDisabled}>${seg.versions.length ? "Generar otra versión" : "Generar"}</button>
      <div class="ver-list">${versions}</div>
    </div>`;
}

async function addSegment(){
  const prompt_ = $("newSegPrompt").value.trim();
  if (!prompt_){ banner($("chainBanner"),"Escribe un prompt para el tramo.","warn"); return; }
  try {
    await jpost(`/chains/${state.chain.id}/segments`, {
      prompt: prompt_, duration_s: +$("newSegDur").value, seed: +$("newSegSeed").value,
    });
    await loadChain(state.chain.id);
    await loadChainsList();
  } catch(e){ banner($("chainBanner"),"Error: "+e.message,"err"); }
}

async function generateSegmentVersion(segId){
  // Tomar el prompt/duración/seed editados en la tarjeta antes de generar.
  const seg = state.chain.segments.find(s => s.id === segId);
  const promptEl = document.querySelector(`[data-prompt-for="${segId}"]`);
  const durEl = document.querySelector(`[data-dur-for="${segId}"]`);
  const seedEl = document.querySelector(`[data-seed-for="${segId}"]`);
  seg.prompt = promptEl.value.trim();
  seg.duration_s = +durEl.value;
  seg.seed = +seedEl.value;
  if (!seg.prompt){ banner($("chainBanner"),"El tramo necesita un prompt.","warn"); return; }

  banner($("chainBanner"),"","");
  $("chainJobCard").style.display = "block";
  state.chainJobSeg = segId;
  renderTimeline();
  try {
    const r = await jpost(`/chains/${state.chain.id}/segments/${segId}/generate`, {
      prompt: seg.prompt, duration_s: seg.duration_s, seed: seg.seed,
    });
    pollChainJob(r.job_id);
  } catch(e){
    banner($("chainBanner"),"Error: "+e.message,"err");
    $("chainJobCard").style.display = "none";
    state.chainJobSeg = null;
    renderTimeline();
  }
}

function pollChainJob(jobId){
  clearInterval(state.chainJobTimer);
  state.chainJobTimer = setInterval(async () => {
    let j; try{ j = await jget("/jobs/"+jobId); } catch(e){ return; }
    $("chainJobPhase").textContent = PHASE[j.phase] || j.phase;
    if (j.phase === "render" && j.steps_total){
      $("chainJobStep").textContent = `${j.step||0}/${j.steps_total}`;
      $("chainJobBar").style.width = (100*(j.step||0)/j.steps_total)+"%";
    }
    if (j.status === "done"){
      clearInterval(state.chainJobTimer);
      $("chainJobCard").style.display = "none";
      state.chainJobSeg = null;
      await loadChain(state.chain.id);
      await loadChainsList();
    }
    if (j.status === "error"){
      clearInterval(state.chainJobTimer);
      $("chainJobCard").style.display = "none";
      state.chainJobSeg = null;
      banner($("chainBanner"),"Error generando el tramo: "+j.error,"err");
      renderTimeline();
    }
  }, 800);
}

async function acceptVersion(segId, runId){
  try {
    await jpost(`/chains/${state.chain.id}/segments/${segId}/accept`, {run_id: runId});
    await loadChain(state.chain.id);
    await loadChainsList();
  } catch(e){ banner($("chainBanner"),"Error: "+e.message,"err"); }
}

async function deleteVersion(segId, runId){
  if (!confirm("¿Borrar esta versión?")) return;
  try {
    await jdel(`/chains/${state.chain.id}/segments/${segId}/versions/${runId}`);
    await loadChain(state.chain.id);
  } catch(e){ banner($("chainBanner"),"Error: "+e.message,"err"); }
}

async function deleteSegmentCascade(segId){
  if (!confirm("Esto borra este tramo Y todos los que vienen después (su continuidad ya no sería válida). ¿Seguir?")) return;
  try {
    await jdel(`/chains/${state.chain.id}/segments/${segId}`);
    await loadChain(state.chain.id);
    await loadChainsList();
  } catch(e){ banner($("chainBanner"),"Error: "+e.message,"err"); }
}

init();
