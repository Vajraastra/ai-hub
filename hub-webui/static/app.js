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
  ws.onopen  = () => { setWsDot("connected", "conectado"); clearTimeout(wsReconnectTimer); };
  ws.onclose = () => { setWsDot("", "reconectando..."); wsReconnectTimer = setTimeout(connectWS, 2000); };
  ws.onerror = () => setWsDot("error", "error");
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
  $("apps-count").textContent = `${installed} de ${total} instaladas`;

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
    running:       ["running",       "● Corriendo"],
    installed:     ["installed",     "○ Instalada"],
    not_installed: ["not_installed", "○ No instalada"],
    launching:     ["busy",          "Iniciando..."],
    installing:    ["busy",          "Instalando..."],
    updating:      ["busy",          "Actualizando..."],
    uninstalling:  ["busy",          "Desinstalando..."],
    stopping:      ["busy",          "Deteniendo..."],
  };
  const [pillCls, pillText] = statusMap[effectiveStatus] || ["", effectiveStatus];
  const initial = app.name[0].toUpperCase();
  const badge = app.port
    ? `<span class="app-badge webui">🌐 WebUI</span>`
    : `<span class="app-badge desktop">🖥 Desktop</span>`;

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
      <button class="btn btn-stop" onclick="appAction('stop','${app.id}')">⏹ Detener</button>
      <button class="btn btn-icon" onclick="openAppSettings('${app.id}')" title="Ajustes">⚙</button>
    </div>`;
  } else if (app.status === "installed") {
    const dis = app.any_running ? "disabled" : "";
    const tip = app.any_running ? `title="Otra app activa. Deténla primero."` : "";
    btns = `<div class="btn-row">
      <button class="btn btn-launch" onclick="appAction('launch','${app.id}')" ${dis} ${tip}>▶ Lanzar</button>
      <button class="btn btn-icon"   onclick="appAction('update','${app.id}')"   title="Actualizar">↑</button>
      <button class="btn btn-icon"   onclick="openAppSettings('${app.id}')"      title="Ajustes">⚙</button>
      <button class="btn btn-icon danger" onclick="confirmUninstall('${app.id}','${app.name}')" title="Desinstalar">✕</button>
    </div>`;
  } else {
    btns = `<div class="btn-row">
      <button class="btn btn-install" onclick="appAction('install','${app.id}')">↓ Instalar</button>
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
    if (data.ok === false) showToast("⚠ Operación no disponible en este momento.");
  } catch {
    showToast("❌ Error de conexión con el backend.");
  }
}

function confirmUninstall(appId, appName) {
  showConfirm(
    `¿Desinstalar <strong>${appName}</strong>?<br><br>Se eliminará su directorio completo. Esta acción no se puede deshacer.`,
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
    if (data.ok) showToast("✅ Herramienta iniciada.");
    else showToast(`❌ ${data.error || "No se pudo iniciar."}`);
  } catch {
    showToast("❌ Error al lanzar la herramienta.");
  }
}

// ════════════════════════════════════════════════════════════════
// Cleanup
// ════════════════════════════════════════════════════════════════
async function runCleanup() {
  const btn = $("btn-cleanup");
  btn.disabled = true;
  btn.textContent = "Limpiando...";
  try {
    const res  = await fetch("/api/cleanup", { method: "POST" });
    const data = await res.json();
    if (data.clean) {
      showToast("✅ Sin procesos stale. Puertos libres.");
    } else {
      const lines = data.lines.map(l =>
        `<div class="cleanup-line">${l.icon} ${l.text}</div>`
      ).join("");
      showModal(
        "Resultado de limpieza",
        `<p style="font-size:12px;color:var(--text-secondary);margin-bottom:10px">
           ${data.killed} proceso(s) terminado(s)
         </p>
         <div class="cleanup-lines">${lines}</div>`,
        false
      );
    }
  } catch {
    showToast("❌ Error durante la limpieza.");
  } finally {
    btn.disabled = false;
    btn.textContent = "🧹 Limpiar procesos";
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
  } catch {
    showToast("❌ Error cargando ajustes.");
  }
}

async function saveSettings() {
  const btn = $("btn-save-settings");
  btn.disabled = true;
  btn.textContent = "Guardando...";
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
    if (data.ok) showToast("✅ Ajustes guardados.");
    else showToast(`❌ ${data.error}`);
  } catch {
    showToast("❌ Error guardando ajustes.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Guardar cambios";
  }
}

// ════════════════════════════════════════════════════════════════
// Event Log
// ════════════════════════════════════════════════════════════════
async function loadEventLog() {
  const list = $("log-list");
  list.innerHTML = `<div class="empty-state"><span style="font-size:22px">↻</span><span>Cargando...</span></div>`;
  try {
    const res  = await fetch("/api/log?lines=200");
    const data = await res.json();
    if (!data.entries.length) {
      list.innerHTML = `<div class="empty-state"><span style="font-size:22px">📋</span><span>Sin eventos registrados aún.</span></div>`;
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
  } catch {
    list.innerHTML = `<div class="empty-state"><span>❌ Error cargando log.</span></div>`;
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
    $("modal-title").textContent = `Ajustes — ${appName}`;

    let html = "";

    // Port override
    html += `<div class="field">
      <label>Puerto (override)</label>
      <input type="number" id="ms-port" placeholder="${data.default_port || 'auto'}"
             value="${data.port_override || ''}"
             style="max-width:140px">
      <span class="field-hint">Puerto predeterminado: ${data.default_port || 'N/A'}</span>
    </div>`;

    // Flags disponibles
    if (data.flags && data.flags.length) {
      html += `<div>
        <label style="font-size:12px;color:var(--text-secondary);display:block;margin-bottom:8px">
          Flags de lanzamiento
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
      <label>Args adicionales</label>
      <textarea id="ms-free-args" placeholder="--argumento=valor  (uno por línea o separados por espacio)"
      >${data.free_args || ''}</textarea>
      <span class="field-hint">Argumentos que no están listados como flags arriba.</span>
    </div>`;

    $("modal-body").innerHTML = html;
    openModal();
  } catch (e) {
    showToast("❌ Error cargando ajustes de la app.");
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

  // Recopilar flags activos
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
    if (data.ok) { closeModal(); showToast("✅ Ajustes guardados."); }
    else showToast(`❌ ${data.error}`);
  } catch {
    showToast("❌ Error guardando ajustes.");
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
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
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
      () => showToast("✅ Log copiado al portapapeles."),
      () => showToast("❌ No se pudo copiar.")
    );
  });

  // Cleanup
  $("btn-cleanup").addEventListener("click", runCleanup);

  // Settings
  $("btn-save-settings").addEventListener("click", saveSettings);
  $("btn-reload-settings").addEventListener("click", loadSettings);
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
