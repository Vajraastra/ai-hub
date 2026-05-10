/* Model Vault WebUI */

// ════════════════════════════════════════════════════════════════
// Estado
// ════════════════════════════════════════════════════════════════
const $ = id => document.getElementById(id);
let allModels     = [];
let filteredModels = [];
let currentCat    = "all";
let currentSearch = "";
let currentSort   = "recent";
let selectedHash  = null;
let _notesTimer   = null;

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
// Carga de modelos
// ════════════════════════════════════════════════════════════════
async function loadModels(search = "", cat = "all", sort = "recent") {
  const params = new URLSearchParams({ q: search, cat, sort });
  try {
    const res  = await fetch(`/api/vault/models?${params}`);
    const data = await res.json();
    allModels = data.models || [];
    filteredModels = allModels;
    renderGrid();
    renderCounts();
  } catch (_) {
    $("model-grid").innerHTML =
      `<div class="grid-empty"><span class="big-icon">❌</span>${t("vault.load_error")}</div>`;
  }
}

function refresh() {
  loadModels(currentSearch, currentCat, currentSort);
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
    const card = document.createElement("div");
    card.className = "model-card" + (m.hash === selectedHash ? " selected" : "");
    card.dataset.hash = m.hash;

    const thumbUrl = m.has_thumbnail
      ? `/api/vault/thumbnail?path=${encodeURIComponent(m.file_path)}`
      : null;

    const displayName = m.display_name || m.name || "Sin nombre";
    const modelType   = m.model_type   || "";
    const baseModel   = m.base_model   || "";

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
      </div>`;

    card.addEventListener("click", () => openDetails(m.hash));
    grid.appendChild(card);
  });
}

// ════════════════════════════════════════════════════════════════
// Conteos por categoría
// ════════════════════════════════════════════════════════════════
function renderCounts() {
  const countAll = allModels.length;
  $("cnt-all").textContent = countAll;

  const cats = ["checkpoint", "lora", "locon", "textualinversion", "vae", "upscaler"];
  cats.forEach(cat => {
    const el = $(`cnt-${cat}`);
    if (el) {
      el.textContent = allModels.filter(
        m => (m.model_type || "").toLowerCase() === cat
      ).length;
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
      (m.model_type || "").toLowerCase() === currentCat.toLowerCase()
    );
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

  // Categorías
  document.querySelectorAll(".cat-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".cat-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentCat = btn.dataset.cat;
      applyFilters();
    });
  });

  // Scan
  $("btn-scan").addEventListener("click", startScan);

  // Civitai sync
  $("btn-civitai-sync").addEventListener("click", syncCivitai);
  $("btn-civitai-force").addEventListener("click", forceSyncCivitai);
});
