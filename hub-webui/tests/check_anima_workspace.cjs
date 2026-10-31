const assert = require('node:assert/strict');
const {test} = require('node:test');
const Workspace = require('../static/anima_workspace.js');

const imageUrl = id => `/api/anima-studio/images/${encodeURIComponent(id)}`;
const piece = values => ({id:'hero',kind:'character',name:'Heroína',revision:7,...values});

test('inventory isolates private projects while keeping shared elements across a 300-piece catalogue', () => {
  const pieces = Object.freeze(Array.from({length:300}, (_,index)=>Object.freeze({
    id:`piece-${index}`, name:`Diseño ${index}`, kind:index%2?'character':'item',
    project_id:index%3===0?'another-game':index%3===1?'game':null,
    prompt:'Diseño de prueba',tags:Object.freeze([]),
  })));
  const first = Workspace.inventory(pieces,{projectId:'game'});
  const more = Workspace.inventory(pieces,{projectId:'game',limit:48});
  assert.equal(first.total,200);
  assert.equal(first.items.length,24);
  assert.deepEqual(first.items.slice(0,4).map(p=>p.id),['piece-1','piece-2','piece-4','piece-5']);
  assert.equal(more.items.length,48);
  assert.deepEqual(more.items.slice(0,24),first.items);
  assert.equal(pieces.length,300);
  assert.equal(pieces[0].id,'piece-0');
  assert.equal(pieces[0].project_id,'another-game');
});

test('search folds accents and combines kind and favorite filters without changing catalogue order', () => {
  const pieces = [
    piece({id:'a',kind:'item',name:'Poción pequeña',tags:['curación'],project_id:'game'}),
    piece({id:'b',kind:'item',name:'Frasco',prompt:'Poción grande',project_id:null}),
    piece({id:'c',kind:'character',name:'Poción, la alquimista',project_id:'game'}),
    piece({id:'d',kind:'item',name:'Poción privada',project_id:'other'}),
  ];
  const before = structuredClone(pieces);
  const searched = Workspace.inventory(pieces,{projectId:'game',kind:'item',query:'  POCION  ',limit:300});
  assert.deepEqual(searched.items.map(p=>p.id),['a','b']);
  const favorites = Workspace.inventory(pieces,{projectId:'game',kind:'item',query:'pocion',
    onlyFavorites:true,favorites:new Set(['a','c','d'])});
  assert.deepEqual(favorites.items.map(p=>p.id),['a']);
  assert.deepEqual(Workspace.inventory(pieces,{projectId:'game',query:'curacion'}).items.map(p=>p.id),['a']);
  assert.deepEqual(pieces,before);
});

test('an empty inventory result remains empty and never exposes another project as fallback', () => {
  const result = Workspace.inventory([piece({project_id:'other'})],{projectId:'game'});
  assert.equal(result.total,0);
  assert.deepEqual(result.items,[]);
});

test('a historical generation snapshot does not become a variant-owned preview', () => {
  const variant = piece({master_id:'parent',generation_snapshot:{asset_id:'parent-test'},seed:'18446744073709551615'});
  const html = Workspace.preview(variant,{imageUrl});
  assert.doesNotMatch(html,/<img\b/);
  assert.match(html,/Variante por probar/);
  assert.doesNotMatch(html,/parent-test/);
  assert.equal(variant.seed,'18446744073709551615');
});

test('deleted previews and revision mismatches use an explicit placeholder instead of current art', () => {
  const current = piece({sample_asset_id:'current-preview'});
  const unavailable = Workspace.preview(current,{imageUrl,isAvailable:()=>false});
  const historical = Workspace.preview(current,{imageUrl,pinnedRevision:3});
  assert.doesNotMatch(unavailable,/<img\b/);
  assert.match(unavailable,/Sin prueba vinculada/);
  assert.doesNotMatch(historical,/<img\b/);
  assert.match(historical,/Versión guardada/);
  const sameRevision = Workspace.preview(current,{imageUrl,pinnedRevision:7});
  assert.match(sameRevision,/<img\b/);
  assert.match(sameRevision,/current-preview/);
});

test('selected cards and frozen slots keep historical revisions visible and cannot add or remove roles', () => {
  const current = piece({sample_asset_id:'latest'});
  const before = structuredClone(current);
  const card = Workspace.card(current,{imageUrl,selected:true,locked:true,pinnedRevision:3});
  assert.match(card,/aria-pressed="true"/);
  assert.match(card,/v3/);
  assert.equal((card.match(/data-add-element="hero"[^>]* disabled/g)||[]).length,2);
  assert.doesNotMatch(card,/<img\b/);
  const slot = Workspace.slot(current,{imageUrl,locked:true,pinnedRevision:3});
  assert.match(slot,/v3 · Fijado/);
  assert.match(slot,/data-remove-element="character"[^>]* disabled/);
  assert.deepEqual(current,before);
});

test('an expression card remains selectable when the caller unlocks only that frozen role', () => {
  const expression = piece({id:'smile',kind:'expression',name:'Alegre'});
  const html = Workspace.card(expression,{imageUrl,selected:false,locked:false});
  assert.match(html,/data-add-element="smile"/);
  assert.doesNotMatch(html,/\sdisabled(?:\s|>)/);
  assert.match(html,/aria-pressed="false"/);
});

test('untrusted names, identifiers and kinds cannot inject markup into cards, image attributes or slots', () => {
  const attack = piece({id:'x" onclick="alert(1)',name:'<img src=x onerror="alert(2)">',
    kind:'<svg onload="alert(3)">',sample_asset_id:'" onload="alert(4)'});
  const html = Workspace.card(attack,{imageUrl}) + Workspace.slot(attack,{imageUrl});
  assert.doesNotMatch(html,/<img src=x/);
  assert.doesNotMatch(html,/<svg onload=/);
  assert.doesNotMatch(html,/data-element-id="x" onclick=/);
  assert.match(html,/&lt;img src=x onerror=&quot;alert\(2\)&quot;&gt;/);
  assert.match(html,/%22%20onload%3D%22alert\(4\)/);
});

test('the creator surface exposes all eight element types before entering a specialized editor', () => {
  const html = Workspace.creators();
  assert.deepEqual(Workspace.TYPES.map(t=>t.kind).sort(),['character','outfit','pose','expression','background','item','personality','scene'].sort());
  for(const kind of Workspace.TYPES.map(t=>t.kind))assert.match(html,new RegExp(`data-create-element="${kind}"`));
  assert.equal((Workspace.creators(true).match(/ disabled/g)||[]).length,8);
});

test('readiness permits text or partial elements with a saved style', () => {
  assert.equal(Workspace.readiness().ready,false);
  assert.match(Workspace.readiness().message,/Elige qué imagen/);
  const missingConfig = Workspace.readiness({category:'characters',profile:'character',pieces:[piece()]});
  assert.equal(missingConfig.ready,false);
  assert.match(missingConfig.message,/modelo|ajustes|estilo/);
  assert.equal(Workspace.readiness({category:'characters',hasConfig:true,additionalPrompt:'identity'}).ready,false);
  for(const profile of ['character','background','item','card','custom']) {
    assert.equal(Workspace.readiness({category:profile,profile,hasStyle:true,additionalPrompt:'a new design'}).ready,true);
    assert.equal(Workspace.readiness({category:profile,profile,hasStyle:true,pieces:[piece({kind:'outfit'})]}).ready,true);
    assert.equal(Workspace.readiness({category:profile,profile,hasStyle:true,additionalPrompt:'   ',pieces:[]}).ready,false);
  }
  assert.equal(Workspace.readiness({category:'characters',profile:'character',hasStyle:true,pieces:[piece()]}).ready,true);
  assert.equal(Workspace.readiness({category:'backgrounds',profile:'background',hasStyle:true,pieces:[piece({kind:'scene'})]}).ready,true);
  assert.equal(Workspace.readiness({category:'items',profile:'item',hasStyle:true,pieces:[piece({kind:'item'})]}).ready,true);
  assert.equal(Workspace.readiness({category:'custom',profile:'custom',hasStyle:true,pieces:[]}).ready,false);
});

test('expression flow requires a ready master and usable expression while allowing saved historical style', () => {
  const base = {category:'characters',profile:'character',mode:'expression',source:true};
  assert.equal(Workspace.readiness({...base,expressionPrompt:'smile'}).ready,false);
  assert.equal(Workspace.readiness({...base,masterReady:true}).ready,false);
  assert.equal(Workspace.readiness({...base,masterReady:true,expressionPrompt:'  '}).ready,false);
  assert.equal(Workspace.readiness({...base,masterReady:true,pieces:[piece({kind:'expression',prompt:''})]}).ready,false);
  assert.equal(Workspace.readiness({...base,masterReady:true,pieces:[piece({kind:'expression',prompt:'happy'})]}).ready,true);
  assert.equal(Workspace.readiness({...base,masterReady:true,expressionPrompt:'smile'}).ready,true);
  assert.equal(Workspace.readiness({category:'characters',profile:'character',source:true,pieces:[]}).ready,true);
});
