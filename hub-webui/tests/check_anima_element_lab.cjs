const assert = require('node:assert/strict');
const {test} = require('node:test');
const Lab = require('../static/anima_element_lab.js');

const project = {id:'project',categories:['characters'],formats:[]};
const config = {model:'base.safetensors',text_encoder:'encoder.safetensors',vae:'vae.safetensors',
  loras:[{file:'paint.safetensors',strength:0,enabled:false}],steps:30,cfg:4,sampler:'er_sde',scheduler:'simple',
  width:1024,height:1024,seed:'18446744073709551615'};
const style = {id:'style',revision:5,prompt:'painted light',negative:'grain',config};
const makePayload = values => Lab.payload({project,draft:{kind:'character',name:'',prompt:'blue eyes',negative:'glasses'},token:'trial-1',style,seed:config.seed,dimensions:{width:1024,height:1536},...values});

test('all element kinds can be tried before naming or saving while inheriting a calibrated style', () => {
  for (const kind of ['character','outfit','pose','expression','background','item','personality','scene']) {
    const result = makePayload({draft:{kind,name:'',prompt:'new design'}});
    assert.equal(result.purpose,'piece_test');assert.equal(result.mode,'txt2img');assert.deepEqual(result.piece_ids,[]);
    assert.equal(result.style_id,'style');assert.equal(result.style_revision,5);assert.equal(result.format_id,undefined);
    assert.equal(result.piece_test.kind,kind);assert.equal(result.piece_test.draft.name,'');
    assert.equal(result.piece_test.token,'trial-1');assert.equal(result.piece_test.reuse_master_settings,false);
    assert.deepEqual(result.config,{seed:config.seed,width:1024,height:1536});assert.equal(result.piece_test.specialized,true);
  }
});

test('trial payload detaches persistent identifiers and history without mutating a variant draft', () => {
  const draft = {id:'old-piece',revision:4,kind:'character',name:'',prompt:'blue eyes',
    master_id:'master',master_revision:7,created_at:'old',updated_at:'new',generation_snapshot:{id:'old-image'},
    sample_asset_id:'old-image',seed:'18446744073709551615',loras:[{file:'identity.safetensors',strength:.8,trigger:'hero'}]};
  const original = structuredClone(draft), result = makePayload({draft});
  for (const key of ['id','revision','created_at','updated_at','generation_snapshot']) assert.equal(result.piece_test.draft[key],undefined);
  assert.equal(result.piece_test.draft.master_id,'master');assert.equal(result.piece_test.draft.master_revision,7);
  assert.deepEqual(result.piece_test.draft.loras,draft.loras);assert.equal(result.piece_test.draft.seed,draft.seed);
  assert.deepEqual(draft,original);
  result.piece_test.draft.loras[0].strength=3;result.config.width=512;
  assert.deepEqual(draft,original);assert.equal(config.loras[0].file,'paint.safetensors');
});

test('quality belongs to the selected preview style while the piece contributes its own negative and context', () => {
  const before=structuredClone(style), result=makePayload({contextPrompt:'full body, sunset',contextNegative:'cropped'});
  assert.equal(result.prompt,'');assert.equal(result.negative,'');assert.equal(result.negative_override,undefined);
  assert.equal(result.piece_test.draft.prompt,'blue eyes');
  assert.equal(result.piece_test.draft.negative,'glasses');
  assert.equal(result.piece_test.context_prompt,'full body, sunset');assert.equal(result.piece_test.context_negative,'cropped');
  assert.equal(result.piece_test.style_prompt,undefined);assert.equal(result.piece_test.style_negative,undefined);
  assert.deepEqual(style,before);assert.equal(result.config.model,undefined);assert.equal(result.config.loras,undefined);
  assert.equal(result.config.steps,undefined);assert.equal(result.config.text_encoder,undefined);assert.equal(result.config.vae,undefined);
});

test('a calibrated style is required to render trials but an empty piece negative never overrides its quality rules', () => {
  for(const invalid of [null,{}, {id:'style',config}, {id:'style',revision:0,config}, {id:'style',revision:1,config:{}}]) {
    assert.throws(()=>makePayload({style:invalid}),/estilo|calibr/i);
  }
  const result=makePayload({draft:{kind:'character',prompt:'blue eyes',negative:''}});
  assert.equal(result.negative_override,undefined);
  assert.equal(result.piece_test.draft.negative,'');
  assert.equal(style.negative,'grain');
});

test('trial category is organizational and accepts existing object category identifiers', () => {
  const result=makePayload({project:{id:'project',categories:[{id:'custom-output',name:'Personalizado'}]}});
  assert.equal(result.category,'custom-output');
  assert.throws(() => makePayload({project:null}),/proyecto/);
  assert.throws(() => makePayload({draft:{kind:'unknown'}}),/tipo/);
});

test('test gallery matching uses only the active trial token and supports old saved-piece tests', () => {
  assert.equal(Lab.matches({piece_test:{token:'trial-1',id:'old-piece'}},'trial-1'),true);
  assert.equal(Lab.matches({metadata:{piece_test:{token:'trial-1'}}},'trial-1'),true);
  assert.equal(Lab.matches({piece_test:{token:'trial-2'}},'trial-1'),false);
  assert.equal(Lab.matches({piece_test:{id:'old-piece'}},'old-piece'),true);
  assert.equal(Lab.matches({piece_test:{token:''}},''),false);
  assert.equal(Lab.matches({},'trial-1'),false);
});

function trialRecord(extra={}) {
  const draft={kind:'character',name:'',prompt:'blue eyes',negative:'glasses',seed:'9',
    loras:[{file:'Characters\\hero.safetensors',strength:0,enabled:true,trigger:'hero'}],tags:['portrait']};
  return {id:'selected-image',seed:'18446744073709551615',piece_test:{kind:'character',token:'trial-1',draft,
    style_prompt:'painted strokes',style_negative:'grain',context_prompt:'standing outdoors',context_negative:'cropped'},
    negative:'effective negative',snapshot:{style:structuredClone(style),pieces:[{...structuredClone(draft),id:'draft-only',revision:1,draft:true}]},
    config:{...structuredClone(config),loras:[{file:'paint.safetensors',strength:.6},{file:'characters/hero.safetensors',strength:0}]},...extra};
}

test('choosing a result recovers its own exact seed, prompt and identity LoRA without adding context to the design', () => {
  const record=trialRecord(), original=structuredClone(record), result=Lab.sample(record);
  assert.equal(result.draft.id,undefined);assert.equal(result.draft.revision,undefined);assert.equal(result.draft.draft,undefined);
  assert.equal(result.draft.name,'');assert.equal(result.draft.prompt,'blue eyes');assert.equal(result.draft.negative,'glasses');
  assert.equal(result.draft.seed,'18446744073709551615');assert.equal(result.draft.sample_asset_id,'selected-image');
  assert.deepEqual(result.draft.loras,record.piece_test.draft.loras);
  assert.deepEqual(result.config.loras,[{file:'paint.safetensors',strength:.6}]);
  assert.equal(result.config.seed,'18446744073709551615');assert.equal(result.stylePrompt,'painted strokes');
  assert.deepEqual(result.style,style);assert.equal(result.negative,'glasses');assert.equal(result.effectiveNegative,'effective negative');
  assert.equal(result.styleNegative,'grain');assert.equal(result.contextPrompt,'standing outdoors');assert.equal(result.contextNegative,'cropped');
  assert.deepEqual(record,original);result.draft.tags.push('changed');assert.deepEqual(record,original);
});

test('old saved-piece samples retain master identifiers while recovering the captured configuration', () => {
  const record=trialRecord();record.snapshot.pieces[0]={id:'hero',revision:3,kind:'character',name:'Heroína',prompt:'blue eyes',
    master_id:'parent',master_revision:7,generation_snapshot:{asset_id:'previous'},loras:[]};
  const result=Lab.sample(record);
  assert.equal(result.draft.id,'hero');assert.equal(result.draft.revision,3);assert.equal(result.draft.name,'Heroína');
  assert.equal(result.draft.master_id,'parent');assert.equal(result.draft.master_revision,7);
  assert.equal(result.draft.generation_snapshot,undefined);assert.equal(result.config.seed,record.seed);
});

test('sample recovery reads metadata-only records and falls back to preserved draft fields', () => {
  const record=trialRecord(), metadata=structuredClone(record);delete metadata.snapshot;
  const result=Lab.sample({id:record.id,metadata});
  assert.equal(result.draft.prompt,'blue eyes');assert.equal(result.draft.seed,record.seed);
  assert.equal(result.config.model,'base.safetensors');assert.equal(result.stylePrompt,'painted strokes');
  assert.throws(() => Lab.sample({id:'broken',piece_test:{kind:'outfit'}}),/descripción/);
});

test('pose-only drafts retain structured control even without a descriptive prompt', () => {
  const pose={enabled:true,model:'control.safetensors',strength:1};
  const result=makePayload({draft:{kind:'pose',name:'',prompt:'',pose_control:pose}});
  assert.deepEqual(result.piece_test.draft.pose_control,pose);assert.deepEqual(result.config.pose_control,pose);assert.equal(result.piece_test.draft.prompt,'');
});


test('legacy samples without a saved style remain explicit instead of inventing one from their model settings', () => {
  const record=trialRecord();record.snapshot.style=null;
  assert.equal(Lab.sample(record).style,null);
});


test('source piece references are explicit and separate from an unsaved draft or a new variant',()=>{
  const source={id:'saved-piece',revision:7,kind:'character',generation_snapshot:{asset_id:'previous'}};
  const before=structuredClone(source),result=makePayload({sourcePiece:source});
  assert.equal(result.piece_test.source_piece_id,'saved-piece');assert.equal(result.piece_test.source_piece_revision,7);
  assert.equal(result.piece_test.draft.id,undefined);assert.equal(result.piece_test.draft.generation_snapshot,undefined);
  assert.deepEqual(source,before);
  for(const values of [{},{draft:{kind:'character',master_id:'saved-piece',master_revision:7}}]){
    const fresh=makePayload(values);assert.equal(fresh.piece_test.source_piece_id,undefined);assert.equal(fresh.piece_test.source_piece_revision,undefined);
  }
});

test('every piece type can contribute LoRAs and be previewed under another style without acquiring either style',()=>{
  const second={id:'watercolour',revision:2,prompt:'soft watercolour',negative:'hard edges',
    config:{...structuredClone(config),model:'another.safetensors',loras:[{file:'water.safetensors',strength:.7}]}};
  for(const kind of ['character','outfit','pose','expression','background','item','personality','scene']){
    const draft={kind,name:'Reusable',prompt:'piece content',negative:'piece exclusion',
      loras:[{file:`pieces/${kind}.safetensors`,strength:0,trigger:`trigger_${kind}`,enabled:true}],
      definition:{presentation:{view:'threequarter'}}};
    const before=structuredClone(draft),first=makePayload({draft}),next=makePayload({draft,style:second});
    assert.equal(first.style_id,'style');assert.equal(next.style_id,'watercolour');
    assert.deepEqual(first.piece_test.draft,next.piece_test.draft);
    assert.deepEqual(next.piece_test.draft.loras,draft.loras);
    assert.equal(next.piece_test.draft.style_id,undefined);assert.equal(next.piece_test.draft.style_revision,undefined);
    assert.equal(next.piece_test.draft.config,undefined);assert.equal(next.negative_override,undefined);
    assert.deepEqual(draft,before);
  }
});
