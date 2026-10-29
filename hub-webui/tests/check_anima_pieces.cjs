const assert=require('node:assert/strict');
const {test}=require('node:test');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const Pieces=require('../static/anima_pieces.js');

test('variants copy the pinned design and exact seed, independently of a masters next revision',()=>{
  const master={id:'hero',revision:4,kind:'character',name:'Heroína',project_id:'game',prompt:'blue eyes',negative:'glasses',seed:'18446744073709551615',
    loras:[{file:'hero.safetensors',strength:.8}],sample_asset_id:'old-preview',generation_snapshot:{config:{cfg:4}}};
  const variant=Pieces.variant(master);
  assert.equal(variant.master_id,'hero');assert.equal(variant.master_revision,4);
  assert.equal(variant.seed,'18446744073709551615');assert.equal(variant.sample_asset_id,undefined);
  variant.prompt='green eyes';variant.loras[0].strength=.5;variant.generation_snapshot.config.cfg=3;
  assert.equal(master.prompt,'blue eyes');assert.equal(master.loras[0].strength,.8);assert.equal(master.generation_snapshot.config.cfg,4);
});
test('composition recovery preserves effective config and historical piece pins without reading current library',()=>{
  const record={id:'image',category:'characters',seed:'18446744073709551615',config:{width:1024,height:1536,cfg:4,loras:[{file:'identity',strength:.7}]},
    snapshot:{style:{id:'style',revision:2},pieces:[{id:'hero',kind:'character',revision:3}]},
    composition:{additional_prompt:'full body',additional_negative:'cropped'}};
  const restored=Pieces.composition(record);
  assert.equal(restored.config.seed,record.seed);assert.equal(restored.style_revision,2);assert.equal(restored.piece_revisions.hero,3);
  assert.equal(restored.additional_prompt,'full body');assert.equal(restored.additional_known,true);
  restored.config.loras[0].strength=1;assert.equal(record.config.loras[0].strength,.7);
});
test('legacy composition stays explicit about missing prompt segmentation and rejects lost seeds',()=>{
  const record={id:'old',seed:'42',snapshot:{style:{id:'style'},pieces:[]}};
  assert.equal(Pieces.composition(record).additional_known,false);
  assert.throws(()=>Pieces.composition({...record,seed:'18446744073709551616'}),/seed exacta/);
  assert.throws(()=>Pieces.composition({...record,seed:'-1'}),/seed exacta/);
  assert.throws(()=>Pieces.composition({...record,seed:9007199254740992}),/seed exacta/);
  assert.throws(()=>Pieces.composition({...record,snapshot:{}}),/estilo/);
});
test('piece editors have distinct descriptions and browser API works without CommonJS',()=>{
  const context=vm.createContext({});
  vm.runInContext(readFileSync(path.join(__dirname,'../static/anima_pieces.js'),'utf8'),context);
  assert.match(context.AnimaPieces.profile('outfit').help,/maniquí/);
  assert.match(context.AnimaPieces.profile('background').help,/clima/);
  assert.match(context.AnimaPieces.profile('expression').help,/sustituye este bloque/);
  const first=Pieces.profile('character');first.help='changed';assert.notEqual(Pieces.profile('character').help,'changed');
});
