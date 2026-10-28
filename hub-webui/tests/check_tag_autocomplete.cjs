const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../static/tag_autocomplete.js'), 'utf8');
const { context, complete, formatCount } = require('../static/tag_autocomplete.js');

class FakeEvent {
  constructor(type, options = {}) { this.type = type; Object.assign(this, options); this.defaultPrevented = false; }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
}
class EventTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(callback); }
  removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
  dispatchEvent(event) { event.target ||= this; for (const callback of [...this.listeners.get(event.type) || []]) callback(event); return !event.defaultPrevented; }
}
class Element extends EventTarget {
  constructor(tag, doc) { super(); this.tagName = tag.toUpperCase(); this.doc = doc; this.attributes = new Map(); this.children = []; this.style = {}; this.dataset = {}; this.className = ''; this.value = ''; this.hidden = false; this.selectionStart = this.selectionEnd = 0; this._text = ''; this.rect = {left:10, top:20, bottom:120, width:300}; }
  set innerHTML(_) { throw new Error('Autocomplete must construct text nodes, never HTML.'); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  get scrollHeight() { return Math.max(40, this.children.length * 40); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  appendChild(child) { child.remove(); this.children.push(child); child.parentElement = this; return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  replaceChildren() { for (const child of this.children) child.parentElement = null; this.children = []; }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); this.parentElement = null; }
  closest(selector) { for (let node = this; node; node = node.parentElement) if (selector === 'dialog' ? node.tagName === 'DIALOG' : node.className.split(' ').includes(selector.slice(1))) return node; return null; }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  getBoundingClientRect() { return this.rect; }
  scrollIntoView() { this.scrolledAfterPosition = this.parentElement?.style.top !== undefined; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  setRangeText(value, start, end) { this.value = this.value.slice(0, start) + value + this.value.slice(end); this.setSelectionRange(start + value.length, start + value.length); }
  focus() { if (this.doc.activeElement === this) return; this.doc.activeElement?.dispatchEvent(new FakeEvent('blur')); this.doc.activeElement = this; this.dispatchEvent(new FakeEvent('focus')); }
}
function harness(options = {}) {
  const doc = new EventTarget(), win = new EventTarget();
  Object.assign(win, {innerWidth:800, innerHeight:600, setTimeout, clearTimeout, Event:FakeEvent, document:doc});
  doc.defaultView = win; doc.documentElement = {clientWidth:800, clientHeight:600};
  doc.createElement = tag => new Element(tag, doc); doc.body = doc.createElement('body');
  const sandbox = {window:win, AbortController}; vm.runInNewContext(source, sandbox);
  let csv = 'danbooru';
  const requests = [], errors = [];
  const engine = win.HubTagAutocomplete.create({debounceMs:0, getCsv:() => csv, onError:error => errors.push(error), search(query, sourceCsv, {signal}) {
    return new Promise((resolve, reject) => requests.push({query, csv:sourceCsv, signal, resolve, reject}));
  }, ...options});
  function textarea(value, parent = doc.body) { const node = doc.createElement('textarea'); node.value = value; node.setSelectionRange(value.length, value.length); parent.appendChild(node); engine.attach(node); return node; }
  return {doc, win, engine, requests, errors, textarea, setCsv(value) {csv = value;}, list:() => {
    const find = node => node.className === 'hub-tag-list' ? node : node.children.map(find).find(Boolean);
    return find(doc.body);
  }};
}
const tick = () => new Promise(resolve => setTimeout(resolve, 5));
function input(textarea, value, start = value.length, end = start) {textarea.value = value; textarea.setSelectionRange(start, end); textarea.dispatchEvent(new FakeEvent('input'));}
function key(textarea, value, options = {}) {const event = new FakeEvent('keydown', {key:value, ...options}); textarea.dispatchEvent(event); return event;}

test('completion replaces a whole token at the caret and preserves other tags and weights', () => {
  assert.equal(complete('blue_hair, red_eyes', 4, 4, 'black_hair').value, 'black_hair, red_eyes');
  assert.equal(complete('(blue_hair:1.2), red_eyes', 5, 5, 'black_hair').value, '(black_hair:1.2), red_eyes');
  assert.equal(complete('first,  blue  , next', 12, 12, 'black_hair').value, 'first,  black_hair  , next');
  assert.equal(complete('blue\nred_eyes', 4, 4, 'black_hair').value, 'black_hair\nred_eyes');
});
test('explicit selection replaces only its selected range without duplicating selected text', () => {
  assert.equal(complete('blue_hair, red_eyes', 0, 9, 'black_hair').value, 'black_hair, red_eyes');
  assert.equal(complete('blue eyes', 0, 4, 'green').value, 'green eyes');
  assert.equal(context('one, two', 0, 8), null);
});
test('literal parentheses are escaped once and remain searchable', () => {
  assert.equal(complete('miku', 4, 4, 'hatsune_miku_(vocaloid)').value, 'hatsune_miku_\\(vocaloid\\), ');
  assert.equal(complete('miku', 4, 4, 'hatsune_miku_\\(vocaloid\\)').value, 'hatsune_miku_\\(vocaloid\\), ');
  const text = 'hatsune_miku_\\(vocaloid\\)';
  assert.equal(context(text, text.length).query, 'hatsune_miku_(vocaloid)');
});
test('open groups close only at end and no comma is inserted inside existing groups', () => {
  assert.equal(complete('(blue', 5, 5, 'blue_hair').value, '(blue_hair)');
  assert.equal(complete('[(blue', 6, 6, 'blue_hair').value, '[(blue_hair)]');
  assert.equal(complete('(blue, red_eyes)', 5, 5, 'blue_hair').value, '(blue_hair, red_eyes)');
  assert.equal(complete('(blue, red_eyes', 5, 5, 'blue_hair').value, '(blue_hair, red_eyes');
  assert.equal(complete('blue_hair', 4, 4, 'black_hair').value, 'black_hair, ');
});
test('numeric weights and LoRA control syntax are not autocomplete targets', () => {
  assert.equal(context('(blue_hair:1.2)', 13), null);
  assert.equal(context('<lora:blue:1.0>', 9), null);
  assert.equal(context('(rating:general)', 10).query, 'rating:ge');
  assert.equal(context('blue, ', 6), null);
  assert.equal(formatCount(2500000), '2.5M'); assert.equal(formatCount(1500), '2k');
});
test('out-of-order results are rejected even if a provider ignores abort', async () => {
  const h = harness(), ta = h.textarea('bl'); ta.focus(); await tick();
  input(ta, 'blu'); await tick();
  assert.equal(h.requests[0].signal.aborted, true);
  h.requests[1].resolve([{name:'blue_hair', category:0, post_count:100}]); await tick();
  h.requests[0].resolve([{name:'black_hair'}]); await tick();
  assert.match(h.list().textContent, /blue hair/); assert.doesNotMatch(h.list().textContent, /black hair/);
  h.engine.destroy();
});
test('responses cannot cross textarea, caret, or CSV changes', async () => {
  const h = harness(), first = h.textarea('blue'), second = h.textarea('green');
  first.focus(); await tick(); second.focus(); await tick();
  h.requests[0].resolve([{name:'blue_hair'}]); await tick(); assert.equal(h.list(), undefined);
  second.setSelectionRange(2, 2); h.requests[1].resolve([{name:'green_hair'}]); await tick(); assert.equal(h.list(), undefined);
  input(second, 'green'); await tick(); h.setCsv('e621'); h.requests[2].resolve([{name:'green_hair'}]); await tick(); assert.equal(h.list(), undefined);
  h.engine.destroy();
});
test('close aborts pending work and destroy restores ARIA and removes handlers', async () => {
  const h = harness(), ta = h.textarea('blue'); ta.focus(); await tick();
  h.engine.close(); assert.equal(h.requests[0].signal.aborted, true);
  h.requests[0].resolve([{name:'blue_hair'}]); await tick(); assert.equal(h.list(), undefined);
  h.engine.destroy(); assert.equal(ta.getAttribute('aria-controls'), null);
  input(ta, 'green'); await tick(); assert.equal(h.requests.length, 1);
});
test('result text and aliases never become HTML and listbox selection is accessible', async () => {
  const h = harness(), ta = h.textarea('blue'); ta.focus(); await tick();
  h.requests[0].resolve([{name:'<img onerror=evil>',matched_alias:'<script>evil</script>',category:4,post_count:2500},{name:'blue_eyes'}]); await tick();
  const list = h.list(); assert.equal(list.getAttribute('role'), 'listbox');
  assert.match(list.textContent, /<img onerror=evil>/); assert.match(list.textContent, /<script>evil<\/script>/);
  assert.equal(list.children[0].getAttribute('role'), 'option');
  assert.equal(list.children[0].getAttribute('aria-selected'), 'true');
  assert.equal(ta.getAttribute('aria-activedescendant'), list.children[0].id);
  key(ta, 'ArrowDown'); assert.equal(list.children[1].getAttribute('aria-selected'), 'true');
  h.engine.destroy();
});
test('Enter inserts a complete weighted token once and does not reopen after synthetic selection', async () => {
  const h = harness(), ta = h.textarea('(blue_hair:1.2)'); ta.setSelectionRange(5, 5); ta.focus(); await tick();
  h.requests[0].resolve([{name:'black_hair'}]); await tick();
  assert.equal(key(ta, 'Enter').defaultPrevented, true);
  assert.equal(ta.value, '(black_hair:1.2)'); assert.equal(ta.getAttribute('aria-expanded'), 'false');
  ta.dispatchEvent(new FakeEvent('select')); await tick(); assert.equal(h.requests.length, 1);
  h.engine.destroy();
});
test('displayed suggestions cannot be inserted after CSV or caret changes', async () => {
  const h = harness(), ta = h.textarea('blue'); ta.focus(); await tick();
  h.requests[0].resolve([{name:'blue_hair'}]); await tick(); h.setCsv('other'); key(ta, 'Enter');
  assert.equal(ta.value, 'blue'); assert.equal(h.list().hidden, true);
  h.engine.destroy();
});
test('an optional insertion formatter converts Anima tags without changing default Painter names', async () => {
  const formatName = name => /^score_\d+$/.test(name.toLowerCase()) ? name.toLowerCase() : name.toLowerCase().replace(/_/g, ' ');
  const h = harness({formatName}), ta = h.textarea('blue'); ta.focus(); await tick();
  h.requests[0].resolve([{name:'Blue_Hair'}]); await tick(); key(ta, 'Enter');
  assert.equal(ta.value, 'blue hair, ');
  input(ta, 'score'); await tick(); h.requests[1].resolve([{name:'score_7'}]); await tick(); key(ta, 'Enter');
  assert.equal(ta.value, 'score_7, ');
  h.engine.destroy();
  const painter = harness(), prompt = painter.textarea('blue'); prompt.focus(); await tick();
  painter.requests[0].resolve([{name:'blue_hair'}]); await tick(); key(prompt, 'Enter');
  assert.equal(prompt.value, 'blue_hair, '); painter.engine.destroy();
});
test('formatter errors close suggestions and subsequent completions remain usable', async () => {
  let fail = true;
  const h = harness({formatName(name) {if (fail) throw new Error('format error'); return name;}});
  const ta = h.textarea('blue'); ta.focus(); await tick(); h.requests[0].resolve([{name:'blue_hair'}]); await tick(); key(ta, 'Enter');
  assert.equal(ta.value, 'blue'); assert.equal(h.errors.length, 1); assert.equal(h.list().hidden, true);
  fail = false; input(ta, 'green'); await tick(); h.requests[1].resolve([{name:'green_eyes'}]); await tick(); key(ta, 'Enter');
  assert.equal(ta.value, 'green_eyes, '); h.engine.destroy();
});
test('IME composition is left intact and searches resume only after composition ends', async () => {
  const h = harness(), ta = h.textarea(''); ta.focus();
  ta.dispatchEvent(new FakeEvent('compositionstart')); input(ta, '青');
  assert.equal(key(ta, 'Enter', {isComposing:true}).defaultPrevented, false); await tick(); assert.equal(h.requests.length, 0);
  ta.dispatchEvent(new FakeEvent('compositionend')); await tick(); assert.equal(h.requests.length, 1);
  h.engine.destroy();
});
test('dropdown belongs to its modal dialog and fits above fields near viewport edges', async () => {
  const h = harness(), dialog = h.doc.createElement('dialog'); h.doc.body.appendChild(dialog);
  const ta = h.textarea('blue', dialog); ta.rect = {left:760, top:560, bottom:590, width:300}; ta.focus(); await tick();
  h.requests[0].resolve(Array.from({length:8}, (_, index) => ({name:'blue_'+index}))); await tick();
  const list = h.list(); assert.equal(list.parentElement, dialog);
  assert.equal(list.children[0].scrolledAfterPosition, true);
  assert.ok(parseFloat(list.style.left) + parseFloat(list.style.width) <= h.win.innerWidth - 8);
  assert.ok(parseFloat(list.style.top) < ta.rect.top); assert.ok(parseFloat(list.style.maxHeight) <= 320);
  dialog.dispatchEvent(new FakeEvent('close')); assert.equal(list.hidden, true);
  h.engine.destroy();
});
test('Escape dismisses suggestions before a dialog closes and pointer selection keeps focus', async () => {
  const h = harness(), ta = h.textarea('blue'); ta.focus(); await tick();
  h.requests[0].resolve([{name:'blue_hair'}]); await tick();
  const event = key(ta, 'Escape'); assert.equal(event.defaultPrevented, true); assert.equal(event.stopped, true);
  input(ta, 'blu'); await tick(); h.requests[1].resolve([{name:'blue_hair'}]); await tick();
  const list = h.list(); list.dispatchEvent(new FakeEvent('pointerdown', {target:list.children[0],button:0}));
  assert.equal(ta.value, 'blue_hair, '); assert.equal(h.doc.activeElement, ta);
  h.engine.destroy();
});
