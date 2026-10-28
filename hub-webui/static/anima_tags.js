'use strict';

(() => {
  const $ = id => document.getElementById(id);
  const toggle = $('anima-tags-enabled'), status = $('anima-tags-status');
  const reload = $('anima-tags-reload'), download = $('anima-tags-download');
  window.AnimaPromptGuide.init();
  const API = '/api/painter/tags';
  let profile = null, ready = false, busy = false, missing = false, epoch = 0;
  let requestController = null, source = null;
  try { toggle.checked = localStorage.getItem('anima-studio-tag-suggestions') !== '0'; } catch {}

  async function request(path, options = {}) {
    const response = await fetch(API + path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${response.status}`);
    return data;
  }
  const autocomplete = window.HubTagAutocomplete.create({
    formatName: (name, result) => window.AnimaPromptGuide.formatTag(name, result, profile?.tag_csv || ''),
    getCsv: () => toggle.checked && ready && !busy ? profile?.tag_csv || '' : '',
    search: async (query, csv, {signal}) => (await request(`/search?q=${encodeURIComponent(query.replace(/^@/, ''))}&csv=${encodeURIComponent(csv)}&limit=8`, {signal})).results,
    onError: () => { status.textContent = 'No se pudieron buscar tags. Puedes continuar escribiendo o volver a cargar la base.'; },
  });
  document.querySelectorAll('textarea[data-tag-autocomplete]').forEach(field => autocomplete.attach(field));

  function controls() {
    reload.disabled = busy || !toggle.checked;
    download.disabled = busy || !toggle.checked;
    download.classList.toggle('hidden', !missing);
  }
  function stop() {
    epoch++;
    requestController?.abort();requestController = null;
    source?.close();source = null;
    autocomplete.close();busy = false;
  }
  async function refresh(force = false) {
    stop();ready = false;
    if (!toggle.checked) { status.textContent = 'Sugerencias desactivadas.';controls();return; }
    const stamp = epoch, controller = new AbortController();requestController = controller;
    busy = true;controls();status.textContent = 'Cargando base de tags…';
    try {
      if (!profile) {
        const config = await request('/profiles', {signal:controller.signal});
        profile = config.profiles?.find(item => item.id === config.default && item.tag_csv)
          || config.profiles?.find(item => item.tag_csv);
        if (!profile) throw new Error('No hay una base de tags configurada en Painter.');
      }
      if (stamp !== epoch) return;
      const result = await request(`/${force ? 'reload' : 'status'}?csv=${encodeURIComponent(profile.tag_csv)}`, {method:force?'POST':'GET',signal:controller.signal});
      if (stamp !== epoch) return;
      ready = result.count > 0 && (force || result.loaded);
      missing = !ready && !result.csv_present;
      status.textContent = ready ? `${result.count.toLocaleString()} tags disponibles.` : missing ? 'Base de tags no instalada.' : 'La base no contiene tags válidos.';
    } catch (error) {
      if (stamp === epoch && error.name !== 'AbortError') status.textContent = `Tags no disponibles: ${error.message}`;
    } finally {
      if (stamp === epoch) { busy = false;requestController = null;controls(); }
    }
  }
  function downloadTags() {
    if (!profile || busy || !toggle.checked) return;
    stop();const stamp = epoch;
    busy = true;ready = false;controls();status.textContent = 'Descargando base de tags…';
    const stream = new EventSource(`${API}/download?profile_id=${encodeURIComponent(profile.id)}`);source = stream;
    function finish(message, success = false) {
      stream.close();
      if (source !== stream || stamp !== epoch) return;
      source = null;busy = false;ready = success;missing = !success;
      status.textContent = message;controls();
    }
    stream.onmessage = ({data}) => {
      if (source !== stream || stamp !== epoch) return;
      let event;
      try { event = JSON.parse(data); } catch { finish('La descarga devolvió una respuesta inválida.');return; }
      if (event.type === 'progress') status.textContent = `Descargando tags… ${event.total ? `${event.pct}%` : `${(event.downloaded / 1048576).toFixed(1)} MB`}`;
      else if (event.type === 'loading') status.textContent = 'Preparando sugerencias…';
      else if (event.type === 'done') finish(`${event.count.toLocaleString()} tags disponibles.`, event.count > 0 && event.csv === profile.tag_csv);
      else if (event.type === 'error') finish(`No se pudo descargar la base: ${event.msg}`);
    };
    stream.onerror = () => finish('Se interrumpió la descarga. Puedes reintentar.');
  }
  toggle.addEventListener('change', () => {
    try { localStorage.setItem('anima-studio-tag-suggestions', toggle.checked?'1':'0'); } catch {}
    void refresh();
  });
  reload.addEventListener('click', () => { void refresh(true); });
  download.addEventListener('click', downloadTags);
  window.addEventListener('pagehide', stop);
  window.addEventListener('pageshow', event => { if(event.persisted)void refresh(); });
  void refresh();
})();
