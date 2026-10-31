const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const AnimaProduction = require('../static/anima_production.js');
const AnimaPieces = require('../static/anima_pieces.js');
const AnimaWorkspace = require('../static/anima_workspace.js');
const AnimaCalibration = require('../static/anima_calibration.js');
const AnimaElementLab = require('../static/anima_element_lab.js');
const AnimaElementProfiles = require('../static/anima_element_profiles.js');
const calibratedConfig={model:'base.safetensors',text_encoder:'encoder.safetensors',vae:'vae.safetensors',steps:37,cfg:4,sampler:'er_sde',scheduler:'simple',width:1024,height:1024,seed:'42',loras:[{file:'finish.safetensors',strength:0}]};
const source = readFileSync(path.join(__dirname, '../static/anima_studio.js'), 'utf8');

function harness() {
  const nodes = new Map(), requests = [], confirmations = [];
  const control = {confirm: true, blocked: false, errors:[], now:100000};
  const selectIds = new Set(['cal-style','prod-style','piece-filter','piece-test-style','sampler','scheduler']);
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
      addEventListener() {}, dispatchEvent() {}, focus() {},
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
        if(id === 'piece-specialized-controls') for(const match of next.matchAll(/<select data-element-control="([^"]+)">([\s\S]*?)<\/select>/g)) {
          const child=makeNode(`presentation-${match[1]}`,true);
          child.dataset.elementControl=match[1];child.innerHTML=match[2];this.children.push(child);
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
    styles:[{id:'style',project_id:'project',name:'Estilo',revision:5,prompt:'painted finish',negative:'grain',config:structuredClone(calibratedConfig)}],
    pieces:[{id:'hero',project_id:'project',kind:'character',name:'Heroína',revision:3},
      {id:'forest',project_id:'project',kind:'scene',name:'Bosque',revision:2}],
    recipes:[], generations:[{id:'image',project_id:'project',name:'Muestra',status:'pending',purpose:'calibration'}],
    catalog:{models:[],loras:[],text_encoders:[],vaes:[]},
  };
  const tabs=['styles','production','integrator','library'].map(name=>Object.assign(makeNode(`tab-${name}-button`),{dataset:{tab:name}}));
  const pages=['styles','production','integrator','library'].map(name=>element(`tab-${name}`));
  const context = vm.createContext({
    console:{...console,error(...args) { control.errors.push(args); }}, AnimaProduction, AnimaPieces, AnimaWorkspace, AnimaCalibration, AnimaElementLab, AnimaElementProfiles, WebSocket:{OPEN:1},
    crypto:require('node:crypto'),
    AnimaPieceLoras:{create() {
      let value=[];
      return {setValue(next=[]) {value=JSON.parse(JSON.stringify(next));},getValue() {return JSON.parse(JSON.stringify(value));},setCatalog() {},destroy() {}};
    }},
    Date:class extends Date { static now() { return control.now; } },
    document:{getElementById:element,createElement:makeNode,
      querySelectorAll(selector) { return selector === '[name="mode"]' ? radios : selector==='.tab'?tabs:selector==='.page'?pages:[]; }},
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
  const exports = `globalThis.studio = {state,session,renderAll,renderGalleries,renderStyles,renderProductionMenu,
    chooseProduction,productionPayload,selectedPieces,loadRecipe,saveRecipe,deleteImage,deleteResource,reload,
    refreshGenerations,resetWorkspace,cleanupStorage,pollJob,renderVisualWorkspace,createVisualElement,
    addVisualElement,removeVisualElement,visualReadiness,updateSessionView,openPiece,savePiece,trialPiece,
    pieceFields,pieceStyleChoices,preferredElementStyle,renderPieceTestStyles,applyPieceStyle,renderPieceEditor,renderElementProfile,readElementPresentation,elementContext,productionPieceChanged,productionStyleChanged,createPieceLoras,setTab,
    beginPieceCreation,isMasterCandidate,restoreComposition,releaseComposition,selectMaster,capturePieceSample,
    elementState:()=>({draft:pieceDraft,token:elementTrialToken,style:pieceTrialStyle}),
    loras:()=>pieceLoras,
    storageStatus:()=>({pending:storagePending,busy:storageCleanupBusy,retryAt:storageRetryAt})};`;
  vm.runInContext(source.replace('  init();', exports), context);
  const studio = context.studio;
  Object.assign(studio.state,database,{projectId:'project'});
  element('prod-seed').value = '42';
  studio.createPieceLoras();
  studio.renderAll();
  return {studio,element,database,requests,control,confirmations,context};
}

const response = json => ({ok:true, async text() { return typeof json==='string'?json:JSON.stringify(json); }});


test('each tab has its function and the visible integrator owns final piece and style selection',()=>{
  const html=readFileSync(path.join(__dirname,'../static/anima_studio.html'),'utf8');
  const styles=html.slice(html.indexOf('<section id="tab-styles"'),html.indexOf('<section id="tab-production"'));
  const production=html.slice(html.indexOf('<section id="tab-production"'),html.indexOf('<section id="tab-integrator"'));
  const integrator=html.slice(html.indexOf('<section id="tab-integrator"'),html.indexOf('<section id="tab-library"'));
  const library=html.slice(html.indexOf('<section id="tab-library"'),html.indexOf('<dialog id="project-dialog"'));
  const piece=html.slice(html.indexOf('<dialog id="piece-dialog"'),html.indexOf('<dialog id="catalog-dialog"'));
  for(const id of ['model-select','encoder-select','vae-select','steps','cfg','sampler','scheduler','width','height']) {
    assert.ok(styles.includes(`id="${id}"`),`${id} belongs to calibration`);
    assert.ok(!production.includes(`id="${id}"`),`${id} cannot recalibrate production`);
    assert.ok(!integrator.includes(`id="${id}"`),`${id} cannot recalibrate the integrator`);
    assert.ok(!piece.includes(`id="${id}"`),`${id} cannot recalibrate an element`);
  }
  assert.doesNotMatch(piece,/piece-generation-controls|piece-style-prompt|piece-style-negative|piece-context-prompt/);
  assert.doesNotMatch(production,/composition-generation-controls/);
  assert.match(html,/data-tab="integrator"/);
  assert.match(integrator,/id="prod-style"/);assert.match(integrator,/id="piece-pickers"/);
  assert.match(integrator,/id="integrator-framing"/);
  assert.doesNotMatch(integrator,/<div[^>]*id="piece-pickers"[^>]*class="[^"]*\bhidden\b/);
  assert.doesNotMatch(production,/id="prod-style"|id="piece-pickers"/);
  assert.doesNotMatch(library,/generate-action|encoder-select|vae-select|model-select/);
  assert.match(piece,/id="piece-loras-editor"/);assert.doesNotMatch(piece,/piece-character-tools/);
  assert.match(piece,/<input(?=[^>]*\bid="piece-kind")(?=[^>]*\btype="hidden")[^>]*>/);
  assert.doesNotMatch(piece,/<select[^>]*\bid="piece-kind"|<select[^>]*\bid="piece-master"/);
  assert.deepEqual([...piece.matchAll(/<textarea[^>]+id="([^"]+)"/g)].map(match=>match[1]),['piece-prompt','piece-negative']);
});

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
  assert.equal(element('prod-seed').value,'42');
  studio.addVisualElement('background');
  assert.deepEqual(Array.from(studio.selectedPieces()),['hero','dress','background']);
  studio.removeVisualElement('background');
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

test('every specialized creator opens clean content and inherits the calibrated style without duplicating its controls',()=>{
  const {studio,element,requests}=harness();const before=JSON.stringify(studio.state.styles);
  for(const kind of ['character','outfit','pose','expression','background','item','personality','scene']){
    element('piece-name').value='Old name';element('piece-prompt').value='old prompt';element('piece-negative').value='old negative';
    studio.createVisualElement(kind);
    assert.equal(element('piece-kind').value,kind);assert.equal(element('piece-dialog').open,true);
    for(const id of ['piece-name','piece-prompt','piece-tags','piece-sample-id']) assert.equal(element(id).value,'',`${kind}: ${id}`);
    assert.equal(studio.elementState().style.id,'style');assert.equal(studio.elementState().style.revision,5);
    assert.equal(element('piece-negative').value,'');assert.equal(element('piece-seed').value,'42');
    assert.equal(element('piece-save-details').open,false);
  }
  assert.equal(JSON.stringify(studio.state.styles),before);assert.equal(requests.length,0);
});

test('generic creation returns to the eight-type chooser without opening a default character editor',()=>{
  const {studio,element,requests}=harness();
  studio.chooseProduction('characters');studio.addVisualElement('hero');
  const before=JSON.stringify(studio.productionPayload());
  studio.setTab('library');studio.beginPieceCreation();
  assert.equal(element('tab-production').classList.contains('active'),true);
  assert.equal(element('piece-dialog').open,false);
  for(const type of AnimaWorkspace.TYPES)assert.match(element('element-type-cards').innerHTML,new RegExp(`data-create-element="${type.kind}"`));
  studio.setTab('library');studio.openPiece();
  assert.equal(element('tab-production').classList.contains('active'),true);
  assert.equal(element('piece-dialog').open,false);
  assert.equal(studio.elementState().draft,null);
  assert.equal(JSON.stringify(studio.productionPayload()),before);
  assert.equal(requests.length,0);
});

for(const mode of ['create','edit','variant'])test(`${mode} keeps every editor bound to its selected type even if the hidden field changes`,()=>{
  const {studio,element}=harness();
  for(const kind of ['character','personality','outfit','pose','expression','background','item','scene']){
    const piece={id:`original-${kind}`,project_id:'project',kind,name:`Design ${kind}`,revision:4,prompt:`authored ${kind}`,negative:`avoid ${kind}`};
    if(mode==='create')studio.createVisualElement(kind);else studio.openPiece(piece,mode==='variant');
    const title=`${mode==='create'?'Crear':mode==='edit'?'Editar':'Variante de'} ${AnimaWorkspace.label(kind).toLowerCase()}`;
    assert.equal(element('piece-dialog-title').textContent,title);
    element('piece-name').value=`draft ${kind}`;element('piece-prompt').value=`edited ${kind}`;
    element('piece-kind').value=kind==='character'?'background':'character';
    studio.renderPieceEditor();studio.renderElementProfile();
    const fields=studio.pieceFields();
    assert.equal(fields.kind,kind);
    assert.equal(fields.prompt,`edited ${kind}`);assert.equal(fields.name,`draft ${kind}`);
    assert.equal(studio.elementState().draft.kind,kind);
    assert.equal(element('piece-dialog-title').textContent,title);
    assert.equal(element('piece-pose-tools').classList.contains('hidden'),kind!=='pose');
    assert.equal(studio.elementContext().prompt,AnimaElementProfiles.context(kind,studio.readElementPresentation()).prompt);
    assert.equal(element('piece-id').value,mode==='edit'?piece.id:'');
    if(mode==='variant'){
      assert.equal(fields.master_id,piece.id);assert.equal(fields.master_revision,4);
      assert.equal(element('piece-master-note').classList.contains('hidden'),false);
      assert.match(element('piece-master-note').textContent,/v4/);
    }
  }
});

test('capturing each test opens the correct fixed editor and labels the type without a character fallback',async()=>{
  const {studio,element,database,context}=harness();
  for(const kind of ['character','personality','outfit','pose','expression','background','item','scene']){
    const draft={kind,name:'',prompt:`captured ${kind}`,negative:`avoid ${kind}`};
    const record={id:`sample-${kind}`,project_id:'project',purpose:'piece_test',seed:'18446744073709551615',
      config:{...calibratedConfig,seed:'18446744073709551615'},piece_test:{kind,token:`sample-token-${kind}`,draft},
      snapshot:{style:database.styles[0],pieces:[{...draft,id:`transient-${kind}`,revision:0,draft:true}]}};
    database.generations.push(record);context.fetch=async()=>response(record);
    element('piece-dialog').close();await studio.capturePieceSample(record.id);
    assert.equal(element('piece-dialog').open,true);
    assert.equal(studio.pieceFields().kind,kind);
    assert.equal(element('piece-dialog-title').textContent,`Guardar prueba de ${AnimaWorkspace.label(kind).toLowerCase()}`);
    assert.equal(element('piece-prompt').value,`captured ${kind}`);
    assert.equal(element('piece-sample-id').value,record.id);
    assert.equal(element('piece-seed').value,record.seed);
    element('piece-kind').value=kind==='character'?'outfit':'character';
    assert.equal(studio.pieceFields().kind,kind);
  }
});

test('capturing a composed background enters a background editor directly without a type switch',async()=>{
  const {studio,element,database,context}=harness();
  const record={id:'background-capture',project_id:'project',purpose:'production',category:'backgrounds',seed:'17',
    config:{...calibratedConfig,seed:'17'},composition:{additional_prompt:'forest clearing'},
    snapshot:{style:database.styles[0],pieces:[{id:'forest-old',kind:'background',name:'Forest',prompt:'dense forest',negative:'people'}]}};
  database.generations.push(record);context.fetch=async()=>response(record);
  await studio.capturePieceSample(record.id);
  assert.equal(studio.pieceFields().kind,'background');
  assert.equal(element('piece-dialog-title').textContent,'Guardar prueba de fondo');
  assert.equal(element('piece-prompt').value,'dense forest');
  assert.equal(element('piece-id').value,'');
});

test('saving requires a name but does not force a trial or make a request for an unnamed draft',async()=>{
  const {studio,element,requests}=harness();studio.createVisualElement('character');element('piece-prompt').value='new identity';
  await assert.rejects(studio.savePiece({preventDefault(){}}),/nombre/);
  assert.equal(requests.length,0);assert.equal(element('piece-dialog').open,true);assert.equal(element('piece-save-details').open,true);
});

test('composition explores text without inventory while inheriting a calibrated style',()=>{
  const {studio,element}=harness();studio.state.pieces=[];studio.chooseProduction('backgrounds');
  element('prod-style').value='style';element('prod-prompt').value='moonlit forest';
  assert.equal(studio.visualReadiness().ready,true);
  const payload=studio.productionPayload();assert.equal(payload.style_id,'style');assert.deepEqual(Array.from(payload.piece_ids),[]);
  assert.equal(payload.prompt,'moonlit forest');assert.equal(payload.integration,true);
  for(const key of ['model','steps','cfg','loras','text_encoder','vae','sampler','scheduler'])assert.equal(payload.config[key],undefined,key);
  assert.ok(payload.config.width>payload.config.height);
  studio.state.styles=[];element('prod-style').value='';assert.equal(studio.visualReadiness().ready,false);
  assert.throws(()=>studio.productionPayload(),/estilo/);
});

test('the integrator combines independent character outfit pose and expression with either selected style',()=>{
  const {studio,element}=harness();
  studio.state.pieces[0]={...studio.state.pieces[0],prompt:'blue eyes',negative:'glasses',
    loras:[{file:'identity.safetensors',strength:.8,trigger:'hero'}],
    generation_snapshot:{config:{model:'old-model'},snapshot:{style:{id:'old-preview-style',revision:1}}}};
  studio.state.pieces.push(
    {id:'dress',kind:'outfit',project_id:'project',name:'Uniforme',revision:2,prompt:'blue uniform',negative:'cape',loras:[{file:'uniform.safetensors',strength:.6}]},
    {id:'standing',kind:'pose',project_id:'project',name:'De pie',revision:4,prompt:'one hand on hip',loras:[{file:'pose.safetensors',strength:0,enabled:false}],definition:{presentation:{view:'threequarter'}}},
    {id:'happy',kind:'expression',project_id:'project',name:'Alegre',revision:1,prompt:'warm smile',negative:'frown',loras:[{file:'smile.safetensors',strength:.4}],definition:{presentation:{intensity:'subtle'}}});
  const piecesBefore=JSON.stringify(studio.state.pieces);
  for(const id of ['hero','dress','standing','happy'])studio.addVisualElement(id);
  assert.equal(element('tab-integrator').classList.contains('active'),true);
  assert.equal(element('tab-production').classList.contains('active'),false);
  element('prod-prompt').value='battle ready';element('prod-negative').value='no speech bubbles';
  const first=studio.productionPayload();
  const secondStyle={id:'second-style',project_id:'project',name:'Acuarela',revision:3,prompt:'soft watercolour',negative:'hard lines',config:{...calibratedConfig,model:'watercolour.safetensors',steps:21,loras:[{file:'watercolour.safetensors',strength:.7}]}};
  studio.state.styles.push(secondStyle);studio.renderStyles();element('prod-style').value=secondStyle.id;studio.productionStyleChanged();
  const second=studio.productionPayload();
  assert.equal(first.style_id,'style');assert.equal(second.style_id,'second-style');assert.equal(second.style_revision,3);
  assert.equal(first.integration,true);assert.equal(second.integration,true);
  assert.deepEqual(Array.from(second.piece_ids),['hero','dress','standing','happy']);
  assert.deepEqual(Array.from(first.piece_ids),Array.from(second.piece_ids));
  for(const key of ['model','loras','steps','cfg','text_encoder','vae','sampler','scheduler'])assert.equal(second.config[key],undefined,key);
  assert.equal(second.negative_override,undefined);assert.equal(second.negative,'no speech bubbles');
  assert.match(second.prompt,/battle ready/);assert.equal(JSON.stringify(studio.state.pieces),piecesBefore);
});


for(const kind of ['character','outfit','pose','expression','background','item','personality','scene'])test(`the ${kind} editor generates only its presentation and identity under the fixed calibrated style`,async()=>{
  const {studio,element,context,requests}=harness(),styleBefore=JSON.stringify(studio.state.styles);
  studio.createVisualElement(kind);element('piece-prompt').value=`new ${kind}`;element('piece-negative').value='';
  const ownLoras=[{file:`pieces/${kind}.safetensors`,strength:0,trigger:`trigger_${kind}`,enabled:true}];studio.loras().setValue(ownLoras);
  element('piece-seed').value='18446744073709551615';
  Object.assign(studio.session,{active:true,status:'ready',socket:{readyState:1}});
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/generate'))return response({job_id:`${kind}-job`});
    if(url.includes('/jobs/'))return response({status:'done',result:{id:`${kind}-image`}});
    return response([]);
  };
  await studio.trialPiece();const payload=JSON.parse(requests.find(r=>r.url.endsWith('/generate')).body);
  assert.equal(payload.style_id,'style');assert.equal(payload.style_revision,5);assert.equal(payload.piece_test.specialized,true);
  assert.equal(payload.piece_test.source_piece_id,undefined);assert.equal(payload.piece_test.source_piece_revision,undefined);
  assert.deepEqual(Object.keys(payload.config).sort(),['height','seed','width']);assert.equal(payload.config.seed,'18446744073709551615');
  const orientation=AnimaElementProfiles.profile(kind).orientation;
  if(orientation==='portrait')assert.ok(payload.config.height>payload.config.width);
  if(orientation==='landscape')assert.ok(payload.config.width>payload.config.height);
  if(orientation==='square')assert.equal(payload.config.width,payload.config.height);
  assert.equal(payload.negative_override,undefined);assert.equal(payload.prompt,'');assert.equal(payload.negative,'');
  assert.equal(payload.piece_test.draft.prompt,`new ${kind}`);assert.equal(payload.piece_test.draft.negative,'');
  assert.deepEqual(payload.piece_test.draft.loras,ownLoras);
  assert.equal(payload.piece_test.draft.definition.trial_negative,undefined);
  assert.equal(payload.piece_test.draft.style_id,undefined);assert.equal(payload.piece_test.draft.style_revision,undefined);
  assert.equal(JSON.stringify(studio.state.styles),styleBefore);assert.equal(requests.some(r=>r.url.endsWith('/pieces')),false);
  assert.equal(element('piece-dialog').open,true);
});

test('an editor can open without styles but generation explains where to prepare one without saving a piece',async()=>{
  const {studio,element,requests}=harness();studio.state.styles=[];studio.renderAll();studio.createVisualElement('character');
  assert.equal(element('piece-dialog').open,true);assert.equal(studio.elementState().style,null);
  assert.match(element('piece-style-note').textContent,/Estilos|estilo calibrado/);
  await assert.rejects(studio.trialPiece(),/estilo calibrado|Estilos/);assert.equal(requests.length,0);
});

test('changing a preview style never rewrites the piece content or its own negative',()=>{
  const {studio,element}=harness();
  const old={id:'old-style',revision:2,name:'Anterior',prompt:'ink',negative:'old grain',config:{...calibratedConfig,seed:'999'}};
  const piece={id:'hero-old',name:'Heroína',kind:'character',project_id:'project',revision:3,prompt:'blue eyes',negative:'glasses',
    definition:{trial_negative:{style_id:old.id,style_revision:old.revision,text:'old restriction'}},seed:'999',
    generation_snapshot:{config:structuredClone(old.config),snapshot:{style:old,pieces:[]}}};
  const before=JSON.stringify(piece);studio.openPiece(piece);
  assert.equal(studio.elementState().style.id,'style');assert.equal(studio.elementState().style.revision,5);
  assert.equal(element('piece-negative').value,'glasses');assert.equal(element('piece-seed').value,'42');
  studio.state.styles.push(old);studio.renderPieceTestStyles('old-style@2');studio.applyPieceStyle();
  assert.equal(studio.elementState().style.id,'old-style');assert.equal(element('piece-negative').value,'glasses');
  assert.equal(element('piece-prompt').value,'blue eyes');
  const saved=studio.pieceFields();assert.equal(saved.negative,'glasses');assert.equal(saved.definition.trial_negative,undefined);
  assert.equal(saved.style_id,undefined);assert.equal(saved.style_revision,undefined);assert.equal(saved.config,undefined);
  assert.equal(JSON.stringify(piece),before);
});

test('composition formats follow the resource orientation and facial edits hide unrelated presentation controls',()=>{
  const {studio,element}=harness();
  studio.state.projects[0].formats.push({id:'misnamed-portrait',name:'Personaje',width:1536,height:864},{id:'icon',name:'Icono',width:512,height:512});
  studio.renderAll();studio.chooseProduction('characters');
  assert.ok(element('format-select').options.some(option=>option.value==='portrait'));
  assert.ok(!element('format-select').options.some(option=>option.value==='misnamed-portrait'||option.value==='icon'));
  studio.chooseProduction('backgrounds');assert.ok(element('format-select').options.some(option=>option.value==='landscape'));
  assert.ok(!element('format-select').options.some(option=>option.value==='portrait'||option.value==='icon'));
  studio.chooseProduction('items');assert.ok(element('format-select').options.some(option=>option.value==='icon'));
  assert.ok(!element('format-select').options.some(option=>option.value==='portrait'||option.value==='landscape'));
  studio.chooseProduction('characters','expression');
  assert.equal(element('production-format-field').classList.contains('hidden'),true);
  assert.equal(element('production-variation-tools').classList.contains('hidden'),true);
  const record={id:'facial-master',category:'characters',seed:'18446744073709551615',config:{...calibratedConfig,width:1024,height:1536},
    snapshot:{style:{...studio.state.styles[0]},pieces:[{id:'hero',kind:'character',name:'Heroína',revision:1}]},
    composition:{additional_prompt:'full body',additional_negative:'grain'}};
  studio.restoreComposition(record,'expression');assert.equal(element('production-style-field').classList.contains('hidden'),true);
  assert.equal(element('prod-seed').value,record.seed);assert.equal(element('prod-seed').disabled,true);
});

test('integrator adds negative instructions without overriding the style or piece negatives',()=>{
  const {studio,element}=harness();studio.chooseProduction('backgrounds');element('prod-prompt').value='forest';
  assert.equal(element('prod-negative').value,'');
  element('prod-negative').value='vignette';const payload=studio.productionPayload();
  assert.equal(payload.negative,'vignette');assert.equal(payload.negative_override,undefined);
  assert.equal(studio.state.styles[0].negative,'grain');assert.equal(payload.integration,true);
});

test('trial galleries group results by token and never mix a different open experiment',()=>{
  const {studio,element}=harness();studio.createVisualElement('item');const token=studio.elementState().token;
  studio.state.generations.push(
    {id:'own-trial',project_id:'project',purpose:'piece_test',name:'Own result',seed:'42',piece_test:{kind:'item',token}},
    {id:'other-trial',project_id:'project',purpose:'piece_test',name:'Other result',seed:'42',piece_test:{kind:'item',token:'other-token'}});
  studio.renderGalleries();assert.match(element('piece-test-gallery').innerHTML,/Own result/);assert.doesNotMatch(element('piece-test-gallery').innerHTML,/Other result/);
  studio.createVisualElement('item');assert.notEqual(studio.elementState().token,token);
  assert.doesNotMatch(element('piece-test-gallery').innerHTML,/Own result|Other result/);
});

test('choosing a trial restores its snapshot before an explicit named save and retains exact identity settings',async()=>{
  const {studio,element,requests,context,database}=harness();
  database.catalog.loras=[{file:'identity.safetensors',name:'Identity'}];studio.state.catalog=database.catalog;
  studio.createVisualElement('character');const token=studio.elementState().token;
  const draft={kind:'character',name:'',prompt:'blue eyes',negative:'glasses',loras:[{file:'identity.safetensors',strength:0,enabled:true,trigger:'hero'}],tags:['portrait']};
  const record={id:'chosen-test',project_id:'project',purpose:'piece_test',seed:'18446744073709551615',negative:'effective negative',piece_test:{kind:'character',token,draft,specialized:true,context_prompt:'standing',context_negative:'cropped'},
    config:{model:'chosen.safetensors',width:1024,height:1024,steps:43,loras:[{file:'paint',strength:.7},{file:'identity.safetensors',strength:0}]},
    snapshot:{style:{id:'chosen-style',name:'Elegido',revision:3,prompt:'painted',negative:'grain',config:{...calibratedConfig,model:'chosen.safetensors'}},pieces:[{...draft,id:'draft-synthetic',revision:1,draft:true}]}};
  database.generations.push(record);element('piece-prompt').value='later unrelated edits';
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/generations/chosen-test'))return response(record);
    if(url.endsWith('/pieces')&&options.method==='POST'){const saved={...JSON.parse(options.body),id:'saved-identity',revision:1};database.pieces.push(saved);return response(saved);}
    return response(database);
  };
  await studio.capturePieceSample(record.id);
  assert.equal(element('piece-name').value,'');assert.equal(element('piece-prompt').value,'blue eyes');assert.equal(element('piece-negative').value,'glasses');
  assert.equal(element('piece-sample-id').value,'chosen-test');assert.equal(studio.elementState().style.id,'chosen-style');
  assert.equal(element('piece-save-details').open,true);
  assert.equal(studio.elementState().style.config.model,'chosen.safetensors');assert.equal(element('piece-seed').value,record.seed);
  assert.equal(studio.elementState().style.revision,3);
  assert.equal(requests.some(request=>request.url.endsWith('/pieces')),false);
  element('piece-name').value='Heroína';await studio.savePiece({preventDefault(){}});
  const saved=JSON.parse(requests.find(request=>request.url.endsWith('/pieces')).body);
  assert.equal(saved.seed,undefined);assert.equal(saved.prompt,'blue eyes');assert.equal(saved.sample_asset_id,'chosen-test');
  assert.deepEqual(saved.loras,draft.loras);assert.equal(saved.id,undefined);
});

for(const scenario of ['private edit','shared edit','new variant','different experiment'])test(`capturing an experimental sample preserves the correct save target for ${scenario}`,async()=>{
  const {studio,element,requests,context,database}=harness();
  const original={id:'editable-piece',kind:'character',project_id:scenario==='shared edit'?null:'project',name:'Identidad',revision:4,prompt:'original identity',seed:'42'};
  database.pieces.push(original);studio.openPiece(original,scenario==='new variant');
  element('piece-prompt').value='updated identity';
  element('piece-seed').value='18446744073709551615';
  Object.assign(studio.session,{active:true,status:'ready',socket:{readyState:1}});
  let record;
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/generate')){
      const payload=JSON.parse(options.body),draft=payload.piece_test.draft;
      record={id:'experiment-result',project_id:'project',purpose:'piece_test',name:draft.name,seed:payload.config.seed,
        piece_test:{...payload.piece_test,token:scenario==='different experiment'?'another-token':payload.piece_test.token},config:payload.config,
        snapshot:{project:{id:'project'},style:structuredClone(studio.elementState().style),pieces:[{...draft,id:'synthetic-draft',revision:1,draft:true}]}};
      database.generations.push(record);return response({job_id:'experiment-job'});
    }
    if(url.includes('/jobs/'))return response({status:'done',result:record});
    if(url.endsWith('/generations/experiment-result'))return response(record);
    if(url.includes('/generations?'))return response(database.generations);
    if(url.endsWith('/pieces')&&options.method==='POST'){
      const payload=JSON.parse(options.body),saved={...payload,id:payload.id||'new-variant',revision:payload.id?original.revision+1:1};
      if(payload.id)database.pieces[database.pieces.findIndex(piece=>piece.id===payload.id)]=saved;else database.pieces.push(saved);
      return response(saved);
    }
    return response(database);
  };
  const count=database.pieces.length;await studio.trialPiece();
  const generated=JSON.parse(requests.find(request=>request.url.endsWith('/generate')).body);
  assert.equal(generated.piece_test.source_piece_id,scenario==='new variant'?undefined:original.id);
  assert.equal(generated.piece_test.source_piece_revision,scenario==='new variant'?undefined:original.revision);
  assert.equal(requests.some(request=>request.url.endsWith('/pieces')),false);
  await studio.capturePieceSample(record.id);
  const retainsTarget=['private edit','shared edit'].includes(scenario);
  assert.equal(element('piece-id').value,retainsTarget?original.id:'');
  assert.equal(element('piece-shared').checked,scenario==='shared edit');
  await studio.savePiece({preventDefault(){}});
  const saved=JSON.parse(requests.find(request=>request.url.endsWith('/pieces')).body);
  assert.equal(saved.id,retainsTarget?original.id:undefined);assert.equal(saved.project_id,scenario==='shared edit'?null:'project');
  assert.equal(saved.seed,undefined);assert.equal(saved.prompt,'updated identity');assert.equal(saved.sample_asset_id,record.id);
  assert.equal(database.pieces.length,count+(retainsTarget?0:1));
  if(scenario==='new variant'){assert.equal(saved.master_id,original.id);assert.equal(saved.master_revision,original.revision);}
});


test('catalogue slash normalization retains identity activators, disabled state and a zero weight',()=>{
  const {studio,element,database}=harness();
  const identity={file:'Characters\\hero.safetensors',strength:0,enabled:false,trigger:'popular lookalike'};
  database.catalog.loras=[{file:'Characters/hero.safetensors',name:'Personaje'}];studio.state.catalog=database.catalog;
  const original={id:'identity-master',kind:'character',project_id:'project',name:'Identidad',revision:3,prompt:'blue eyes',loras:[identity]};
  database.pieces.push(original);studio.openPiece(original,true);
  assert.equal(studio.loras().getValue()[0].strength,0);
  const fields=studio.pieceFields();assert.equal(fields.loras.length,1);
  assert.equal(fields.loras[0].trigger,identity.trigger);assert.equal(fields.loras[0].enabled,false);assert.equal(fields.loras[0].strength,0);
  assert.equal(fields.loras[0].file.replace(/\\/g,'/'),'Characters/hero.safetensors');assert.deepEqual(original.loras,[identity]);
});

test('a new master captured from production copies only its explicit identity and presentation block',async()=>{
  const {studio,element,context,database}=harness();
  const identity={file:'Characters/hero.safetensors',strength:0,enabled:false,trigger:'hero lookalike'};
  database.catalog.loras=[{file:identity.file,name:'Identity'}];studio.state.catalog=database.catalog;
  const style={...database.styles[0],revision:2},definition={presentation:{framing:'portrait',view:'profile'}};
  const block={id:'old-variant',revision:9,master_id:'old-master',master_revision:3,kind:'character',name:'Heroína',prompt:'blue eyes',negative:'glasses',definition,loras:[identity],tags:['identity']};
  const record={id:'production-identity',project_id:'project',purpose:'production',category:'characters',seed:'18446744073709551615',prompt:'effective full prompt',negative:'effective negative',
    config:{...calibratedConfig,loras:[{file:'finish.safetensors',strength:.7},identity,{file:'unrelated.safetensors',strength:1}]},snapshot:{style,pieces:[block]}};
  database.generations.push(record);context.fetch=async()=>response(record);await studio.capturePieceSample(record.id);
  assert.equal(element('piece-id').value,'');const fields=studio.pieceFields();
  for(const key of ['id','revision','master_id','master_revision','generation_snapshot'])assert.equal(fields[key],undefined,key);
  assert.deepEqual(JSON.parse(JSON.stringify(fields.definition.presentation)),definition.presentation);
  assert.deepEqual(JSON.parse(JSON.stringify(fields.loras)),[identity]);assert.deepEqual(Array.from(fields.tags),block.tags);
  assert.equal(fields.prompt,block.prompt);assert.equal(fields.negative,block.negative);assert.equal(fields.seed,undefined);assert.equal(fields.sample_asset_id,record.id);
  assert.equal(studio.elementState().style.revision,2);
  // An effective graph may contain an identity LoRA without a typed identity
  // block. It does not authorize guessing which file belongs to a new master.
  record.id='production-without-identity';record.snapshot.pieces=[];database.generations.push(record);
  await studio.capturePieceSample(record.id);assert.deepEqual(Array.from(studio.pieceFields().loras),[]);
});

test('capturing a production image restores its style provenance and exact seed without showing generic calibration controls',async()=>{
  const {studio,element,context,database}=harness();
  const originalStyle={id:'style',revision:2,prompt:'painted finish',negative:'grain',config:{...calibratedConfig,model:'production-model.safetensors',steps:49}};
  const record={id:'production-source',project_id:'project',purpose:'production',category:'characters',seed:'18446744073709551615',prompt:'full prompt',
    config:{...originalStyle.config,width:1024,height:1536},snapshot:{style:originalStyle,pieces:[{id:'hero',kind:'character',name:'Heroína',prompt:'blue eyes'}]}};
  database.generations.push(record);context.fetch=async()=>response(record);await studio.capturePieceSample(record.id);
  assert.equal(studio.elementState().style.id,'style');assert.equal(studio.elementState().style.revision,2);
  assert.equal(element('piece-seed').value,record.seed);assert.equal(element('piece-prompt').value,'blue eyes');assert.equal(element('piece-sample-id').value,record.id);
});

test('reopening an element or variant preserves its design without binding the old preview style or seed',()=>{
  const {studio,element}=harness();
  const historic={id:'style',name:'Anterior',revision:2,prompt:'old finish',negative:'old grain',config:{...calibratedConfig,steps:39}};
  const piece={id:'saved-hero',project_id:'project',kind:'character',name:'Heroína',revision:2,prompt:'blue eyes',negative:'glasses',seed:'18446744073709551615',
    definition:{presentation:{framing:'portrait'},trial_negative:{style_id:'style',style_revision:2,text:'old restriction'}},
    generation_snapshot:{config:{...calibratedConfig,seed:'18446744073709551615'},snapshot:{style:historic,pieces:[]}}};
  const before=JSON.stringify(piece);
  for(const variant of [false,true]){
    studio.openPiece(piece,variant);
    assert.equal(studio.elementState().style.revision,5);assert.equal(element('piece-negative').value,'glasses');
    assert.equal(element('piece-prompt').value,'blue eyes');assert.equal(element('piece-seed').value,'42');
    assert.equal(element('piece-test-style').options.some(option=>option.value==='style@2'),false);
    assert.equal(studio.pieceFields().definition.trial_negative,undefined);
    assert.equal(studio.readElementPresentation().framing,'portrait');
  }
  assert.equal(JSON.stringify(piece),before);
});

for(const change of ['new experiment','closed editor','another project'])test(`a late sample response cannot overwrite ${change}`,async()=>{
  const {studio,element,context,database}=harness();studio.createVisualElement('character');
  const record={id:'late-sample',project_id:'project',purpose:'piece_test',seed:'42',piece_test:{kind:'character',draft:{kind:'character',prompt:'old description',name:''}},config:{model:'old-model'},snapshot:{pieces:[]}};
  database.generations.push(record);
  let resolveDetail;
  context.fetch=()=>new Promise(resolve=>{resolveDetail=resolve;});
  const pending=studio.capturePieceSample(record.id);await until(()=>!!resolveDetail);
  if(change==='new experiment')studio.createVisualElement('outfit');
  if(change==='closed editor')element('piece-dialog').close();
  if(change==='another project')studio.state.projectId='other-project';
  element('piece-prompt').value='keep this draft';const before=JSON.stringify(studio.elementState().style);
  resolveDetail(response(record));await pending;
  assert.equal(element('piece-prompt').value,'keep this draft');assert.equal(JSON.stringify(studio.elementState().style),before);
  assert.equal(element('piece-sample-id').value,'');
  if(change==='new experiment')assert.equal(element('piece-kind').value,'outfit');
  if(change==='closed editor')assert.equal(element('piece-dialog').open,false);
});

test('a free composition can be recovered and released with its captured model and exact seed',()=>{
  const {studio,element}=harness();
  const record={id:'free-source',category:'backgrounds',seed:'18446744073709551615',
    config:{model:'free-model.safetensors',width:1024,height:1024,steps:39,cfg:5,loras:[{file:'paint',strength:0}]},
    snapshot:{project:{id:'project'},style:null,pieces:[]},composition:{additional_prompt:'moonlit forest',additional_negative:'grain'}};
  studio.restoreComposition(record,'txt2img');
  const frozen=studio.productionPayload();assert.equal(frozen.source_asset_id,'free-source');assert.equal(frozen.style_id==null,true);
  assert.equal(frozen.config.model,'free-model.safetensors');assert.equal(frozen.config.seed,record.seed);assert.equal(element('prod-prompt').value,'moonlit forest');
  studio.releaseComposition(false);const released=studio.productionPayload();
  assert.equal(released.source_asset_id,undefined);assert.equal(released.style_id==null,true);assert.equal(released.config.model,'free-model.safetensors');
  assert.equal(released.config.seed,record.seed);assert.equal(released.prompt,'moonlit forest');assert.equal(released.config.steps,39);
});
async function until(predicate) {
  for(let step=0;step<30;step++) { if(predicate())return; await Promise.resolve(); }
  assert.ok(predicate(),'Expected request was never started.');
}

test('a variant inherits its exact master revision and own LoRA without claiming the preview or its seed', async()=>{
  const {studio,element,context,database,requests}=harness();
  const master={id:'master',revision:7,name:'Identidad',kind:'character',project_id:'project',prompt:'blue eyes',negative:'glasses',seed:'18446744073709551615',sample_asset_id:'preview',loras:[{file:'hero.safetensors',strength:.8,trigger:'hero'}]};
  studio.state.pieces.push(master);studio.openPiece(master,true);
  assert.equal(element('piece-kind').value,'character');assert.equal(studio.pieceFields().kind,'character');
  assert.equal(element('piece-seed').value,'42');assert.equal(element('piece-sample-id').value,'');
  element('piece-prompt').value='green eyes';
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/pieces')&&options.method==='POST')return response({...JSON.parse(options.body),id:'variant',revision:1});
    return response(database);
  };
  await studio.savePiece({preventDefault(){}});
  const body=JSON.parse(requests.find(r=>r.url.endsWith('/pieces')).body);
  assert.equal(body.master_id,'master');assert.equal(body.master_revision,7);assert.equal(body.seed,undefined);
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
  assert.equal(studio.productionPayload().integration,true);assert.equal(studio.productionPayload().style_revision,2);
  for(const key of ['cfg','steps','loras','model'])assert.equal(studio.productionPayload().config[key],undefined,key);
});

test('selecting a character never imports its preview seed and unrelated resources release the old composition',()=>{
  const {studio,element}=harness();
  studio.chooseProduction('characters');element('prod-style').value='style';
  studio.state.pieces[0].seed='18446744073709551615';
  const picker=element('piece-pickers').querySelector('[data-piece-kind="character"]');picker.value='hero';studio.productionPieceChanged(picker);
  assert.equal(studio.productionPayload().config.seed,'42');
  studio.state.sourceAssetId='old';studio.chooseProduction('backgrounds');
  assert.equal(studio.state.sourceAssetId,null);assert.equal(element('prod-style').disabled,false);
});

test('a variant can use the current preview style even when its original style was deleted',async()=>{
  const {studio,element,context,requests}=harness();
  const historical={id:'deleted-style',name:'Anterior',prompt:'old finish',revision:4,config:{...calibratedConfig,model:'historic.safetensors',seed:'999'}};
  const piece={id:'outfit',revision:2,kind:'outfit',name:'Uniforme',project_id:'project',prompt:'blue uniform',negative:'buttons',seed:'999',
    loras:[{file:'uniform.safetensors',strength:.7,trigger:'uniform'}],generation_snapshot:{config:historical.config,snapshot:{style:historical}}};
  studio.state.pieces.push(piece);studio.openPiece(piece,true);
  assert.equal(element('piece-test-style').value,'style@5');assert.equal(studio.elementState().style.config.model,calibratedConfig.model);
  Object.assign(studio.session,{active:true,status:'ready',socket:{readyState:1}});
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/generate'))return response({job_id:'test-job'});
    if(url.includes('/jobs/'))return response({status:'done',result:{id:'result'}});return response([]);
  };
  await studio.trialPiece();const payload=JSON.parse(requests.find(request=>request.url.endsWith('/generate')).body);
  assert.equal(payload.style_id,'style');assert.equal(payload.style_revision,5);assert.equal(payload.piece_test.specialized,true);
  assert.equal(payload.config.seed,'42');assert.equal(element('prod-seed').value,'42');
  for(const key of ['model','steps','loras','text_encoder','vae','cfg','sampler','scheduler'])assert.equal(payload.config[key],undefined,key);
  assert.equal(payload.piece_test.draft.master_id,'outfit');assert.equal(payload.piece_test.draft.master_revision,2);
  assert.deepEqual(payload.piece_test.draft.loras,piece.loras);assert.equal(payload.piece_test.draft.negative,'buttons');
  assert.deepEqual(payload.piece_ids,[]);assert.equal(element('piece-dialog').open,true);assert.equal(requests.some(request=>request.url.endsWith('/pieces')),false);
});

test('trying an unnamed unsaved element keeps the editor and frozen composition open and separates its result',async()=>{
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
  element('piece-name').value='';element('piece-prompt').value='small blue potion, glass bottle';
  element('piece-seed').value='12345678901234567890';
  const token=studio.elementState().token;
  Object.assign(studio.session,{active:true,status:'ready',socket:{readyState:1}});
  const result={id:'piece-test-result',project_id:'project',category:'items',purpose:'piece_test',mode:'txt2img',status:'pending',
    name:'Poción nueva',seed:'12345678901234567890',piece_test:{token,kind:'item'}};
  context.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET',body:options.body});
    if(url.endsWith('/bootstrap'))return response(database);
    if(url.endsWith('/generate')){database.generations.push(result);return response({job_id:'element-job'});}
    if(url.includes('/jobs/'))return response({status:'done',result});
    if(url.includes('/generations?'))return response(database.generations);
    return response({});
  };
  const piecesBefore=JSON.stringify(database.pieces);
  await studio.trialPiece();
  const payload=JSON.parse(requests.find(request=>request.url.endsWith('/generate')).body);
  assert.equal(payload.purpose,'piece_test');assert.equal(payload.category,'characters');
  assert.equal(payload.style_id,'style');assert.equal(payload.style_revision,5);
  assert.equal(payload.config.seed,'12345678901234567890');assert.deepEqual(payload.piece_ids,[]);
  assert.equal(payload.config.model,undefined);assert.equal(payload.config.steps,undefined);assert.equal(payload.config.loras,undefined);
  assert.equal(payload.piece_test.draft.name,'');assert.equal(payload.piece_test.token,token);
  assert.equal(requests.some(request=>request.url.endsWith('/pieces')),false);
  assert.equal(JSON.stringify(database.pieces),piecesBefore);assert.equal(element('piece-dialog').open,true);
  assert.equal(payload.source_asset_id,undefined);
  assert.equal(JSON.stringify(studio.productionPayload()),before);
  assert.equal(studio.state.productionCategory,'characters');assert.equal(studio.state.productionMode,'txt2img');
  assert.equal(element('prod-style').value,'style');assert.equal(element('prod-seed').value,'18446744073709551615');
  assert.equal(studio.state.recipeStyleRevision,2);
  assert.deepEqual(JSON.parse(JSON.stringify(studio.state.recipePieceRevisions)),{hero:1,dress:2});
  assert.equal(studio.state.sourceAssetId,'frozen-source');assert.equal(JSON.stringify(studio.state.sourceRecord),sourceBefore);
  assert.match(element('piece-test-gallery').innerHTML,/Poción nueva[\s\S]*12345678901234567890/);
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

test('frozen expression recipes are blocked and normal recipes store pieces and style instead of effective LoRAs',async()=>{
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
  assert.equal(payload.config.loras,undefined);assert.equal(payload.style_id,'style');assert.equal(payload.style_revision,2);
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
  assert.deepEqual(element('master-select').options.map(o=>o.value),['','portrait-result']);
  assert.doesNotMatch(element('review-gallery').innerHTML,/data-master="background-result"/);
  assert.match(element('review-gallery').innerHTML,/data-master="portrait-result"/);
  studio.chooseProduction('backgrounds');
  assert.match(element('production-gallery').innerHTML,/data-delete-image="background-result"/);
  assert.doesNotMatch(element('production-gallery').innerHTML,/Como maestro|data-master=/);
  assert.doesNotMatch(element('production-results-help').textContent,/Como maestro/);
});

test('facial masters exclude calibration and every type of element test even when they share the character category',()=>{
  const {studio,element}=harness();
  const base={project_id:'project',category:'characters',status:'pending'};
  const eligible=[
    {...base,id:'production-master',name:'Approved design',purpose:'production',status:'accepted'},
    {...base,id:'legacy-master',name:'Legacy design'},
    {...base,id:'previous-expression',name:'An expression selected explicitly',purpose:'production',mode:'expression'},
  ];
  const ineligible=[
    {...base,id:'calibration',purpose:'calibration'},
    {...base,id:'metadata-calibration',metadata:{purpose:'calibration'}},
    {...base,id:'marked-calibration',purpose:'production',calibration:{style_prompt:'quality'}},
    {...base,id:'metadata-marked-calibration',metadata:{calibration:{test_prompt:'portrait'}}},
    {...base,id:'rejected-master',purpose:'production',status:'rejected'},
    {...base,id:'purged-master',purpose:'production',purged_at:'2026-10-31'},
    {...base,id:'purged-flag',purpose:'production',purged:true},
    {...base,id:'foreign-master',purpose:'production',project_id:'another-project'},
    {...base,id:'background-master',purpose:'production',category:'backgrounds'},
    {...base,id:'object-master',purpose:'production',category:'items'},
    {...base,id:'metadata-element-test',metadata:{purpose:'piece_test'}},
    {...base,id:'marked-element-test',purpose:'production',piece_test:{kind:'character'}},
    {...base,id:'metadata-marked-element-test',metadata:{piece_test:{kind:'expression'}}},
    ...['character','personality','outfit','pose','expression','background','item','scene'].map(kind=>
      ({...base,id:`${kind}-test`,purpose:'piece_test',piece_test:{kind}})),
  ];
  studio.state.generations=[...eligible,...ineligible];
  studio.chooseProduction('characters','expression');
  for(const record of eligible)assert.equal(studio.isMasterCandidate(record),true,record.id);
  for(const record of ineligible)assert.equal(studio.isMasterCandidate(record),false,record.id);
  assert.deepEqual(element('master-select').options.map(option=>option.value).filter(Boolean).sort(),eligible.map(record=>record.id).sort());
  const html=element('review-gallery').innerHTML;
  for(const record of eligible)assert.match(html,new RegExp(`data-master="${record.id}"`));
  for(const record of ineligible)assert.doesNotMatch(html,new RegExp(`data-master="${record.id}"`));
});

test('an incompatible facial master cannot change the current master or its prepared mask context',async()=>{
  const {studio,element,requests}=harness();
  studio.state.projects[0].categories.push({id:'npcs',name:'Personajes'});
  studio.chooseProduction('characters','expression');
  const base={project_id:'project',category:'characters',status:'accepted'};
  const invalid=[
    {...base,id:'item-test-as-character',purpose:'piece_test',piece_test:{kind:'item'}},
    {...base,id:'calibration-as-character',purpose:'calibration'},
    {...base,id:'rejected-character',purpose:'production',status:'rejected'},
    {...base,id:'purged-character',purpose:'production',purged:true},
    {...base,id:'different-category',purpose:'production',category:'npcs'},
    {...base,id:'different-project',purpose:'production',project_id:'other'},
  ];
  studio.state.generations=invalid;
  Object.assign(studio.state,{masterId:'preserved-master',masterBase64:'preserved-upload',masterReady:true,masterToken:19});
  element('master-upload').value='preserved-file';element('prod-seed').value='77';
  const before=JSON.stringify({id:studio.state.masterId,data:studio.state.masterBase64,ready:studio.state.masterReady,token:studio.state.masterToken});
  for(const record of invalid){
    await assert.rejects(studio.selectMaster(record.id),/compatible|producción/);
    assert.equal(JSON.stringify({id:studio.state.masterId,data:studio.state.masterBase64,ready:studio.state.masterReady,token:studio.state.masterToken}),before);
    assert.equal(element('master-upload').value,'preserved-file');assert.equal(element('prod-seed').value,'77');
  }
  assert.equal(requests.length,0);
});

test('a valid legacy production portrait remains selectable as a facial master',async()=>{
  const {studio,element,context}=harness();
  const record={id:'legacy-portrait',project_id:'project',category:'characters',status:'accepted',seed:'18446744073709551615'};
  studio.state.generations=[record];studio.chooseProduction('characters','expression');
  context.Image=class {constructor(){this.naturalWidth=512;this.naturalHeight=768;} async decode(){}};
  await studio.selectMaster(record.id);
  assert.equal(studio.state.masterId,record.id);assert.equal(studio.state.masterReady,true);
  assert.equal(element('prod-seed').value,record.seed);
  assert.equal(element('master-canvas').width,512);assert.equal(element('mask-canvas').height,768);
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
