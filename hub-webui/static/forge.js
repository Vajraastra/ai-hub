// Forge Lab — frontend mínimo (Fase 0): solo muestra el diagnóstico.

const mark = (ok) => ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>';

async function loadStatus() {
  const el = document.getElementById('status-content');
  try {
    const s = await (await fetch('/api/forge/status')).json();
    document.getElementById('arch-label').textContent = s.arch;

    let html = `<div class="status-row"><span>ComfyUI (API :8188)</span>${mark(s.comfyui)}</div>`;
    for (const [kind, m] of Object.entries(s.models)) {
      html += `<div class="status-row"><span>${kind}<br><span class="path">${m.path}</span></span>${mark(m.present)}</div>`;
    }
    for (const [node, ok] of Object.entries(s.nodes)) {
      html += `<div class="status-row"><span>nodo ${node}</span>${mark(ok)}</div>`;
    }
    if (!Object.keys(s.nodes).length) {
      html += `<div class="status-row"><span>nodos Realtime-Lora</span><span class="bad">sin comprobar (ComfyUI apagado)</span></div>`;
    }
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<span class="bad">Error consultando /api/forge/status: ${e}</span>`;
  }
}

loadStatus();
