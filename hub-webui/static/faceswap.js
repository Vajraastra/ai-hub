/* Face Swap — lógica de la UI (patrón de ideogram.js) */
"use strict";

const $ = (id) => document.getElementById(id);
const api = async (path, opts = {}) => {
  const r = await fetch("/api/faceswap" + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const d = (await r.json()).detail;
      if (typeof d === "string") msg = d;
      else if (Array.isArray(d)) {
        // errores de validación de FastAPI (422): lista de {loc, msg}
        msg = d.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
      } else if (d) msg = JSON.stringify(d);
    } catch {}
    throw new Error(`[${r.status}] ${msg}`);
  }
  return r.json();
};

/* ── tabs ──────────────────────────────────────────────────────────────── */
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".page").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("page-" + t.dataset.page).classList.add("active");
    if (t.dataset.page === "hist") loadHistory();
  });
});

/* ── status ────────────────────────────────────────────────────────────── */
let status = null;

async function loadStatus() {
  const pill = $("statusPill");
  const banner = $("statusBanner");
  try {
    status = await api("/status");
  } catch (e) {
    pill.textContent = "hub sin conexión";
    pill.className = "phase-pill bad";
    return;
  }
  banner.innerHTML = "";
  if (!status.comfyui) {
    pill.textContent = "ComfyUI apagado";
    pill.className = "phase-pill bad";
    banner.innerHTML = '<div class="banner err">ComfyUI no responde en :8188 — levantalo desde el hub (pestaña Aplicaciones) y recargá.</div>';
    return;
  }
  if (!status.nodes_ok) {
    const missing = Object.entries(status.nodes).filter(([, v]) => !v).map(([k]) => k);
    pill.textContent = "faltan nodos";
    pill.className = "phase-pill bad";
    banner.innerHTML = `<div class="banner err">Nodos ausentes en ComfyUI: <b>${missing.join(", ")}</b>. Revisá la consola de ComfyUI (fallo de import del custom node).</div>`;
    return;
  }
  if (!status.swap_model_ok) {
    pill.textContent = "falta modelo";
    pill.className = "phase-pill warn";
    banner.innerHTML = `<div class="banner warn">ReActor no ve <b>${status.default_swap_model}</b> en models/hyperswap. Modelos visibles: ${status.swap_models.join(", ") || "ninguno"}.</div>`;
  } else {
    pill.textContent = "listo";
    pill.className = "phase-pill ok";
  }
  // poblar selects
  const det = $("optDetector");
  det.innerHTML = status.detectors.map((d) => `<option>${d}</option>`).join("");
  const sm = $("optSwapModel");
  const models = status.swap_models.filter((m) => !m.toLowerCase().includes("inswapper"));
  sm.innerHTML = models.map((m) => `<option>${m}</option>`).join("");
  if (models.includes(status.default_swap_model)) sm.value = status.default_swap_model;
  // filtro NSFW
  if (status.nsfw) applyNsfwConfig(status.nsfw);
  renderNsfwLast(status.nsfw_last);
  try { renderPatchStatus((await api("/nsfw")).patch); } catch {}
}

function applyNsfwConfig(cfg) {
  $("nsfwEnabled").checked = cfg.enabled;
  $("nsfwThreshold").value = cfg.threshold;
  $("vNsfw").textContent = Number(cfg.threshold).toFixed(3);
}

function renderNsfwLast(last) {
  const el = $("nsfwLast");
  if (!last || last.score === undefined) { el.textContent = ""; return; }
  const cls = last.blocked ? "bad" : "ok";
  const verdict = last.blocked ? "BLOQUEADA" : "ok";
  el.innerHTML = `Último análisis: score <span class="mono ${cls}">${last.score}</span> → <span class="${cls}">${verdict}</span>`;
}

/* ── dropzones ─────────────────────────────────────────────────────────── */
const images = { scene: null, donor0: null, donor1: null, donor2: null, measure: null }; // data URIs

function setupDrop(id, key) {
  const zone = $(id);
  const input = zone.querySelector("input[type=file]");
  const clear = zone.querySelector(".clear");

  const setImage = (dataUri) => {
    images[key] = dataUri;
    let img = zone.querySelector("img");
    if (!img) { img = document.createElement("img"); zone.appendChild(img); }
    img.src = dataUri;
    zone.classList.add("filled");
  };
  const readFile = (file) => {
    if (!file || !file.type.startsWith("image/")) return;
    const fr = new FileReader();
    fr.onload = () => setImage(fr.result);
    fr.readAsDataURL(file);
  };

  zone.addEventListener("click", (e) => {
    if (e.target === clear) return;
    input.click();
  });
  input.addEventListener("change", () => readFile(input.files[0]));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("over");
    readFile(e.dataTransfer.files[0]);
  });
  clear.addEventListener("click", (e) => {
    e.stopPropagation();
    images[key] = null;
    input.value = "";
    zone.querySelector("img")?.remove();
    zone.classList.remove("filled");
  });
}
setupDrop("dropScene", "scene");
setupDrop("dropDonor0", "donor0");
setupDrop("dropDonor1", "donor1");
setupDrop("dropDonor2", "donor2");
setupDrop("dropMeasure", "measure");

/* ── sliders con valor visible ─────────────────────────────────────────── */
[["optEyes", "vEyes"], ["optMouth", "vMouth"], ["optCrop", "vCrop"],
 ["optRestoreVis", "vRestoreVis"], ["optMaskBlur", "vMaskBlur"],
 ["optMaskDilation", "vMaskDilation"], ["optMaskSigma", "vMaskSigma"]].forEach(([sl, lb]) => {
  $(sl).addEventListener("input", () => { $(lb).textContent = $(sl).value; });
});
$("nsfwThreshold").addEventListener("input", () => {
  $("vNsfw").textContent = Number($("nsfwThreshold").value).toFixed(3);
});
$("optReenact").addEventListener("change", () => {
  $("reenactOpts").style.display = $("optReenact").checked ? "" : "none";
});
$("optRestore").addEventListener("change", () => {
  $("restoreOpts").style.display = $("optRestore").checked ? "" : "none";
});
$("optMask").addEventListener("change", () => {
  $("maskOpts").style.display = $("optMask").checked ? "" : "none";
});

/* ── ejecución ─────────────────────────────────────────────────────────── */
let currentRun = null;

function setBusy(busy) {
  $("btnRun").disabled = busy;
  $("btnInterrupt").style.display = busy ? "" : "none";
  $("progressWrap").style.display = busy ? "" : "none";
}

const PHASES = { queued: "en cola", upload: "subiendo fotos",
                 render: "procesando", save: "guardando" };

async function runSwap() {
  const donors = [images.donor0, images.donor1, images.donor2].filter(Boolean);
  if (!images.scene || !donors.length) {
    alert("Faltan fotos: cargá la escena y al menos un donante.");
    return;
  }
  const body = {
    scene: images.scene,
    donors,
    use_reenact: $("optReenact").checked,
    retargeting_eyes: parseFloat($("optEyes").value),
    retargeting_mouth: parseFloat($("optMouth").value),
    crop_factor: parseFloat($("optCrop").value),
    command: $("optCommand").value,
    input_faces_index: $("optInputIdx").value.trim() || "0",
    source_faces_index: $("optSourceIdx").value.trim() || "0",
    facedetection: $("optDetector").value || "retinaface_resnet50",
    swap_model: $("optSwapModel").value || undefined,
    restore_model: $("optRestore").checked ? "GFPGANv1.4.pth" : "none",
    restore_visibility: parseFloat($("optRestoreVis").value),
    use_mask_helper: $("optMask").checked,
    mask_blur: parseInt($("optMaskBlur").value, 10),
    mask_dilation: parseInt($("optMaskDilation").value, 10),
    mask_sigma: parseFloat($("optMaskSigma").value),
    nsfw_enabled: $("nsfwEnabled").checked,
    nsfw_threshold: parseFloat($("nsfwThreshold").value),
  };
  let jobId;
  setBusy(true);
  $("phasePill").textContent = "en cola";
  $("progressBar").style.width = "0";
  $("stepLabel").textContent = "";
  try {
    jobId = (await api("/run", { method: "POST", body: JSON.stringify(body) })).job_id;
  } catch (e) {
    setBusy(false);
    alert("No se pudo lanzar el swap: " + e.message);
    return;
  }
  // poll
  const timer = setInterval(async () => {
    let job;
    try { job = await api("/jobs/" + jobId); }
    catch { return; }
    $("phasePill").textContent = PHASES[job.phase] || job.phase;
    if (job.steps_total > 0) {
      $("progressBar").style.width = (100 * job.step / job.steps_total) + "%";
      $("stepLabel").textContent = `${job.step}/${job.steps_total}`;
    }
    if (job.status === "done") {
      clearInterval(timer);
      setBusy(false);
      refreshNsfwLast();
      showResult(job.run_id, job.has_reenact);
      loadGenerations();
    } else if (job.status === "error") {
      clearInterval(timer);
      setBusy(false);
      refreshNsfwLast();
      alert("Falló el swap:\n" + job.error);
    }
  }, 500);
}
$("btnRun").addEventListener("click", runSwap);
$("btnInterrupt").addEventListener("click", () => api("/interrupt", { method: "POST" }).catch(() => {}));

async function refreshNsfwLast() {
  try { renderNsfwLast((await api("/nsfw")).last); } catch {}
}

/* ── medir filtro / reparar ────────────────────────────────────────────── */
$("btnMeasure").addEventListener("click", async () => {
  if (!images.measure) { alert("Cargá una imagen para medir."); return; }
  const el = $("measureResult");
  el.textContent = "midiendo…";
  try {
    const r = await api("/measure", { method: "POST", body: JSON.stringify({ image: images.measure }) });
    const thr = parseFloat($("nsfwThreshold").value);
    const pass = r.score <= thr;
    el.innerHTML = `score NSFW = <b class="${pass ? "ok" : "bad"}">${r.score}</b> — ` +
      (pass ? `pasa con el umbral actual (${thr.toFixed(3)})`
            : `<span class="bad">BLOQUEADA</span> con umbral ${thr.toFixed(3)}; subilo por encima de ${r.score}`);
  } catch (e) {
    el.innerHTML = `<span class="bad">error: ${e.message}</span>`;
  }
});

$("btnRepairFilter").addEventListener("click", async () => {
  try {
    const r = await api("/repair-filter", { method: "POST" });
    alert(r.ok ? `Filtro reparado (${r.action}).` + (r.note ? "\n" + r.note : "")
               : "No se pudo reparar: " + (r.reason || "desconocido"));
    loadStatus();
  } catch (e) { alert("Error: " + e.message); }
});

function renderPatchStatus(patch) {
  const el = $("patchStatus");
  if (!patch) { el.textContent = ""; return; }
  if (!patch.installed) { el.innerHTML = '<span class="warn">ReActor no instalado</span>'; return; }
  el.innerHTML = patch.patched
    ? '<span class="ok">control de umbral activo</span>'
    : '<span class="bad">parche ausente — reparalo</span>';
}

/* ── resultado ─────────────────────────────────────────────────────────── */
function showResult(runId, hasReenact) {
  currentRun = runId;
  $("resultEmpty").style.display = "none";
  $("viewTabs").style.display = "";
  $("tabReenact").style.display = hasReenact ? "" : "none";
  $("btnReveal").style.display = "";
  selectView("result");
  // marcar la miniatura activa en la tira
  document.querySelectorAll(".genstrip .gen").forEach((g) =>
    g.classList.toggle("sel", g.dataset.id === runId));
}

/* ── tira de generaciones (bajo el visor) ──────────────────────────────── */
async function loadGenerations() {
  let runs = [];
  try { runs = (await api("/history")).runs; } catch { return; }
  const strip = $("genStrip");
  strip.innerHTML = "";
  $("genEmpty").style.display = runs.length ? "none" : "";
  $("genCount").textContent = runs.length
    ? `${runs.length} ${runs.length === 1 ? "generación" : "generaciones"}` : "";
  const total = runs.length;
  runs.forEach((r, i) => {
    const n = total - i; // #1 = la más vieja, la más nueva = mayor
    const gen = document.createElement("div");
    gen.className = "gen" + (r.id === currentRun ? " sel" : "");
    gen.dataset.id = r.id;
    gen.innerHTML = `<span class="num">#${n}</span>` +
      `<img src="/api/faceswap/history/${r.id}/image?kind=result" loading="lazy">`;
    gen.addEventListener("click", () => showResult(r.id, r.has_reenact));
    strip.appendChild(gen);
  });
}

function selectView(kind) {
  document.querySelectorAll("#viewTabs button").forEach((b) =>
    b.classList.toggle("sel", b.dataset.kind === kind));
  const img = $("resultImg");
  img.style.display = "";
  img.src = `/api/faceswap/history/${currentRun}/image?kind=${kind}&t=${Date.now()}`;
}
document.querySelectorAll("#viewTabs button").forEach((b) =>
  b.addEventListener("click", () => selectView(b.dataset.kind)));
$("btnReveal").addEventListener("click", () =>
  api(`/history/${currentRun}/reveal`, { method: "POST" }).catch((e) => alert(e.message)));

/* ── historial ─────────────────────────────────────────────────────────── */
async function loadHistory() {
  let runs = [];
  try { runs = (await api("/history")).runs; } catch { return; }
  const g = $("gallery");
  g.innerHTML = "";
  $("histEmpty").style.display = runs.length ? "none" : "";
  for (const r of runs) {
    const card = document.createElement("div");
    card.className = "gcard";
    card.innerHTML = `
      <img src="/api/faceswap/history/${r.id}/image?kind=result" loading="lazy">
      <div class="meta">
        <span>${(r.ts || "").replace("T", " ")}</span>
        <span class="spacer"></span>
        <button title="Abrir carpeta">📂</button>
        <button title="Borrar">🗑</button>
      </div>`;
    card.querySelector("img").addEventListener("click", () => {
      showResult(r.id, r.has_reenact);
      document.querySelector('.tab[data-page="gen"]').click();
    });
    const [bReveal, bDel] = card.querySelectorAll(".meta button");
    bReveal.addEventListener("click", () =>
      api(`/history/${r.id}/reveal`, { method: "POST" }).catch((e) => alert(e.message)));
    bDel.addEventListener("click", async () => {
      if (!confirm("¿Borrar este swap del historial?")) return;
      await api(`/history/${r.id}`, { method: "DELETE" }).catch((e) => alert(e.message));
      loadHistory();
    });
    g.appendChild(card);
  }
}
$("btnRefreshHist").addEventListener("click", loadHistory);

/* ── init ──────────────────────────────────────────────────────────────── */
loadStatus();
loadGenerations();
