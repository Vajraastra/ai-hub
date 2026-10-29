/* Visual style selection. Native selects stay as the existing form contract. */
(function (root) {
  'use strict';

  const PAGE_SIZE = 24;
  const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const basename = value => String(value || '').replace(/\\/g, '/').split('/').pop() || '';
  function searchText(style) {
    const tags = Array.isArray(style.tags) ? style.tags : [];
    const loras = Array.isArray(style.config?.loras) ? style.config.loras : [];
    return normalize([style.name, style.prompt, style.negative, style.config?.model,
      ...tags, ...loras.map(item => item?.file || '')].join(' '));
  }
  function matchingStyles(styles, {query = '', filter = 'all', favorites = new Set()} = {}) {
    const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
    const favoriteIds = favorites instanceof Set ? favorites : new Set(favorites || []);
    return styles.filter(style => {
      if (filter === 'favorites' && !favoriteIds.has(style.id)) return false;
      if (filter === 'samples' && !style.sample_ids?.length) return false;
      return !terms.length || terms.every(term => searchText(style).includes(term));
    });
  }
  function stylePage(styles, options = {}) {
    const matches = matchingStyles(styles, options);
    const requested = Number(options.limit ?? PAGE_SIZE);
    const limit = Number.isFinite(requested) ? Math.max(PAGE_SIZE, Math.floor(requested)) : PAGE_SIZE;
    return {items: matches.slice(0, limit), total: matches.length,
      hasMore: matches.length > limit, nextLimit: limit + PAGE_SIZE};
  }
  function selectEntries(styles, options = []) {
    const entries = styles.filter(style => style && typeof style.id === 'string' && style.id);
    const known = new Set(entries.map(style => style.id));
    for (const option of options) {
      if (!option.value || known.has(option.value)) continue;
      entries.push({id: option.value, name: String(option.textContent ?? option.text ?? option.label ?? option.value),
        historic: true, unavailable: !!option.disabled || /^No disponible:/i.test(option.textContent ?? option.text ?? ''),
        sample_ids: []});
      known.add(option.value);
    }
    return entries;
  }
  function initials(value) {
    return String(value || 'Estilo').trim().split(/\s+/).slice(0, 2).map(word => word[0] || '').join('').toUpperCase();
  }
  function swatchHue(id) {
    let hash = 0;
    for (const char of String(id)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
    return hash % 360;
  }

  /**
   * create({getStyles, imageUrl, getFavorites?, onChoose?})
   * attach(selectId) hides and augments a select without replacing its value or
   * change handler. refresh() follows programmatic value/disabled changes.
   * onChoose({selectId, style, select}) runs after the native change event.
   * Only getStyles plus historic options already in that select are exposed;
   * the caller remains responsible for project scoping and deleted records.
   */
  function create({getStyles, imageUrl, getFavorites = () => new Set(), onChoose} = {}) {
    if (typeof getStyles !== 'function') throw new Error('La galería necesita un catálogo de estilos.');
    const document = root.document;
    const attachments = new Map(), brokenSamples = new Set();
    const urlForImage = imageUrl || (id => `/api/anima-studio/images/${encodeURIComponent(id)}`);
    let active = null, mode = 'all', limit = PAGE_SIZE, returnFocus = null;
    const element = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const dialog = element('dialog', 'asb-dialog');
    const serial = document.querySelectorAll?.('.asb-dialog').length || 0;
    dialog.id = `anima-style-browser-${serial + 1}`;
    dialog.setAttribute('aria-labelledby', `${dialog.id}-title`);
    const header = element('div', 'asb-dialog-header');
    const headingGroup = element('div');
    const eyebrow = element('p', 'asb-eyebrow', 'BIBLIOTECA DE ESTILOS');
    const heading = element('h2', '', 'Elige el acabado de tu imagen');
    heading.id = `${dialog.id}-title`;
    const dismiss = element('button', 'asb-dismiss', '✕');
    dismiss.type = 'button'; dismiss.setAttribute('aria-label', 'Cerrar galería de estilos');
    headingGroup.append(eyebrow, heading); header.append(headingGroup, dismiss);
    const searchLabel = element('label', 'asb-search-label', 'Buscar estilos');
    const search = element('input', 'asb-search');
    search.type = 'search'; search.placeholder = 'Nombre, etiquetas, modelo o LoRA…';
    search.setAttribute('aria-label', 'Buscar estilos');
    searchLabel.append(search);
    const filters = element('div', 'asb-filters');
    filters.setAttribute('aria-label', 'Filtrar estilos');
    const filterButtons = [];
    for (const [id, label] of [['all', 'Todos'], ['favorites', 'Favoritos'], ['samples', 'Con muestras']]) {
      const button = element('button', 'asb-filter', label);
      button.type = 'button'; button.dataset.filter = id;
      button.onclick = () => { mode = id; limit = PAGE_SIZE; render(); };
      filters.append(button); filterButtons.push(button);
    }
    const selectedNote = element('p', 'asb-selected-note');
    const status = element('p', 'asb-count'); status.setAttribute('aria-live', 'polite');
    const grid = element('div', 'asb-grid'); grid.setAttribute('aria-label', 'Estilos disponibles');
    const footer = element('div', 'asb-footer');
    const clear = element('button', 'asb-clear', 'Sin estilo'); clear.type = 'button';
    const more = element('button', 'asb-more', 'Mostrar más estilos'); more.type = 'button';
    const hint = element('p', 'asb-hint', 'Las muestras muestran el acabado guardado. Puedes preparar otros estilos en Estilos.');
    footer.append(clear, more); dialog.append(header, searchLabel, filters, selectedNote, status, grid, footer, hint);
    document.body.append(dialog);

    function entries(select) {
      return selectEntries(getStyles() || [], Array.from(select.options || []));
    }
    function picture(style, compact = false) {
      const frame = element('span', `asb-art${compact ? ' asb-art-compact' : ''}`);
      const placeholder = element('span', 'asb-placeholder');
      placeholder.style.setProperty('--asb-swatch-hue', swatchHue(style.id));
      placeholder.append(element('strong', '', initials(style.name)), element('small', '', 'Sin muestra'));
      frame.append(placeholder);
      const sample = style.sample_ids?.find(id => typeof id === 'string' && id && !brokenSamples.has(id));
      if (sample) {
        const image = element('img'); image.alt = ''; image.loading = 'lazy'; image.decoding = 'async';
        image.onerror = () => { brokenSamples.add(sample); image.remove(); placeholder.hidden = false; };
        image.onload = () => { placeholder.hidden = true; };
        image.src = urlForImage(sample); frame.append(image);
      }
      return frame;
    }
    function choiceLabel(select, style) {
      return style ? `Cambiar estilo: ${style.name}` : 'Elegir un estilo visual';
    }
    function renderChoice(attachment) {
      const {select, button} = attachment;
      const style = entries(select).find(item => item.id === select.value);
      button.replaceChildren(); button.disabled = !!select.disabled;
      button.setAttribute('aria-label', choiceLabel(select, style));
      const chosen = !!style;
      button.classList.toggle('asb-choice-selected', chosen);
      const text = element('span', 'asb-choice-text');
      if (style) {
        button.append(picture(style, true));
        text.append(element('strong', '', style.name), element('small', '',
          `${style.revision ? `v${style.revision}` : 'Guardado'}${style.historic ? ' · Del maestro' : ''}`));
      } else {
        const icon = element('span', 'asb-choice-empty', '✦'); icon.setAttribute('aria-hidden', 'true');
        button.append(icon); text.append(element('strong', '', 'Elegir estilo'), element('small', '', 'Explora las muestras de tu biblioteca'));
      }
      button.append(text, element('span', 'asb-choice-action', chosen ? 'Cambiar' : 'Explorar'));
    }
    function choose(id) {
      if (!active || active.select.disabled) return;
      const {select} = active;
      const style = id ? entries(select).find(item => item.id === id) : null;
      if (id && (!style || style.unavailable)) return;
      const option = Array.from(select.options || []).find(item => item.value === id);
      if (!option && id) {
        const added = document.createElement('option'); added.value = id; added.textContent = style.name;
        select.append(added);
      }
      if (!id && !option) return;
      select.value = id;
      select.dispatchEvent(new root.Event('change', {bubbles: true}));
      renderChoice(active);
      onChoose?.({selectId: select.id, style, select});
      close();
    }
    function render() {
      if (!active) return;
      const select = active.select;
      const styles = entries(select);
      const selected = styles.find(style => style.id === select.value);
      const page = stylePage(styles, {query: search.value, filter: mode, favorites: getFavorites(), limit});
      selectedNote.textContent = selected ? `Elegido: ${selected.name}` : 'Elige una tarjeta para usar su acabado.';
      for (const button of filterButtons) button.setAttribute('aria-pressed', String(button.dataset.filter === mode));
      status.textContent = page.total ? `${page.items.length} de ${page.total} estilos` : 'No hay estilos con estos filtros.';
      grid.replaceChildren();
      for (const style of page.items) {
        const card = element('button', 'asb-card'); card.type = 'button'; card.disabled = !!style.unavailable;
        card.setAttribute('aria-label', `Elegir estilo ${style.name}${style.revision ? `, versión ${style.revision}` : ''}`);
        card.setAttribute('aria-pressed', String(style.id === select.value));
        card.dataset.styleId = style.id;
        card.append(picture(style));
        const text = element('span', 'asb-card-text');
        const favorite = getFavorites()?.has?.(style.id) ? '★ ' : '';
        text.append(element('strong', '', favorite + style.name));
        const model = basename(style.config?.model);
        text.append(element('small', '', `${style.revision ? `v${style.revision}` : 'Guardado'}${style.historic ? ' · Del maestro' : ''}${model ? ` · ${model}` : ''}`));
        card.append(text); card.onclick = () => choose(style.id); grid.append(card);
      }
      if (!page.total) {
        const empty = element('div', 'asb-empty');
        empty.append(element('strong', '', styles.length ? 'Prueba otra búsqueda' : 'Tu biblioteca de estilos empieza aquí'),
          element('p', '', styles.length ? 'Puedes buscar por nombre, etiqueta, modelo o LoRA.' : 'Crea una prueba en Estilos y guarda el acabado que te guste.'));
        grid.append(empty);
      }
      clear.hidden = !Array.from(select.options || []).some(option => option.value === '');
      more.hidden = !page.hasMore; more.textContent = `Mostrar ${Math.min(PAGE_SIZE, Math.max(0, page.total - page.items.length))} estilos más`;
    }
    function openAttachment(attachment) {
      if (attachment.select.disabled) return;
      active = attachment; returnFocus = attachment.button; mode = 'all'; limit = PAGE_SIZE; search.value = '';
      render();
      if (!dialog.open) dialog.showModal();
      search.focus();
    }
    function close() {
      if (dialog.open) dialog.close();
      active = null;
      if (returnFocus?.isConnected !== false) returnFocus?.focus();
      returnFocus = null;
    }
    function attach(selectId) {
      const select = typeof selectId === 'string' ? document.getElementById(selectId) : selectId;
      if (!select) return null;
      if (attachments.has(select)) { renderChoice(attachments.get(select)); return attachments.get(select).button; }
      const button = element('button', 'asb-choice'); button.type = 'button';
      button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-controls', dialog.id);
      const attachment = {select, button}; attachments.set(select, attachment);
      select.hidden = true; select.tabIndex = -1; select.setAttribute('aria-hidden', 'true'); select.classList.add('asb-native-select');
      select.insertAdjacentElement('afterend', button);
      button.onclick = () => openAttachment(attachment);
      select.addEventListener('change', () => renderChoice(attachment));
      if (root.MutationObserver) {
        attachment.observer = new root.MutationObserver(() => renderChoice(attachment));
        attachment.observer.observe(select, {attributes: true, childList: true, subtree: true,
          attributeFilter: ['disabled', 'selected']});
      }
      renderChoice(attachment); return button;
    }
    function refresh() {
      for (const attachment of attachments.values()) renderChoice(attachment);
      if (dialog.open) {
        if (active?.select.disabled) close();
        else render();
      }
    }
    function open(selectId) {
      const select = typeof selectId === 'string' ? document.getElementById(selectId) : selectId;
      if (!select) return;
      if (!attachments.has(select)) attach(select);
      openAttachment(attachments.get(select));
    }
    search.oninput = () => { limit = PAGE_SIZE; render(); };
    more.onclick = () => { limit += PAGE_SIZE; render(); };
    clear.onclick = () => choose(''); dismiss.onclick = close;
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    // A native close (including a surrounding dialog being dismissed) must not
    // leave an active picker or detached focus target behind.
    dialog.addEventListener('close', () => { active = null; returnFocus?.focus(); returnFocus = null; });
    return {attach, refresh, open, close};
  }

  const api = {create, matchingStyles, stylePage, selectEntries, initials, PAGE_SIZE};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AnimaStyleBrowser = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
