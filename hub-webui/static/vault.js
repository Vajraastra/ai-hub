/* Model Vault WebUI */

// ════════════════════════════════════════════════════════════════
// Estado
// ════════════════════════════════════════════════════════════════
const $ = id => document.getElementById(id);
let allModels      = [];
let filteredModels = [];
let currentCat     = "all";
let currentSubfolder = null;   // null = todas; otro valor = directorio físico relativo completo
let expandedCat    = null;     // qué categoría tiene su lista de subfolders visible
let subfolderMap   = {};       // { "lora": { "personajes": 5, "": 2 }, ... }
let currentSearch  = "";
let currentArch    = "";
let currentImageFilter = "all";
let currentSort    = "visual";
let selectedHash   = null;
let _notesTimer    = null;
let pendingMoves   = {};   // { hash: { targetSub, targetCat, label } }
let operationBusy  = false;
let modelsRequestId = 0;
let detailRequestId = 0;
const savingModels = new Set();

const CATEGORIES = [
  { id: "checkpoint",        label: () => t("vault.cat_checkpoint") },
  { id: "lora",              label: () => t("vault.cat_lora")       },
  { id: "locon",             label: () => t("vault.cat_lycoris")    },
  { id: "textualinversion",  label: () => t("vault.cat_embedding")  },
  { id: "vae",               label: () => t("vault.cat_vae")        },
  { id: "upscaler",          label: () => t("vault.cat_upscaler")   },
  { id: "diffusion_model",   label: () => t("vault.cat_diffusion")  },
  { id: "controlnet",        label: () => "ControlNet"              },
  { id: "clip",              label: () => t("vault.cat_encoder")    },
  { id: "embedding",         label: () => t("vault.cat_embedding")  },
  { id: "unknown",           label: () => t("vault.cat_unknown")    },
];

function modelCategories() {
  const known = new Set(CATEGORIES.map(c => c.id));
  const extra = [...new Set(allModels.map(m => (m.model_type || "unknown").toLowerCase()))]
    .filter(id => !known.has(id)).sort();
  return [...CATEGORIES, ...extra.map(id => ({ id, label: () => id }))];
}

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
// Copy for WebUI
// ════════════════════════════════════════════════════════════════
function getCopyString(model, weight) {
  if (model.missing) return null;
  const type       = (model.model_type || "").toLowerCase();
  const fname      = String(model.file_path || model.name || "").replace(/\\/g, "/").split("/").pop();
  const nameNoExt  = fname.replace(/\.[^.]+$/, "");
  if (!nameNoExt) return null;

  if (type === "lora" || type === "locon")  return `<lora:${nameNoExt}:${weight}>`;
  if (type === "textualinversion" || type === "embedding") return `(${nameNoExt}:${weight})`;
  return null;
}

function copyWeight() {
  const weight = Number.parseFloat($("copy-weight")?.value);
  return Number.isFinite(weight) ? weight : 1;
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(`${t("vault.copied")}: ${text}`);
  } catch (_) {
    showToast("❌ " + t("vault.copy_failed"));
  }
}

// Llamada desde onclick inline en el detail panel
function copyModelFromDetail(hash) {
  const m = allModels.find(x => x.hash === hash);
  if (!m) return;
  const weight = copyWeight();
  const str    = getCopyString(m, weight);
  if (str) copyToClipboard(str);
}

function updateCopyPreview(hash) {
  const m = allModels.find(x => x.hash === hash);
  if (!m) return;
  const weight = copyWeight();
  const el     = $("copy-string-preview");
  if (el) el.textContent = getCopyString(m, weight) || "";
}

// ════════════════════════════════════════════════════════════════
// Carga de modelos
// ════════════════════════════════════════════════════════════════
async function loadModels() {
  const requestId = ++modelsRequestId;
  try {
    const res  = await fetch("/api/vault/models");
    if (!res.ok) throw new Error(t("vault.load_error"));
    const data = await res.json();
    if (requestId !== modelsRequestId) return;
    if (!Array.isArray(data.models)) throw new Error(t("vault.load_error"));
    allModels = data.models || [];
    computeSubfolderMap();
    renderArchitectureOptions();
    renderSidebar();
    applyFilters();
    return true;
  } catch (_) {
    if (requestId !== modelsRequestId) return;
    if (allModels.length) showToast("❌ " + t("vault.load_error"));
    else $("model-grid").innerHTML =
      `<div class="grid-empty"><span class="big-icon">❌</span>${t("vault.load_error")}</div>`;
    return false;
  }
}

function refresh() {
  loadModels();
}

function thumbnailUrl(model) {
  if (!model.has_thumbnail) return null;
  return model.thumbnail_url || `/api/vault/thumbnail?path=${encodeURIComponent(model.file_path)}`;
}

function previewState(model) {
  if (model.missing) return "vault.file_missing";
  const status = model.civitai_assets_status || model.preview_status;
  if (status === "not_found") return "vault.preview_not_found";
  if (status === "unavailable") return "vault.preview_unavailable";
  if (status === "error") return "vault.preview_error";
  return "vault.preview_pending";
}

function previewPlaceholder(model) {
  const state = previewState(model);
  return `<div class="model-thumb-placeholder"><span class="preview-placeholder-icon" aria-hidden="true">▧</span>
    <span class="preview-placeholder-status">${escapeHtml(t(state))}</span></div>`;
}

function showOperationResult(message, warning = false) {
  const result = $("operation-result");
  result.hidden = false;
  result.textContent = message;
  result.classList.toggle("warning", warning);
}

function civitaiResultMessage(stats) {
  return t("vault.civitai_done", {
    synced: stats.synced ?? 0, skipped: stats.skipped ?? 0,
    previews: stats.previews_downloaded ?? 0, no_preview: stats.no_preview ?? 0,
    not_found: stats.not_found ?? 0, errors: stats.errors ?? 0
  });
}

// ════════════════════════════════════════════════════════════════
// Renderizado del grid
// ════════════════════════════════════════════════════════════════
function renderGrid() {
  const grid = $("model-grid");
  const cnt = filteredModels.length;
  $("model-count").textContent = cnt !== 1
    ? t("vault.models_count_plural", { count: cnt })
    : t("vault.models_count", { count: cnt });

  if (!filteredModels.length) {
    const emptyMsg = currentSearch
      ? t("vault.empty_search", { query: currentSearch })
      : t("vault.empty") + ".";
    grid.innerHTML = `<div class="grid-empty">
      <span class="big-icon">📦</span>
      ${escapeHtml(emptyMsg)}
      ${!allModels.length ? `<br>${t("vault.empty_hint")}` : ""}
    </div>`;
    return;
  }

  grid.innerHTML = "";
  filteredModels.forEach(m => {
    const pending     = pendingMoves[m.hash];
    const displayName = m.display_name || m.name || "Sin nombre";
    const modelType   = m.model_type   || "";
    const baseModel   = m.base_model   || "";
    const thumbUrl    = thumbnailUrl(m);

    const copyStr = getCopyString(m, 1);

    const card = document.createElement("div");
    card.className = [
      "model-card",
      m.hash === selectedHash ? "selected"     : "",
      pending                 ? "pending-move" : "",
    ].filter(Boolean).join(" ");
    card.dataset.hash = m.hash;
    card.draggable    = !m.missing;

    card.innerHTML = `
      ${thumbUrl
        ? `<img class="model-thumb" src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(displayName)}" loading="lazy">`
        : previewPlaceholder(m)}
      <div class="model-card-body">
        <div class="model-card-name" title="${escapeHtml(m.file_path || displayName)}">${escapeHtml(displayName)}</div>
        <div class="model-card-meta">
          ${baseModel ? `<span class="model-type-badge">${escapeHtml(baseModel)}</span> ` : ""}
          ${modelType ? `<span style="color:var(--text-dim)">${escapeHtml(modelType)}</span>` : ""}
        </div>
        ${m.missing ? `<span class="missing-badge">${t("vault.file_missing")}</span>` : ""}
        ${pending ? `<span class="move-badge">→ ${escapeHtml(pending.label)}</span>` : ""}
      </div>
      ${copyStr ? `<button class="card-copy-btn" title="${escapeHtml(copyStr)}">⎘</button>` : ""}`;

    // Copy button (solo tipos con formato WebUI)
    if (copyStr) {
      card.querySelector(".card-copy-btn").addEventListener("click", e => {
        e.stopPropagation();
        copyToClipboard(copyStr);
      });
    }

    // Drag events
    card.addEventListener("dragstart", e => {
      if (m.missing || operationBusy) { e.preventDefault(); return; }
      // Ghost semitransparente
      const ghost = card.cloneNode(true);
      ghost.style.cssText = "position:fixed;top:-9999px;left:-9999px;width:160px;opacity:0.6;pointer-events:none;";
      document.body.appendChild(ghost);
      e.dataTransfer.setDragImage(ghost, e.offsetX, e.offsetY);
      setTimeout(() => ghost.remove(), 0);

      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("application/json", JSON.stringify({
        hash:   m.hash,
        catId:  (m.model_type || "unknown").toLowerCase(),
        folder: modelFolder(m),
      }));
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));

    card.addEventListener("click", () => openDetails(m.hash));
    grid.appendChild(card);
  });
}

// ════════════════════════════════════════════════════════════════
// Subfolders
// ════════════════════════════════════════════════════════════════
function computeSubfolderMap() {
  subfolderMap = Object.create(null);
  libraryFilterModels().forEach(m => {
    const cat = (m.model_type || "unknown").toLowerCase();
    const sub = modelFolder(m);
    if (!subfolderMap[cat]) subfolderMap[cat] = Object.create(null);
    subfolderMap[cat][sub] = (subfolderMap[cat][sub] || 0) + 1;
  });
}

function selectCategory(catId) {
  if (catId === "all") {
    currentCat       = "all";
    currentSubfolder = null;
    expandedCat      = null;
  } else if (currentCat === catId) {
    expandedCat = currentSubfolder !== null || expandedCat !== catId ? catId : null;
    currentSubfolder = null;
  } else {
    currentCat       = catId;
    currentSubfolder = null;
    expandedCat      = catId;
  }
  renderSidebar();
  applyFilters();
}

function selectSubfolder(catId, sub) {
  // Toggle: click en el mismo subfolder activo lo deselecciona
  const isSame = currentCat === catId && currentSubfolder === sub;
  currentCat       = catId;
  currentSubfolder = isSame ? null : sub;
  expandedCat      = catId;
  renderSidebar();
  applyFilters();
}

function renderSidebar() {
  computeSubfolderMap();
  const visibleModels = libraryFilterModels();
  const nav = $("sidebar");
  nav.innerHTML = "";

  const title = document.createElement("div");
  title.className = "sidebar-title";
  title.textContent = t("vault.sidebar_type");
  nav.appendChild(title);

  // Botón "Todos"
  const allBtn = document.createElement("button");
  allBtn.className = "cat-btn" + (currentCat === "all" ? " active" : "");
  allBtn.innerHTML = `<span>${t("vault.cat_all")}</span><span class="cat-count">${visibleModels.length}</span>`;
  allBtn.addEventListener("click", () => selectCategory("all"));
  nav.appendChild(allBtn);

  modelCategories().forEach(({ id, label }) => {
    const catCount = visibleModels.filter(m => (m.model_type || "unknown").toLowerCase() === id).length;
    if (!catCount) return;
    const subs     = subfolderMap[id] || {};
    const subEntries = Object.entries(subs).sort(([a], [b]) => {
      if (a === "") return -1;
      if (b === "") return 1;
      return a.localeCompare(b);
    });
    const hasSubfolders = subEntries.length > 0;
    const isActive      = currentCat === id;
    const isExpanded    = expandedCat === id;

    const btn = document.createElement("button");
    btn.className = [
      "cat-btn",
      isActive   ? "active"   : "",
      isExpanded && hasSubfolders ? "expanded" : "",
    ].filter(Boolean).join(" ");
    btn.innerHTML = `
      <span>${escapeHtml(label())}</span>
      <span style="display:flex;align-items:center;gap:2px">
        <span class="cat-count">${catCount}</span>
        ${hasSubfolders ? `<span class="cat-expand">&#9658;</span>` : ""}
      </span>`;
    btn.addEventListener("click", () => selectCategory(id));
    nav.appendChild(btn);

    if (hasSubfolders) {
      const list = document.createElement("div");
      list.className = "subfolder-list";
      if (!isExpanded) list.hidden = true;

      subEntries.forEach(([sub, cnt]) => {
        const isSubActive = isActive && currentSubfolder === sub;
        const subBtn = document.createElement("button");
        subBtn.className = [
          "subfolder-btn",
          !sub.includes("/") ? "is-root" : "",
          isSubActive ? "active" : "",
        ].filter(Boolean).join(" ");
        subBtn.innerHTML = `
          <span class="subfolder-label">${sub === "" ? t("vault.subfolder_root") : escapeHtml(sub)}</span>
          <span class="subfolder-count">${cnt}</span>`;
        subBtn.addEventListener("click", e => {
          e.stopPropagation();
          selectSubfolder(id, sub);
        });

        // Drop target
        subBtn.addEventListener("dragover", e => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          subBtn.classList.add("drop-target");
        });
        subBtn.addEventListener("dragleave", e => {
          if (!subBtn.contains(e.relatedTarget)) {
            subBtn.classList.remove("drop-target");
          }
        });
        subBtn.addEventListener("drop", e => {
          e.preventDefault();
          e.stopPropagation();
          subBtn.classList.remove("drop-target");
          let dragData;
          try { dragData = JSON.parse(e.dataTransfer.getData("application/json")); }
          catch (_) { return; }
          stageMove(dragData, id, sub);
        });

        list.appendChild(subBtn);
      });

      nav.appendChild(list);
    }
  });
}

// ════════════════════════════════════════════════════════════════
// Panel de detalles
// ════════════════════════════════════════════════════════════════
async function openDetails(hash) {
  if (selectedHash === hash) {
    closeDetails();
    return;
  }
  selectedHash = hash;
  const requestId = ++detailRequestId;

  // Marcar tarjeta activa
  document.querySelectorAll(".model-card").forEach(c => {
    c.classList.toggle("selected", c.dataset.hash === hash);
  });

  $("details-panel").classList.add("open");
  $("details-content").innerHTML =
    `<div style="padding:20px;text-align:center;color:var(--text-dim)">${t("vault.loading")}</div>`;

  try {
    const res = await fetch(`/api/vault/models/${hash}`);
    if (requestId !== detailRequestId || selectedHash !== hash) return;
    if (!res.ok) throw new Error(t("vault.detail_not_found"));
    const m = await res.json();
    if (requestId !== detailRequestId || selectedHash !== hash) return;
    renderDetails(m);
  } catch (error) {
    if (requestId !== detailRequestId || selectedHash !== hash) return;
    $("details-content").innerHTML =
      `<div style="color:var(--status-error);font-size:12px">❌ ${escapeHtml(error.message || t("vault.detail_error"))}</div>`;
  }
}

function closeDetails() {
  detailRequestId++;
  selectedHash = null;
  $("details-panel").classList.remove("open");
  document.querySelectorAll(".model-card.selected").forEach(c => c.classList.remove("selected"));
}

function renderDetails(m) {
  const displayName = m.display_name || m.name || "Sin nombre";
  const thumbUrl = thumbnailUrl(m);

  const triggers = (m.triggers || "").split(",").map(s => s.trim()).filter(Boolean);
  const triggerHtml = triggers.length
    ? triggers.map(s => `<span class="trigger-chip">${escapeHtml(s)}</span>`).join("")
    : `<span style="color:var(--text-dim);font-size:12px">${t("vault.detail_no_triggers")}</span>`;

  const civitaiTags = (m.civitai_tags || "").split(",").map(s => s.trim()).filter(Boolean);
  const civitaiTagsHtml = civitaiTags.length
    ? civitaiTags.map(s => `<span class="civitai-tag-chip">${escapeHtml(s)}</span>`).join("")
    : null;
  const otherPaths = (m.file_paths || []).filter(filePath => normalizeSearch(filePath) !== normalizeSearch(m.file_path));

  $("details-content").innerHTML = `
    ${thumbUrl
      ? `<img class="detail-thumb" src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(displayName)}">`
      : `<p class="detail-preview-status">${escapeHtml(t(previewState(m)))}</p>`}
    <div class="detail-name">${escapeHtml(displayName)}</div>
    ${m.missing ? `<p class="missing-badge">${t("vault.file_missing")}</p>` : ""}

    <div class="detail-row">
      <span class="detail-lbl">${t("vault.detail_type")}</span>
      <span class="detail-val">${escapeHtml(m.model_type || "—")}</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">${t("vault.detail_base")}</span>
      <span class="detail-val">${escapeHtml(m.base_model || "—")}</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">${t("vault.detail_version")}</span>
      <span class="detail-val">${escapeHtml(m.version_name || "—")}</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">${t("vault.detail_creator")}</span>
      <span class="detail-val">${escapeHtml(m.creator_name || "—")}</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">${t("vault.detail_file")}</span>
      <span class="detail-val" style="font-family:'JetBrains Mono',monospace;font-size:11px;word-break:break-all">
        ${escapeHtml(m.name || "—")}
      </span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">${t("vault.detail_path")}</span>
      <span class="detail-val" style="font-size:11px;word-break:break-all">${escapeHtml(m.file_path || "—")}</span>
    </div>
    ${otherPaths.length ? `
    <div class="detail-section-title">${t("vault.other_locations")}</div>
    <div style="font-size:11px;line-height:1.6;word-break:break-all">${otherPaths.map(escapeHtml).join("<br>")}</div>
    <p style="font-size:11px;color:var(--text-dim);margin-top:6px">${t("vault.move_primary_only")}</p>` : ""}

    ${getCopyString(m, 1) !== null ? `
    <div class="detail-section-title">${t("vault.copy_webui")}</div>
    <div class="copy-webui-row">
      <input type="number" id="copy-weight" class="copy-weight-input"
             value="1" step="0.05"
             oninput="updateCopyPreview('${m.hash}')">
      <span id="copy-string-preview" class="copy-string-preview">${escapeHtml(getCopyString(m, 1) || "")}</span>
      <button class="btn btn-outline" style="font-size:11px;padding:4px 10px;flex-shrink:0"
              onclick="copyModelFromDetail('${m.hash}')">${t("vault.copy_webui")}</button>
    </div>` : ""}

    ${m.description ? `
    <div class="detail-section-title">${t("vault.detail_description")}</div>
    <div style="font-size:12px;color:var(--text-secondary);line-height:1.5">
      ${escapeHtml(m.description).substring(0, 400)}${m.description.length > 400 ? "…" : ""}
    </div>` : ""}

    <div class="detail-section-title">${t("vault.detail_triggers")}</div>
    <div>${triggerHtml}</div>

    ${civitaiTagsHtml ? `
    <div class="detail-section-title">${t("vault.detail_civitai_tags")}</div>
    <div>${civitaiTagsHtml}</div>` : ""}

    <div class="detail-section-title">${t("vault.detail_custom_tags")}</div>
    <input type="text" id="detail-tags" value="${escapeHtml(m.custom_tags || "")}"
           placeholder="${escapeHtml(t("vault.detail_custom_tags_placeholder"))}">

    <div class="detail-section-title">${t("vault.detail_notes")}</div>
    <textarea id="detail-notes" placeholder="${escapeHtml(t("vault.detail_notes_placeholder"))}">${escapeHtml(m.user_notes || "")}</textarea>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button id="detail-save" class="btn btn-primary" style="flex:1;font-size:12px" ${savingModels.has(m.hash) ? "disabled" : ""}
              onclick="saveModelData('${m.hash}')">${t("vault.detail_save")}</button>
    </div>`;
}

async function saveModelData(hash) {
  if (selectedHash !== hash || savingModels.has(hash)) return;
  const notes = $("detail-notes")?.value ?? null;
  const tags  = $("detail-tags")?.value  ?? null;
  savingModels.add(hash);
  if ($("detail-save")) $("detail-save").disabled = true;
  try {
    const data = await checkedJson(`/api/vault/models/${hash}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_notes: notes, custom_tags: tags }),
    });
    if (!data.ok) throw new Error(t("vault.save_error"));
    const model = allModels.find(m => m.hash === hash);
    if (model) { model.user_notes = notes; model.custom_tags = tags; }
    applyFilters();
    showToast("✅ " + t("vault.saved"));
  } catch (error) {
    showToast("❌ " + (error.message || t("vault.conn_error")));
  } finally {
    savingModels.delete(hash);
    if (selectedHash === hash && $("detail-save")) $("detail-save").disabled = false;
  }
}

// ════════════════════════════════════════════════════════════════
// Scan con SSE
// ════════════════════════════════════════════════════════════════
const OPERATION_BUTTONS = ["btn-scan", "btn-civitai-menu", "btn-civitai-sync", "btn-civitai-force", "btn-apply-moves", "btn-cancel-moves"];

function setOperationBusy(busy) {
  operationBusy = busy;
  OPERATION_BUTTONS.forEach(id => { $(id).disabled = busy; });
  if (busy) closeCivitaiMenu();
}

function closeCivitaiMenu() {
  $("civitai-menu").hidden = true;
  $("btn-civitai-menu").setAttribute("aria-expanded", "false");
}

async function withLibraryOperation(action) {
  if (operationBusy) return;
  setOperationBusy(true);
  try {
    await action();
  } catch (error) {
    showToast("❌ " + error.message);
  } finally {
    $("scan-progress").classList.remove("visible");
    setOperationBusy(false);
  }
}

async function checkedJson(url, options) {
  const res = await fetch(url, options);
  const info = await res.json();
  if (!res.ok) throw new Error(info.detail || info.error || `Error ${res.status}`);
  return info;
}

async function runLibraryJob(url, kind) {
  const local = kind === "scan";
  $("operation-result").hidden = true;
  $("scan-kind").dataset.kind = local ? "vault.scanning_local" : "vault.downloading_civitai";
  $("scan-kind").textContent = t(local ? "vault.scanning_local" : "vault.downloading_civitai");
  $("scan-progress").classList.add("visible");
  $("scan-bar-fill").style.width = "0%";
  $("scan-label").textContent = t(local ? "vault.scan_starting" : "vault.civitai_connecting");
  $("scan-fraction").textContent = "";
  const info = await checkedJson(url, { method: "POST" });
  if (!info.session_id) throw new Error(t(local ? "vault.scan_start_error" : "vault.civitai_start_error"));
  return new Promise((resolve, reject) => {
    const sse = new EventSource(`/api/vault/${local ? "scan-progress" : "civitai-progress"}/${encodeURIComponent(info.session_id)}`);
    const fail = message => { sse.close(); reject(new Error(message)); };
    sse.onmessage = e => {
      let ev;
      try { ev = JSON.parse(e.data); }
      catch (_) { fail(t("vault.invalid_progress")); return; }
      if (ev.type === "progress") {
        const phaseKey = ev.phase === "civitai" || (!local && ev.phase !== "local")
          ? "vault.downloading_civitai" : "vault.scanning_local";
        $("scan-kind").dataset.kind = phaseKey;
        $("scan-kind").textContent = t(phaseKey);
        const pct = ev.total ? Math.round(ev.current / ev.total * 100) : 0;
        $("scan-bar-fill").style.width = `${pct}%`;
        $("scan-label").textContent = ev.name || "";
        $("scan-fraction").textContent = ev.total ? `${ev.current}/${ev.total}` : "";
      } else if (ev.type === "done") {
        sse.close();
        resolve(ev);
      } else if (ev.type === "error") {
        fail(ev.error || t(local ? "vault.scan_error" : "vault.civitai_stream_error"));
      }
    };
    sse.onerror = () => fail(t(local ? "vault.scan_error" : "vault.civitai_stream_error"));
  });
}

async function startScan() {
  await withLibraryOperation(async () => {
    const ev = await runLibraryJob("/api/vault/scan?deep=false", "scan");
    const warnings = Boolean(ev.errors || ev.civitai?.errors || ev.civitai_error);
    let message = t("vault.scan_complete", { total: ev.total })
      + (ev.errors ? " " + t("vault.scan_partial_errors", { count: ev.errors }) : "");
    if (ev.civitai) message += " " + civitaiResultMessage(ev.civitai);
    if (ev.civitai_error) message += " " + t("vault.civitai_partial_error", { error: ev.civitai_error });
    showOperationResult(message, warnings);
    showToast((warnings ? "⚠ " : "✅ ") + message);
    await loadModels();
  });
}

async function syncCivitai() {
  await withLibraryOperation(async () => {
    const data = await checkedJson("/api/vault/civitai-pending");
    const pending = data.pending ?? 0;
    if (pending === 0) {
      showToast("✅ " + t("vault.civitai_all_synced"));
      return;
    }
    const etaMin = Math.ceil(pending * 1.5 / 60);
    if (!confirm(t("vault.civitai_confirm", { count: pending, eta: etaMin }))) return;
    await runCivitaiSync(false);
  });
}

async function forceSyncCivitai() {
  await withLibraryOperation(async () => {
    const etaMin = Math.ceil(allModels.length * 1.5 / 60);
    if (!confirm(t("vault.civitai_force_confirm", { eta: etaMin }))) return;
    await runCivitaiSync(true);
  });
}

async function runCivitaiSync(force) {
  const ev = await runLibraryJob(`/api/vault/civitai-sync?force=${force}`, "civitai");
  const message = civitaiResultMessage(ev);
  showOperationResult(message, Boolean(ev.errors));
  showToast("☁ " + message);
  await loadModels();
}

// ════════════════════════════════════════════════════════════════
// Filtrado en cliente
// ════════════════════════════════════════════════════════════════
function normalizeSearch(value) {
  return String(value || "").toLowerCase().replace(/\\/g, "/");
}

function modelFolder(model) {
  return model.folder !== undefined ? model.folder : model.subfolder || "";
}

function modelArchitecture(model) {
  return String(model.base_model || "Unknown").trim() || "Unknown";
}

function architectureModels() {
  return currentArch ? allModels.filter(m => modelArchitecture(m).toLowerCase() === currentArch.toLowerCase()) : allModels;
}

function libraryFilterModels() {
  const models = architectureModels();
  if (currentImageFilter === "with") return models.filter(m => Boolean(m.has_thumbnail));
  if (currentImageFilter === "without") return models.filter(m => !m.has_thumbnail);
  return models;
}

function renderArchitectureOptions() {
  const architectureNames = new Map();
  allModels.forEach(m => {
    const arch = modelArchitecture(m);
    if (!architectureNames.has(arch.toLowerCase())) architectureNames.set(arch.toLowerCase(), arch);
  });
  const architectures = [...architectureNames.values()]
    .sort((a, b) => a.localeCompare(b));
  if (currentArch && !architectures.includes(currentArch)) currentArch = "";
  const select = $("arch-select");
  select.innerHTML = `<option value="">${escapeHtml(t("vault.all_architectures"))}</option>`
    + architectures.map(arch => `<option value="${escapeHtml(arch)}">${escapeHtml(arch)}</option>`).join("");
  select.value = currentArch;
}

function renderFilterContext() {
  const category = modelCategories().find(c => c.id === currentCat);
  const location = currentCat === "all" ? t("vault.all_types") : category?.label() || currentCat;
  const folder = currentSubfolder === null ? t("vault.all_folders") : currentSubfolder || t("vault.subfolder_root");
  $("filter-context").textContent = `${t("vault.location")}: ${location} · ${folder}`;
  const withImage = allModels.filter(m => m.has_thumbnail).length;
  $("preview-summary").textContent = t("vault.preview_summary", { with: withImage, without: allModels.length - withImage });
  $("btn-clear-filters").hidden = !currentSearch && !currentArch && currentImageFilter === "all" && currentCat === "all" && currentSubfolder === null;
}

function clearFilters() {
  currentCat = "all";
  currentSubfolder = null;
  expandedCat = null;
  currentSearch = "";
  currentArch = "";
  currentImageFilter = "all";
  $("search-box").value = "";
  $("arch-select").value = "";
  $("image-select").value = "all";
  renderSidebar();
  applyFilters();
}

function applyFilters() {
  let result = libraryFilterModels();

  if (currentCat !== "all") {
    result = result.filter(m =>
      (m.model_type || "unknown").toLowerCase() === currentCat
    );
    if (currentSubfolder !== null) {
      result = result.filter(m => modelFolder(m) === currentSubfolder);
    }
  }

  if (currentSearch) {
    const ql = normalizeSearch(currentSearch);
    result = result.filter(m =>
      [m.name, m.file_path, ...(m.file_paths || []), m.display_name, m.creator_name, m.custom_tags,
        m.civitai_tags, m.base_model, m.triggers].some(value => normalizeSearch(value).includes(ql))
    );
  }

  if (currentSort === "visual") {
    result = [...result].sort((a, b) => Number(Boolean(b.has_thumbnail)) - Number(Boolean(a.has_thumbnail)));
  } else if (currentSort === "name") {
    result = [...result].sort((a, b) =>
      (a.display_name || a.name || "").localeCompare(b.display_name || b.name || "")
    );
  } else if (currentSort === "arch") {
    result = [...result].sort((a, b) =>
      (a.base_model || "").localeCompare(b.base_model || "")
    );
  }

  filteredModels = result;
  renderFilterContext();
  renderGrid();
}

// ════════════════════════════════════════════════════════════════
// Drag & Drop — staging
// ════════════════════════════════════════════════════════════════
function stageMove(dragData, targetCat, targetSub) {
  if (operationBusy) return;
  const { hash } = dragData || {};
  const model = allModels.find(m => m.hash === hash);
  if (!model) return;
  if (model.missing) { showToast(t("vault.file_missing")); return; }
  const catId = (model.model_type || "unknown").toLowerCase();
  const currentSub = modelFolder(model);

  // Ya está en el destino
  if (catId === targetCat && currentSub === targetSub) {
    if (pendingMoves[hash]) {
      delete pendingMoves[hash];
      updateSyncBtn();
      renderGrid();
      showToast(t("vault.move_canceled"));
    } else showToast(t("vault.move_already_there"));
    return;
  }

  if (catId !== targetCat) {
    showToast(t("vault.move_cross_cat_blocked"));
    return;
  }

  const label = targetSub || t("vault.subfolder_root");
  pendingMoves[hash] = { targetFolder: targetSub, targetCat, label };
  updateSyncBtn();
  renderGrid();
}

function updateSyncBtn() {
  const count = Object.keys(pendingMoves).length;
  const btn   = $("btn-apply-moves");
  btn.hidden = count === 0;
  btn.textContent = t("vault.apply_moves", { count });
  $("btn-cancel-moves").hidden = count === 0;
}

async function applyPendingMoves() {
  const moves = Object.entries(pendingMoves).map(([hash, info]) => ({
    hash,
    target_folder: info.targetFolder,
  }));
  if (!moves.length) return true;

  try {
    const res = await fetch("/api/vault/apply-moves", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ moves }),
    });

    if (!res.ok) {
      const err = await res.text();
      showToast(`❌ Error ${res.status}: ${err.substring(0, 120)}`);
      return false;
    }

    const data = await res.json();
    for (const hash of data.moved_hashes || []) delete pendingMoves[hash];
    updateSyncBtn();

    if (data.errors > 0) {
      const detail = (data.error_detail || []).join(" | ").substring(0, 200);
      showToast(`⚠ ${data.moved} movido(s), ${data.errors} error(es): ${detail}`);
    } else {
      showToast(`📦 ${data.moved} modelo(s) movidos`);
    }
    return true;
  } catch (_) {
    showToast("❌ " + t("vault.conn_error"));
    return false;
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
  // Carga inicial
  loadModels();

  // Búsqueda (debounced)
  let _searchTimer = null;
  $("search-box").addEventListener("input", e => {
    currentSearch = e.target.value.trim();
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(applyFilters, 280);
  });

  // Ordenamiento
  $("sort-select").addEventListener("change", e => {
    currentSort = e.target.value;
    applyFilters();
  });

  // Scan
  $("btn-scan").addEventListener("click", startScan);

  // Civitai sync
  $("btn-civitai-sync").addEventListener("click", syncCivitai);
  $("btn-civitai-force").addEventListener("click", forceSyncCivitai);
  $("btn-civitai-menu").addEventListener("click", () => {
    if (operationBusy) return;
    const menu = $("civitai-menu");
    menu.hidden = !menu.hidden;
    $("btn-civitai-menu").setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.addEventListener("click", e => {
    if (!$("civitai-menu").contains(e.target) && !$("btn-civitai-menu").contains(e.target)) closeCivitaiMenu();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeCivitaiMenu(); });
  $("btn-apply-moves").addEventListener("click", () => withLibraryOperation(async () => {
    if (await applyPendingMoves()) await loadModels();
  }));
  $("btn-cancel-moves").addEventListener("click", () => {
    if (operationBusy) return;
    pendingMoves = {};
    updateSyncBtn();
    renderGrid();
  });
  $("arch-select").addEventListener("change", e => {
    currentArch = e.target.value;
    currentCat = "all";
    currentSubfolder = null;
    expandedCat = null;
    renderSidebar();
    applyFilters();
  });
  $("btn-clear-filters").addEventListener("click", clearFilters);
  $("image-select").addEventListener("change", e => {
    currentImageFilter = e.target.value;
    renderSidebar();
    applyFilters();
  });

  // Re-render sidebar al cambiar idioma (locale.js solo actualiza data-i18n estático)
  document.querySelectorAll("[data-i18n-lang-btn]").forEach(btn => {
    btn.addEventListener("click", () => setTimeout(() => {
      renderSidebar(); renderArchitectureOptions(); renderFilterContext(); updateSyncBtn();
      if (operationBusy) $("scan-kind").textContent = t($("scan-kind").dataset.kind || "vault.scanning_local");
    }, 0));
  });
});
