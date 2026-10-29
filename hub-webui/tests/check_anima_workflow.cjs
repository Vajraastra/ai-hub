const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const AnimaProduction = require('../static/anima_production.js');
const AnimaPieces = require('../static/anima_pieces.js');
const AnimaWorkspace = require('../static/anima_workspace.js');
const source = readFileSync(path.join(__dirname, '../static/anima_studio.js'), 'utf8');

function harness() {
  const nodes = new Map(), requests = [], confirmations = [];
  const control = {confirm: true, blocked: false, errors:[], now:100000};
  const selectIds = new Set(['cal-style','prod-style','piece-kind','piece-filter','piece-master','piece-lora','piece-test-style','piece-test-format','sampler','scheduler']);
  function makeNode(id, isSelect = selectIds.has(id) || id.endsWith('-select')) {
    const classes = new Set(), nested = new Map();
    let html = '', value = '';
    const node = {
      id, dataset:{}, attrs:{}, options:[], children:[], checked:false, disabled:false, textContent:'', open:false,
      get value() { return value; },
      set value(next) { value = String(next ?? ''); if (isSelect && !this.options.some(o=>o.value === value)) value = ''; },
      classList: {add:c=>classes.add(c), remove:c=>classes.delete(c), contains:c=>classes.has(c),
        toggle(c, force) { const add = force ?? !classes.has(c); add ? classes.add(c) : classes.delete(c); return add; }},
      setAttribute(key,value) { this.attrs[key]=String(value); },
      getAttribute(key) { return this.attrs[key]??this[key]??null; },
      removeAttribute(key) { delete this.attrs[key]; delete this[key]; },
      addEventListener() {}, focus() {},
      getContext() { return {clearRect() {}, drawImage() {}}; },
      close() { this.open = false; }, showModal() { this.open = true; },
      reset() {},
      add(option) { this.options.push(option); if(option.selected || this.options.length===1) value = String(option.value); },
      replaceChildren(...children) { this.children = children; html = ''; },
      append(child) { this.children.push(child); },
      querySelectorAll(selector) { return selector === 'select' ? this.children.filter(c=>c.isSelect) : []; },
      querySelector(selector) {
        const kind = selector.match(/^\[data-piece-kind="([^"]+)"\]$/)?.[1];
        if(kind) return this.children.find(c=>c.dataset.pieceKind === kind) || null;
        if(!nested.has(selector)) nested.set(selector,makeNode(selector));
        return nested.get(selector);
      },
      isSelect,
      get innerHTML() { return html; },
      set innerHTML(next) {
        html = next; this.children = [];
        if(isSelect) {
          this.options = [...next.matchAll(/<option(?: value="([^"]*)")?[^>]*>([^<]*)<\/option>/g)].map(m=>({value:m[1]??m[2], text:m[2]}));
          value = String(this.options[0]?.value ?? '');
        }
        if(id === 'piece-pickers') for(const match of next.matchAll(/<select data-piece-kind="([^"]+)">([\s\S]*?)<\/select>/g)) {
          const child = makeNode(`piece-${match[1]}`,true);
          child.dataset.pieceKind = match[1]; child.innerHTML = match[2]; this.children.push(child);
        }
      },
    };
    return node;
  }
  function element(id) { if(!nodes.has(id)) nodes.set(id,makeNode(id)); return nodes.get(id); }
  const radios = ['txt2img','expression'].map(value=>({value,checked:value==='txt2img'}));
  const database = {
    projects:[{id:'project',name:'Prueba',categories:['characters','backgrounds','items'],formats:[
      {id:'portrait',name:'Retrato',width:1024,height:1536},{id:'landscape',name:'Fondo',width:1536,height:864},
    ]}],
    styles:[{id:'style',project_id:'project',name:'Estilo',revision:5,config:{}}],
    pieces:[{id:'hero',project_id:'project',kind:'character',name:'Heroína',revision:3},
      {id:'forest',project_id:'project',kind:'scene',name:'Bosque',revision:2}],
    recipes:[], generations:[{id:'image',project_id:'project',name:'Muestra',status:'pending',purpose:'calibration'}],
    catalog:{models:[],loras:[],text_encoders:[],vaes:[]},
  };
  const context = vm.createContext({
    console:{...console,error(...args) { control.errors.push(args); }}, AnimaProduction, AnimaPieces, AnimaWorkspace, WebSocket:{OPEN:1},
    Date:class extends Date { static now() { return control.now; } },
    document:{getElementById:element,createElement:makeNode,
      querySelectorAll(selector) { return selector === '[name="mode"]' ? radios : []; }},
    window:{confirm(message) { confirmations.push(message); return control.confirm; }},
    localStorage:{getItem() { return null; }, setItem() {}},
    Option:function(text,value) { this.text=text; this.value=value; },
    async fetch(url, options={}) {
      requests.push({url,method:options.method || 'GET',body:options.body});
      if(options.method === 'DELETE') {
        if(control.blocked) return {ok:false,status:409,async text() { return JSON.stringify({detail:'Muestra usada por otro recurso.'}); }};
        const [,kind,id] = url.match(/\/api\/anima-studio\/([^/]+)\/([^/]+)$/);
        database[kind] = database[kind].filter(item=>item.id !== decodeURIComponent(id));
      }
      const result = url.endsWith('/bootstrap') ? database : url.includes('/generations?') ? database.generations : {deleted:true};
      return {ok:true,async text() { return JSON.stringify(result); }};
    },
  });
  vm.runInContext(source.replace('  init();', '  globalThis.studio = {state,session,renderAll,renderGalleries,renderStyles,renderProductionMenu,chooseProduction,productionPayload,selectedPieces,loadRecipe,saveRecipe,deleteImage,deleteResource,reload,refreshGenerations,resetWorkspace,cleanupStorage,pollJob,renderVisualWorkspace,createVisualElement,addVisualElement,removeVisualElement,visualReadiness,updateSessionView,openPiece,savePiece,testPiece,choosePieceMaster,productionPieceChanged,restoreComposition,releaseComposition,selectMaster,capturePieceSample,storageStatus:()=>({pending:storagePending,busy:storageCleanupBusy,retryAt:storageRetryAt})};'), context);
  const studio = context.studio;
  Object.assign(studio.state,database,{projectId:'project'});
  element('piece-kind').innerHTML=['character','personality','outfit','pose','expression','background','item','scene'].map(kind=>`<option value="${kind}">${kind}</option>`).join('');
  element('prod-seed').value = '42';
  studio.renderAll();
  return {studio,element,database,requests,control,confirmations,context};
}

const response = json => ({ok:true, async text() { return typeof json==='string'?json:JSON.stringify(json); }});

test('visual workshop exposes creation and inventory before a composition target',()=>{
  const {studio,element}=harness();
  assert.match(element('element-type-cards').innerHTML,/Crear personaje/);
  assert.match(element('inventory-grid').innerHTML,/Heroína/);
  assert.equal(studio.visualReadiness().ready,false);
  assert.equal(element('generate-production').disabled,true);
});

test('visual selection uses one piece per role and filters never rewrite the composition',()=>{
  const {studio,element}=harness();
  studio.state.pieces.push({id:'dress',kind:'outfit',name:'Vestido',revision:2,project_id:'project',prompt:'green dress'});
  studio.state.pieces.push({id:'background',kind:'background',name:'Paisaje',revision:1,project_id:'project',prompt:'forest'});
  studio.state.pieces.find(p=>p.id==='hero').seed='18446744073709551615';
  studio.addVisualElement('hero');element('prod-style').value='style';studio.addVisualElement('dress');
  studio.state.recipePieceRevisions.hero=3;
  const before=JSON.stringify(studio.productionPayload());
  element('inventory-search').value='no matches';studio.renderVisualWorkspace();
  assert.equal(JSON.stringify(studio.productionPayload()),before);
  assert.match(element('composition-slots').innerHTML,/Heroína[\s\S]*Vestido/);
  assert.equal(element('prod-seed').value,'18446744073709551615');
  assert.throws(()=>studio.addVisualElement('background'),/otro tipo/);
  assert.deepEqual(Array.from(studio.selectedPieces()),['hero','dress']);
  studio.removeVisualElement('outfit');
  assert.deepEqual(Array.from(studio.selectedPieces()),['hero']);
});

test('creation can be opened and cancelled without changing selected composition',()=>{
  const {studio,element}=harness();studio.chooseProduction('characters');element('prod-style').value='style';
  studio.addVisualElement('hero');const before=JSON.stringify(studio.productionPayload());
  studio.createVisualElement('outfit');
  assert.equal(element('piece-kind').value,'outfit');
  assert.equal(JSON.stringify(studio.productionPayload()),before);
});
async function until(predicate) {
  for(let step=0;step<30;step++) { if(predicate())return; await Promise.resolve(); }
  assert.ok(predicate(),'Expected request was never started.');
}

test('a variant editor inherits the exact master revision, seed and character LoRA without claiming its preview', async()=>{
  const {studio,element,context,database,requests}=harness();
  const master={id:'master',revision:7,name:'Identidad',kind:'character',project_id:'project',prompt:'blue eyes',negative:'glasses',seed:'18446744073709551615',sample_asset_id:'preview',loras:[{file:'hero.safetensors',strength:.8,trigger:'hero'}]};
  studio.state.pieces.push(master);studio.openPiece(master,true);
  assert.equal(element('piece-kind').value,'character');assert.equal(element('piece-kind').disabled,true);
  assert.equal(element('piece-seed').value,'18446744073709551615');assert.equal(element('piece-sample-id').value,'');
  element('piece-prompt').value='green eyes';
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/pieces')&&options.method==='POST')return response({...JSON.parse(options.body),id:'variant',revision:1});
    return response(database);
  };
  await studio.savePiece({preventDefault(){}});
  const body=JSON.parse(requests.find(r=>r.url.endsWith('/pieces')).body);
  assert.equal(body.master_id,'master');assert.equal(body.master_revision,7);assert.equal(body.seed,'18446744073709551615');
  assert.equal(body.prompt,'green eyes');assert.equal(body.loras[0].trigger,'hero');assert.equal(body.sample_asset_id,null);
  assert.equal(master.prompt,'blue eyes');assert.equal(master.sample_asset_id,'preview');
});

test('a recovered composition pins historical settings and only replaces the expression until explicitly released',()=>{
  const {studio,element}=harness();
  const record={id:'source',category:'characters',seed:'18446744073709551615',config:{width:1024,height:1536,steps:39,cfg:4,loras:[{file:'style-lora',strength:.5},{file:'old-character',strength:1}]},
    snapshot:{style:{id:'style',revision:2,config:{cfg:2,loras:[{file:'style-lora',strength:.5}]}},pieces:[{id:'hero',kind:'character',name:'Heroína',revision:1}]},
    composition:{additional_prompt:'full body',additional_negative:'cropped'}};
  studio.restoreComposition(record,'expression');
  assert.equal(element('prod-seed').value,record.seed);assert.equal(element('prod-seed').disabled,true);
  assert.equal(element('prod-style').disabled,true);
  assert.equal(element('piece-pickers').querySelector('[data-piece-kind="character"]').disabled,true);
  assert.equal(element('piece-pickers').querySelector('[data-piece-kind="expression"]').disabled,false);
  element('prod-prompt').value='raised eyebrows, surprised';
  const payload=studio.productionPayload();
  assert.equal(payload.source_asset_id,'source');assert.equal(payload.expression_prompt,'raised eyebrows, surprised');
  assert.equal(payload.config.seed,record.seed);assert.equal(payload.config.steps,39);assert.equal(payload.style_revision,2);assert.equal(payload.piece_revisions.hero,1);
  studio.releaseComposition(false);
  assert.equal(studio.productionPayload().source_asset_id,undefined);assert.equal(element('prod-seed').disabled,false);
  assert.equal(studio.productionPayload().piece_revisions.hero,1);
  assert.equal(studio.productionPayload().config.cfg,4);assert.equal(studio.productionPayload().config.steps,39);
  assert.deepEqual(JSON.parse(JSON.stringify(studio.productionPayload().config.loras)),[{file:'style-lora',strength:.5}]);
});

test('selecting a character proposes its exact seed and selecting an unrelated resource releases the old composition',()=>{
  const {studio,element}=harness();
  studio.chooseProduction('characters');element('prod-style').value='style';
  studio.state.pieces[0].seed='18446744073709551615';
  const picker=element('piece-pickers').querySelector('[data-piece-kind="character"]');picker.value='hero';studio.productionPieceChanged(picker);
  assert.equal(studio.productionPayload().config.seed,'18446744073709551615');
  studio.state.sourceAssetId='old';studio.chooseProduction('backgrounds');
  assert.equal(studio.state.sourceAssetId,null);assert.equal(element('prod-style').disabled,false);
});

test('a piece test can replay a deleted style from its historical snapshot and displays its actual seed',async()=>{
  const {studio,element,context,requests}=harness();
  studio.state.styles=[];
  const piece={id:'outfit',revision:2,kind:'outfit',name:'Uniforme',project_id:'project',prompt:'blue uniform',seed:'18446744073709551615',
    generation_snapshot:{config:{width:1024,height:1536},snapshot:{style:{id:'deleted-style',name:'Acabado anterior',revision:4,config:{seed:'12'}}}}};
  studio.state.pieces.push(piece);studio.openPiece(piece,true);
  assert.equal(element('piece-test-style').value,'deleted-style');
  assert.match(element('piece-test-style').innerHTML,/Acabado anterior · v4 · del maestro/);
  assert.equal(element('piece-reuse-settings').checked,true);assert.equal(element('piece-reuse-settings').disabled,false);
  Object.assign(studio.session,{active:true,status:'ready',socket:{readyState:1}});
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/generate'))return response({job_id:'test-job'});
    if(url.includes('/jobs/'))return response({status:'done',result:{id:'result'}});
    return response([]);
  };
  await studio.testPiece(piece,'deleted-style','portrait',true);
  const payload=JSON.parse(requests.find(request=>request.url.endsWith('/generate')).body);
  assert.equal(payload.style_id,'deleted-style');assert.equal(payload.style_revision,4);assert.equal(payload.piece_test.reuse_master_settings,true);
  assert.equal(payload.config.seed,'18446744073709551615');assert.equal(element('prod-seed').value,'42');
  assert.deepEqual(payload.piece_ids,['outfit']);
});

test('saving and testing a new element preserves an existing frozen composition and keeps its result separate',async()=>{
  const {studio,element,database,context,requests}=harness();
  database.styles.push({id:'test-style',name:'Acabado para objetos',project_id:'project',revision:6,config:{seed:'17'}});
  studio.state.styles=database.styles;
  database.pieces.push({id:'dress',name:'Vestido actual',kind:'outfit',project_id:'project',revision:8,prompt:'red dress'});
  const sourceRecord={id:'frozen-source',project_id:'project',category:'characters',purpose:'production',mode:'txt2img',status:'accepted',
    seed:'18446744073709551615',config:{width:1024,height:1536,steps:39,cfg:4,seed:'18446744073709551615'},
    snapshot:{style:{id:'style',name:'Acabado guardado',revision:2,config:{cfg:3}},pieces:[
      {id:'hero',kind:'character',name:'Heroína original',revision:1,prompt:'blue eyes'},
      {id:'dress',kind:'outfit',name:'Vestido original',revision:2,prompt:'green dress'},
    ]},composition:{additional_prompt:'full body',additional_negative:'cropped'}};
  database.generations.push(sourceRecord);
  studio.renderAll();studio.restoreComposition(sourceRecord,'txt2img');
  const before=JSON.stringify(studio.productionPayload());
  const sourceBefore=JSON.stringify(studio.state.sourceRecord);
  studio.createVisualElement('item');
  element('piece-name').value='Poción nueva';element('piece-prompt').value='small blue potion, glass bottle';
  element('piece-seed').value='12345678901234567890';
  element('piece-test-style').value='test-style';element('piece-test-format').value='portrait';
  Object.assign(studio.session,{active:true,status:'ready',socket:{readyState:1}});
  const result={id:'piece-test-result',project_id:'project',category:'items',purpose:'piece_test',mode:'txt2img',status:'pending',
    name:'Poción nueva',seed:'12345678901234567890',piece_test:{id:'new-item',kind:'item'}};
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/pieces')&&options.method==='POST'){
      const saved={...JSON.parse(options.body),id:'new-item',revision:1};database.pieces.push(saved);return response(saved);
    }
    if(url.endsWith('/bootstrap'))return response(database);
    if(url.endsWith('/generate')){database.generations.push(result);return response({job_id:'element-job'});}
    if(url.includes('/jobs/'))return response({status:'done',result});
    if(url.includes('/generations?'))return response(database.generations);
    return response({});
  };
  await studio.savePiece({preventDefault(){},submitter:{id:'save-test-piece'}});
  const payload=JSON.parse(requests.find(request=>request.url.endsWith('/generate')).body);
  assert.equal(payload.purpose,'piece_test');assert.equal(payload.category,'items');
  assert.equal(payload.style_id,'test-style');assert.equal(payload.style_revision,6);
  assert.equal(payload.config.seed,'12345678901234567890');assert.deepEqual(payload.piece_ids,['new-item']);
  assert.equal(payload.source_asset_id,undefined);
  assert.equal(JSON.stringify(studio.productionPayload()),before);
  assert.equal(studio.state.productionCategory,'characters');assert.equal(studio.state.productionMode,'txt2img');
  assert.equal(element('prod-style').value,'style');assert.equal(element('prod-seed').value,'18446744073709551615');
  assert.equal(studio.state.recipeStyleRevision,2);
  assert.deepEqual(JSON.parse(JSON.stringify(studio.state.recipePieceRevisions)),{hero:1,dress:2});
  assert.equal(studio.state.sourceAssetId,'frozen-source');assert.equal(JSON.stringify(studio.state.sourceRecord),sourceBefore);
  assert.match(element('element-test-gallery').innerHTML,/Poción nueva[\s\S]*12345678901234567890/);
  assert.doesNotMatch(element('production-gallery').innerHTML,/piece-test-result/);
});

test('piece test cards cannot become composition sources that leak their presentation template',()=>{
  const {studio,element}=harness();studio.chooseProduction('characters');
  const record={id:'piece-preview',project_id:'project',category:'characters',purpose:'piece_test',piece_test:{id:'hero'},seed:'42',snapshot:{style:{id:'style',revision:1},pieces:[]}};
  studio.state.elementTestId='hero';studio.state.generations.push(record);studio.renderGalleries();
  assert.match(element('element-test-gallery').innerHTML,/Elegir imagen del elemento/);
  assert.doesNotMatch(element('production-gallery').innerHTML,/piece-preview/);
  assert.doesNotMatch(element('production-gallery').innerHTML,/data-use-composition="piece-preview"/);
  assert.doesNotMatch(element('production-gallery').innerHTML,/data-master="piece-preview"/);
  assert.throws(()=>studio.restoreComposition(record,'txt2img'),/prueba de pieza/);
});

test('frozen expression recipes are blocked and normal recipes exclude effective character LoRAs',async()=>{
  const {studio,element,context,requests,database}=harness();
  const record={id:'master',category:'characters',seed:'42',config:{width:1024,height:1536,loras:[{file:'finish',strength:.5},{file:'character',strength:.8}]},
    snapshot:{style:{id:'style',revision:2,config:{loras:[{file:'finish',strength:.5}]}},pieces:[{id:'hero',kind:'character',revision:3}]},
    composition:{additional_prompt:'full body',additional_negative:'cropped'}};
  studio.restoreComposition(record,'expression');element('recipe-name').value='Alegre';
  await assert.rejects(studio.saveRecipe(),/maestro y la máscara facial/);
  assert.equal(requests.length,0);
  studio.restoreComposition(record,'txt2img');element('recipe-name').value='Composición';
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/recipes')&&options.method==='POST')return response({...JSON.parse(options.body),id:'saved-recipe'});
    return response(database);
  };
  await studio.saveRecipe();
  const payload=JSON.parse(requests.find(request=>request.url.endsWith('/recipes')).body);
  assert.equal(payload.source_asset_id,undefined);assert.equal(payload.prompt,'full body');
  assert.deepEqual(payload.config.loras,[{file:'finish',strength:.5}]);
});

test('production starts with a resource menu and blocks payloads until a choice', () => {
  const {studio,element} = harness();
  assert.equal(element('production-title').textContent,'¿Qué quieres generar?');
  assert.equal(element('production-workspace').classList.contains('hidden'),true);
  assert.equal(element('production-menu').classList.contains('hidden'),false);
  assert.match(element('production-menu').innerHTML,/Personajes[\s\S]*Fondos[\s\S]*Objetos/);
  assert.throws(()=>studio.productionPayload(),/Elige primero/);
  studio.chooseProduction('characters');
  assert.equal(element('production-workspace').classList.contains('hidden'),false);
  assert.equal(element('production-menu').classList.contains('hidden'),false);
});

test('switching to backgrounds discards character pieces and uses the background category and format', () => {
  const {studio,element} = harness();
  studio.chooseProduction('characters');
  element('prod-style').value='style';
  element('piece-pickers').querySelector('[data-piece-kind="character"]').value='hero';
  assert.deepEqual(Array.from(studio.productionPayload().piece_ids),['hero']);
  studio.chooseProduction('backgrounds');
  element('piece-pickers').querySelector('[data-piece-kind="scene"]').value='forest';
  const payload=studio.productionPayload();
  assert.equal(element('piece-pickers').querySelector('[data-piece-kind="character"]'),null);
  assert.deepEqual(Array.from(payload.piece_ids),['forest']);
  assert.equal(payload.character,'');
  assert.equal(payload.category,'backgrounds');
  assert.equal(payload.format_id,'landscape');
  assert.equal(payload.mode,'txt2img');
  assert.throws(()=>studio.chooseProduction('backgrounds','expression'),/expresiones requieren/);
});

test('loading a recipe selects its workspace and preserves revision and format pins', () => {
  const {studio,element} = harness();
  studio.state.recipes.push({id:'recipe',project_id:'project',name:'Fondo original',category:'backgrounds',
    style_id:'style',style_revision:2,piece_ids:['forest'],piece_revisions:{forest:1},
    format_id:'old-wide',format_snapshot:{id:'old-wide',name:'Formato anterior',width:1280,height:768},config:{seed:'18446744073709551615'}});
  studio.loadRecipe('recipe');
  const payload=studio.productionPayload();
  assert.equal(studio.state.productionCategory,'backgrounds');
  assert.equal(payload.style_revision,2);
  assert.equal(payload.piece_revisions.forest,1);
  assert.equal(payload.format_id,'old-wide');
  assert.equal(payload.config.width,1280);
  assert.equal(payload.config.seed,'18446744073709551615');
  assert.equal(element('recipe-select').value,'recipe');
});

test('object categories keep their IDs in menu choices and generated payloads', () => {
  const {studio,element} = harness();
  studio.state.projects[0].categories=[{id:'location-art',name:'Fondos'},{id:'icons',name:'Íconos'}];
  studio.renderAll();
  assert.match(element('production-menu').innerHTML,/data-production-category="location-art"/);
  studio.chooseProduction('location-art');element('prod-style').value='style';
  assert.equal(studio.productionPayload().category,'location-art');
  assert.equal(element('piece-pickers').querySelector('[data-piece-kind="character"]'),null);
  studio.chooseProduction('icons');element('prod-style').value='style';
  assert.equal(studio.productionPayload().category,'icons');
  assert.equal(element('production-title').textContent,'Íconos');
});

test('categories with the same semantic profile keep separate labels, recipes and results', () => {
  const {studio,element} = harness();
  studio.state.projects[0].categories=['characters','secondary-characters'];
  studio.state.recipes=[
    {id:'main-recipe',project_id:'project',name:'Principal',category:'characters',style_id:'style'},
    {id:'secondary-recipe',project_id:'project',name:'Secundario',category:'secondary-characters',style_id:'style'},
  ];
  studio.state.generations=[
    {id:'main-result',project_id:'project',name:'Resultado principal',category:'characters',purpose:'production'},
    {id:'secondary-result',project_id:'project',name:'Resultado secundario',category:'secondary-characters',purpose:'production'},
  ];
  studio.renderAll();
  assert.match(element('production-menu').innerHTML,/<strong>Personajes<\/strong>/);
  assert.match(element('production-menu').innerHTML,/<strong>secondary-characters<\/strong>/);
  studio.chooseProduction('characters');
  assert.deepEqual(element('recipe-select').options.map(o=>o.value),['','main-recipe']);
  assert.match(element('production-gallery').innerHTML,/Resultado principal/);
  assert.doesNotMatch(element('production-gallery').innerHTML,/Resultado secundario/);
  studio.loadRecipe('secondary-recipe');
  assert.equal(studio.state.productionCategory,'secondary-characters');
  assert.equal(studio.productionPayload().category,'secondary-characters');
  assert.equal(element('production-title').textContent,'secondary-characters');
  assert.deepEqual(element('recipe-select').options.map(o=>o.value),['','secondary-recipe']);
  assert.match(element('production-gallery').innerHTML,/Resultado secundario/);
  assert.doesNotMatch(element('production-gallery').innerHTML,/Resultado principal/);
});

test('object categories sharing a label retain their exact ID when a recipe is restored', () => {
  const {studio,element} = harness();
  studio.state.projects[0].categories=[{id:'heroes',name:'Personajes'},{id:'npcs',name:'Personajes'}];
  studio.state.recipes=[
    {id:'heroes-recipe',project_id:'project',name:'Héroes',category:'heroes',style_id:'style'},
    {id:'npcs-recipe',project_id:'project',name:'Habitantes',category:'npcs',style_id:'style'},
  ];
  studio.state.generations=[
    {id:'heroes-result',project_id:'project',name:'Héroe',category:'heroes',purpose:'production'},
    {id:'npcs-result',project_id:'project',name:'Habitante',category:'npcs',purpose:'production'},
  ];
  studio.renderAll();studio.loadRecipe('npcs-recipe');
  assert.equal(studio.state.productionCategory,'npcs');
  assert.equal(studio.productionPayload().category,'npcs');
  assert.deepEqual(element('recipe-select').options.map(o=>o.value),['','npcs-recipe']);
  assert.match(element('production-gallery').innerHTML,/Habitante/);
  assert.doesNotMatch(element('production-gallery').innerHTML,/Héroe/);
  assert.deepEqual(element('master-select').options.map(o=>o.value),['','npcs-result']);
});

test('every image gallery offers direct deletion and backgrounds never offer facial masters', () => {
  const {studio,element} = harness();
  studio.state.generations=[
    {id:'sample',project_id:'project',name:'Prueba de estilo',category:'characters',purpose:'calibration'},
    {id:'portrait-result',project_id:'project',name:'Retrato final',category:'characters',purpose:'production'},
    {id:'background-result',project_id:'project',name:'Paisaje final',category:'backgrounds',purpose:'production'},
  ];
  studio.chooseProduction('characters');
  assert.match(element('cal-gallery').innerHTML,/data-delete-image="sample"/);
  assert.match(element('production-gallery').innerHTML,/data-delete-image="portrait-result"/);
  for(const id of ['sample','portrait-result','background-result']) {
    assert.match(element('review-gallery').innerHTML,new RegExp(`data-delete-image="${id}"`));
  }
  studio.chooseProduction('characters','expression');
  assert.deepEqual(element('master-select').options.map(o=>o.value),['','portrait-result','sample']);
  assert.doesNotMatch(element('review-gallery').innerHTML,/data-master="background-result"/);
  assert.match(element('review-gallery').innerHTML,/data-master="portrait-result"/);
  studio.chooseProduction('backgrounds');
  assert.match(element('production-gallery').innerHTML,/data-delete-image="background-result"/);
  assert.doesNotMatch(element('production-gallery').innerHTML,/Como maestro|data-master=/);
  assert.doesNotMatch(element('production-results-help').textContent,/Como maestro/);
});

test('canceling image deletion sends no request and retains selected samples', async () => {
  const {studio,requests,control} = harness();
  studio.state.sampleIds.add('image'); control.confirm=false;
  await studio.deleteImage('image');
  assert.equal(requests.length,0);
  assert.equal(studio.state.sampleIds.has('image'),true);
  assert.equal(studio.state.generations.length,1);
});

test('blocked image deletion retains the sample; successful deletion refreshes it out of the gallery', async () => {
  const {studio,requests,control,element} = harness();
  studio.state.sampleIds.add('image');studio.state.purgeIds.add('image');control.blocked=true;
  await assert.rejects(studio.deleteImage('image'),/Muestra usada/);
  assert.equal(studio.state.sampleIds.has('image'),true);
  assert.equal(studio.state.purgeIds.has('image'),true);
  control.blocked=false;
  await studio.deleteImage('image');
  assert.equal(studio.state.sampleIds.has('image'),false);
  assert.equal(studio.state.purgeIds.has('image'),false);
  assert.equal(studio.state.generations.length,0);
  assert.equal(requests.filter(r=>r.method==='DELETE').length,2);
  assert.doesNotMatch(element('cal-gallery').innerHTML,/data-delete-image/);
});

test('resource deletion preserves the active style on cancel or conflict and clears it after success', async () => {
  const {studio,element,requests,control} = harness();
  studio.state.currentStyle=studio.state.styles[0];element('cal-style').value='style';
  control.confirm=false;await studio.deleteResource('styles','style');
  assert.equal(requests.length,0);assert.equal(studio.state.currentStyle.id,'style');
  control.confirm=true;control.blocked=true;
  await assert.rejects(studio.deleteResource('styles','style'),/Muestra usada/);
  assert.equal(studio.state.currentStyle.id,'style');
  control.blocked=false;await studio.deleteResource('styles','style');
  assert.equal(studio.state.currentStyle,null);
  assert.equal(studio.state.styles.length,0);
  assert.equal(element('cal-style').value,'');
});

test('confirmed image deletion removes the card before the list refresh returns', async () => {
  const {studio,element,context,database} = harness();
  studio.state.sampleIds.add('image');studio.state.purgeIds.add('image');
  const staleImages=JSON.stringify(database.generations), fetch=context.fetch;
  let resume;
  context.fetch=(url,options)=>url.includes('/generations?')
    ?new Promise(resolve=>{resume=resolve;}):fetch(url,options);
  const deleting=studio.deleteImage('image');
  await until(()=>resume);
  assert.equal(studio.state.generations.length,0);
  assert.equal(studio.state.sampleIds.has('image'),false);
  assert.equal(studio.state.purgeIds.has('image'),false);
  for(const gallery of ['cal-gallery','production-gallery','review-gallery'])
    assert.doesNotMatch(element(gallery).innerHTML,/data-delete-image="image"/);
  resume(response(staleImages));await deleting;
  assert.equal(studio.state.generations.length,0,'A stale post-delete response must not restore the image.');
});

test('confirmed style deletion removes the shelf and select options before bootstrap returns', async () => {
  const {studio,element,context,database} = harness();
  studio.state.currentStyle=studio.state.styles[0];element('cal-style').value='style';element('prod-style').value='style';
  const staleBootstrap=JSON.stringify(database), fetch=context.fetch;
  let resume;
  context.fetch=(url,options)=>url.endsWith('/bootstrap')
    ?new Promise(resolve=>{resume=resolve;}):fetch(url,options);
  const deleting=studio.deleteResource('styles','style');
  await until(()=>resume);
  assert.equal(studio.state.styles.length,0);
  assert.equal(studio.state.currentStyle,null);
  assert.doesNotMatch(element('style-shelf').innerHTML,/data-load-style="style"/);
  for(const select of ['cal-style','prod-style'])
    assert.equal(element(select).options.some(o=>o.value==='style'),false);
  resume(response(staleBootstrap));await deleting;
  assert.equal(studio.state.styles.length,0,'A stale post-delete bootstrap must not restore the style.');
});

test('deleting a calibration sample updates every style preview and the open style before refresh', async () => {
  const {studio,element,context,database} = harness();
  database.generations.push({id:'replacement',project_id:'project',name:'Otra muestra',purpose:'calibration',file:'replacement.png'});
  database.styles[0].sample_ids=['image','replacement'];
  database.styles.push({id:'second-style',name:'Otra prueba',project_id:'project',sample_ids:['image']});
  studio.state.styles=database.styles;studio.state.generations=database.generations;
  studio.state.currentStyle=studio.state.styles[0];studio.state.sampleIds=new Set(['image','replacement']);studio.renderAll();
  const fetch=context.fetch;let resume;
  context.fetch=(url,options)=>url.includes('/generations?')
    ?new Promise(resolve=>{resume=resolve;}):fetch(url,options);
  const deleting=studio.deleteImage('image');await until(()=>resume);
  assert.doesNotMatch(element('style-shelf').innerHTML,/\/images\/image(?:["?]|$)/);
  assert.match(element('style-shelf').innerHTML,/\/images\/replacement/);
  assert.match(element('style-shelf').innerHTML,/style-swatch/);
  assert.equal(studio.state.sampleIds.has('image'),false);
  assert.equal(studio.state.currentStyle.sample_ids.includes('image'),false);
  assert.equal(studio.state.styles.some(style=>style.sample_ids.includes('image')),false);
  resume(response(database.generations));await deleting;
});

for(const kind of ['generations','styles']) test(`an earlier bootstrap cannot resurrect deleted ${kind}`, async () => {
  const {studio,element,context,database} = harness();
  const staleBootstrap=JSON.stringify(database), fetch=context.fetch;
  let resume, held=false;
  context.fetch=(url,options)=>{
    if(url.endsWith('/bootstrap')&&!held){held=true;return new Promise(resolve=>{resume=resolve;});}
    return fetch(url,options);
  };
  const oldReload=studio.reload();
  await until(()=>resume);
  if(kind==='generations')await studio.deleteImage('image');else await studio.deleteResource('styles','style');
  assert.equal(studio.state[kind].length,0);
  resume(response(staleBootstrap));await oldReload;
  assert.equal(studio.state[kind].length,0);
  assert.doesNotMatch(element(kind==='generations'?'cal-gallery':'style-shelf').innerHTML,
    kind==='generations'?/data-delete-image="image"/:/data-load-style="style"/);
});

test('an earlier gallery request cannot resurrect an image after another delete refresh completes', async () => {
  const {studio,element,context,database} = harness();
  const staleImages=JSON.stringify(database.generations), fetch=context.fetch;
  let resume, held=false;
  context.fetch=(url,options)=>{
    if(url.includes('/generations?')&&!held){held=true;return new Promise(resolve=>{resume=resolve;});}
    return fetch(url,options);
  };
  const oldRefresh=studio.refreshGenerations();await until(()=>resume);
  await studio.deleteImage('image');resume(response(staleImages));await oldRefresh;
  assert.equal(studio.state.generations.length,0);
  assert.doesNotMatch(element('cal-gallery').innerHTML,/data-delete-image="image"/);
});

for(const kind of ['generations','styles']) test(`failed post-delete refresh keeps ${kind} removed and reports the completed deletion`, async () => {
  const {studio,element,context} = harness(), fetch=context.fetch;
  context.fetch=(url,options)=>options?.method==='DELETE'?fetch(url,options):Promise.reject(new Error('Servidor no disponible'));
  if(kind==='generations')await studio.deleteImage('image');else await studio.deleteResource('styles','style');
  assert.equal(studio.state[kind].length,0);
  assert.match(element('notice').innerHTML,/borrad/i);
  assert.match(element('notice').innerHTML,/actualiz|recarg/i);
});

test('purged metadata never becomes a gallery card or a saved style thumbnail', () => {
  const {studio,element} = harness();
  studio.state.generations=[
    {id:'gone',project_id:'project',name:'Imagen borrada',purpose:'calibration',category:'characters',purged_at:'2026-10-28T12:00:00Z',file:null},
    {id:'gone-production',project_id:'project',name:'Producción borrada',purpose:'production',category:'characters',purged_at:'2026-10-28T12:00:00Z',file:null},
    {id:'live',project_id:'project',name:'Imagen vigente',purpose:'calibration',category:'characters',file:'live.png'},
  ];
  studio.state.styles=[{id:'mixed-style',name:'Con muestra',project_id:'project',sample_ids:['gone','live']},
    {id:'empty-style',name:'Sin muestra',project_id:'project',sample_ids:['gone']}];
  studio.state.sampleIds.add('gone');studio.chooseProduction('characters');studio.renderAll();
  for(const gallery of ['cal-gallery','production-gallery','review-gallery']) {
    assert.doesNotMatch(element(gallery).innerHTML,/data-delete-image="gone(?:-production)?"/);
    assert.doesNotMatch(element(gallery).innerHTML,/\/images\/gone/);
  }
  assert.doesNotMatch(element('style-shelf').innerHTML,/\/images\/gone/);
  assert.match(element('style-shelf').innerHTML,/\/images\/live/);
  assert.match(element('style-shelf').innerHTML,/style-swatch/);
});

test('bootstrap and gallery reads explicitly bypass the HTTP response cache', async () => {
  const {studio,context} = harness(), fetch=context.fetch, reads=[];
  context.fetch=(url,options)=>{reads.push({url,options});return fetch(url,options);};
  await studio.reload();await studio.refreshGenerations();
  assert.equal(reads.length,2);
  assert.equal(reads.every(r=>r.options.cache==='no-store'),true);
});

test('pending backend cleanup removes the card but reports that physical cleanup is unfinished', async () => {
  const {studio,context,element} = harness(), fetch=context.fetch;
  context.fetch=async(url,options)=>{
    const result=await fetch(url,options);
    return options?.method==='DELETE'?response({deleted:true,cleanup_pending:true,cleanup_errors:['Archivo bloqueado']}):result;
  };
  await studio.deleteImage('image');
  assert.equal(studio.state.generations.length,0);
  assert.doesNotMatch(element('cal-gallery').innerHTML,/data-delete-image="image"/);
  assert.match(element('notice').innerHTML,/Imagen retirada/);
  assert.match(element('notice').innerHTML,/limpieza de archivos pendiente/);
  assert.doesNotMatch(element('notice').innerHTML,/Imagen borrada\./);
  assert.equal(studio.storageStatus().pending,true);
});

test('deletion confirmations include the image record and permanent resource files without backup', async () => {
  const {studio,control,confirmations} = harness();
  control.confirm=false;
  await studio.deleteImage('image');
  await studio.deleteResource('styles','style');
  assert.match(confirmations[0],/Borrar definitivamente.*sus variantes y su ficha/);
  assert.doesNotMatch(confirmations[0],/ficha[^?]*se conservar/);
  assert.match(confirmations[1],/Borrar definitivamente estilo/);
  assert.match(confirmations[1],/archivos de todas sus revisiones, sin respaldo/);
  assert.equal(studio.state.styles.length,1);
  assert.equal(studio.state.generations.length,1);
});

test('failed cleanup stays visible and manual retry can finish removal', async () => {
  const {studio,context,element} = harness(), fetch=context.fetch;
  let calls=0;
  context.fetch=(url,options)=>{
    if(!url.endsWith('/storage/cleanup'))return fetch(url,options);
    calls++;
    if(calls===1)return Promise.reject(new Error('No se pudo limpiar el archivo bloqueado'));
    return Promise.resolve(response({cleanup_pending:false,removed_files:2,deleted_images:['image'],errors:[]}));
  };
  await studio.cleanupStorage();
  assert.match(element('notice').innerHTML,/archivo bloqueado/);
  assert.equal(element('notice').attrs.role,'alert');
  assert.equal(studio.storageStatus().pending,true);
  assert.equal(studio.storageStatus().busy,false);
  assert.equal(studio.state.generations.length,1);
  await studio.cleanupStorage();
  assert.equal(calls,2,'A manual retry must remain available during the automatic retry delay.');
  assert.equal(studio.storageStatus().pending,false);
  assert.equal(studio.storageStatus().busy,false);
  assert.equal(studio.state.generations.length,0);
  assert.match(element('notice').innerHTML,/Limpieza completada: 2/);
});

test('automatic cleanup waits for a ready idle session, does not overlap and retries at most every 30 seconds', async () => {
  const {studio,context,database,control} = harness(), fetch=context.fetch;
  const attempts=[];
  context.fetch=(url,options)=>{
    if(url.endsWith('/storage/cleanup'))return new Promise(resolve=>attempts.push(resolve));
    return fetch(url,options);
  };
  database.storage_cleanup={cleanup_pending:true,deferred:true};
  await studio.reload();
  assert.equal(attempts.length,0,'An unready session cannot automatically clean backend files.');
  Object.assign(studio.session,{active:true,status:'ready',socket:{readyState:1}});
  studio.state.activeJob='job';studio.updateSessionView();
  assert.equal(attempts.length,0);
  studio.state.activeJob=null;studio.session.generationPending=true;studio.updateSessionView();
  assert.equal(attempts.length,0);
  studio.session.generationPending=false;studio.updateSessionView();
  await until(()=>attempts.length===1);
  assert.equal(studio.storageStatus().busy,true);
  studio.updateSessionView();studio.updateSessionView();
  assert.equal(attempts.length,1,'Repeated ready events must share the active cleanup.');
  attempts[0](response({cleanup_pending:true,deferred:true,errors:['Backend ocupado']}));
  await until(()=>!studio.storageStatus().busy);
  studio.updateSessionView();control.now+=29999;studio.updateSessionView();
  assert.equal(attempts.length,1);
  control.now++;studio.updateSessionView();await until(()=>attempts.length===2);
  attempts[1](response({cleanup_pending:false,removed_files:0,errors:[]}));
  await until(()=>!studio.storageStatus().busy);
  assert.equal(studio.storageStatus().pending,false);
});

test('a completed generation preserves its cleanup warning instead of showing unconditional success', async () => {
  const {studio,context,element} = harness(), fetch=context.fetch;
  studio.state.activeJob='job';
  context.fetch=(url,options)=>url.endsWith('/jobs/job')
    ?Promise.resolve(response({status:'done',result:{id:'image'},warning:'El resultado está guardado; queda una limpieza pendiente.'}))
    :fetch(url,options);
  await studio.pollJob('job',{purpose:'calibration',mode:'txt2img'});
  assert.match(element('notice').innerHTML,/limpieza pendiente/);
  assert.doesNotMatch(element('notice').innerHTML,/Generación terminada/);
  assert.equal(element('notice').attrs.role,'alert');
  assert.equal(studio.state.sampleIds.has('image'),true);
  assert.equal(studio.storageStatus().pending,true);
});

test('a late completed job cannot select a sample already deleted from the workshop', async () => {
  const {studio,context} = harness(), fetch=context.fetch;
  await studio.deleteImage('image');
  studio.state.activeJob='job';
  context.fetch=(url,options)=>url.endsWith('/jobs/job')
    ?Promise.resolve(response({status:'done',result:{id:'image'}})):fetch(url,options);
  await studio.pollJob('job',{purpose:'calibration',mode:'txt2img'});
  assert.equal(studio.state.sampleIds.has('image'),false);
  assert.equal(studio.state.generations.some(image=>image.id==='image'),false);
});
