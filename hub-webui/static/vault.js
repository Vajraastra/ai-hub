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
  } catch (e) {
    $("model-grid").innerHTML =
      `<div class="grid-empty"><span class="big-icon">❌</span>Error cargando modelos.</div>`;
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
  $("model-count").textContent = `${filteredModels.length} modelo${filteredModels.length !== 1 ? "s" : ""}`;

  if (!filteredModels.length) {
    grid.innerHTML = `<div class="grid-empty">
      <span class="big-icon">📦</span>
      Sin modelos${currentSearch ? ` para "${escapeHtml(currentSearch)}"` : ""}.
      ${!allModels.length ? "<br>Configura el directorio de modelos en Ajustes y pulsa Sincronizar." : ""}
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
    `<div style="padding:20px;text-align:center;color:var(--text-dim)">Cargando...</div>`;

  try {
    const res = await fetch(`/api/vault/models/${hash}`);
    if (!res.ok) { showToast("❌ Modelo no encontrado."); return; }
    const m = await res.json();
    renderDetails(m);
  } catch {
    $("details-content").innerHTML =
      `<div style="color:var(--status-error);font-size:12px">❌ Error cargando detalles.</div>`;
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

  const triggers = (m.triggers || "").split(",").map(t => t.trim()).filter(Boolean);
  const triggerHtml = triggers.length
    ? triggers.map(t => `<span class="trigger-chip">${escapeHtml(t)}</span>`).join("")
    : `<span style="color:var(--text-dim);font-size:12px">Sin trigger words.</span>`;

  $("details-content").innerHTML = `
    ${thumbUrl
      ? `<img class="detail-thumb" src="${thumbUrl}" alt="${escapeHtml(displayName)}">`
      : ""}
    <div class="detail-name">${escapeHtml(displayName)}</div>

    <div class="detail-row">
      <span class="detail-lbl">Tipo</span>
      <span class="detail-val">${escapeHtml(m.model_type || "—")}</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">Base model</span>
      <span class="detail-val">${escapeHtml(m.base_model || "—")}</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">Versión</span>
      <span class="detail-val">${escapeHtml(m.version_name || "—")}</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">Creador</span>
      <span class="detail-val">${escapeHtml(m.creator_name || "—")}</span>
    </div>
    <div class="detail-row">
      <span class="detail-lbl">Archivo</span>
      <span class="detail-val" style="font-family:'JetBrains Mono',monospace;font-size:11px;word-break:break-all">
        ${escapeHtml(m.name || "—")}
      </span>
    </div>

    ${m.description ? `
    <div class="detail-section-title">Descripción</div>
    <div style="font-size:12px;color:var(--text-secondary);line-height:1.5">
      ${escapeHtml(m.description).substring(0, 400)}${m.description.length > 400 ? "…" : ""}
    </div>` : ""}

    <div class="detail-section-title">Trigger words</div>
    <div>${triggerHtml}</div>

    <div class="detail-section-title">Tags personalizados</div>
    <input type="text" id="detail-tags" value="${escapeHtml(m.custom_tags || "")}"
           placeholder="tag1, tag2, ...">

    <div class="detail-section-title">Notas</div>
    <textarea id="detail-notes" placeholder="Notas personales sobre este modelo...">${escapeHtml(m.user_notes || "")}</textarea>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="btn btn-primary" style="flex:1;font-size:12px"
              onclick="saveModelData('${m.hash}')">Guardar</button>
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
    if (data.ok) showToast("✅ Guardado.");
    else         showToast("❌ Error guardando.");
  } catch {
    showToast("❌ Error de conexión.");
  }
}

// ════════════════════════════════════════════════════════════════
// Scan con SSE
// ════════════════════════════════════════════════════════════════
async function startScan() {
  const btn = $("btn-scan");
  btn.disabled = true;
  btn.textContent = "Escaneando...";
  $("scan-progress").classList.add("visible");
  $("scan-bar-fill").style.width = "0%";
  $("scan-label").textContent = "Iniciando...";
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
        btn.textContent = "↻ Sincronizar";
        showToast(`✅ Scan completo. ${ev.total} modelos indexados.`);
        refresh();
      }

      if (ev.type === "error") {
        sse.close();
        $("scan-progress").classList.remove("visible");
        btn.disabled = false;
        btn.textContent = "↻ Sincronizar";
        showToast(`❌ ${ev.error}`);
      }
    };
    sse.onerror = () => {
      sse.close();
      $("scan-progress").classList.remove("visible");
      btn.disabled = false;
      btn.textContent = "↻ Sincronizar";
      showToast("❌ Error de conexión con el stream de scan.");
    };
  } catch {
    $("scan-progress").classList.remove("visible");
    btn.disabled = false;
    btn.textContent = "↻ Sincronizar";
    showToast("❌ Error iniciando el scan.");
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
});
