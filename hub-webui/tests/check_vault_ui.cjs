const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = readFileSync(path.join(__dirname, '../static/vault.js'), 'utf8');
const nextTick = () => new Promise(resolve => setImmediate(resolve));

function setup() {
  const elements = new Map();
  const ready = [];
  const streams = [];
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const classes = new Set();
    const node = {
      hidden: false, disabled: false, value: '', innerHTML: '', textContent: '',
      dataset: {}, style: {}, listeners: {}, attrs: {}, children: [],
      classList: { add: key => classes.add(key), remove: key => classes.delete(key), contains: key => classes.has(key), toggle() {} },
      addEventListener(name, fn) { this.listeners[name] = fn; },
      setAttribute(name, value) { this.attrs[name] = value; },
      contains(target) { return this === target; },
      appendChild(child) { this.children.push(child); },
      querySelector() { return element(Symbol('child')); },
    };
    elements.set(id, node);
    return node;
  }
  const context = vm.createContext({
    console, encodeURIComponent, setTimeout: () => 1, clearTimeout() {},
    t: (key, vars) => vars ? `${key} ${JSON.stringify(vars)}` : key,
    confirm: () => true,
    document: {
      getElementById: element,
      querySelectorAll: () => [],
      createElement: tag => element(Symbol(tag)),
      addEventListener(name, fn) { if (name === 'DOMContentLoaded') ready.push(fn); },
    },
    EventSource: class {
      constructor(url) { this.url = url; this.closed = false; streams.push(this); }
      close() { this.closed = true; }
      send(data) { this.onmessage({ data: JSON.stringify(data) }); }
    },
    fetch: async () => { throw new Error('Unexpected request'); },
    loads: 0,
  });
  vm.runInContext(source, context);
  const original = { renderSidebar: context.renderSidebar, renderGrid: context.renderGrid, loadModels: context.loadModels };
  vm.runInContext('renderGrid = () => {}; renderSidebar = () => {}; loadModels = async () => { loads++; };', context);
  const run = code => vm.runInContext(code, context);
  return { context, element, streams, run, restore: name => { context[name] = original[name]; }, init: () => ready.forEach(fn => fn()) };
}

const models = [
  { hash: 'wai', name: 'waiANIMA_v10Base10.safetensors', display_name: 'WAI-ANIMA', base_model: 'Anima', model_type: 'Checkpoint', folder: 'checkpoints', subfolder: '', file_path: 'E:/Models/checkpoints\\waiANIMA_v10Base10.safetensors' },
  { hash: 'anima', name: 'anima-base-v1.0.safetensors', base_model: 'Anima', model_type: 'diffusion_model', folder: 'diffusion_models/anima', subfolder: 'anima', file_path: 'E:/Models/diffusion_models/anima/anima-base-v1.0.safetensors' },
  { hash: 'sdxl', name: 'other.safetensors', base_model: 'SDXL', model_type: 'Checkpoint', folder: 'checkpoints', subfolder: '', file_path: 'E:/Models/checkpoints/other.safetensors' },
];

test('Anima architecture includes WAI in checkpoints root and clears an old physical folder filter', () => {
  const h = setup();
  h.init();
  h.run(`allModels = ${JSON.stringify(models)}; currentCat = 'diffusion_model'; currentSubfolder = 'anima';`);
  h.element('arch-select').listeners.change({ target: { value: 'Anima' } });
  assert.equal(h.run('currentCat'), 'all');
  assert.equal(h.run('currentSubfolder'), null);
  assert.equal(h.run('JSON.stringify(filteredModels.map(m => m.hash))'), '["wai","anima"]');
  assert.equal(h.element('btn-clear-filters').hidden, false);
});

test('search matches a full Windows path, slash path and filename independently of Civitai display name', () => {
  const h = setup();
  h.run(`allModels = ${JSON.stringify(models)};`);
  for (const query of ['E:\\Models\\checkpoints\\waiANIMA_v10Base10.safetensors', 'e:/models/checkpoints/waianima_v10base10.safetensors', 'waiANIMA_v10Base10.safetensors']) {
    h.run(`currentSearch = ${JSON.stringify(query)}; applyFilters();`);
    assert.equal(h.run('JSON.stringify(filteredModels.map(m => m.hash))'), '["wai"]');
  }
});

test('folder context stays visible and clear filters restores the entire library', () => {
  const h = setup();
  h.run(`allModels = ${JSON.stringify(models)}; currentCat = 'checkpoint'; currentSubfolder = 'checkpoints'; currentArch = 'Anima'; currentSearch = 'missing'; applyFilters();`);
  assert.match(h.element('filter-context').textContent, /checkpoints/);
  h.run('clearFilters()');
  assert.equal(h.run('filteredModels.length'), 3);
  assert.equal(h.element('btn-clear-filters').hidden, true);
});

test('all registered types can appear in sidebar, including future types', () => {
  const h = setup();
  h.run(`allModels = ${JSON.stringify([...models, { model_type: 'FutureType' }])};`);
  assert.equal(h.run("modelCategories().some(c => c.id === 'diffusion_model')"), true);
  assert.equal(h.run("modelCategories().some(c => c.id === 'futuretype')"), true);
});

test('local update does not apply staged moves or start Civitai and concurrent clicks cannot submit twice', async () => {
  const h = setup();
  const requests = [];
  h.context.fetch = async url => { requests.push(url); return { ok: true, json: async () => ({ session_id: 'scan-1' }) }; };
  h.run("pendingMoves = { wai: { targetSub: 'anima' } }; updateSyncBtn();");
  assert.equal(h.element('btn-apply-moves').hidden, false);
  const first = h.run('startScan()');
  await h.run('startScan()');
  await nextTick();
  assert.deepEqual(requests, ['/api/vault/scan?deep=false']);
  for (const id of ['btn-scan', 'btn-civitai-sync', 'btn-civitai-force', 'btn-apply-moves']) assert.equal(h.element(id).disabled, true);
  assert.equal(h.run('Object.keys(pendingMoves).length'), 1);
  h.streams[0].send({ type: 'done', total: 3 });
  await first;
  assert.equal(h.streams[0].closed, true);
  assert.equal(h.element('btn-scan').disabled, false);
  assert.equal(h.context.loads, 1);
});

test('operation lock includes pending-count preflight and canceled Civitai confirmation', async () => {
  const h = setup();
  let finish;
  let count = 0;
  h.context.fetch = async () => { count++; return new Promise(resolve => { finish = resolve; }); };
  h.context.confirm = () => false;
  const sync = h.run('syncCivitai()');
  await h.run('startScan()');
  assert.equal(count, 1);
  assert.equal(h.element('btn-scan').disabled, true);
  finish({ ok: true, json: async () => ({ pending: 4 }) });
  await sync;
  assert.equal(h.element('btn-scan').disabled, false);
  assert.equal(h.streams.length, 0);
});

test('HTTP conflict preserves backend message, releases controls and never opens invalid stream', async () => {
  const h = setup();
  h.context.fetch = async () => ({ ok: false, status: 409, json: async () => ({ detail: 'Ya hay una operación en curso.' }) });
  await h.run('startScan()');
  assert.match(h.element('toast').textContent, /Ya hay una operación en curso/);
  assert.equal(h.element('btn-scan').disabled, false);
  assert.equal(h.streams.length, 0);
  assert.equal(h.element('scan-progress').classList.contains('visible'), false);
});

test('missing session identifier and SSE failures release controls', async () => {
  const h = setup();
  h.context.fetch = async () => ({ ok: true, json: async () => ({}) });
  await h.run('startScan()');
  assert.equal(h.streams.length, 0);
  assert.equal(h.element('btn-scan').disabled, false);
  h.context.fetch = async () => ({ ok: true, json: async () => ({ session_id: 's' }) });
  const scan = h.run('startScan()');
  await nextTick();
  h.streams[0].onerror();
  await scan;
  assert.equal(h.streams[0].closed, true);
  assert.equal(h.element('btn-civitai-sync').disabled, false);
});

test('partial local failures are visible while successfully indexed models refresh', async () => {
  const h = setup();
  h.context.fetch = async () => ({ ok: true, json: async () => ({ session_id: 's' }) });
  const scan = h.run('startScan()');
  await nextTick();
  h.streams[0].send({ type: 'done', total: 2, errors: 1 });
  await scan;
  assert.match(h.element('toast').textContent, /scan_partial_errors/);
  assert.equal(h.context.loads, 1);
});

test('physical folders remain distinct under one model type and counters respect architecture', () => {
  const h = setup();
  const moved = { ...models[0], hash: 'wai-copy', folder: 'loras/Anima/style', file_path: 'E:/Models/loras/Anima/style/wai.safetensors' };
  h.run(`allModels = ${JSON.stringify([...models, moved])}; currentArch = 'Anima';`);
  h.restore('renderSidebar');
  h.run('renderSidebar()');
  assert.equal(h.run("subfolderMap.checkpoint.checkpoints"), 1);
  assert.equal(h.run("subfolderMap.checkpoint['loras/Anima/style']"), 1);
  assert.match(h.element('sidebar').children[1].innerHTML, />3<\/span>/);
  h.run("currentCat = 'checkpoint'; currentSubfolder = 'loras/Anima/style'; applyFilters();");
  assert.equal(h.run('filteredModels[0].hash'), 'wai-copy');
  assert.equal(h.run('filteredModels.length'), 1);
});

test('move payload uses exact folder, rejects cross-type and dragging back cancels pending move', () => {
  const h = setup();
  h.run(`allModels = ${JSON.stringify(models)}; stageMove({hash:'wai'}, 'lora', 'loras/style');`);
  assert.equal(h.run('Object.keys(pendingMoves).length'), 0);
  h.run("stageMove({hash:'wai'}, 'checkpoint', 'loras/Anima/style');");
  assert.equal(h.run('pendingMoves.wai.targetFolder'), 'loras/Anima/style');
  h.run("stageMove({hash:'wai'}, 'checkpoint', 'checkpoints');");
  assert.equal(h.run('Object.keys(pendingMoves).length'), 0);
  assert.equal(h.element('btn-apply-moves').hidden, true);
});

test('partial movement removes only completed staging and keeps failed items for retry', async () => {
  const h = setup();
  h.run(`allModels = ${JSON.stringify(models)}; stageMove({hash:'wai'},'checkpoint','checkpoints/new'); stageMove({hash:'sdxl'},'checkpoint','checkpoints/new');`);
  let payload;
  h.context.fetch = async (url, opts) => {
    payload = JSON.parse(opts.body);
    return { ok: true, json: async () => ({ moved: 1, errors: 1, moved_hashes: ['wai'], failed_hashes: ['sdxl'], error_detail: ['sdxl locked'] }) };
  };
  assert.equal(await h.run('applyPendingMoves()'), true);
  assert.deepEqual(payload.moves[0], { hash: 'wai', target_folder: 'checkpoints/new' });
  assert.equal(h.run('JSON.stringify(Object.keys(pendingMoves))'), '["sdxl"]');
  assert.equal(h.element('btn-apply-moves').hidden, false);
});

test('copy strips Windows folders and preserves zero and negative weights', async () => {
  const h = setup();
  h.run("allModels = [{hash:'lora',model_type:'LORA',file_path:'E:\\\\Models\\\\loras\\\\style.safetensors'}];");
  const copies = [];
  h.context.navigator = { clipboard: { writeText: async value => { copies.push(value); } } };
  for (const weight of ['0', '-0.5']) {
    h.element('copy-weight').value = weight;
    await h.run("copyModelFromDetail('lora')");
  }
  assert.deepEqual(copies, ['<lora:style:0>', '<lora:style:-0.5>']);
  h.element('copy-weight').value = '';
  assert.equal(h.run('copyWeight()'), 1);
});

test('late detail response cannot overwrite a newer selection or reopen a closed panel', async () => {
  const h = setup();
  const pending = new Map();
  h.context.fetch = url => new Promise(resolve => pending.set(url, resolve));
  h.run('renderDetails = m => { renderedHash = m.hash; };');
  const first = h.run("openDetails('wai')");
  const second = h.run("openDetails('sdxl')");
  pending.get('/api/vault/models/sdxl')({ ok: true, json: async () => ({ hash: 'sdxl' }) });
  await second;
  pending.get('/api/vault/models/wai')({ ok: true, json: async () => ({ hash: 'wai' }) });
  await first;
  assert.equal(h.context.renderedHash, 'sdxl');
  const third = h.run("openDetails('anima')");
  h.run('closeDetails()');
  pending.get('/api/vault/models/anima')({ ok: true, json: async () => ({ hash: 'anima' }) });
  await third;
  assert.equal(h.context.renderedHash, 'sdxl');
});

test('saved tags immediately update search and repeated save clicks submit once', async () => {
  const h = setup();
  h.run(`allModels = ${JSON.stringify(models)}; selectedHash = 'wai'; currentSearch = 'fresh-tag';`);
  h.element('detail-tags').value = 'fresh-tag';
  h.element('detail-notes').value = 'personal note';
  let finish;
  let calls = 0;
  h.context.fetch = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const save = h.run("saveModelData('wai')");
  await h.run("saveModelData('wai')");
  assert.equal(calls, 1);
  finish({ ok: true, json: async () => ({ ok: true }) });
  await save;
  assert.equal(h.run('filteredModels[0].hash'), 'wai');
  assert.equal(h.run('allModels[0].user_notes'), 'personal note');
  assert.equal(h.element('detail-save').disabled, false);
});

test('alias paths are searchable and unavailable models have no copy or drag action', () => {
  const h = setup();
  const absent = { ...models[0], missing: true, file_paths: [models[0].file_path, 'E:/Models/archive/WAI.safetensors'] };
  h.run(`allModels = [${JSON.stringify(absent)}]; currentSearch='e:\\\\models\\\\archive\\\\wai.safetensors'; applyFilters();`);
  assert.equal(h.run('filteredModels.length'), 1);
  assert.equal(h.run('getCopyString(allModels[0],1)'), null);
  h.restore('renderGrid');
  h.run('renderGrid()');
  const card = h.element('model-grid').children[0];
  assert.equal(card.draggable, false);
  assert.match(card.innerHTML, /vault.file_missing/);
  let blocked = false;
  card.listeners.dragstart({ preventDefault() { blocked = true; } });
  assert.equal(blocked, true);
  h.run("stageMove({hash:'wai'},'checkpoint','checkpoints/new');");
  assert.equal(h.run('Object.keys(pendingMoves).length'), 0);
});

test('late library responses cannot replace a newer inventory and reload failure preserves visible models', async () => {
  const h = setup();
  h.restore('loadModels');
  const pending = [];
  h.context.fetch = () => new Promise(resolve => pending.push(resolve));
  const first = h.run('loadModels()');
  const second = h.run('loadModels()');
  pending[1]({ ok: true, json: async () => ({ models: [models[1]] }) });
  await second;
  pending[0]({ ok: true, json: async () => ({ models: [models[0]] }) });
  await first;
  assert.equal(h.run('allModels[0].hash'), 'anima');
  h.context.fetch = async () => ({ ok: false });
  await h.run('loadModels()');
  assert.equal(h.run('allModels[0].hash'), 'anima');
  assert.match(h.element('toast').textContent, /vault.load_error/);
});
