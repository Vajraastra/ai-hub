/* AI Hub WebUI — SPA completa */

// ════════════════════════════════════════════════════════════════
// Estado global del cliente
// ════════════════════════════════════════════════════════════════
let appsState   = [];
let logBuffer   = [];
let currentAppSettings = null;   // app_id abierto en el modal
const LOG_LIMIT = 600;

// ════════════════════════════════════════════════════════════════
// Utilidades DOM
// ════════════════════════════════════════════════════════════════
const $  = id => document.getElementById(id);
const q  = sel => document.querySelector(sel);

// ════════════════════════════════════════════════════════════════
// Navegación por tabs
// ════════════════════════════════════════════════════════════════
function initTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
      if (btn.dataset.tab === "log")      loadEventLog();
      if (btn.dataset.tab === "settings") loadSettings();
    });
  });
}

// ════════════════════════════════════════════════════════════════
// WebSocket
// ════════════════════════════════════════════════════════════════
let ws = null;
let wsReconnectTimer = null;

function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen  = () => { setWsDot("connected", t("ws.connected")); clearTimeout(wsReconnectTimer); };
  ws.onclose = () => { setWsDot("", t("ws.reconnecting")); wsReconnectTimer = setTimeout(connectWS, 2000); };
  ws.onerror = () => setWsDot("error", t("ws.error"));
  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.type === "init") {
      appsState = msg.apps;
      renderApps();
      renderSysinfo(msg.sysinfo);
    } else if (msg.type === "state_changed") {
      appsState = msg.apps;
      renderApps();
      updateActiveBadge();
    } else if (msg.type === "log") {
      appendTerminalLine(msg.line);
    }
  };
}

function setWsDot(cls, label) {
  q(".ws-dot").className = `ws-dot ${cls}`;
  $("ws-label").textContent = label;
}

// ════════════════════════════════════════════════════════════════
// Sysinfo
// ════════════════════════════════════════════════════════════════
function renderSysinfo(info) {
  $("sys-gpu").textContent  = info.gpu  || "—";
  $("sys-cuda").textContent = info.cuda || "—";
  $("sys-disk").textContent = info.disk || "—";
}

function updateActiveBadge() {
  const running = appsState.find(a => a.status === "running");
  const badge = $("active-badge");
  if (running) {
    badge.style.display = "flex";
    $("active-app-name").textContent = running.name + (running.port ? ` :${running.port}` : "");
  } else {
    badge.style.display = "none";
  }
}

// ════════════════════════════════════════════════════════════════
// Apps — renderizado
// ════════════════════════════════════════════════════════════════
function renderApps() {
  const container = $("apps-list");
  const total = appsState.length;
  const installed = appsState.filter(a => a.status !== "not_installed").length;
  $("apps-count").textContent = t("apps.count", { installed, total });

  appsState.forEach(app => {
    let card = $(`card-${app.id}`);
    if (!card) {
      card = document.createElement("div");
      card.id = `card-${app.id}`;
      container.appendChild(card);
    }
    const eff = app.busy_label || app.status;
    card.className = `app-card ${app.busy_label ? "busy" : app.status}`;
    card.innerHTML = buildCardHTML(app, eff);
  });
}

function buildCardHTML(app, effectiveStatus) {
  const statusMap = {
    running:       ["running",       t("apps.status.running")],
    installed:     ["installed",     t("apps.status.installed")],
    not_installed: ["not_installed", t("apps.status.not_installed")],
    launching:     ["busy",          t("apps.status.launching")],
    installing:    ["busy",          t("apps.status.installing")],
    updating:      ["busy",          t("apps.status.updating")],
    uninstalling:  ["busy",          t("apps.status.uninstalling")],
    stopping:      ["busy",          t("apps.status.stopping")],
  };
  const [pillCls, pillText] = statusMap[effectiveStatus] || ["", effectiveStatus];
  const initial = app.name[0].toUpperCase();
  const badge = app.port
    ? `<span class="app-badge webui">🌐 ${t("apps.badge_webui")}</span>`
    : `<span class="app-badge desktop">🖥 ${t("apps.badge_desktop")}</span>`;

  const meta = app.status === "running" && (app.port || app.pid)
    ? `<span class="meta-row">${app.port ? `🔌 ${app.port}` : ""}${app.pid ? `  🔑 ${app.pid}` : ""}</span>`
    : "";

  let btns = "";
  if (app.busy_label) {
    btns = `<div class="btn-row">
      <button class="btn btn-outline" disabled>
        <span class="spinner"></span>${pillText}
      </button>
    </div>
    <div class="progress-bar"><div class="progress-fill"></div></div>`;
  } else if (app.status === "running") {
    btns = `<div class="btn-row">
      <button class="btn btn-stop" onclick="appAction('stop','${app.id}')">${t("apps.btn.stop")}</button>
      <button class="btn btn-icon" onclick="openAppSettings('${app.id}')" title="${t("apps.btn.settings")}">${t("apps.btn.settings")}</button>
    </div>`;
  } else if (app.status === "installed") {
    const dis = app.any_running ? "disabled" : "";
    const tip = app.any_running ? `title="${t("apps.blocked")}"` : "";
    btns = `<div class="btn-row">
      <button class="btn btn-launch" onclick="appAction('launch','${app.id}')" ${dis} ${tip}>${t("apps.btn.launch")}</button>
      <button class="btn btn-icon"   onclick="appAction('update','${app.id}')"   title="${t("apps.btn.update")}">${t("apps.btn.update")}</button>
      <button class="btn btn-icon"   onclick="openAppSettings('${app.id}')"      title="${t("apps.btn.settings")}">${t("apps.btn.settings")}</button>
      <button class="btn btn-icon danger" onclick="confirmUninstall('${app.id}','${app.name}')" title="${t("apps.btn.uninstall")}">${t("apps.btn.uninstall")}</button>
    </div>`;
  } else {
    btns = `<div class="btn-row">
      <button class="btn btn-install" onclick="appAction('install','${app.id}')">${t("apps.btn.install")}</button>
    </div>`;
  }

  return `
    <div class="app-icon">${initial}</div>
    <div class="app-info">
      <div class="app-name-row">
        <span class="app-name">${app.name}</span>${badge}
      </div>
      ${app.description ? `<span class="app-desc">${app.description}</span>` : ""}
    </div>
    <div class="app-right">
      <div class="status-pill ${pillCls}">
        <span class="status-dot"></span>
        <span class="status-text">${pillText}</span>
      </div>
      ${meta}
      ${btns}
    </div>`;
}

// ════════════════════════════════════════════════════════════════
// Acciones de apps
// ════════════════════════════════════════════════════════════════
async function appAction(action, appId) {
  const urls = {
    launch:    `/api/apps/${appId}/launch`,
    stop:      `/api/apps/${appId}/stop`,
    install:   `/api/apps/${appId}/install`,
    update:    `/api/apps/${appId}/update`,
    uninstall: `/api/apps/${appId}/uninstall`,
  };
  try {
    const res  = await fetch(urls[action], { method: "POST" });
    const data = await res.json();
    if (data.ok === false) showToast("⚠ " + t("apps.op_unavailable"));
  } catch (_) {
    showToast("❌ " + t("apps.op_error"));
  }
}

function confirmUninstall(appId, appName) {
  showConfirm(
    t("apps.uninstall_confirm", { name: `<strong>${appName}</strong>` }),
    () => appAction("uninstall", appId)
  );
}

// ════════════════════════════════════════════════════════════════
// Herramientas externas
// ════════════════════════════════════════════════════════════════
async function launchTool(toolId) {
  try {
    const res  = await fetch(`/api/tools/${toolId}/launch`, { method: "POST" });
    const data = await res.json();
    if (data.ok) showToast("✅ " + t("tools.launched"));
    else showToast(`❌ ${data.error || t("tools.launch_error")}`);
  } catch (_) {
    showToast("❌ " + t("tools.error"));
  }
}

// ════════════════════════════════════════════════════════════════
// Cleanup
// ════════════════════════════════════════════════════════════════
async function runCleanup() {
  const btn = $("btn-cleanup");
  btn.disabled = true;
  btn.textContent = t("apps.cleaning");
  try {
    const res  = await fetch("/api/cleanup", { method: "POST" });
    const data = await res.json();
    if (data.clean) {
      showToast("✅ " + t("cleanup.clean"));
    } else {
      const lines = data.lines.map(l =>
        `<div class="cleanup-line">${l.icon} ${l.text}</div>`
      ).join("");
      showModal(
        t("cleanup.title"),
        `<p style="font-size:12px;color:var(--text-secondary);margin-bottom:10px">
           ${t("cleanup.killed", { count: data.killed })}
         </p>
         <div class="cleanup-lines">${lines}</div>`,
        false
      );
    }
  } catch (_) {
    showToast("❌ " + t("cleanup.error"));
  } finally {
    btn.disabled = false;
    btn.textContent = t("apps.cleanup");
  }
}

// ════════════════════════════════════════════════════════════════
// Terminal
// ════════════════════════════════════════════════════════════════
function appendTerminalLine(line) {
  const out = $("terminal-output");
  const atBottom = out.scrollHeight - out.clientHeight - out.scrollTop < 40;
  let cls = "";
  if (/✅|✓|iniciada|started/i.test(line)) cls = "success";
  else if (/❌|error|failed|traceback/i.test(line)) cls = "error";
  else if (/⚠|warn/i.test(line)) cls = "warn";
  else if (/===|🌐|🔑|🔌/.test(line)) cls = "system";

  const el = document.createElement("div");
  el.className = `log-line ${cls}`;
  el.textContent = line;
  out.appendChild(el);
  logBuffer.push(el);
  if (logBuffer.length > LOG_LIMIT) logBuffer.shift().remove();
  if (atBottom) out.scrollTop = out.scrollHeight;
}

// ════════════════════════════════════════════════════════════════
// Settings
// ════════════════════════════════════════════════════════════════
let _settingsData = null;

async function loadSettings() {
  try {
    const res  = await fetch("/api/settings");
    _settingsData = await res.json();
    const d = _settingsData;
    $("set-models-path").value  = d.paths?.models  || "";
    $("set-outputs-path").value = d.paths?.outputs || "";
    $("set-civitai-key").value  = d.civitai_key    || "";
    $("set-gpu").textContent    = d.gpu?.name      || "—";
    $("set-vram").textContent   = d.gpu?.vram_mb ? `${Math.round(d.gpu.vram_mb/1024)} GB` : "—";
    $("set-arch").textContent   = d.gpu?.arch      || "—";
    $("set-cuda").textContent   = d.cuda?.tag      || "—";
    $("set-torch").textContent  = d.cuda?.torch_version || "—";
    _hidePathStatus();
  } catch (_) {
    showToast("❌ " + t("settings.load_error"));
  }
}

async function saveSettings() {
  const btn = $("btn-save-settings");
  btn.disabled = true;
  btn.textContent = t("settings.saving");
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        models_path:  $("set-models-path").value.trim(),
        outputs_path: $("set-outputs-path").value.trim(),
        civitai_key:  $("set-civitai-key").value.trim(),
      }),
    });
    const data = await res.json();
    if (data.ok) showToast(t("settings.saved"));
    else showToast(`Error: ${data.error}`);
  } catch (_) {
    showToast(t("settings.save_error"));
  } finally {
    btn.disabled = false;
    btn.textContent = t("settings.save");
  }
}

// ── Purga del cache de uv ─────────────────────────────────────────────────────

function confirmPurgeUvCache() {
  const pkg = $("set-purge-package").value.trim();
  const msg = pkg
    ? t("settings.purge_confirm_pkg", { pkg: `<strong>${pkg}</strong>` })
    : t("settings.purge_confirm_all");
  showConfirm(msg, () => purgeUvCache(pkg));
}

async function purgeUvCache(pkg) {
  const btn = $("btn-purge-uv-cache");
  btn.disabled = true;
  btn.textContent = t("settings.purging");
  try {
    const res = await fetch("/api/settings/purge-uv-cache", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ package: pkg }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast("✅ " + t("settings.purge_done", { gb: data.freed_gb }));
      $("set-purge-package").value = "";
    } else {
      showToast("❌ " + t("settings.purge_error"));
    }
  } catch (_) {
    showToast("❌ " + t("settings.purge_error"));
  } finally {
    btn.disabled = false;
    btn.textContent = t("settings.purge_btn");
  }
}

// ── Models Path Validation Flow ──────────────────────────────────────────────

function _hidePathStatus() {
  const el = $("models-path-status");
  if (el) { el.style.display = "none"; el.innerHTML = ""; }
}

function _showPathStatus(html) {
  const el = $("models-path-status");
  if (!el) return;
  el.style.display = "block";
  el.innerHTML = html;
}

async function validateModelsPath() {
  const path = $("set-models-path").value.trim();
  if (!path) { _hidePathStatus(); return; }

  const btn = $("btn-validate-models-path");
  btn.disabled = true;
  btn.textContent = t("settings.validating");

  try {
    const res  = await fetch("/api/settings/validate-models-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const d = await res.json();
    _renderPathStatus(d, path);
  } catch (_) {
    _showPathStatus(`<div class="path-status error">${t("settings.path.error")}</div>`);
  } finally {
    btn.disabled = false;
    btn.textContent = t("settings.validate");
  }
}

function _renderPathStatus(d, path) {
  if (d.state === "ok") {
    _showPathStatus(`
      <div class="path-status ok">${t("settings.path.ok")}</div>`);
    return;
  }

  if (d.state === "not_found") {
    _showPathStatus(`
      <div class="path-status warn">
        ${t("settings.path.not_found", { path: `<code>${path}</code>` })}
        <div style="margin-top:8px">
          <button class="btn btn-primary btn-sm" onclick="_createModelsPath('${_esc(path)}')">
            ${t("settings.path.create_canonical")}
          </button>
          <button class="btn btn-outline btn-sm" onclick="_hidePathStatus()">${t("settings.path.cancel")}</button>
        </div>
      </div>`);
    return;
  }

  // State: "incomplete" or "empty"
  let html = `<div class="path-status warn">`;
  if (d.missing_dirs && d.missing_dirs.length) {
    html += `<p>${t("settings.path.missing", { count: d.missing_dirs.length, dirs: `<code>${d.missing_dirs.join(", ")}</code>` })}</p>
             <p>${t("settings.path.will_add")}</p>`;
  }
  if (d.orphan_count > 0) {
    html += `<p style="margin-top:6px">${t("settings.path.orphans", { count: `<strong>${d.orphan_count}</strong>` })}</p>`;
    if (d.orphan_preview && d.orphan_preview.length) {
      const previews = d.orphan_preview.map(p => `<li><code>${p}</code></li>`).join("");
      html += `<ul style="margin:4px 0 8px 16px;font-size:11px;color:#aaa">${previews}</ul>`;
    }
    html += `<div style="margin-top:8px">
      <button class="btn btn-primary btn-sm" onclick="_reorganizeOrphans('${_esc(path)}')">
        ${t("settings.path.move_inbox")}
      </button>
      <button class="btn btn-outline btn-sm" onclick="_hidePathStatus()">${t("settings.path.ignore")}</button>
    </div>`;
  } else {
    html += `<div style="margin-top:8px">
      <button class="btn btn-primary btn-sm" onclick="_applyModelsPath('${_esc(path)}', false)">
        ${t("settings.path.apply")}
      </button>
    </div>`;
  }
  html += `</div>`;
  _showPathStatus(html);
}

async function _createModelsPath(path) {
  const res  = await fetch("/api/settings/apply-models-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, create_if_missing: true }),
  });
  const d = await res.json();
  if (d.ok) {
    _showPathStatus(`<div class="path-status ok">
      ${t("settings.path.created", { count: d.missing_added.length })}
    </div>`);
  } else {
    _showPathStatus(`<div class="path-status error">Error: ${d.error}</div>`);
  }
}

async function _applyModelsPath(path, createIfMissing) {
  const res = await fetch("/api/settings/apply-models-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, create_if_missing: createIfMissing }),
  });
  const d = await res.json();
  if (d.ok) {
    const msg = d.missing_added.length
      ? t("settings.path.added", { count: d.missing_added.length })
      : t("settings.path.complete");
    _showPathStatus(`<div class="path-status ok">${msg}</div>`);
  } else {
    _showPathStatus(`<div class="path-status error">Error: ${d.error}</div>`);
  }
}

async function _reorganizeOrphans(path) {
  _showPathStatus(`<div class="path-status warn">${t("settings.path.moving")}</div>`);
  const res = await fetch("/api/settings/reorganize-orphans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const d = await res.json();
  if (d.errors && d.errors.length) {
    _showPathStatus(`<div class="path-status warn">
      Movidos: ${d.moved} | Saltados: ${d.skipped}<br>
      Errores: ${d.errors.slice(0, 3).join(", ")}
    </div>`);
  } else {
    _showPathStatus(`<div class="path-status ok">
      ${t("settings.path.moved", { count: d.moved })}
    </div>`);
  }
}

function _esc(s) {
  return s.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

// ════════════════════════════════════════════════════════════════
// Event Log
// ════════════════════════════════════════════════════════════════
async function loadEventLog() {
  const list = $("log-list");
  list.innerHTML = `<div class="empty-state"><span style="font-size:22px">↻</span><span>${t("log.loading")}</span></div>`;
  try {
    const res  = await fetch("/api/log?lines=200");
    const data = await res.json();
    if (!data.entries.length) {
      list.innerHTML = `<div class="empty-state"><span style="font-size:22px">📋</span><span>${t("log.empty")}</span></div>`;
      return;
    }
    list.innerHTML = data.entries.map(e => {
      const cat = e.category || "INFO";
      return `<div class="log-entry">
        <span class="log-ts">${e.ts || ""}</span>
        <span class="log-cat ${cat}">${cat}</span>
        <span class="log-app">${e.app_id || ""}</span>
        <span class="log-msg">${e.message || ""}</span>
      </div>`;
    }).join("");
  } catch (_) {
    list.innerHTML = `<div class="empty-state"><span>❌ ${t("log.error")}</span></div>`;
  }
}

// ════════════════════════════════════════════════════════════════
// Modal de ajustes por app
// ════════════════════════════════════════════════════════════════
async function openAppSettings(appId) {
  try {
    const res  = await fetch(`/api/apps/${appId}/config`);
    const data = await res.json();
    currentAppSettings = data;

    const appName = appsState.find(a => a.id === appId)?.name || appId;
    $("modal-title").textContent = t("settings.modal_title", { name: appName });

    let html = "";

    // Port override
    html += `<div class="field">
      <label>${t("settings.port_label")}</label>
      <input type="number" id="ms-port" placeholder="${data.default_port || 'auto'}"
             value="${data.port_override || ''}"
             style="max-width:140px">
      <span class="field-hint">${t("settings.port_hint", { port: data.default_port || 'N/A' })}</span>
    </div>`;

    // Flags disponibles
    if (data.flags && data.flags.length) {
      html += `<div>
        <label style="font-size:12px;color:var(--text-secondary);display:block;margin-bottom:8px">
          ${t("settings.flags_label")}
        </label>
        <div class="flags-list" id="flags-list">`;

      data.flags.forEach((f, i) => {
        const active = f.active ? "active" : "";
        const riskHtml = `<span class="risk-badge risk-${f.risk}">${f.risk}</span>`;
        const argInput = f.requires_arg ? `
          <div class="flag-arg-input" style="${f.active ? '' : 'display:none'}">
            <input type="text" id="ms-flagarg-${i}" placeholder="${f.arg_placeholder || 'valor'}"
                   value="${f.arg_value || ''}">
          </div>` : "";

        html += `<div class="flag-item ${active}" data-idx="${i}" onclick="toggleFlag(this,${i})">
          <div class="flag-check"></div>
          <div class="flag-info">
            <span class="flag-label">${f.label}${riskHtml}</span>
            <span class="flag-desc">${f.description}</span>
            ${f.risk_note ? `<span class="flag-desc" style="color:var(--accent-amber);margin-top:2px">${f.risk_note}</span>` : ""}
            ${argInput}
          </div>
        </div>`;
      });
      html += `</div></div>`;
    }

    // Args libres
    html += `<div class="field">
      <label>${t("settings.args_label")}</label>
      <textarea id="ms-free-args" placeholder="${t("settings.args_placeholder")}"
      >${data.free_args || ''}</textarea>
      <span class="field-hint">${t("settings.args_hint")}</span>
    </div>`;

    $("modal-body").innerHTML = html;
    openModal();
  } catch (_) {
    showToast("❌ " + t("settings.modal_load_error"));
  }
}

function toggleFlag(el, idx) {
  el.classList.toggle("active");
  const argInput = el.querySelector(".flag-arg-input");
  if (argInput) {
    argInput.style.display = el.classList.contains("active") ? "" : "none";
  }
}

async function saveAppSettings() {
  if (!currentAppSettings) return;
  const appId = currentAppSettings.app_id;

  const flagItems = document.querySelectorAll("#flags-list .flag-item");
  const activeFlags = [];
  flagItems.forEach((el, i) => {
    const flagDef = currentAppSettings.flags[i];
    if (!flagDef) return;
    const isActive = el.classList.contains("active");
    const argInput = el.querySelector(`#ms-flagarg-${i}`);
    activeFlags.push({
      flag:        flagDef.flag,
      active:      isActive,
      requires_arg: flagDef.requires_arg,
      arg_value:   argInput ? argInput.value.trim() : null,
    });
  });

  const portVal = $("ms-port")?.value.trim();
  const freeArgs = $("ms-free-args")?.value.trim() || "";

  try {
    const res = await fetch(`/api/apps/${appId}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        port_override: portVal ? parseInt(portVal) : null,
        active_flags:  activeFlags,
        free_args:     freeArgs,
      }),
    });
    const data = await res.json();
    if (data.ok) { closeModal(); showToast("✅ " + t("settings.modal_saved")); }
    else showToast(`❌ ${data.error}`);
  } catch (_) {
    showToast("❌ " + t("settings.modal_error"));
  }
}

// ════════════════════════════════════════════════════════════════
// Modal genérico
// ════════════════════════════════════════════════════════════════
function showModal(title, bodyHTML, withSave = false) {
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = bodyHTML;
  $("modal-save").style.display = withSave ? "" : "none";
  openModal();
}

function openModal() {
  $("modal-overlay").classList.add("open");
}

function closeModal() {
  $("modal-overlay").classList.remove("open");
  currentAppSettings = null;
}

// ════════════════════════════════════════════════════════════════
// Confirm modal
// ════════════════════════════════════════════════════════════════
let _confirmCallback = null;

function showConfirm(msgHTML, onConfirm) {
  $("confirm-msg").innerHTML = msgHTML;
  _confirmCallback = onConfirm;
  $("confirm-overlay").classList.add("open");
}

function closeConfirm() {
  $("confirm-overlay").classList.remove("open");
  _confirmCallback = null;
}

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
// Init
// ════════════════════════════════════════════════════════════════
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  connectWS();

  // Terminal
  $("terminal-clear").addEventListener("click", () => {
    $("terminal-output").innerHTML = "";
    logBuffer = [];
  });
  $("terminal-copy").addEventListener("click", () => {
    const text = logBuffer.map(el => el.textContent).join("\n");
    navigator.clipboard.writeText(text).then(
      () => showToast("✅ " + t("terminal.copied")),
      () => showToast("❌ " + t("terminal.copy_error"))
    );
  });

  // Cleanup
  $("btn-cleanup").addEventListener("click", runCleanup);

  // Settings
  $("btn-save-settings").addEventListener("click", saveSettings);
  $("btn-reload-settings").addEventListener("click", loadSettings);
  $("btn-validate-models-path").addEventListener("click", validateModelsPath);
  $("set-models-path").addEventListener("input", _hidePathStatus);
  $("btn-purge-uv-cache").addEventListener("click", confirmPurgeUvCache);
  $("btn-refresh-log").addEventListener("click", loadEventLog);

  // Modal
  $("modal-close").addEventListener("click", closeModal);
  $("modal-cancel").addEventListener("click", closeModal);
  $("modal-save").addEventListener("click", () => {
    if (currentAppSettings) saveAppSettings();
    else closeModal();
  });
  $("modal-overlay").addEventListener("click", e => {
    if (e.target === $("modal-overlay")) closeModal();
  });

  // Confirm
  $("confirm-no").addEventListener("click", closeConfirm);
  $("confirm-yes").addEventListener("click", () => {
    closeConfirm();
    if (_confirmCallback) _confirmCallback();
  });
  $("confirm-overlay").addEventListener("click", e => {
    if (e.target === $("confirm-overlay")) closeConfirm();
  });
});

// Detener apps al cerrar la pestaña/ventana del hub
window.addEventListener("beforeunload", () => {
  navigator.sendBeacon("/api/stop-all");
});
