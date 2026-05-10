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
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
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
      <span>${t("merger.empty").replace("\n", "<br>")}</span>
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
            <div class="lora-meta">${t("merger.analyzing")}</div>
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
          <div class="impact-section-label">${t("merger.impact_title")}</div>
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
  $("lora-count").textContent = count !== 1
    ? t("merger.lora_count_plural", { count })
    : t("merger.lora_count", { count });

  const loaded = loras.filter(l => !l.loading && !l.error);
  $("btn-suggest").style.display = loaded.length >= 2 ? "inline-flex" : "none";

  const canMerge = loaded.length >= 2;
  $("btn-merge").disabled = !canMerge;

  if (!count) {
    setValStatus(t("merger.no_loras"), "");
    return;
  }

  const loading = loras.some(l => l.loading);
  if (loading) {
    setValStatus(t("merger.status_analyzing"), "");
    return;
  }

  const errors   = loras.filter(l => l.error).map(l => l.error);
  const archIds  = [...new Set(loaded.map(l => l.info?.arch_id).filter(Boolean))];

  if (errors.length) {
    setValStatus(t("merger.status_errors", { count: errors.length }), "err");
  } else if (archIds.length > 1) {
    setValStatus(t("merger.status_mixed_arch"), "warn");
  } else if (loaded.length >= 2) {
    setValStatus(t("merger.status_ready"), "ok");
  } else {
    setValStatus(t("merger.status_need_more"), "");
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
function onMethodChange() {
  const m = $("cfg-method").value;
  $("method-desc").textContent = t(`merger.method.${m}`) || "";
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
  try {
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
          $("browser-selected-count").textContent =
            t("merger.browser_selected", { count: browserSelected.size });
        });
      } else {
        el.style.opacity = ".4";
        el.title = t("merger.already_loaded");
      }
      list.appendChild(el);
    });

    if (!data.dirs.length && !data.files.length) {
      list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-dim);font-size:13px">
        ${t("merger.browser_empty")}</div>`;
    }

    $("browser-selected-count").textContent =
      t("merger.browser_selected", { count: browserSelected.size });
  } catch (_) {
    showToast("❌ " + t("merger.error_browse"));
  }
}

async function addLorasFromPaths(paths) {
  paths = paths.filter(p => !loras.some(l => l.path === p));
  if (!paths.length) return;

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
    });
    const n = loras.length;
    loras.forEach(l => l.weight = 1 / n);
  } catch (_) {
    for (let i = startIdx; i < loras.length; i++) {
      loras[i].loading = false;
      loras[i].error   = t("merger.error_analyze");
    }
  }

  renderLoraList();
  updateWeightsPanel();
  updateUI();
}

async function confirmBrowserSelection() {
  if (!browserSelected.size) return;
  const paths = [...browserSelected];
  closeBrowser();
  browserSelected.clear();
  await addLorasFromPaths(paths);
}

async function openNativeFilePicker() {
  let initDir = "";
  try {
    const s = await fetch("/api/settings").then(r => r.json());
    initDir = s.paths?.models || "";
  } catch (_) {}

  try {
    const res = await fetch(`/api/merger/pick-files?initial_dir=${encodeURIComponent(initDir)}`);
    if (res.ok) {
      const data = await res.json();
      if (data.paths && data.paths.length) {
        await addLorasFromPaths(data.paths);
        return;
      }
      if (data.available) return;
    }
  } catch (_) {}

  openBrowser();
}

// ════════════════════════════════════════════════════════════════
// Sugerencia automática
// ════════════════════════════════════════════════════════════════
async function getSuggestion() {
  const paths = loras.filter(l => !l.loading && !l.error).map(l => l.path);
  if (paths.length < 2) return;

  $("btn-suggest").disabled = true;
  $("btn-suggest").textContent = t("merger.btn_suggesting");

  try {
    const res  = await fetch("/api/merger/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths }),
    });
    const s = await res.json();

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
    box.innerHTML = `<strong>${t("merger.suggestion_label")}</strong> ${escapeHtml(s.method_label)} — ${escapeHtml(s.explanation)}` +
      (s.warnings?.length ? `<br><span style="color:var(--accent-amber)">${s.warnings.map(escapeHtml).join("; ")}</span>` : "");

  } catch (_) {
    showToast("❌ " + t("merger.error_suggestion"));
  } finally {
    $("btn-suggest").disabled = false;
    $("btn-suggest").textContent = t("merger.btn_suggest");
  }
}

// ════════════════════════════════════════════════════════════════
// Merge — polling + lock de UI
// ════════════════════════════════════════════════════════════════
let _mergePoller   = null;
let _mergeSession  = null;

function _lockMergeUI() {
  document.body.classList.add("merging");
  [
    "cfg-method", "cfg-layer-filter", "cfg-rank",
    "cfg-output-dir", "cfg-output-name", "cfg-dtype",
    "cfg-slerp-t", "cfg-dare-density", "cfg-ties-k",
    "btn-add", "btn-suggest", "btn-normalize",
  ].forEach(id => { const el = $(id); if (el) el.disabled = true; });
  document.querySelectorAll(".weight-slider, .weight-val input").forEach(el => {
    el.disabled = true;
  });
}

function _unlockMergeUI() {
  document.body.classList.remove("merging");
  [
    "cfg-method", "cfg-layer-filter", "cfg-rank",
    "cfg-output-dir", "cfg-output-name", "cfg-dtype",
    "cfg-slerp-t", "cfg-dare-density", "cfg-ties-k",
    "btn-add", "btn-suggest", "btn-normalize",
  ].forEach(id => { const el = $(id); if (el) el.disabled = false; });
  document.querySelectorAll(".weight-slider, .weight-val input").forEach(el => {
    el.disabled = false;
  });
  updateUI();
}

function _stopPoller() {
  if (_mergePoller) { clearInterval(_mergePoller); _mergePoller = null; }
}

function _startPoller(session_id) {
  _stopPoller();
  _mergeSession = session_id;
  _mergePoller  = setInterval(async () => {
    try {
      const res = await fetch(`/api/merger/status/${session_id}`);
      if (!res.ok) {
        _stopPoller();
        _finishMergeError(t("merger.error_status"));
        return;
      }
      const ev = await res.json();

      if (ev.type === "progress") {
        const cur = ev.current || 0;
        const tot = ev.total   || 0;
        const pct = tot ? Math.round(cur / tot * 100) : 2;

        $("merge-bar-fill").style.width  = `${Math.max(pct, 2)}%`;
        $("merge-pct-badge").textContent = `${pct}%`;
        $("merge-layer-text").textContent = tot
          ? t("merger.layer_text", { cur, tot, layer: ev.layer || "" })
          : (ev.layer || t("merger.merging"));
        $("merge-status-label").textContent = t("merger.merging");
      }

      if (ev.type === "done") {
        _stopPoller();
        $("merge-bar-fill").style.width  = "100%";
        $("merge-pct-badge").textContent = "100%";
        if (ev.output_path) {
          _finishMergeOk(ev.output_path);
        } else {
          _finishMergeError(ev.error || t("merger.unknown_error"));
        }
      }
    } catch (_) {
      // Error de red transitorio — el merge sigue corriendo, ignorar
    }
  }, 600);
}

function _finishMergeOk(outputPath) {
  _unlockMergeUI();
  $("merge-status-label").textContent = t("merger.completed");
  $("merge-result").style.display = "";
  $("merge-result").innerHTML = `
    <div class="merge-ok">
      <div class="merge-ok-title">${t("merger.merge_ok_title")}</div>
      <div class="merge-ok-path">${escapeHtml(outputPath)}</div>
    </div>`;
}

function _finishMergeError(msg) {
  _unlockMergeUI();
  $("merge-status-label").textContent = t("merger.error");
  $("merge-pct-badge").textContent = "!";
  $("merge-pct-badge").style.color = "var(--accent-red)";
  $("merge-pct-badge").style.background = "rgba(255,69,101,.12)";
  $("merge-result").style.display = "";
  $("merge-result").innerHTML = `
    <div class="merge-fail">
      <div class="merge-fail-title">${t("merger.merge_error_title")}</div>
      <div class="merge-fail-msg">${escapeHtml(msg)}</div>
    </div>`;
}

async function cancelMerge() {
  if (!_mergeSession) return;
  $("btn-cancel").disabled = true;
  $("btn-cancel").textContent = t("merger.canceling");
  try {
    await fetch(`/api/merger/cancel/${_mergeSession}`, { method: "POST" });
  } catch (_) {}
}

async function startMerge() {
  const paths = loras.filter(l => !l.loading && !l.error).map(l => l.path);
  if (paths.length < 2) return;

  const outputDir  = $("cfg-output-dir").value.trim();
  const outputName = $("cfg-output-name").value.trim() || "merged_lora.safetensors";
  const outputPath = outputDir ? `${outputDir}/${outputName}` : outputName;

  if (!outputDir) {
    showToast("⚠ " + t("merger.no_output_dir"));
    return;
  }

  const weights = loras
    .filter(l => !l.loading && !l.error)
    .map(l => l.weight ?? (1 / paths.length));

  const config = {
    method:       $("cfg-method").value,
    weights,
    target_rank:  $("cfg-rank").value ? parseInt($("cfg-rank").value) : null,
    layer_filter: $("cfg-layer-filter").value,
    dare_density: parseFloat($("cfg-dare-density").value) || 0.5,
    ties_k:       parseFloat($("cfg-ties-k").value)       || 0.2,
    slerp_t:      parseFloat($("cfg-slerp-t").value)      || 0.5,
    output_path:  outputPath,
    output_dtype: $("cfg-dtype").value,
  };

  $("merge-result").style.display = "none";
  $("merge-result").innerHTML = "";
  $("merge-pct-badge").style.color = "";
  $("merge-pct-badge").style.background = "";
  $("merge-pct-badge").textContent = "0%";
  $("merge-bar-fill").style.width = "2%";
  $("merge-layer-text").textContent = t("merger.initializing");
  $("merge-status-label").textContent = t("merger.merge_starting");
  $("btn-cancel").disabled = false;
  $("btn-cancel").textContent = t("merger.btn_cancel");

  _lockMergeUI();

  try {
    const res = await fetch("/api/merger/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, config }),
    });
    if (!res.ok) {
      _finishMergeError(t("merger.error_start"));
      return;
    }
    const { session_id } = await res.json();
    _startPoller(session_id);
  } catch (_) {
    _finishMergeError(t("merger.error_conn"));
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
  $("btn-add").addEventListener("click", openNativeFilePicker);
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

  fetch("/api/settings").then(r => r.json()).then(d => {
    const outDir = d.paths?.outputs || "";
    if (outDir) $("cfg-output-dir").value = outDir;
  }).catch(() => {});

  onMethodChange();
});
