const assert=require('node:assert/strict');
const {test}=require('node:test');
const Prompts=require('../static/anima_prompt_library.js');

const plain=value=>JSON.parse(JSON.stringify(value));
const preset=values=>({id:'prompt-one',name:'Tinta',style_prompt:'painted lines',style_negative:'grain',test_prompt:'a traveler',revision:1,...values});
function harness(records=[],sharedStore) {
  const nodes=new Map(),calls=[],notices=[],applied=[],confirmations=[];
  const store=sharedStore||{records:plain(records),next:1};
  const control={readError:null,saveError:null,deleteError:null,confirm:true};
  let fields={style_prompt:'  soft\npaint\r\n',style_negative:'  blur  ',test_prompt:' garden '};
  const generation={model:'checkpoint.safetensors',loras:[{file:'paint.safetensors',strength:.4}],seed:'18446744073709551615'};
  function makeNode(id='') {
    let value='';
    return {id,options:[],children:[],textContent:'',disabled:false,open:false,attrs:{},
      get value(){return value;},set value(next){value=String(next??'');if(id==='prompt-library-select'&&!this.options.some(option=>option.value===value))value='';},
      setAttribute(key,next){this.attrs[key]=next;},focus(){},
      replaceChildren(...children){this.children=children;if(id==='prompt-library-select'){this.options=children;value=String(children[0]?.value??'');}},
      showModal(){this.open=true;},close(){if(this.open){this.open=false;this.onclose?.();}},
    };
  }
  function element(id){if(!nodes.has(id))nodes.set(id,makeNode(id));return nodes.get(id);}
  async function defaultApi(path,method='GET',body) {
    calls.push({path,method,body:body===undefined?undefined:plain(body)});
    if(method==='GET'){
      if(control.readError)throw new Error(control.readError);
      return plain(store.records);
    }
    if(method==='POST'){
      if(control.saveError)throw new Error(control.saveError);
      const old=store.records.find(record=>record.id===body.id);
      if(body.id&&(!old||old.revision!==body.expected_revision))throw new Error('La configuración cambió; recarga antes de actualizar.');
      const saved={...plain(body),id:body.id||`prompt-${store.next++}`,revision:old?old.revision+1:1};
      store.records=[saved,...store.records.filter(record=>record.id!==saved.id)];return plain(saved);
    }
    if(method==='DELETE'){
      if(control.deleteError)throw new Error(control.deleteError);
      const id=decodeURIComponent(path.split('/').pop().split('?')[0]);
      store.records=store.records.filter(record=>record.id!==id);return {id,deleted:true};
    }
  }
  let apiOverride=null;
  const app=Prompts.create({api:(...args)=>(apiOverride||defaultApi)(...args),notice:(message,error)=>notices.push({message,error}),
    document:{getElementById:element,createElement:()=>makeNode()},confirm:message=>{confirmations.push(message);return control.confirm;},
    getFields:()=>fields,setFields:value=>{fields=plain(value);applied.push(plain(value));}});
  function select(id){element('prompt-library-select').value=id;element('prompt-library-select').onchange();}
  return {app,element,store,calls,control,notices,applied,confirmations,generation,select,
    get fields(){return fields;},set fields(value){fields=value;},setApi(value){apiOverride=value;},defaultApi};
}

test('initialization loads named global prompts without applying them or changing generation settings',async()=>{
  const h=harness([preset()]),before=plain(h.fields),settings=plain(h.generation);
  await h.app.init();
  assert.deepEqual(h.calls.map(call=>[call.path,call.method]),[['/prompts','GET']]);
  assert.equal(h.element('prompt-library-select').options[1].textContent,'Tinta');
  assert.deepEqual(h.fields,before);assert.deepEqual(h.generation,settings);assert.equal(h.applied.length,0);
  assert.equal(h.element('prompt-library-apply').disabled,true);
});

test('selection alone preserves the editor and explicit apply restores all three texts including empty ones',async()=>{
  const h=harness([preset({style_prompt:'  ink\r\nline\n',style_negative:'',test_prompt:' scene '})]);
  const before=plain(h.fields),settings=plain(h.generation);await h.app.init();h.select('prompt-one');
  assert.deepEqual(h.fields,before);assert.equal(h.applied.length,0);
  h.app.apply();
  assert.deepEqual(h.fields,{style_prompt:'  ink\r\nline\n',style_negative:'',test_prompt:' scene '});
  assert.deepEqual(h.generation,settings);assert.equal(h.applied.length,1);
});

test('saving creates an independent named snapshot without trimming prompt text or taking later editor changes',async()=>{
  const h=harness([preset()]);await h.app.init();h.select('prompt-one');
  const snapshot=plain(h.fields);h.app.openSave();h.element('prompt-save-name').value='  Favorito  ';
  h.fields={style_prompt:'later',style_negative:'later',test_prompt:'later'};
  await h.app.submit({preventDefault(){}});
  const saved=h.calls.find(call=>call.method==='POST').body;
  assert.equal(saved.name,'Favorito');assert.equal('id' in saved,false);assert.equal('expected_revision' in saved,false);
  for(const key of ['style_prompt','style_negative','test_prompt'])assert.equal(saved[key],snapshot[key]);
  assert.equal(h.store.records.length,2);assert.equal(h.store.records.some(record=>record.name==='Tinta'),true);
  assert.equal(h.element('prompt-save-dialog').open,false);assert.equal(h.fields.style_prompt,'later');
});

test('a restarted controller can load and apply a previously saved configuration',async()=>{
  const first=harness();await first.app.init();first.app.openSave();first.element('prompt-save-name').value='Persistente';
  const expected=plain(first.fields);await first.app.submit();
  const second=harness([],first.store);await second.app.init();
  assert.equal(second.applied.length,0);second.select(first.store.records[0].id);second.app.apply();
  assert.deepEqual(second.fields,expected);assert.equal(second.element('prompt-library-select').options[1].textContent,'Persistente');
});

test('updating is explicit and captures the selected id and revision rather than silently matching a name',async()=>{
  const h=harness([preset(),preset({id:'other',name:'Otro',revision:4})]);await h.app.init();h.select('prompt-one');
  h.app.openSave('update');assert.equal(h.element('prompt-save-name').value,'Tinta');
  h.select('other');h.element('prompt-save-name').value='Tinta revisada';await h.app.submit();
  const payload=h.calls.find(call=>call.method==='POST').body;
  assert.equal(payload.id,'prompt-one');assert.equal(payload.expected_revision,1);
  assert.equal(h.store.records.find(record=>record.id==='prompt-one').revision,2);
  assert.equal(h.store.records.find(record=>record.id==='other').revision,4);
});

test('search matches names and prompt content without losing a selected configuration',async()=>{
  const h=harness([preset({name:'Lápiz'}),preset({id:'other',name:'Agua',test_prompt:'bosque nocturno'})]);
  await h.app.init();h.select('prompt-one');h.element('prompt-library-search').value='BOSQUE';h.element('prompt-library-search').oninput();
  assert.deepEqual(h.element('prompt-library-select').options.map(option=>option.value),['','prompt-one','other']);
  assert.equal(h.element('prompt-library-select').value,'prompt-one');assert.equal(h.applied.length,0);
  h.element('prompt-library-search').value='lapiz';h.element('prompt-library-search').oninput();
  assert.deepEqual(h.element('prompt-library-select').options.map(option=>option.value),['','prompt-one']);
});

test('untrusted names and prompts are written as literal text in options and save previews',async()=>{
  const attack='<img src=x onerror="attack()">',h=harness([preset({name:attack})]);
  h.fields={style_prompt:'<script>attack()</script>',style_negative:'&',test_prompt:'"'};
  await h.app.init();h.select('prompt-one');h.app.openSave('update');
  assert.equal(h.element('prompt-library-select').options[1].textContent,attack);
  assert.match(h.element('prompt-save-preview').textContent,/<script>attack\(\)<\/script>/);
  assert.equal('innerHTML' in h.element('prompt-save-preview'),false);
  assert.equal(h.element('prompt-save-name').value,attack);
});

test('canceling save does not mutate the library or editor and the discarded snapshot cannot be submitted',async()=>{
  const h=harness([preset()]),before=plain(h.fields);await h.app.init();h.app.openSave();h.element('prompt-save-dialog').close();
  await assert.rejects(h.app.submit(),/Abre el guardado/);
  assert.equal(h.calls.some(call=>call.method==='POST'),false);assert.deepEqual(h.fields,before);
});

test('empty text and missing names do not send an invalid save request',async()=>{
  const h=harness();await h.app.init();h.fields={style_prompt:' \n ',style_negative:'',test_prompt:'\t'};
  assert.throws(()=>h.app.openSave(),/al menos un prompt/);
  h.fields.style_prompt='ink';h.app.openSave();h.element('prompt-save-name').value='   ';
  await assert.rejects(h.app.submit(),/nombre/);assert.equal(h.calls.some(call=>call.method==='POST'),false);
  assert.equal(h.element('prompt-save-dialog').open,true);
});

test('failed save and revision conflict retain the modal snapshot and leave saved data untouched',async()=>{
  const h=harness([preset()]);await h.app.init();h.select('prompt-one');h.app.openSave('update');
  h.element('prompt-save-name').value='Revisado';h.control.saveError='Ya existe un nombre igual';
  await assert.rejects(h.app.submit(),/Ya existe/);
  assert.equal(h.element('prompt-save-dialog').open,true);assert.equal(h.element('prompt-save-submit').disabled,false);
  assert.equal(h.element('prompt-save-name').value,'Revisado');assert.equal(h.store.records[0].name,'Tinta');
  h.control.saveError=null;h.store.records[0].revision=2;
  await assert.rejects(h.app.submit(),/cambió/);assert.equal(h.element('prompt-save-dialog').open,true);
  assert.equal(h.store.records[0].name,'Tinta');
});

test('initial load failure leaves calibration and save available and reload recovers the library',async()=>{
  const h=harness([preset()]),before=plain(h.fields);h.control.readError='Sin conexión';await h.app.init();
  assert.match(h.element('prompt-library-status').textContent,/Recargar/);
  assert.equal(h.element('prompt-library-status').attrs.role,'alert');assert.equal(h.element('prompt-library-save').disabled,false);
  assert.deepEqual(h.fields,before);h.control.readError=null;await h.element('prompt-library-refresh').onclick();
  assert.equal(h.element('prompt-library-select').options.length,2);assert.equal(h.applied.length,0);
});

test('delete requires named confirmation and never clears the texts currently open in calibration',async()=>{
  const h=harness([preset()]),before=plain(h.fields);await h.app.init();h.select('prompt-one');h.control.confirm=false;
  await h.app.remove();assert.equal(h.calls.some(call=>call.method==='DELETE'),false);
  assert.match(h.confirmations[0],/Tinta/);h.control.confirm=true;await h.app.remove();
  const request=h.calls.find(call=>call.method==='DELETE');
  assert.equal(request.path,'/prompts/prompt-one?expected_revision=1');assert.equal(h.store.records.length,0);
  assert.deepEqual(h.fields,before);assert.equal(h.element('prompt-library-apply').disabled,true);
});

test('failed delete keeps the selected preset available and reports its error through the bound action',async()=>{
  const h=harness([preset()]);await h.app.init();h.select('prompt-one');h.control.deleteError='Archivo bloqueado';
  await h.element('prompt-library-delete').onclick();
  assert.equal(h.store.records.length,1);assert.equal(h.element('prompt-library-select').value,'prompt-one');
  assert.match(h.notices.at(-1).message,/Archivo bloqueado/);assert.equal(h.notices.at(-1).error,true);
  assert.equal(h.element('prompt-library-delete').disabled,false);
});

test('double submit produces one mutation and closing a pending save does not reopen its dialog',async()=>{
  const h=harness();await h.app.init();h.app.openSave();h.element('prompt-save-name').value='Único';
  let release;h.setApi((...args)=>args[1]==='POST'?new Promise(resolve=>{release=async()=>resolve(await h.defaultApi(...args));}):h.defaultApi(...args));
  const first=h.app.submit();assert.equal(h.element('prompt-save-submit').disabled,true);await h.app.submit();
  h.element('prompt-save-dialog').close();await release();await first;
  assert.equal(h.calls.filter(call=>call.method==='POST').length,1);assert.equal(h.element('prompt-save-dialog').open,false);
  assert.equal(h.element('prompt-save-submit').disabled,false);assert.equal(h.store.records.length,1);
});

test('a list response started before a save cannot replace the newly saved preset with stale data',async()=>{
  const h=harness([preset()]);await h.app.init();let deliver;
  h.setApi((...args)=>args[1]===undefined?new Promise(resolve=>{deliver=()=>resolve([preset()]);}):h.defaultApi(...args));
  const reading=h.app.refresh();h.app.openSave();h.element('prompt-save-name').value='Nuevo';await h.app.submit();
  deliver();await reading;
  assert.equal(h.element('prompt-library-select').options.some(option=>option.textContent==='Nuevo'),true);
  assert.equal(h.element('prompt-library-refresh').disabled,false);
});

test('a stale list cannot restore a preset deleted while its request was in flight',async()=>{
  const h=harness([preset()]);await h.app.init();h.select('prompt-one');let deliver;
  h.setApi((...args)=>args[1]===undefined?new Promise(resolve=>{deliver=()=>resolve([preset()]);}):h.defaultApi(...args));
  const reading=h.app.refresh();await h.app.remove();deliver();await reading;
  assert.equal(h.element('prompt-library-select').options.length,1);
  assert.equal(h.element('prompt-library-apply').disabled,true);assert.equal(h.element('prompt-library-refresh').disabled,false);
});

test('a failed read invalidated by saving cannot replace success status or emit a stale error',async()=>{
  const h=harness();await h.app.init();let fail;
  h.setApi((...args)=>args[1]===undefined?new Promise((resolve,reject)=>{fail=()=>reject(new Error('Lectura antigua fallida'));}):h.defaultApi(...args));
  const reading=h.element('prompt-library-refresh').onclick();
  h.app.openSave();h.element('prompt-save-name').value='Guardado reciente';await h.app.submit();
  fail();await reading;
  assert.match(h.element('prompt-library-status').textContent,/Guardado reciente.*guardado/);
  assert.equal(h.notices.some(notice=>notice.error),false);
  assert.equal(h.element('prompt-library-refresh').disabled,false);
});

test('the same controller can initialize twice without applying prompts or duplicating event actions',async()=>{
  const h=harness([preset()]);await h.app.init();await h.app.init();h.select('prompt-one');
  await h.element('prompt-library-apply').onclick();assert.equal(h.applied.length,1);
  assert.equal(h.calls.filter(call=>call.method==='GET').length,2);
});
