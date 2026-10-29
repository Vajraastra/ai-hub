const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const modulePath = path.join(__dirname, '../static/anima_production.js');
const {profile, preferredFormat, matchesCategory} = require(modulePath);

test('English and Spanish project categories choose the same tools without mutating IDs', () => {
  for (const [id, aliases] of Object.entries({
    character: ['characters', 'Personajes'], background: ['backgrounds', 'FONDOS'],
    item: ['items', 'objetos'], card: ['cards', 'cartas'],
  })) for (const category of aliases) assert.equal(profile(category).id, id);
  const category = {id: 'custom-environment-id', name: 'Escenários'};
  assert.equal(profile(category).id, 'background');
  assert.deepEqual(category, {id: 'custom-environment-id', name: 'Escenários'});
  assert.equal(profile({id: 'props', label: 'Decoración'}).id, 'item');
});

test('backgrounds and objects omit character tools and facial mode', () => {
  for (const [category,kind] of [['fondos','background'], ['objects','item']]) {
    const result = profile(category);
    assert.deepEqual(result.kinds, [kind,'scene']);
    assert.equal(result.expression, false);
  }
  assert.equal(profile('characters').expression, true);
  assert.ok(profile('cards').kinds.includes('character'));
  assert.ok(profile('cards').kinds.includes('scene'));
});

test('custom categories remain usable and profiles cannot be changed by callers', () => {
  assert.equal(profile('iconos').label, 'iconos');
  assert.equal(profile({id:'fx', name:'Efectos mágicos'}).label, 'Efectos mágicos');
  assert.equal(profile('iconos').id, 'custom');
  assert.equal(profile('iconos').expression, false);
  profile('characters').kinds.length = 0;
  assert.equal(profile('personajes').kinds.length, 6);
  assert.equal(profile(null).id, 'custom');
  assert.equal(profile('giftcards').id, 'custom');
});

test('format suggestion uses meaning then orientation while preserving the complete catalog', () => {
  const formats = [
    {id:'square', name:'Cuadrado', width:1024, height:1024},
    {id:'portrait', name:'Retrato', width:1024, height:1536},
    {id:'landscape', name:'Panorámica', width:1536, height:864},
  ];
  const before = JSON.stringify(formats);
  assert.equal(preferredFormat('backgrounds', formats), 'landscape');
  assert.equal(preferredFormat(profile('personajes'), formats), 'portrait');
  assert.equal(preferredFormat('cartas', formats), 'portrait');
  assert.equal(preferredFormat('items', formats), 'square');
  assert.equal(preferredFormat('iconos', formats), null);
  assert.equal(preferredFormat('fondos', []), null);
  assert.equal(preferredFormat('fondos', [{id:'wide',width:1536,height:864},
    {id:'background',name:'Fondo especial',width:1024,height:1024}]), 'background');
  assert.equal(JSON.stringify(formats), before);
});

test('category matching accepts known aliases and keeps unknown categories distinct', () => {
  assert.equal(matchesCategory('characters', 'personajes'), true);
  assert.equal(matchesCategory('characters', 'fondos'), false);
  assert.equal(matchesCategory({id:'icons',name:'Íconos'}, 'icons'), true);
  assert.equal(matchesCategory('icons', 'efectos'), false);
});

test('module also publishes the browser API without CommonJS', () => {
  const context = vm.createContext({});
  vm.runInContext(readFileSync(modulePath, 'utf8'), context);
  assert.equal(context.AnimaProduction.profile('Fondos').id, 'background');
});
