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
  loraExtra: [],           // archivos de loras/h3/ sin preset curado
  loraExtraState: {},      // filename -> {on, strength}
};

// ── Tabs ─────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".page").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("page-" + t.dataset.page).classList.add("active");
  if (t.dataset.page === "hist") loadHistory();
  if (t.dataset.page === "diag") loadDiag();
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
    state.loraExtraState = {};
    presetData.extra.forEach(f => { state.loraExtraState[f] = { on:false, strength:1.0 }; });
    renderLoraPresets();
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

  // Extras sin preset (LoRAs nuevas que el usuario agregue a loras/h3/)
  $("loraExtraWrap").style.display = state.loraExtra.length ? "block" : "none";
  $("loraExtra").innerHTML = state.loraExtra.map(f => {
    const name = f.split(/[\\/]/).pop();
    const st = state.loraExtraState[f];
    return `<label class="fld" style="display:flex; align-items:center; gap:6px; text-transform:none; letter-spacing:0; font-weight:400">
      <input type="checkbox" class="lora-extra-chk" data-file="${f}" ${st.on?"checked":""} style="width:auto"> ${name}
    </label>`;
  }).join("");
  document.querySelectorAll(".lora-extra-chk").forEach(cb => cb.onchange = () => {
    state.loraExtraState[cb.dataset.file].on = cb.checked;
    updateLorasSummary();
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
          + Object.values(state.loraExtraState).filter(s=>s.on).length;
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
  for (const f of state.loraExtra){
    const st = state.loraExtraState[f];
    if (st && st.on) out.push({ name: f, strength: st.strength });
  }
  return out;
}

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

init();
