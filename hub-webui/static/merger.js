/* LoRA Merger WebUI */

// ════════════════════════════════════════════════════════════════
// Estado
// ════════════════════════════════════════════════════════════════
const $ = id => document.getElementById(id);
let loras   = [];  // [{path, info, analysis, weight}]
let browserSelected = new Set();
let browserCurrentPath = "";

// ════════════════════════════════════════════════════════════════
// Toast
// ════════════════════════════════════════════════════════════════
let _toastTimer = null;
function showToast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

// ════════════════════════════════════════════════════════════════
// Arch helpers
// ════════════════════════════════════════════════════════════════
function archClass(arch_id) {
  if (!arch_id) return "arch-other";
  if (arch_id.startsWith("sd15"))  return "arch-sd15";
  if (arch_id.startsWith("sdxl") || arch_id.startsWith("pony") || arch_id.startsWith("illustrious")) return "arch-sdxl";
  if (arch_id.startsWith("flux"))  return "arch-flux";
  if (arch_id.startsWith("sd3"))   return "arch-sd3";
  if (arch_id.startsWith("wan"))   return "arch-wan";
  return "arch-other";
}

function contentIcon(type) {
  const icons = { estilo:"🎨", personaje:"👤", concepto:"💡", mixto:"🔀" };
  return icons[type] || "◈";
}

function rankSummary(info) {
  if (!info || !info.ranks) return "";
  const vals = Object.values(info.ranks);
  if (!vals.length) return "";
  const unique = [...new Set(vals)];
  return unique.length === 1 ? `r${unique[0]}` : `r${Math.min(...unique)}–${Math.max(...unique)}`;
}

// ════════════════════════════════════════════════════════════════
// Renderizado de tarjetas LoRA
// ════════════════════════════════════════════════════════════════
function renderLoraList() {
  const list = $("lora-list");
  if (!loras.length) {
    list.innerHTML = `<div class="lora-empty">
      <span class="big-icon">🔀</span>
      <span>Agrega archivos .safetensors<br>para comenzar el merge</span>
    </div>`;
    return;
  }

  list.innerHTML = "";
  loras.forEach((lora, i) => {
    const card = document.createElement("div");
    card.className = "lora-card" + (lora.loading ? " loading" : "");
    card.id = `lora-card-${i}`;

    if (lora.loading) {
      card.innerHTML = `
        <div class="lora-card-top">
          <div class="lora-info">
            <div class="lora-name">${escapeHtml(lora.name)}</div>
            <div class="lora-meta">Analizando...</div>
          </div>
          <button class="lora-remove" onclick="removeLora(${i})">✕</button>
        </div>
        <div class="lora-loading-bar"></div>`;
    } else {
      const info     = lora.info     || {};
      const analysis = lora.analysis || {};
      const aCls     = archClass(info.arch_id);
      const rank     = rankSummary(info);
      const cType    = analysis.content_type || "";
      const cExpl    = analysis.content_explanation || "";

      // Top 5 bloques por score
      const blocks = Object.entries(analysis.block_scores || {})
        .sort((a, b) => b[1] - a[1]).slice(0, 5);
      const maxScore = blocks.length ? blocks[0][1] : 1;

      const barsHtml = blocks.map(([name, score]) => `
        <div class="impact-bar-row">
          <span class="impact-bar-label" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
          <div class="impact-bar-track">
            <div class="impact-bar-fill" style="width:${Math.round(score/maxScore*100)}%"></div>
          </div>
          <span class="impact-val">${Math.round(score)}</span>
        </div>`).join("");

      card.innerHTML = `
        <div class="lora-card-top">
          <span class="lora-arch-badge ${aCls}">${escapeHtml(info.arch || "?")}</span>
          <div class="lora-info">
            <div class="lora-name" title="${escapeHtml(lora.path)}">${escapeHtml(lora.name)}</div>
            <div class="lora-meta">${rank ? rank + " · " : ""}${info.size_mb ? info.size_mb + " MB" : ""}</div>
            ${cType ? `<span class="lora-content-type">${contentIcon(cType)} ${cExpl || cType}</span>` : ""}
          </div>
          <button class="lora-remove" onclick="removeLora(${i})" title="Eliminar">✕</button>
        </div>
        ${blocks.length ? `<div class="impact-section">
          <div class="impact-section-label">Impacto por bloque</div>
          ${barsHtml}
        </div>` : ""}`;

      if (lora.error) {
        const errDiv = document.createElement("div");
        errDiv.style.cssText = "margin-top:6px;font-size:11px;color:var(--status-error)";
        errDiv.textContent = `⚠ ${lora.error}`;
        card.appendChild(errDiv);
      }
    }

    list.appendChild(card);
  });
}

function removeLora(idx) {
  loras.splice(idx, 1);
  renderLoraList();
  updateWeightsPanel();
  updateUI();
}

// ════════════════════════════════════════════════════════════════
// Pesos
// ════════════════════════════════════════════════════════════════
function updateWeightsPanel() {
  const section = $("weights-section");
  const list    = $("weights-list");

  if (loras.length < 2) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";
  list.innerHTML = "";

  loras.forEach((lora, i) => {
    const row  = document.createElement("div");
    row.className = "weight-row";
    const w = lora.weight ?? (1 / loras.length);
    row.innerHTML = `
      <span class="weight-label" title="${escapeHtml(lora.path)}">${escapeHtml(lora.name)}</span>
      <input type="range" class="weight-slider" id="ws-${i}"
             min="0" max="200" step="1" value="${Math.round(w * 100)}">
      <span class="weight-val" id="wv-${i}">${w.toFixed(2)}</span>`;
    list.appendChild(row);

    const slider = row.querySelector(`#ws-${i}`);
    slider.addEventListener("input", () => {
      const val = slider.value / 100;
      loras[i].weight = val;
      $(`wv-${i}`).textContent = val.toFixed(2);
    });
  });
}

function normalizeWeights() {
  if (!loras.length) return;
  const total = loras.reduce((s, l) => s + (l.weight ?? 1), 0) || 1;
  loras.forEach((l, i) => {
    const w = (l.weight ?? 1) / total;
    l.weight = w;
    const slider = $(`ws-${i}`);
    const valEl  = $(`wv-${i}`);
    if (slider) slider.value = Math.round(w * 100);
    if (valEl)  valEl.textContent = w.toFixed(2);
  });
}

// ════════════════════════════════════════════════════════════════
// Validación + UI global
// ════════════════════════════════════════════════════════════════
function updateUI() {
  const count = loras.length;
  $("lora-count").textContent = `${count} LoRA${count !== 1 ? "s" : ""}`;

  const loaded = loras.filter(l => !l.loading && !l.error);
  $("btn-suggest").style.display = loaded.length >= 2 ? "inline-flex" : "none";

  const canMerge = loaded.length >= 2;
  $("btn-merge").disabled = !canMerge;

  if (!count) {
    setValStatus("Sin LoRAs", "");
    return;
  }

  const loading = loras.some(l => l.loading);
  if (loading) {
    setValStatus("Analizando...", "");
    return;
  }

  const errors   = loras.filter(l => l.error).map(l => l.error);
  const archIds  = [...new Set(loaded.map(l => l.info?.arch_id).filter(Boolean))];

  if (errors.length) {
    setValStatus(`${errors.length} error(es)`, "err");
  } else if (archIds.length > 1) {
    setValStatus("Arqs. mixtas — cuidado", "warn");
  } else if (loaded.length >= 2) {
    setValStatus("Listos para merge", "ok");
  } else {
    setValStatus("Agrega al menos 2 LoRAs", "");
  }
}

function setValStatus(text, cls) {
  const el = $("val-status");
  el.textContent = text;
  el.className = cls;
}

// ════════════════════════════════════════════════════════════════
// Método — parámetros avanzados
// ════════════════════════════════════════════════════════════════
const methodDescs = {
  weighted_sum: "Suma ponderada de deltas. Resultado = ∑ wi × ΔWi",
  slerp:        "Interpolación esférica. Transición suave en espacio de alta dimensión. Solo 2 LoRAs.",
  ties:         "Elimina parámetros con baja magnitud y resuelve conflictos de signo por votación.",
  dare:         "Poda aleatoria de parámetros por magnitud y reescala los restantes.",
  dare_ties:    "DARE seguido de TIES: poda + resolución de conflictos combinados.",
};

function onMethodChange() {
  const m = $("cfg-method").value;
  $("method-desc").textContent = methodDescs[m] || "";
  $("adv-slerp").style.display = m === "slerp"                         ? "" : "none";
  $("adv-dare").style.display  = m === "dare"  || m === "dare_ties"    ? "" : "none";
  $("adv-ties").style.display  = m === "ties"  || m === "dare_ties"    ? "" : "none";
}

// ════════════════════════════════════════════════════════════════
// File Browser
// ════════════════════════════════════════════════════════════════
function openBrowser() {
  browserSelected.clear();
  $("browser-overlay").classList.add("open");
  loadBrowserPath("");
}

function closeBrowser() {
  $("browser-overlay").classList.remove("open");
}

async function loadBrowserPath(path) {
  const res  = await fetch(`/api/merger/browse?path=${encodeURIComponent(path)}`);
  const data = await res.json();

  if (data.error) { showToast("❌ " + data.error); return; }

  browserCurrentPath = data.path;
  $("browser-path-input").value = data.path;
  $("browser-up").disabled = !data.parent;

  const list = $("browser-list");
  list.innerHTML = "";

  // Directorios
  data.dirs.forEach(dir => {
    const el = document.createElement("div");
    el.className = "browser-entry";
    el.innerHTML = `<span class="browser-icon">📁</span>
                    <span class="browser-name">${escapeHtml(dir)}</span>`;
    el.addEventListener("click", () => loadBrowserPath(data.path + "/" + dir));
    list.appendChild(el);
  });

  // Archivos .safetensors
  data.files.forEach(file => {
    const alreadyLoaded = loras.some(l => l.path === file.path);
    const el = document.createElement("div");
    el.className = "browser-entry";
    el.innerHTML = `
      <input type="checkbox" class="browser-check" ${browserSelected.has(file.path) ? "checked" : ""}
             ${alreadyLoaded ? "disabled" : ""}>
      <span class="browser-icon">🔷</span>
      <span class="browser-name">${escapeHtml(file.name)}</span>
      <span class="browser-size">${file.size_mb} MB</span>`;
    if (!alreadyLoaded) {
      const cb = el.querySelector("input[type=checkbox]");
      el.addEventListener("click", e => {
        if (e.target !== cb) cb.click();
      });
      cb.addEventListener("change", () => {
        if (cb.checked) browserSelected.add(file.path);
        else            browserSelected.delete(file.path);
        $("browser-selected-count").textContent = `${browserSelected.size} seleccionados`;
      });
    } else {
      el.style.opacity = ".4";
      el.title = "Ya está en la lista";
    }
    list.appendChild(el);
  });

  if (!data.dirs.length && !data.files.length) {
    list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-dim);font-size:13px">
      Sin directorios ni archivos .safetensors aquí.</div>`;
  }

  $("browser-selected-count").textContent = `${browserSelected.size} seleccionados`;
}

async function confirmBrowserSelection() {
  if (!browserSelected.size) return;
  const paths = [...browserSelected];
  closeBrowser();
  browserSelected.clear();

  // Añadir como loading
  const startIdx = loras.length;
  paths.forEach(path => {
    loras.push({
      path,
      name: path.split("/").pop(),
      info: null, analysis: null, weight: null, loading: true, error: null,
    });
  });
  renderLoraList();
  updateUI();

  // Analizar en el backend
  try {
    const res  = await fetch("/api/merger/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths }),
    });
    const data = await res.json();
    data.results.forEach((r, i) => {
      const idx = startIdx + i;
      if (idx >= loras.length) return;
      loras[idx].loading  = false;
      loras[idx].info     = r.info;
      loras[idx].analysis = r.analysis;
      loras[idx].error    = r.error || null;
      loras[idx].weight   = 1 / loras.length;
    });
    // Redistribuir pesos uniformemente
    const n = loras.length;
    loras.forEach(l => l.weight = 1 / n);
  } catch (e) {
    for (let i = startIdx; i < loras.length; i++) {
      loras[i].loading = false;
      loras[i].error   = "Error de conexión con el backend";
    }
  }

  renderLoraList();
  updateWeightsPanel();
  updateUI();
}

// ════════════════════════════════════════════════════════════════
// Sugerencia automática
// ════════════════════════════════════════════════════════════════
async function getSuggestion() {
  const paths = loras.filter(l => !l.loading && !l.error).map(l => l.path);
  if (paths.length < 2) return;

  $("btn-suggest").disabled = true;
  $("btn-suggest").textContent = "...";

  try {
    const res  = await fetch("/api/merger/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths }),
    });
    const s = await res.json();

    // Aplicar sugerencias a la UI
    $("cfg-method").value = s.method || "weighted_sum";
    onMethodChange();

    if (s.layer_filter) $("cfg-layer-filter").value = s.layer_filter;
    if (s.target_rank)  $("cfg-rank").value = s.target_rank;

    if (s.weights && s.weights.length === loras.length) {
      loras.forEach((l, i) => l.weight = s.weights[i]);
      updateWeightsPanel();
    }

    const box = $("suggestion-box");
    box.style.display = "";
    box.innerHTML = `<strong>✦ Sugerencia:</strong> ${escapeHtml(s.method_label)} — ${escapeHtml(s.explanation)}` +
      (s.warnings?.length ? `<br><span style="color:var(--accent-amber)">${s.warnings.map(escapeHtml).join("; ")}</span>` : "");

  } catch (e) {
    showToast("❌ Error obteniendo sugerencia.");
  } finally {
    $("btn-suggest").disabled = false;
    $("btn-suggest").textContent = "✦ Sugerir";
  }
}

// ════════════════════════════════════════════════════════════════
// Merge
// ════════════════════════════════════════════════════════════════
async function startMerge() {
  const paths = loras.filter(l => !l.loading && !l.error).map(l => l.path);
  if (paths.length < 2) return;

  const outputDir  = $("cfg-output-dir").value.trim();
  const outputName = $("cfg-output-name").value.trim() || "merged_lora.safetensors";
  const outputPath = outputDir ? `${outputDir}/${outputName}` : outputName;

  const weights = loras
    .filter(l => !l.loading && !l.error)
    .map(l => l.weight ?? (1 / paths.length));

  const config = {
    method:       $("cfg-method").value,
    weights,
    target_rank:  $("cfg-rank").value ? parseInt($("cfg-rank").value) : null,
    layer_filter: $("cfg-layer-filter").value,
    dare_density: parseFloat($("cfg-dare-density").value),
    ties_k:       parseFloat($("cfg-ties-k").value),
    slerp_t:      parseFloat($("cfg-slerp-t").value),
    output_path:  outputPath,
    output_dtype: $("cfg-dtype").value,
  };

  // Validar output path
  if (!outputDir) {
    showToast("⚠ Especifica un directorio de salida.");
    return;
  }

  $("btn-merge").disabled = true;
  $("merge-progress").style.display = "";
  $("merge-result").style.display   = "none";
  $("merge-progress-fill").style.width = "0%";
  $("merge-progress-text").textContent = "Iniciando...";

  try {
    const res     = await fetch("/api/merger/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, config }),
    });
    const { session_id } = await res.json();

    const sse = new EventSource(`/api/merger/progress/${session_id}`);
    sse.onmessage = e => {
      const ev = JSON.parse(e.data);
      if (ev.type === "ping") return;

      if (ev.type === "progress") {
        const pct = ev.total ? Math.round(ev.current / ev.total * 100) : 0;
        $("merge-progress-fill").style.width = `${pct}%`;
        $("merge-progress-text").textContent =
          `${ev.current}/${ev.total}  ${ev.layer || ""}`;
      }

      if (ev.type === "done") {
        sse.close();
        $("merge-progress-fill").style.width = "100%";
        $("merge-result").style.display = "";
        if (ev.output_path) {
          $("merge-result").innerHTML =
            `<div class="merge-ok">✅ Merge completado:<br>
             <span style="font-family:JetBrains Mono,monospace;font-size:11px">
               ${escapeHtml(ev.output_path)}
             </span></div>`;
        } else {
          $("merge-result").innerHTML =
            `<div class="merge-fail">❌ ${escapeHtml(ev.error || "Error desconocido")}</div>`;
        }
        $("btn-merge").disabled = false;
        $("merge-progress-text").textContent = "Finalizado.";
      }
    };
    sse.onerror = () => {
      sse.close();
      $("merge-result").style.display = "";
      $("merge-result").innerHTML =
        `<div class="merge-fail">❌ Error de conexión con el stream de progreso.</div>`;
      $("btn-merge").disabled = false;
    };
  } catch (e) {
    showToast("❌ Error iniciando el merge.");
    $("btn-merge").disabled = false;
  }
}

// ════════════════════════════════════════════════════════════════
// Helpers
// ════════════════════════════════════════════════════════════════
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ════════════════════════════════════════════════════════════════
// Init
// ════════════════════════════════════════════════════════════════
document.addEventListener("DOMContentLoaded", () => {
  $("btn-add").addEventListener("click", openBrowser);
  $("btn-suggest").addEventListener("click", getSuggestion);
  $("btn-merge").addEventListener("click", startMerge);
  $("btn-normalize").addEventListener("click", normalizeWeights);
  $("cfg-method").addEventListener("change", onMethodChange);

  $("browser-go").addEventListener("click", () =>
    loadBrowserPath($("browser-path-input").value));
  $("browser-path-input").addEventListener("keydown", e => {
    if (e.key === "Enter") loadBrowserPath($("browser-path-input").value);
  });
  $("browser-up").addEventListener("click", () => {
    const parent = $("browser-path-input").value.split("/").slice(0, -1).join("/") || "/";
    loadBrowserPath(parent);
  });
  $("browser-confirm").addEventListener("click", confirmBrowserSelection);
  $("browser-overlay").addEventListener("click", e => {
    if (e.target === $("browser-overlay")) closeBrowser();
  });

  // Cargar outputs path del hub como default del output-dir
  fetch("/api/settings").then(r => r.json()).then(d => {
    const outDir = d.paths?.outputs || "";
    if (outDir) $("cfg-output-dir").value = outDir;
  }).catch(() => {});

  onMethodChange();
});
