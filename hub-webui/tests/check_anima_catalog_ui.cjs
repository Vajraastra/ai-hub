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
      value: '', checked: false, open: false, options: [], children: [], attrs: {},
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
  const exposed = source.replace('  init();', '  globalThis.studio = {state,catalogPicker,matchingCatalogEntries,catalogThumbnail,catalogPreview,renderCatalog,openCatalogPicker,chooseCatalogFile,addLora,loadConfig,calibrationConfig};');
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
  studio.chooseCatalogFile('styles/af32.safetensors');
  const row = element('lora-list').children[0];
  row.querySelector('input').value = '.8';
  studio.openCatalogPicker('loras');
  studio.chooseCatalogFile('styles/af32.safetensors');
  assert.equal(element('lora-list').children.length, 1);
  studio.openCatalogPicker('loras', row);
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
