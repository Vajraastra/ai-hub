/* Model Vault WebUI */

// ════════════════════════════════════════════════════════════════
// Estado
// ════════════════════════════════════════════════════════════════
const $ = id => document.getElementById(id);
let allModels      = [];
let filteredModels = [];
let currentCat     = "all";
let currentSubfolder = null;   // null = todos los subfolders de la categoría
let expandedCat    = null;     // qué categoría tiene su lista de subfolders visible
let subfolderMap   = {};       // { "lora": { "personajes": 5, "": 2 }, ... }
let currentSearch  = "";
let currentSort    = "recent";
let selectedHash   = null;
let _notesTimer    = null;
let pendingMoves   = {};   // { hash: { targetSub, targetCat, label } }

const CATEGORIES = [
  { id: "checkpoint",        label: () => t("vault.cat_checkpoint") },
  { id: "lora",              label: () => t("vault.cat_lora")       },
  { id: "locon",             label: () => t("vault.cat_lycoris")    },
  { id: "textualinversion",  label: () => t("vault.cat_embedding")  },
  { id: "vae",               label: () => t("vault.cat_vae")        },
  { id: "upscaler",          label: () => t("vault.cat_upscaler")   },
];

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
  const type       = (model.model_type || "").toLowerCase();
  const fname      = (model.file_path  || "").split("/").pop() || (model.name || "");
  const nameNoExt  = fname.replace(/\.[^.]+$/, "");
  if (!nameNoExt) return null;

  if (type === "lora" || type === "locon")  return `<lora:${nameNoExt}:${weight}>`;
  if (type === "textualinversion")           return `(${nameNoExt}:${weight})`;
  return null;
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
  const weight = parseFloat($("copy-weight")?.value) || 1;
  const str    = getCopyString(m, weight);
  if (str) copyToClipboard(str);
}

function updateCopyPreview(hash) {
  const m = allModels.find(x => x.hash === hash);
  if (!m) return;
  const weight = parseFloat($("copy-weight")?.value) || 1;
  const el     = $("copy-string-preview");
  if (el) el.textContent = getCopyString(m, weight) || "";
}

// ════════════════════════════════════════════════════════════════
// Carga de modelos
// ════════════════════════════════════════════════════════════════
async function loadModels() {
  try {
    const res  = await fetch("/api/vault/models");
    const data = await res.json();
    allModels = data.models || [];
    computeSubfolderMap();
    renderSidebar();
    applyFilters();
  } catch (_) {
    $("model-grid").innerHTML =
      `<div class="grid-empty"><span class="big-icon">❌</span>${t("vault.load_error")}</div>`;
  }
}

function refresh() {
  loadModels();
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
    const thumbUrl    = m.has_thumbnail
      ? `/api/vault/thumbnail?path=${encodeURIComponent(m.file_path)}`
      : null;

    const copyStr = getCopyString(m, 1);

    const card = document.createElement("div");
    card.className = [
      "model-card",
      m.hash === selectedHash ? "selected"     : "",
      pending                 ? "pending-move" : "",
    ].filter(Boolean).join(" ");
    card.dataset.hash = m.hash;
    card.draggable    = true;

    card.innerHTML = `
      ${thumbUrl
        ? `<img class="model-thumb" src="${thumbUrl}" alt="${escapeHtml(displayName)}" loading="lazy">`
        : `<div class="model-thumb-placeholder">📦</div>`}
      <div class="model-card-body">
        <div class="model-card-name" title="${escapeHtml(displayName)}">${escapeHtml(displayName)}</div>
        <div class="model-card-meta">
          ${baseModel ? `<span class="model-type-badge">${escapeHtml(baseModel)}</span> ` : ""}
          ${modelType ? `<span style="color:var(--text-dim)">${escapeHtml(modelType)}</span>` : ""}
        </div>
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
      // Ghost semitransparente
      const ghost = card.cloneNode(true);
      ghost.style.cssText = "position:fixed;top:-9999px;left:-9999px;width:160px;opacity:0.6;pointer-events:none;";
      document.body.appendChild(ghost);
      e.dataTransfer.setDragImage(ghost, e.offsetX, e.offsetY);
      setTimeout(() => ghost.remove(), 0);

      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("application/json", JSON.stringify({
        hash:   m.hash,
        catId:  (m.model_type || "").toLowerCase(),
        sub:    m.subfolder || "",
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
  subfolderMap = {};
  allModels.forEach(m => {
    const cat = (m.model_type || "").toLowerCase();
    const sub = m.subfolder  || "";
    if (!subfolderMap[cat]) subfolderMap[cat] = {};
    subfolderMap[cat][sub] = (subfolderMap[cat][sub] || 0) + 1;
  });
}

function selectCategory(catId) {
  if (catId === "all") {
    currentCat       = "all";
    currentSubfolder = null;
    expandedCat      = null;
  } else if (currentCat === catId) {
    // Ya seleccionada — solo toggle del panel de subfolders
    expandedCat = (expandedCat === catId) ? null : catId;
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
  const nav = $("sidebar");
  nav.innerHTML = "";

  const title = document.createElement("div");
  title.className = "sidebar-title";
  title.textContent = t("vault.sidebar_type");
  nav.appendChild(title);

  // Botón "Todos"
  const allBtn = document.createElement("button");
  allBtn.className = "cat-btn" + (currentCat === "all" ? " active" : "");
  allBtn.innerHTML = `<span>${t("vault.cat_all")}</span><span class="cat-count">${allModels.length}</span>`;
  allBtn.addEventListener("click", () => selectCategory("all"));
  nav.appendChild(allBtn);

  CATEGORIES.forEach(({ id, label }) => {
    const catCount = allModels.filter(m => (m.model_type || "").toLowerCase() === id).length;
    const subs     = subfolderMap[id] || {};
    const subEntries = Object.entries(subs).sort(([a], [b]) => {
      if (a === "") return -1;
      if (b === "") return 1;
      return a.localeCompare(b);
    });
    const hasSubfolders = subEntries.some(([s]) => s !== "");
    const isActive      = currentCat === id;
    const isExpanded    = expandedCat === id;

    const btn = document.createElement("button");
    btn.className = [
      "cat-btn",
      isActive   ? "active"   : "",
      isExpanded && hasSubfolders ? "expanded" : "",
    ].filter(Boolean).join(" ");
    btn.innerHTML = `
      <span>${label()}</span>
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
          sub === "" ? "is-root" : "",
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

  // Marcar tarjeta activa
  document.querySelectorAll(".model-card").forEach(c => {
    c.classList.toggle("selected", c.dataset.hash === hash);
  });

  $("details-panel").classList.add("open");
  $("details-content").innerHTML =
    `<div style="padding:20px;text-align:center;color:var(--text-dim)">${t("vault.loading")}</div>`;

  try {
    const res = await fetch(`/api/vault/models/${hash}`);
    if (!res.ok) { showToast("❌ " + t("vault.detail_not_found")); return; }
    const m = await res.json();
    renderDetails(m);
  } catch (_) {
    $("details-content").innerHTML =
      `<div style="color:var(--status-error);font-size:12px">❌ ${t("vault.detail_error")}</div>`;
  }
}

function closeDetails() {
  selectedHash = null;
  $("details-panel").classList.remove("open");
  document.querySelectorAll(".model-card.selected").forEach(c => c.classList.remove("selected"));
}

function renderDetails(m) {
  const displayName = m.display_name || m.name || "Sin nombre";
  const thumbUrl = m.has_thumbnail
    ? `/api/vault/thumbnail?path=${encodeURIComponent(m.file_path)}`
    : null;

  const triggers = (m.triggers || "").split(",").map(s => s.trim()).filter(Boolean);
  const triggerHtml = triggers.length
    ? triggers.map(s => `<span class="trigger-chip">${escapeHtml(s)}</span>`).join("")
    : `<span style="color:var(--text-dim);font-size:12px">${t("vault.detail_no_triggers")}</span>`;

  const civitaiTags = (m.civitai_tags || "").split(",").map(s => s.trim()).filter(Boolean);
  const civitaiTagsHtml = civitaiTags.length
    ? civitaiTags.map(s => `<span class="civitai-tag-chip">${escapeHtml(s)}</span>`).join("")
    : null;

  $("details-content").innerHTML = `
    ${thumbUrl
      ? `<img class="detail-thumb" src="${thumbUrl}" alt="${escapeHtml(displayName)}">`
      : ""}
    <div class="detail-name">${escapeHtml(displayName)}</div>

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

    ${getCopyString(m, 1) !== null ? `
    <div class="detail-section-title">${t("vault.copy_webui")}</div>
    <div class="copy-webui-row">
      <input type="number" id="copy-weight" class="copy-weight-input"
             value="1" min="0.1" max="2" step="0.05"
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
      <button class="btn btn-primary" style="flex:1;font-size:12px"
              onclick="saveModelData('${m.hash}')">${t("vault.detail_save")}</button>
    </div>`;
}

async function saveModelData(hash) {
  const notes = $("detail-notes")?.value ?? null;
  const tags  = $("detail-tags")?.value  ?? null;
  try {
    const res  = await fetch(`/api/vault/models/${hash}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_notes: notes, custom_tags: tags }),
    });
    const data = await res.json();
    if (data.ok) showToast("✅ " + t("vault.saved"));
    else         showToast("❌ " + t("vault.save_error"));
  } catch (_) {
    showToast("❌ " + t("vault.conn_error"));
  }
}

// ════════════════════════════════════════════════════════════════
// Scan con SSE
// ════════════════════════════════════════════════════════════════
async function startScan() {
  // Si hay movimientos pendientes: aplicar y recargar desde DB (sin scan de filesystem)
  if (Object.keys(pendingMoves).length > 0) {
    const ok = await applyPendingMoves();
    if (ok) await loadModels();
    return;
  }

  const btn = $("btn-scan");
  btn.disabled = true;
  btn.textContent = t("vault.scanning") + "...";
  $("scan-progress").classList.add("visible");
  $("scan-bar-fill").style.width = "0%";
  $("scan-label").textContent = t("vault.scan_starting");
  $("scan-fraction").textContent = "";

  try {
    const res = await fetch("/api/vault/scan?deep=false", { method: "POST" });
    const { session_id } = await res.json();

    const sse = new EventSource(`/api/vault/scan-progress/${session_id}`);
    sse.onmessage = e => {
      const ev = JSON.parse(e.data);
      if (ev.type === "ping") return;

      if (ev.type === "progress") {
        const pct = ev.total ? Math.round(ev.current / ev.total * 100) : 0;
        $("scan-bar-fill").style.width = `${pct}%`;
        $("scan-label").textContent = ev.name || "";
        $("scan-fraction").textContent = ev.total ? `${ev.current}/${ev.total}` : "";
      }

      if (ev.type === "done") {
        sse.close();
        $("scan-progress").classList.remove("visible");
        btn.disabled = false;
        btn.textContent = t("vault.btn_scan");
        showToast("✅ " + t("vault.scan_complete", { total: ev.total }));
        refresh();
      }

      if (ev.type === "error") {
        sse.close();
        $("scan-progress").classList.remove("visible");
        btn.disabled = false;
        btn.textContent = t("vault.btn_scan");
        showToast(`❌ ${ev.error}`);
      }
    };
    sse.onerror = () => {
      sse.close();
      $("scan-progress").classList.remove("visible");
      btn.disabled = false;
      btn.textContent = t("vault.btn_scan");
      showToast("❌ " + t("vault.scan_error"));
    };
  } catch (_) {
    $("scan-progress").classList.remove("visible");
    btn.disabled = false;
    btn.textContent = t("vault.btn_scan");
    showToast("❌ " + t("vault.scan_start_error"));
  }
}

// ════════════════════════════════════════════════════════════════
// Civitai Tag Sync
// ════════════════════════════════════════════════════════════════
async function syncCivitai() {
  // Paso 1: consultar pendientes SIN iniciar el thread
  let pending = 0;
  try {
    const res  = await fetch("/api/vault/civitai-pending");
    const data = await res.json();
    pending = data.pending ?? 0;
  } catch (_) {
    showToast("❌ " + t("vault.civitai_pending_error"));
    return;
  }

  if (pending === 0) {
    showToast("✅ " + t("vault.civitai_all_synced"));
    return;
  }

  const etaMin = Math.ceil(pending * 1.5 / 60);
  const ok = confirm(t("vault.civitai_confirm", { count: pending, eta: etaMin }));
  if (!ok) return;

  await _runCivitaiSync(false);
}

async function forceSyncCivitai() {
  let total = allModels.length || 100;
  const etaMin = Math.ceil(total * 1.5 / 60);
  const ok = confirm(t("vault.civitai_force_confirm", { eta: etaMin }));
  if (!ok) return;
  await _runCivitaiSync(true);
}

async function _runCivitaiSync(force) {
  const btn = force ? $("btn-civitai-force") : $("btn-civitai-sync");

  // Iniciar el sync
  let session_id;
  try {
    const res  = await fetch(`/api/vault/civitai-sync?force=${force}`, { method: "POST" });
    const info = await res.json();
    session_id = info.session_id;
  } catch (_) {
    showToast("❌ " + t("vault.civitai_start_error"));
    return;
  }

  const btnLabel = t(force ? "vault.btn_resync_all" : "vault.btn_sync_civitai");
  btn.disabled = true;
  btn.textContent = t("vault.scanning") + "...";
  $("scan-progress").classList.add("visible");
  $("scan-bar-fill").style.width = "0%";
  $("scan-label").textContent = t("vault.civitai_connecting");
  $("scan-fraction").textContent = "";

  try {
    const sse = new EventSource(`/api/vault/civitai-progress/${session_id}`);
    sse.onmessage = e => {
      const ev = JSON.parse(e.data);
      if (ev.type === "ping") return;

      if (ev.type === "progress") {
        const pct = ev.total ? Math.round(ev.current / ev.total * 100) : 0;
        $("scan-bar-fill").style.width = `${pct}%`;
        $("scan-label").textContent = ev.name || "";
        $("scan-fraction").textContent = ev.total ? `${ev.current}/${ev.total}` : "";
      }

      if (ev.type === "done") {
        sse.close();
        $("scan-progress").classList.remove("visible");
        btn.disabled = false;
        btn.textContent = btnLabel;
        showToast("☁ " + t("vault.civitai_done", {
          synced: ev.synced ?? 0, skipped: ev.skipped ?? 0,
          not_found: ev.not_found ?? 0, errors: ev.errors ?? 0
        }));
        refresh();
      }

      if (ev.type === "error") {
        sse.close();
        $("scan-progress").classList.remove("visible");
        btn.disabled = false;
        btn.textContent = btnLabel;
        showToast(`❌ ${ev.error}`);
      }
    };
    sse.onerror = () => {
      sse.close();
      $("scan-progress").classList.remove("visible");
      btn.disabled = false;
      btn.textContent = btnLabel;
      showToast("❌ " + t("vault.civitai_stream_error"));
    };
  } catch (_) {
    $("scan-progress").classList.remove("visible");
    btn.disabled = false;
    btn.textContent = btnLabel;
    showToast("❌ " + t("vault.civitai_start_error"));
  }
}

// ════════════════════════════════════════════════════════════════
// Filtrado en cliente
// ════════════════════════════════════════════════════════════════
function applyFilters() {
  let result = allModels;

  if (currentCat !== "all") {
    result = result.filter(m =>
      (m.model_type || "").toLowerCase() === currentCat
    );
    if (currentSubfolder !== null) {
      result = result.filter(m => (m.subfolder || "") === currentSubfolder);
    }
  }

  if (currentSearch) {
    const ql = currentSearch.toLowerCase();
    result = result.filter(m =>
      (m.name          || "").toLowerCase().includes(ql) ||
      (m.display_name  || "").toLowerCase().includes(ql) ||
      (m.creator_name  || "").toLowerCase().includes(ql) ||
      (m.custom_tags   || "").toLowerCase().includes(ql) ||
      (m.civitai_tags  || "").toLowerCase().includes(ql) ||
      (m.base_model    || "").toLowerCase().includes(ql) ||
      (m.triggers      || "").toLowerCase().includes(ql)
    );
  }

  if (currentSort === "name") {
    result = [...result].sort((a, b) =>
      (a.display_name || a.name || "").localeCompare(b.display_name || b.name || "")
    );
  } else if (currentSort === "arch") {
    result = [...result].sort((a, b) =>
      (a.base_model || "").localeCompare(b.base_model || "")
    );
  }

  filteredModels = result;
  renderGrid();
}

// ════════════════════════════════════════════════════════════════
// Drag & Drop — staging
// ════════════════════════════════════════════════════════════════
function stageMove(dragData, targetCat, targetSub) {
  const { hash, catId, sub: currentSub } = dragData;

  // Ya está en el destino
  if (catId === targetCat && currentSub === targetSub) {
    showToast(t("vault.move_already_there"));
    return;
  }

  // Warning cross-categoría
  if (catId !== targetCat) {
    const ok = confirm(t("vault.move_cross_cat_warn", {
      from: catId, to: targetCat,
    }));
    if (!ok) return;
  }

  const label = targetSub || t("vault.subfolder_root");
  pendingMoves[hash] = { targetSub, targetCat, label };
  updateSyncBtn();
  renderGrid();
}

function updateSyncBtn() {
  const count = Object.keys(pendingMoves).length;
  const btn   = $("btn-scan");
  btn.textContent = count > 0
    ? `${t("vault.btn_scan")} (${count})`
    : t("vault.btn_scan");
}

async function applyPendingMoves() {
  const moves = Object.entries(pendingMoves).map(([hash, info]) => ({
    hash,
    target_subfolder: info.targetSub,
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
    pendingMoves = {};
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

  // Re-render sidebar al cambiar idioma (locale.js solo actualiza data-i18n estático)
  document.querySelectorAll("[data-i18n-lang-btn]").forEach(btn => {
    btn.addEventListener("click", () => setTimeout(renderSidebar, 0));
  });
});
