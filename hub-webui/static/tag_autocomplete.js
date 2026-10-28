/* Shared prompt tag completion for Painter and Anima Studio. */
(function (root) {
  'use strict';
  let nextId = 0;
  const COLORS = {0:'#4a9eff',1:'#ff4d4d',3:'#b04dff',4:'#44dd77',5:'#ff9900',7:'#5bb8ff',8:'#ff6666',9:'#ffd700',10:'#cc77ff',11:'#66ee99',12:'#ff7755',14:'#ffbb44',15:'#44bb88'};
  const PAIRS = {'(':')','[':']','<':'>'};
  const BOUNDARIES = ',\r\n<>()[]';
  function escaped(text, index) {
    let slashes = 0;
    while (index > 0 && text[--index] === '\\') slashes++;
    return slashes % 2 === 1;
  }
  function groupsBefore(text, end) {
    const stack = [];
    let valid = true;
    for (let i = 0; i < end; i++) {
      if (escaped(text, i)) continue;
      if (PAIRS[text[i]]) stack.push(text[i]);
      else if (')]>'.includes(text[i])) {
        if (PAIRS[stack[stack.length - 1]] === text[i]) stack.pop();
        else valid = false;
      }
    }
    return {stack, valid};
  }
  function context(value, selectionStart, selectionEnd = selectionStart) {
    const text = String(value ?? '');
    const caret = Math.max(0, Math.min(text.length, Number(selectionStart) || 0));
    const end = Math.max(caret, Math.min(text.length, Number(selectionEnd) || 0));
    const group = groupsBefore(text, caret);
    if (group.stack.includes('<')) return null;
    let left = caret, right = end;
    while (left > 0 && (!BOUNDARIES.includes(text[left - 1]) || escaped(text, left - 1))) left--;
    while (right < text.length && (!BOUNDARIES.includes(text[right]) || escaped(text, right))) right++;
    for (let i = caret; i < end; i++) {
      if (BOUNDARIES.includes(text[i]) && !escaped(text, i)) return null;
    }
    const segment = text.slice(left, right);
    const weight = group.stack.some(char => char === '(' || char === '[')
      ? /:([+-]?(?:\d*(?:\.\d*)?))\s*$/.exec(segment) : null;
    if (weight) {
      const colon = left + weight.index;
      if (caret > colon || end > colon) return null;
      right = colon;
    }
    let start = end > caret ? caret : left;
    let finish = end > caret ? end : right;
    while (start < finish && /\s/.test(text[start])) start++;
    while (finish > start && /\s/.test(text[finish - 1])) finish--;
    const query = text.slice(start, end > caret ? finish : Math.min(caret, finish)).trim().replace(/\\([()[\]])/g, '$1');
    return query ? {query, start, end:finish, caret, selectionEnd:end, group} : null;
  }
  function escapeName(name) {
    const text = String(name);
    let result = '';
    for (let i = 0; i < text.length; i++) {
      if ('()[]'.includes(text[i]) && !escaped(text, i)) result += '\\';
      result += text[i];
    }
    return result;
  }
  function complete(value, selectionStart, selectionEnd, name) {
    const text = String(value ?? ''), token = context(text, selectionStart, selectionEnd);
    if (!token || typeof name !== 'string' || !name.trim()) return null;
    const before = text.slice(0, token.start), after = text.slice(token.end);
    let replacement = escapeName(name), caret;
    if (!after.trim()) {
      if (token.group.valid && token.group.stack.length) {
        replacement += [...token.group.stack].reverse().map(char => PAIRS[char]).join('');
      } else if (!token.group.stack.length) {
        replacement += ',' + (after ? '' : ' ');
      }
    }
    caret = before.length + replacement.length;
    if (!token.group.stack.length && !after.trim()) caret += after.length;
    return {value:before + replacement + after, caret, start:token.start, end:token.end, replacement};
  }
  function formatCount(number) {
    const count = Math.max(0, Number(number) || 0);
    if (count >= 1e6) return (count / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (count >= 1e3) return Math.round(count / 1e3) + 'k';
    return String(count);
  }
  function create({search, getCsv, formatName = name => name, onError = () => {}, debounceMs = 50} = {}) {
    if (typeof search !== 'function' || typeof getCsv !== 'function') throw new TypeError('search y getCsv son obligatorios.');
    if (typeof formatName !== 'function') throw new TypeError('formatName debe ser una función.');
    const doc = root.document, win = doc.defaultView || root;
    const id = `hub-tag-list-${++nextId}`;
    const drop = doc.createElement('div');
    drop.id = id; drop.className = 'hub-tag-list'; drop.hidden = true;
    drop.setAttribute('role', 'listbox'); drop.setAttribute('aria-label', 'Sugerencias de etiquetas');
    const attached = new Map(), cleanups = [];
    let active = null, results = [], selected = 0, timer = null, controller = null;
    let revision = 0, destroyed = false, applying = false, shownSnapshot = null;
    function bind(target, event, handler, options) {
      target.addEventListener(event, handler, options);
      cleanups.push(() => target.removeEventListener(event, handler, options));
    }
    function cancelSearch() {
      revision++;
      if (timer !== null) win.clearTimeout(timer);
      timer = null;
      if (controller) controller.abort();
      controller = null;
    }
    function hide() {
      drop.hidden = true; results = []; shownSnapshot = null;
      if (active) {active.setAttribute('aria-expanded', 'false'); active.removeAttribute('aria-activedescendant');}
    }
    function close() {cancelSearch(); hide();}
    function updateSelected() {
      [...drop.children].forEach((option, index) => {
        const chosen = index === selected;
        option.setAttribute('aria-selected', String(chosen));
        if (chosen) {active.setAttribute('aria-activedescendant', option.id); option.scrollIntoView?.({block:'nearest', inline:'nearest'});}
      });
    }
    function position() {
      if (drop.hidden || !active) return;
      const rect = active.getBoundingClientRect();
      const width = win.innerWidth || doc.documentElement.clientWidth;
      const height = win.innerHeight || doc.documentElement.clientHeight;
      if (rect.bottom <= 0 || rect.top >= height) {close(); return;}
      const listWidth = Math.max(0, Math.min(Math.max(rect.width, 240), 420, width - 16));
      drop.style.width = `${listWidth}px`;
      const below = height - rect.bottom - 8, above = rect.top - 8;
      const useBelow = below >= Math.min(drop.scrollHeight, 160) || below >= above;
      const available = Math.min(320, useBelow ? below : above);
      if (available < 36) {close(); return;}
      drop.style.maxHeight = `${available}px`;
      const listHeight = Math.min(drop.scrollHeight, available);
      drop.style.left = `${Math.max(8, Math.min(rect.left, width - listWidth - 8))}px`;
      drop.style.top = `${Math.max(8, useBelow ? rect.bottom + 2 : rect.top - listHeight - 2)}px`;
    }
    function show(items, textarea, snapshot) {
      results = Array.isArray(items) ? items.filter(item => item && typeof item.name === 'string' && item.name.trim()).slice(0, 12) : [];
      if (!results.length) {hide(); return;}
      active = textarea; selected = 0; shownSnapshot = snapshot;
      const host = textarea.closest('dialog') || doc.body;
      if (drop.parentElement !== host) host.appendChild(drop);
      drop.replaceChildren();
      results.forEach((result, index) => {
        const option = doc.createElement('div');
        option.id = `${id}-option-${index}`; option.className = 'hub-tag-option'; option.dataset.index = String(index);
        option.setAttribute('role', 'option');
        const main = doc.createElement('div'); main.className = 'hub-tag-main';
        const dot = doc.createElement('span'); dot.className = 'hub-tag-dot'; dot.style.backgroundColor = COLORS[result.category] || '#888'; dot.setAttribute('aria-hidden', 'true');
        const name = doc.createElement('span'); name.className = 'hub-tag-name'; name.textContent = result.name.replace(/_/g, ' ');
        const count = doc.createElement('span'); count.className = 'hub-tag-count'; count.textContent = formatCount(result.post_count);
        main.append(dot, name, count); option.appendChild(main);
        if (result.matched_alias) {
          const alias = doc.createElement('div'); alias.className = 'hub-tag-alias'; alias.textContent = '→ ' + String(result.matched_alias).replace(/_/g, ' '); option.appendChild(alias);
        }
        drop.appendChild(option);
      });
      drop.hidden = false; active.setAttribute('aria-expanded', 'true');
      position(); if (!drop.hidden) updateSelected();
    }
    function currentCsv() {return String(getCsv() || '');}
    function schedule(textarea) {
      if (destroyed || applying || attached.get(textarea)?.composing) return;
      close(); active = textarea;
      const token = context(textarea.value, textarea.selectionStart, textarea.selectionEnd);
      let csv;
      try {csv = currentCsv();} catch (error) {onError(error); return;}
      if (!token || !csv || doc.activeElement !== textarea) return;
      const snapshot = {value:textarea.value, start:textarea.selectionStart, end:textarea.selectionEnd, csv, revision};
      timer = win.setTimeout(async () => {
        timer = null;
        const request = new AbortController(); controller = request;
        try {
          const items = await search(token.query, csv, {signal:request.signal});
          if (destroyed || request.signal.aborted || revision !== snapshot.revision || active !== textarea || doc.activeElement !== textarea || textarea.value !== snapshot.value || textarea.selectionStart !== snapshot.start || textarea.selectionEnd !== snapshot.end || currentCsv() !== snapshot.csv) return;
          show(items, textarea, snapshot);
        } catch (error) {
          if (error?.name !== 'AbortError' && revision === snapshot.revision && !destroyed) {hide(); onError(error);}
        } finally {if (controller === request) controller = null;}
      }, Math.max(0, Number(debounceMs) || 0));
    }
    function insert() {
      const textarea = active, item = results[selected];
      if (!textarea || !item) return;
      try {
        if (!shownSnapshot || doc.activeElement !== textarea || textarea.value !== shownSnapshot.value || textarea.selectionStart !== shownSnapshot.start || textarea.selectionEnd !== shownSnapshot.end || currentCsv() !== shownSnapshot.csv) {close(); return;}
      } catch (error) {close(); onError(error); return;}
      let formattedName;
      try {
        formattedName = formatName(item.name, item);
        if (typeof formattedName !== 'string') throw new TypeError('formatName debe devolver texto.');
      } catch (error) {close(); onError(error); return;}
      const insertion = complete(textarea.value, textarea.selectionStart, textarea.selectionEnd, formattedName);
      close();
      if (!insertion) return;
      applying = true;
      try {
        if (typeof textarea.setRangeText === 'function') textarea.setRangeText(insertion.replacement, insertion.start, insertion.end, 'end');
        else textarea.value = insertion.value;
        textarea.setSelectionRange(insertion.caret, insertion.caret);
        attached.get(textarea).inserted = {value:textarea.value, caret:insertion.caret};
        textarea.dispatchEvent(new win.Event('input', {bubbles:true})); textarea.focus();
      } finally {applying = false;}
    }
    function attach(textarea) {
      if (destroyed || !textarea || attached.has(textarea)) return;
      const attributes = ['aria-autocomplete','aria-controls','aria-haspopup','aria-expanded','aria-activedescendant'];
      const state = {composing:false, inserted:null, attributes:new Map(attributes.map(name => [name, textarea.getAttribute(name)]))};
      attached.set(textarea, state);
      textarea.setAttribute('aria-autocomplete', 'list'); textarea.setAttribute('aria-controls', id);
      textarea.setAttribute('aria-haspopup', 'listbox'); textarea.setAttribute('aria-expanded', 'false');
      bind(textarea, 'input', event => {if (!event.isComposing && !applying) {state.inserted = null; schedule(textarea);}});
      bind(textarea, 'focus', () => schedule(textarea));
      bind(textarea, 'blur', close);
      const selectionChanged = () => {
        if (state.inserted?.value === textarea.value && state.inserted.caret === textarea.selectionStart && textarea.selectionStart === textarea.selectionEnd) return;
        state.inserted = null; schedule(textarea);
      };
      bind(textarea, 'click', selectionChanged); bind(textarea, 'select', selectionChanged);
      bind(textarea, 'compositionstart', () => {state.composing = true; close();});
      bind(textarea, 'compositionend', () => {state.composing = false; schedule(textarea);});
      bind(textarea, 'keydown', event => {
        if (event.isComposing || event.keyCode === 229 || state.composing || drop.hidden || active !== textarea) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault(); selected = (selected + (event.key === 'ArrowDown' ? 1 : results.length - 1)) % results.length; updateSelected();
        } else if (event.key === 'Tab' || event.key === 'Enter') {event.preventDefault(); insert();}
        else if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); close();}
        else if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) close();
      });
      bind(textarea, 'keyup', event => {if (!event.isComposing && ['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) selectionChanged();});
      const dialog = textarea.closest('dialog');
      if (dialog) {
        const closeDialog = () => {if (active?.closest('dialog') === dialog) close();};
        bind(dialog, 'close', closeDialog); bind(dialog, 'cancel', closeDialog);
      }
    }
    bind(drop, 'pointerdown', event => {
      const option = event.target.closest('.hub-tag-option');
      if (!option || drop.hidden || (event.button != null && event.button !== 0)) return;
      event.preventDefault(); selected = Number(option.dataset.index); insert();
    });
    bind(drop, 'click', event => {
      const option = event.target.closest('.hub-tag-option');
      if (option && !drop.hidden && event.detail === 0) {selected = Number(option.dataset.index); insert();}
    });
    bind(drop, 'pointermove', event => {
      const option = event.target.closest('.hub-tag-option');
      if (option && !drop.hidden) {selected = Number(option.dataset.index); updateSelected();}
    });
    bind(doc, 'pointerdown', event => {if (active && event.target !== active && !drop.contains(event.target)) close();});
    bind(win, 'resize', position); bind(win, 'scroll', position, true);
    function destroy() {
      if (destroyed) return;
      close(); destroyed = true; cleanups.splice(0).forEach(cleanup => cleanup());
      for (const [textarea, state] of attached) for (const [name, value] of state.attributes) {
        if (value === null) textarea.removeAttribute(name); else textarea.setAttribute(name, value);
      }
      attached.clear(); active = null; drop.remove();
    }
    return {attach, close, destroy};
  }
  const api = {create, context, complete, formatCount};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HubTagAutocomplete = api;
})(typeof window !== 'undefined' ? window : globalThis);
