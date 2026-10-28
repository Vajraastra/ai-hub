const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = readFileSync(path.join(__dirname, '../static/anima_studio.js'), 'utf8');
function harness() {
  const nodes = new Map();
  const makeNode = id => {
    const classes = new Set(), nested = new Map();
    let html = '';
    const node = {
      value: '', checked: false, open: false, hidden: false, scrollTop: 0, options: [], children: [], attrs: {},
      classList: { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x), toggle(x, force) { if (force ?? !classes.has(x)) classes.add(x); else classes.delete(x); } },
      add(option) { this.options.push(option); if (option.selected) this.value = option.value; },
      append(child) { this.children.push(child); child.parentElement = this; },
      remove() { this.parentElement.children = this.parentElement.children.filter(child => child !== this); },
      replaceChildren() { this.children = []; },
      addEventListener() {}, setAttribute(key, value) { this.attrs[key] = value; },
      showModal() { this.open = true; }, close() { this.open = false; this.onclose?.(); }, focus() {},
      querySelector(selector) { if (!nested.has(selector)) nested.set(selector, makeNode(selector)); return nested.get(selector); },
      get innerHTML() { return html; },
      set innerHTML(value) {
        html = value;
        if (id === 'select' || id.endsWith('-select')) {
          this.options = [...value.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map(match => ({ value: match[1], text: match[2] }));
          this.value = this.options[0]?.value || '';
        }
      },
    };
    return node;
  };
  const element = id => { if (!nodes.has(id)) nodes.set(id, makeNode(id)); return nodes.get(id); };
  const context = vm.createContext({
    console, document: { getElementById: element, createElement: makeNode },
    localStorage: { getItem() { return null; } },
    Option: function (text, value) { this.text = text; this.value = value; },
  });
  const exposed = source.replace('  init();', '  globalThis.studio = {state,catalogPicker,catalogFolder,catalogFolders,selectCatalogFolder,matchingCatalogEntries,catalogThumbnail,catalogPreview,renderCatalog,renderCatalogPicker,openCatalogPicker,chooseCatalogFile,addLora,loadConfig,calibrationConfig};');
  vm.runInContext(exposed, context);
  const studio = context.studio;
  studio.state.catalog = {
    models: [{ file: 'checkpoints::7d9.safetensors', relative_path: 'checkpoints/7d9.safetensors', name: '7d9', display_name: 'WAI Anima · v1.0', tags: ['ilustración'], thumbnail_url: '/api/anima-studio/catalog/preview?id=wai' }],
    loras: [
      { file: 'styles/af32.safetensors', name: 'af32', display_name: 'Acuarela suave', tags: ['lápiz', 'pintura'], thumbnail_url: '/api/anima-studio/catalog/preview?id=watercolor' },
      { file: 'outfits/8bc.safetensors', name: '8bc', display_name: 'Uniforme camarera', tags: ['outfit'], thumbnail_url: null },
    ],
    text_encoders: [{ file: 'qwen.safetensors' }], vaes: [{ file: 'vae.safetensors' }],
  };
  return { studio, element };
}

test('search finds human names, cryptic filenames, tags and accents', () => {
  const { studio } = harness();
  for (const query of ['acuarela', 'af32', 'lapiz', 'PINTURA suave']) {
    assert.equal(studio.matchingCatalogEntries('loras', query).length, 1);
    assert.equal(studio.matchingCatalogEntries('loras', query)[0].file, 'styles/af32.safetensors');
  }
  assert.equal(studio.matchingCatalogEntries('models', 'WAI ilustracion')[0].file, 'checkpoints::7d9.safetensors');
  assert.equal(studio.matchingCatalogEntries('loras', 'camarera', true).length, 0);
  assert.equal(studio.matchingCatalogEntries('loras', 'camarera').length, 1);
});

test('previews only use local asset endpoints and unavailable previews stay explicit', () => {
  const { studio } = harness();
  for (const url of ['https://outside.test/preview.jpg', '//outside.test/img.jpg', 'javascript:alert(1)', '/api/vault-evil/file']) {
    assert.equal(studio.catalogThumbnail({ thumbnail_url: url }), '');
  }
  assert.equal(studio.catalogThumbnail({ thumbnail_url: '/api/vault/models/abc/thumbnail' }), '/api/vault/models/abc/thumbnail');
  assert.match(studio.catalogPreview({ display_name: '<script>', thumbnail_url: '/api/anima-studio/catalog/preview?id=1' }), /&lt;script&gt;/);
  assert.match(studio.catalogPreview({}), /Sin miniatura/);
});

test('restoring a style preserves opaque model IDs, separate LoRA weights and exact seeds', () => {
  const { studio, element } = harness();
  studio.loadConfig({ model: 'checkpoints::7d9.safetensors', text_encoder: 'qwen.safetensors', vae: 'vae.safetensors', seed: '18446744073709551615', loras: [{ file: 'styles/af32.safetensors', strength: .65 }, { file: 'outfits/8bc.safetensors', strength: -.2 }] });
  for (const [id, value] of Object.entries({ steps: 30, cfg: 4, width: 1024, height: 1024, sampler: 'er_sde', scheduler: 'simple' })) element(id).value = String(value);
  const config = studio.calibrationConfig();
  assert.equal(config.model, 'checkpoints::7d9.safetensors');
  assert.equal(config.seed, '18446744073709551615');
  assert.equal(JSON.stringify(config.loras), '[{"file":"styles/af32.safetensors","strength":0.65},{"file":"outfits/8bc.safetensors","strength":-0.2}]');
  assert.match(element('choose-model').innerHTML, /WAI Anima/);
  assert.match(element('choose-model').innerHTML, /checkpoints\/7d9/);
});

test('gallery adds and changes a LoRA without changing its weight or allowing duplicates', () => {
  const { studio, element } = harness();
  studio.openCatalogPicker('loras');
  studio.selectCatalogFolder('styles');
  studio.chooseCatalogFile('styles/af32.safetensors');
  const row = element('lora-list').children[0];
  row.querySelector('input').value = '.8';
  studio.openCatalogPicker('loras');
  studio.chooseCatalogFile('styles/af32.safetensors');
  assert.equal(element('lora-list').children.length, 1);
  studio.openCatalogPicker('loras', row);
  studio.selectCatalogFolder('outfits');
  studio.chooseCatalogFile('outfits/8bc.safetensors');
  assert.equal(row.querySelector('select').value, 'outfits/8bc.safetensors');
  assert.equal(row.querySelector('input').value, '.8');
  assert.equal(element('catalog-dialog').open, false);
});

test('catalog refresh keeps a saved unavailable model instead of silently substituting another', () => {
  const { studio, element } = harness();
  studio.loadConfig({ model: 'checkpoints::removed.safetensors', loras: [] });
  studio.renderCatalog();
  assert.equal(element('model-select').value, 'checkpoints::removed.safetensors');
  assert.equal(element('choose-model').classList.contains('unavailable'), true);
  assert.match(element('choose-model').innerHTML, /Archivo no disponible/);
});

test('model selection passes the opaque identity intact and displays the selected state', () => {
  const { studio, element } = harness();
  studio.renderCatalog();
  studio.openCatalogPicker('models');
  assert.match(element('catalog-grid').innerHTML, /aria-pressed="true"/);
  studio.chooseCatalogFile('checkpoints::7d9.safetensors');
  assert.equal(element('model-select').value, 'checkpoints::7d9.safetensors');
  assert.match(element('choose-model').innerHTML, /WAI Anima/);
});

test('folder identities normalize Windows paths without changing nested or root categories', () => {
  const { studio } = harness();
  const cases = [
    [{ backend_file: 'Anima\\style\\watercolor\\a.safetensors', file: 'other/b.safetensors' }, 'style/watercolor'],
    [{ file: 'aNiMa/characters/shared/a.safetensors' }, 'characters/shared'],
    [{ file: 'Anima/style/shared/a.safetensors' }, 'style/shared'],
    [{ relative_path: 'loras\\Anima\\clothes\\a.safetensors' }, 'clothes'],
    [{ relative_path: 'loras/Anima/a.safetensors' }, ''],
    [{ file: 'Anima/a.safetensors' }, ''],
    [{ file: 'a.safetensors' }, ''],
    [{ file: 'style/a.safetensors' }, 'style'],
    [{ file: 'loras/style/a.safetensors' }, 'loras/style'],
  ];
  for (const [entry, expected] of cases) assert.equal(studio.catalogFolder(entry), expected);
});

test('folder tabs count every entry once and retain distinct nested paths', () => {
  const { studio } = harness();
  studio.state.catalog.loras = [
    { file: 'Anima/style/a.safetensors' },
    { file: 'Anima\\style\\b.safetensors' },
    { file: 'Anima/characters/shared/a.safetensors' },
    { file: 'Anima/style/shared/b.safetensors' },
    { file: 'Anima/root.safetensors' },
    { file: 'another-root.safetensors' },
  ];
  const folders = JSON.parse(JSON.stringify(studio.catalogFolders()));
  assert.deepEqual(folders[0], { value: '*', label: 'Todos', count: 6 });
  assert.equal(folders.slice(1).reduce((total, folder) => total + folder.count, 0), 6);
  assert.deepEqual(folders.find(folder => folder.value === ''), { value: '', label: 'Sin carpeta', count: 2 });
  assert.deepEqual(folders.filter(folder => folder.value !== '*' && folder.value !== '').map(folder => folder.value), ['characters/shared', 'style', 'style/shared']);
  assert.equal(folders.find(folder => folder.value === 'style').count, 2);
  assert.equal(folders.find(folder => folder.value === 'characters/shared').label, 'characters/shared');
});

test('folder filtering combines search and thumbnails while leaving model identities unchanged', () => {
  const { studio } = harness();
  studio.state.catalog.loras.push(
    { file: 'styles/missing.safetensors', display_name: 'Acuarela sin imagen', tags: ['pintura'] },
    { file: 'Anima/root.safetensors', display_name: 'Acuarela raíz', tags: ['pintura'], thumbnail_url: '/api/anima-studio/catalog/preview?id=root' },
    { file: 'Anima/characters/hero.safetensors', display_name: 'Acuarela personaje', tags: ['pintura'], thumbnail_url: '/api/anima-studio/catalog/preview?id=hero' },
  );
  assert.equal(studio.matchingCatalogEntries('loras', 'acuarela pintura', true, '*').length, 3);
  assert.equal(studio.matchingCatalogEntries('loras', 'acuarela pintura', false, 'styles').length, 2);
  assert.equal(studio.matchingCatalogEntries('loras', 'acuarela pintura', true, 'styles')[0].file, 'styles/af32.safetensors');
  assert.equal(studio.matchingCatalogEntries('loras', 'acuarela', true, '')[0].file, 'Anima/root.safetensors');
  assert.equal(studio.matchingCatalogEntries('loras', 'camarera', false, 'styles').length, 0);
  assert.equal(studio.matchingCatalogEntries('models', 'WAI', false, 'styles')[0].file, 'checkpoints::7d9.safetensors');
});

test('folder navigation escapes labels and remains available when search has no results', () => {
  const { studio, element } = harness();
  studio.state.catalog.loras.push({ file: 'Anima/style & "archive"/a.safetensors', display_name: 'Archivo' });
  studio.openCatalogPicker('loras');
  const navigation = element('catalog-folders');
  assert.equal(navigation.hidden || navigation.classList.contains('hidden'), false);
  assert.match(navigation.innerHTML, /role="tab"/);
  assert.match(navigation.innerHTML, /style &amp; &quot;archive&quot;/);
  assert.doesNotMatch(navigation.innerHTML, /style & "archive"/);
  studio.selectCatalogFolder('styles');
  const tabsBeforeSearch = navigation.innerHTML;
  element('catalog-search').value = 'ningún archivo coincide';
  studio.renderCatalogPicker();
  assert.match(element('catalog-grid').innerHTML, /No hay coincidencias/);
  assert.equal(navigation.innerHTML, tabsBeforeSearch);
  assert.equal(studio.catalogPicker.folder, 'styles');
});

test('switching folders resets pagination and reopening starts at all LoRAs', () => {
  const { studio, element } = harness();
  studio.state.catalog.loras = Array.from({ length: 70 }, (_, index) => ({ file: `Anima/style/${index}.safetensors` }));
  studio.state.catalog.loras.push({ file: 'Anima/characters/hero.safetensors' });
  studio.openCatalogPicker('loras');
  studio.catalogPicker.limit = 96;
  studio.renderCatalogPicker();
  assert.equal((element('catalog-grid').innerHTML.match(/data-catalog-file=/g) || []).length, 71);
  studio.selectCatalogFolder('characters');
  assert.equal(studio.catalogPicker.limit, 48);
  assert.equal(studio.catalogPicker.folder, 'characters');
  assert.equal((element('catalog-grid').innerHTML.match(/data-catalog-file=/g) || []).length, 1);
  studio.selectCatalogFolder('missing');
  assert.equal(studio.catalogPicker.folder, 'characters');
  studio.selectCatalogFolder('style');
  assert.equal((element('catalog-grid').innerHTML.match(/data-catalog-file=/g) || []).length, 48);
  assert.equal(element('catalog-more').classList.contains('hidden'), false);
  studio.openCatalogPicker('loras');
  assert.equal(studio.catalogPicker.folder, '*');
  assert.equal(studio.catalogPicker.limit, 48);
});

test('refreshing a removed folder falls back to all and model picker hides folder tabs', () => {
  const { studio, element } = harness();
  studio.openCatalogPicker('loras');
  studio.selectCatalogFolder('styles');
  studio.state.catalog.loras = studio.state.catalog.loras.filter(item => item.file.startsWith('outfits/'));
  studio.renderCatalog();
  assert.equal(studio.catalogPicker.folder, '*');
  assert.match(element('catalog-grid').innerHTML, /Uniforme camarera/);
  assert.doesNotMatch(element('catalog-folders').innerHTML, /styles/);
  studio.openCatalogPicker('models');
  const navigation = element('catalog-folders');
  assert.equal(navigation.hidden || navigation.classList.contains('hidden'), true);
  assert.match(element('catalog-grid').innerHTML, /WAI Anima/);
});
