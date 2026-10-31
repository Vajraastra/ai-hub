const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const Calibration = require('../static/anima_calibration.js');
const AnimaProduction = require('../static/anima_production.js');
const AnimaElementProfiles = require('../static/anima_element_profiles.js');
const AnimaPieces = require('../static/anima_pieces.js');
const source = readFileSync(path.join(__dirname, '../static/anima_studio.js'), 'utf8');

const plain = value => JSON.parse(JSON.stringify(value));
const response = json => ({ok:true,async text(){ return JSON.stringify(json); }});
function sample(values={}) {
  return {id:'sample',project_id:'project',purpose:'calibration',name:'Calibración',seed:'18446744073709551615',
    config:{model:'anima\\Aesthetic.safetensors',text_encoder:'qwen.safetensors',vae:'vae.safetensors',
      loras:[{file:'ink\\line.safetensors',strength:0},{file:'finish\\paint.safetensors',strength:-.35}],
      steps:36,cfg:0,sampler:'euler',scheduler:'simple',width:1024,height:1536,seed:'18446744073709551615'},
    calibration:{style_prompt:'painted lines',style_negative:'grain',test_prompt:'a heroine in a forest'},
    prompt:'painted lines\na heroine in a forest',negative:'grain',...values};
}

test('calibration cards retain full model/LoRA paths, every weight and effective comparison settings',()=>{
  const html=Calibration.details(sample());
  for(const value of ['Aesthetic.safetensors','ink\\line.safetensors','finish\\paint.safetensors',
    '-0.35','18446744073709551615','36','1024','1536','euler','simple'])assert.ok(html.includes(value),value);
  assert.match(html,/CFG[\s\S]*?0/);
  assert.match(html,/line\.safetensors[\s\S]*?0/);
});

test('metadata-only imported records expose their original settings and keep test scene separate',()=>{
  const original=sample(),record={id:'imported',project_id:'project',metadata:{config:original.config,
    calibration:original.calibration,prompt:original.prompt,negative:original.negative,seed:original.seed}};
  const snapshot=Calibration.snapshot(record);
  assert.equal(snapshot.config.model,original.config.model);
  assert.equal(snapshot.prompt,'painted lines');
  assert.equal(snapshot.negative,'grain');
  assert.equal(snapshot.testPrompt,'a heroine in a forest');
  assert.equal(snapshot.hasCalibration,true);
});

test('style payload is made from the chosen sample, excluding its test subject and unrelated record fields',()=>{
  const record=sample({status:'rejected',snapshot:{pieces:[{name:'Not a style'}]}});
  record.config.prompt='A scene stored by an older renderer';record.config.source_asset_id='unrelated-master';
  const before=plain(record),payload=Calibration.stylePayload(record,{projectId:'project',name:'  Tinta  '});
  assert.equal(payload.name,'Tinta');
  assert.equal(payload.project_id,'project');
  assert.equal(payload.prompt,'painted lines');
  assert.equal(payload.negative,'grain');
  assert.equal(payload.config.seed,'18446744073709551615');
  assert.deepEqual(payload.config.loras,record.config.loras);
  assert.deepEqual(payload.sample_ids,['sample']);
  assert.equal(payload.id,undefined);
  assert.equal(payload.status,undefined);
  assert.equal(payload.snapshot,undefined);
  assert.equal(payload.test_prompt,undefined);
  assert.equal(payload.config.prompt,undefined);assert.equal(payload.config.source_asset_id,undefined);
  assert.deepEqual(record,before);
});

test('independent styles from one sample never silently revise the previously saved style',()=>{
  const record=sample(),a=Calibration.stylePayload(record,{projectId:'project',name:'Líneas'}),
    b=Calibration.stylePayload(record,{projectId:'project',name:'Pintura',shared:true});
  assert.equal(a.id,undefined);assert.equal(b.id,undefined);
  assert.equal(a.project_id,'project');assert.equal(b.project_id,null);
  assert.deepEqual(a.config,b.config);assert.deepEqual(a.sample_ids,b.sample_ids);
  assert.notEqual(a.name,b.name);
});

test('revising an existing style requires an explicit target and still uses the sample settings',()=>{
  const target={id:'old-style',project_id:'project',name:'Anterior',config:{steps:5}},
    payload=Calibration.stylePayload(sample(),{projectId:'project',name:'Revisado',targetStyle:target});
  assert.equal(payload.id,'old-style');assert.equal(payload.config.steps,36);
  assert.deepEqual(payload.sample_ids,['sample']);
});

test('an explicit revision puts its new calibration first while retaining prior samples without duplicates',()=>{
  const target={id:'old-style',project_id:'project',sample_ids:['old-sample','sample','old-sample','other-sample']},
    before=plain(target),payload=Calibration.stylePayload(sample(),{projectId:'project',name:'Revisado',targetStyle:target});
  assert.deepEqual(payload.sample_ids,['sample','old-sample','other-sample']);
  assert.deepEqual(target,before);
  assert.deepEqual(Calibration.stylePayload(sample(),{projectId:'project',name:'Nuevo'}).sample_ids,['sample']);
});

test('cross-project, deleted and incomplete samples cannot become styles with guessed settings',()=>{
  const options={projectId:'project',name:'Estilo'};
  assert.throws(()=>Calibration.stylePayload(sample({project_id:'other'}),options),/proyecto/i);
  assert.throws(()=>Calibration.stylePayload(sample({purged_at:'2026-10-29'}),options),/disponible|borrad/i);
  assert.throws(()=>Calibration.stylePayload(sample({config:{steps:36}}),options),/modelo/i);
  assert.throws(()=>Calibration.stylePayload(sample(),{...options,shared:true,
    targetStyle:{id:'private-style',project_id:'project'}}),/proyecto|revisión/i);
});

test('editing one saved payload cannot rewrite the original sample or another saved style',()=>{
  const record=sample(),before=plain(record),options={projectId:'project',name:'Estilo'};
  const a=Calibration.stylePayload(record,options),b=Calibration.stylePayload(record,options);
  a.config.loras[0].strength=2;a.config.loras.push({file:'new.safetensors',strength:1});
  assert.deepEqual(record,before);assert.deepEqual(b.config.loras,before.config.loras);
  assert.equal(b.config.seed,'18446744073709551615');
});

test('legacy missing settings are labelled as unrecorded while a recorded empty LoRA list is explicit',()=>{
  const missing=Calibration.details({id:'legacy',config:{}}),empty=Calibration.details({id:'none',config:{loras:[]}});
  assert.match(missing,/No registrad/);assert.match(empty,/Sin LoRAs/);
  assert.doesNotMatch(missing,/Sin LoRAs/);
});

test('legacy records never reinterpret the complete calibration scene as reusable style prompt',()=>{
  const record=sample({calibration:undefined});
  const snapshot=Calibration.snapshot(record);
  assert.equal(snapshot.prompt,'');assert.equal(snapshot.hasCalibration,false);
  assert.equal(snapshot.testPrompt,record.prompt);
  const payload=Calibration.stylePayload(record,{projectId:'project',name:'Legado'});
  assert.equal(payload.prompt,'');assert.equal(payload.negative,'grain');
  assert.deepEqual(payload.config.loras,record.config.loras);
});

test('stored metadata is escaped in the comparison card instead of becoming executable markup',()=>{
  const record=sample();record.config.model='<svg onload="attack()">';
  record.config.loras[0].file='<img src=x onerror="attack()">';
  record.calibration.style_prompt='<script>attack()</script>';
  const html=Calibration.details(record);
  assert.doesNotMatch(html,/<svg|<img src=x|<script>/);
  assert.match(html,/&lt;svg/);assert.match(html,/&lt;img/);
});

function harness() {
  const nodes=new Map(),requests=[],saves=[],projectSaves=[];
  const control={failProject:false};
  const selectIds=new Set(['cal-style','prod-style','style-save-target','sampler','scheduler','piece-kind',
    'piece-filter','piece-master','piece-lora','piece-test-style','piece-test-format']);
  function makeNode(id,isSelect=selectIds.has(id)||id.endsWith('-select')) {
    const classes=new Set(),nested=new Map();let html='',value='';
    return {id,dataset:{},attrs:{},options:[],children:[],checked:false,disabled:false,textContent:'',open:false,
      get value(){return value;},set value(next){value=String(next??'');if(isSelect&&!this.options.some(o=>o.value===value))value='';},
      classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),
        toggle(c,force){const next=force??!classes.has(c);next?classes.add(c):classes.delete(c);return next;}},
      setAttribute(k,v){this.attrs[k]=String(v);},getAttribute(k){return this.attrs[k]??this[k]??null;},
      removeAttribute(k){delete this.attrs[k];delete this[k];},addEventListener(){},focus(){},
      getContext(){return {clearRect(){},drawImage(){}};},close(){this.open=false;this.onclose?.();},
      showModal(){this.open=true;},reset(){},add(option){this.options.push(option);if(option.selected||this.options.length===1)value=String(option.value);},
      replaceChildren(...children){this.children=children;html='';},append(child){this.children.push(child);},
      querySelectorAll(selector){return selector==='select'?this.children.filter(child=>child.isSelect):[];},
      querySelector(selector){if(!nested.has(selector))nested.set(selector,makeNode(selector));return nested.get(selector);},
      isSelect,get innerHTML(){return html;},set innerHTML(next){html=next;this.children=[];
        if(isSelect){this.options=[...next.matchAll(/<option(?: value="([^"]*)")?[^>]*>([^<]*)<\/option>/g)].map(m=>({value:m[1]??m[2],text:m[2]}));value=String(this.options[0]?.value??'');}}
    };
  }
  function element(id){if(!nodes.has(id))nodes.set(id,makeNode(id));return nodes.get(id);}
  const tabs=['styles','production','integrator','library'].map(name=>Object.assign(makeNode(`tab-${name}-button`),{dataset:{tab:name}}));
  const pages=['styles','production','integrator','library'].map(name=>element(`tab-${name}`));
  const database={projects:[{id:'project',name:'Prueba',categories:['characters'],formats:[]}],
    styles:[],pieces:[],recipes:[],generations:[sample()],catalog:{models:[{file:'anima\\Aesthetic.safetensors',name:'Aesthetic'}],loras:[],text_encoders:[],vaes:[]}};
  const context=vm.createContext({console,AnimaCalibration:Calibration,AnimaProduction,AnimaElementProfiles,AnimaPieces,
    WebSocket:{OPEN:1},localStorage:{getItem(){return null;},setItem(){}},
    document:{getElementById:element,createElement:makeNode,addEventListener(){},querySelector(){return null;},
      querySelectorAll(selector){return selector==='.tab'?tabs:selector==='.page'?pages:[];},body:{append(){}}},
    window:{confirm(){return true;}},Option:function(text,value){this.text=text;this.value=value;},
    async fetch(url,options={}){requests.push({url,method:options.method||'GET',body:options.body});
      if(url.endsWith('/projects')&&options.method==='POST'){
        if(control.failProject)return {ok:false,async text(){return JSON.stringify({detail:'No se pudo crear el proyecto'});}};
        const payload=JSON.parse(options.body),saved={...payload,id:`project-${projectSaves.length+1}`,revision:1};
        projectSaves.push(saved);database.projects.push(saved);return response(saved);
      }
      if(url.endsWith('/styles')&&options.method==='POST'){
        const payload=JSON.parse(options.body),saved={...payload,id:payload.id||`style-${saves.length+1}`,revision:1};
        saves.push(saved);database.styles.push(saved);return response(saved);
      }
      if(url.endsWith('/bootstrap'))return response(database);
      if(url.includes('/generations/'))return response(database.generations.find(g=>url.endsWith('/'+g.id)));
      return response(database.generations);
    }});
  vm.runInContext(source.replace('  init();',
    '  globalThis.studio = {state,assetCard,openCalibrationStyle,saveCalibrationStyle,calibrationStyleTargetChanged,forgetDeleted,resetWorkspace,resetCalibrationDashboard,createProject,wireEvents};'),context);
  Object.assign(context.studio.state,database,{projectId:'project',projects:plain(database.projects)});
  element('cal-seed').value='42';element('steps').value='25';element('cfg').value='4';
  return {studio:context.studio,element,requests,saves,projectSaves,database,context,control};
}

test('calibration image cards expose their own save action and comparison data',()=>{
  const {studio}=harness(),html=studio.assetCard(sample(),'cal');
  assert.match(html,/data-save-calibration="sample"/);
  assert.match(html,/Aesthetic\.safetensors/);assert.match(html,/paint\.safetensors/);
  assert.match(html,/-0\.35/);assert.match(html,/18446744073709551615/);
});

test('saving from a sample keeps its immutable config after the live dashboard changes',async()=>{
  const {studio,element,saves}=harness();
  await studio.openCalibrationStyle('sample');
  assert.equal(element('style-dialog').open,true);
  element('style-name').value='Muestra original';
  element('model-select').options=[{value:'changed-model'}];element('model-select').value='changed-model';
  element('steps').value='9';element('cfg').value='7';element('cal-seed').value='12';
  element('style-prompt').value='Changed current prompt';element('test-prompt').value='Changed test';
  await studio.saveCalibrationStyle({preventDefault(){}});
  assert.equal(saves.length,1);assert.equal(saves[0].config.model,'anima\\Aesthetic.safetensors');
  assert.equal(saves[0].config.steps,36);assert.equal(saves[0].config.cfg,0);
  assert.equal(saves[0].config.seed,'18446744073709551615');
  assert.equal(saves[0].prompt,'painted lines');assert.equal(saves[0].negative,'grain');
  assert.deepEqual(saves[0].sample_ids,['sample']);assert.equal(saves[0].id,'style-1');
  assert.equal(element('steps').value,'9');assert.equal(element('cal-seed').value,'12');
  assert.equal(element('style-prompt').value,'Changed current prompt');
});

test('opening the same card twice starts two independent styles rather than accidental revisions',async()=>{
  const {studio,element,saves}=harness();
  for(const name of ['Tinta fina','Tinta suave']){
    await studio.openCalibrationStyle('sample');
    assert.equal(element('style-save-target').value,'');element('style-name').value=name;
    await studio.saveCalibrationStyle({preventDefault(){}});
  }
  assert.equal(saves.length,2);assert.notEqual(saves[0].id,saves[1].id);
  assert.deepEqual(saves.map(s=>s.name),['Tinta fina','Tinta suave']);
});

test('an in-flight sample load cannot open a style dialog in a different project',async()=>{
  const {studio,element,context,database}=harness();let resolve;
  context.fetch=()=>new Promise(done=>{resolve=()=>done(response(database.generations[0]));});
  const loading=studio.openCalibrationStyle('sample');
  studio.state.projectId='another-project';studio.resetWorkspace();resolve();await loading;
  assert.equal(element('style-dialog').open,false);
});

test('an image deleted during its detail request cannot return as a saveable style source',async()=>{
  const {studio,element,context,database}=harness();const record=plain(database.generations[0]);let resolve;
  context.fetch=()=>new Promise(done=>{resolve=()=>done(response(record));});
  const loading=studio.openCalibrationStyle('sample');
  studio.forgetDeleted('generations','sample');resolve();await loading;
  assert.equal(element('style-dialog').open,false);assert.equal(studio.state.generations.length,0);
});

test('double submission creates one style while its save request is still in flight',async()=>{
  const {studio,element,saves,context}=harness();
  await studio.openCalibrationStyle('sample');element('style-name').value='Solo uno';
  const fetch=context.fetch;let release;
  context.fetch=(url,options={})=>url.endsWith('/styles')&&options.method==='POST'
    ?new Promise(resolve=>{release=async()=>resolve(await fetch(url,options));}):fetch(url,options);
  const first=studio.saveCalibrationStyle({preventDefault(){}});
  assert.equal(element('save-style').disabled,true);
  await studio.saveCalibrationStyle({preventDefault(){}});
  await release();await first;
  assert.equal(saves.length,1);assert.equal(element('save-style').disabled,false);
});

test('choosing a revision explicitly preserves its scope and earlier samples while using the selected config',async()=>{
  const {studio,element,saves,database}=harness();
  database.styles.push({id:'old-style',project_id:'project',name:'Viejo',revision:4,
    config:{steps:6},sample_ids:['old-sample']});
  await studio.openCalibrationStyle('sample');
  element('style-save-target').value='old-style';studio.calibrationStyleTargetChanged();
  assert.equal(element('style-name').value,'Viejo');assert.equal(element('style-shared').disabled,true);
  await studio.saveCalibrationStyle({preventDefault(){}});
  assert.equal(saves[0].id,'old-style');assert.equal(saves[0].project_id,'project');
  assert.equal(saves[0].config.steps,36);assert.deepEqual(saves[0].sample_ids,['sample','old-sample']);
});

test('saving a revision does not restore one of its earlier samples deleted while the dialog was open',async()=>{
  const {studio,element,saves,database}=harness();
  database.styles.push({id:'old-style',project_id:'project',name:'Viejo',revision:4,
    config:{steps:6},sample_ids:['old-sample','deleted-sample','sample','old-sample']});
  await studio.openCalibrationStyle('sample');
  element('style-save-target').value='old-style';studio.calibrationStyleTargetChanged();
  studio.forgetDeleted('generations','deleted-sample');
  await studio.saveCalibrationStyle({preventDefault(){}});
  assert.deepEqual(saves[0].sample_ids,['sample','old-sample']);
});

test('resetting the dashboard clears the calibration while retaining stored project samples',()=>{
  const {studio,element}=harness();
  element('style-prompt').value='painted lines';element('style-negative').value='grain';
  element('test-prompt').value='forest';element('steps').value='90';element('cfg').value='20';
  element('cal-seed').value='999';element('lora-list').children.push({id:'old-lora'});
  studio.resetCalibrationDashboard();
  assert.equal(element('style-prompt').value,'');assert.equal(element('style-negative').value,'');
  assert.equal(element('test-prompt').value,'');assert.equal(element('lora-list').children.length,0);
  assert.notEqual(element('steps').value,'90');assert.notEqual(element('cfg').value,'20');
  assert.notEqual(element('cal-seed').value,'999');assert.equal(studio.state.generations.length,1);
});

test('the new project button starts a default calibration workspace without opening a setup dialog',async()=>{
  const {studio,element,projectSaves}=harness();
  studio.wireEvents();
  await element('new-project').onclick({preventDefault(){}});
  assert.equal(projectSaves.length,1);
  assert.equal(element('project-dialog').open,false);assert.equal(element('style-dialog').open,false);
  assert.equal(element('tab-styles').classList.contains('active'),true);
  const saved=projectSaves[0];
  assert.match(saved.name,/^Proyecto \d+$/);
  assert.ok(Array.isArray(saved.categories)&&saved.categories.length>0);
  assert.ok(Array.isArray(saved.formats)&&saved.formats.length>0);
  assert.ok(saved.formats.every(format=>format.width>0&&format.height>0));
  assert.equal(saved.retention_days,30);
});

test('automatic project names remain unique across existing and newly created projects',async()=>{
  const {studio,database,projectSaves}=harness();
  database.projects.push({id:'used-1',name:'  PROYECTO 1  '},{id:'used-3',name:'Proyecto 3'});
  studio.state.projects=plain(database.projects);
  await studio.createProject();await studio.createProject();
  const names=projectSaves.map(project=>project.name);
  assert.equal(new Set(names).size,2);
  for(const name of names){assert.notEqual(name,'Proyecto 1');assert.notEqual(name,'Proyecto 3');}
  assert.equal(studio.state.projectId,projectSaves[1].id);
});

test('successful project creation resets the editor and preserves the previous projects and calibration records',async()=>{
  const {studio,element,requests,database,projectSaves}=harness();
  const oldProject=plain(database.projects[0]),oldSamples=plain(database.generations);
  element('style-prompt').value='existing finish';element('style-negative').value='grain';
  element('test-prompt').value='existing scene';element('steps').value='90';element('cal-seed').value='999';
  studio.state.sampleIds.add('sample');
  await studio.createProject();
  assert.equal(studio.state.projectId,projectSaves[0].id);
  assert.deepEqual(plain(database.projects[0]),oldProject);
  assert.deepEqual(plain(studio.state.generations),oldSamples);
  assert.equal(element('style-prompt').value,'');assert.equal(element('test-prompt').value,'');
  assert.equal(element('steps').value,'30');assert.equal(element('cal-seed').value,'42');
  assert.equal(studio.state.sampleIds.size,0);
  assert.equal(requests.some(request=>request.method==='DELETE'),false);
});

test('failed project creation leaves the active project and unsaved calibration intact and permits retry',async()=>{
  const {studio,element,database,projectSaves,control}=harness();
  const oldProjects=plain(database.projects),oldSamples=plain(database.generations);
  element('style-prompt').value='existing finish';element('test-prompt').value='existing scene';
  element('steps').value='90';element('cal-seed').value='999';studio.state.sampleIds.add('sample');
  control.failProject=true;
  await assert.rejects(studio.createProject(),/No se pudo crear el proyecto/);
  assert.equal(studio.state.projectId,'project');assert.equal(projectSaves.length,0);
  assert.deepEqual(plain(database.projects),oldProjects);assert.deepEqual(plain(studio.state.generations),oldSamples);
  assert.equal(element('style-prompt').value,'existing finish');assert.equal(element('test-prompt').value,'existing scene');
  assert.equal(element('steps').value,'90');assert.equal(element('cal-seed').value,'999');
  assert.equal(studio.state.sampleIds.has('sample'),true);assert.equal(element('new-project').disabled,false);
  control.failProject=false;await studio.createProject();assert.equal(projectSaves.length,1);
});

test('double activation while project creation is pending produces one project',async()=>{
  const {studio,element,context,projectSaves}=harness();
  const originalFetch=context.fetch;let release;
  context.fetch=(url,options={})=>url.endsWith('/projects')&&options.method==='POST'
    ?new Promise(resolve=>{release=async()=>resolve(await originalFetch(url,options));}):originalFetch(url,options);
  const first=studio.createProject();assert.equal(element('new-project').disabled,true);
  await studio.createProject();await release();await first;
  assert.equal(projectSaves.length,1);assert.equal(element('new-project').disabled,false);
});
