const assert=require('node:assert/strict');
const {test}=require('node:test');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const Profiles=require('../static/anima_element_profiles.js');
const KINDS=['character','outfit','pose','expression','background','item','personality','scene'];

test('every specialised profile exposes presentation controls without calibration or dimensions',()=>{
  for(const kind of KINDS){
    const p=Profiles.profile(kind);
    assert.deepEqual(Object.keys(p).sort(),['controls','help','label','orientation','placeholder']);
    assert.ok(p.help&&p.label&&p.placeholder);
    for(const c of p.controls){assert.ok(c.key&&c.label&&c.options.length);for(const o of c.options)assert.deepEqual(Object.keys(o).sort(),['label','prompt','value']);}
  }
  assert.deepEqual(Profiles.profile('personality').controls,[]);
});

test('returned profile definitions are independent, including nested controls and scene aliases',()=>{
  const a=Profiles.profile('background'),b=Profiles.profile('scene');
  a.controls[0].options[0].label='Changed';a.controls.pop();b.controls[0].key='changed';
  assert.equal(Profiles.profile('background').controls.length,3);
  assert.equal(Profiles.profile('scene').controls[0].key,'light');
  assert.equal(Profiles.profile('scene').controls[0].options[0].label,'Según la descripción');
});

test('saved presentation restores only recognised choices without changing unrelated piece data',()=>{
  const definition={presentation:{framing:'waist-up',view:'profile',model:'forged'},identity:{age:'adult'}};
  const before=structuredClone(definition),result=Profiles.values('character',definition);
  assert.deepEqual(result,{framing:'waist-up',view:'profile'});result.view='back';
  assert.deepEqual(definition,before);
});

test('invalid, missing and malformed saved choices fall back to useful type defaults',()=>{
  for(const definition of [undefined,null,[],{},'bad',{presentation:null},{presentation:[]},{presentation:{view:'panorama',framing:false}}])
    assert.deepEqual(Profiles.values('character',definition),{framing:'full-body',view:'front'});
  assert.deepEqual(Profiles.values('personality',{presentation:{extra:'ignored'}}),{});
});

test('character framing avoids contradictory full-body instructions for close portraits',()=>{
  const full=Profiles.context('character',{framing:'full-body',view:'front'});
  const waist=Profiles.context('character',{framing:'waist-up',view:'threequarter'});
  const portrait=Profiles.context('character',{framing:'portrait',view:'profile'});
  assert.match(full.prompt,/entire figure visible including feet/);
  assert.match(waist.prompt,/Waist-up/);assert.match(waist.prompt,/Three-quarter/);
  assert.doesNotMatch(waist.prompt,/entire figure|including feet/);
  assert.match(portrait.prompt,/Close-up/);assert.match(portrait.prompt,/Side profile/);
  assert.doesNotMatch(portrait.prompt,/entire figure|including feet/);
});

test('every visible choice contributes its declared direction to the actual trial context',()=>{
  for(const kind of KINDS){
    const p=Profiles.profile(kind),defaults=Profiles.values(kind);
    for(const c of p.controls)for(const o of c.options){
      const values={...defaults,[c.key]:o.value},before=structuredClone(values),context=Profiles.context(kind,values);
      if(o.prompt)assert.ok(context.prompt.includes(o.prompt),`${kind}/${c.key}/${o.value}`);
      assert.deepEqual(values,before);assert.ok(context.negative);
    }
  }
});

test('outfits, backgrounds, objects, poses and expressions have task-specific presentation',()=>{
  assert.match(Profiles.context('outfit',{view:'back'}).prompt,/mannequin[\s\S]*Back view/);
  const background=Profiles.context('background',{light:'night',weather:'rain',space:'left'});
  assert.match(background.prompt,/Night-time[\s\S]*Rainy[\s\S]*space on the left/);
  assert.match(background.negative,/people/);
  assert.match(Profiles.context('item',{view:'threequarter',presentation:'icon'}).prompt,/Three-quarter[\s\S]*Inventory icon/);
  assert.match(Profiles.context('pose',{view:'profile'}).prompt,/selected posture[\s\S]*Side profile/);
  assert.match(Profiles.context('expression',{intensity:'subtle'}).prompt,/front-facing[\s\S]*Subtle restrained/);
});

test('automatic formats follow the resource type when no project format fits',()=>{
  for(const kind of ['character','outfit','pose'])assert.deepEqual(Profiles.format({},kind),{width:1024,height:1536});
  for(const kind of ['background','scene'])assert.deepEqual(Profiles.format({},kind),{width:1536,height:864});
  for(const kind of ['item','expression','personality'])assert.deepEqual(Profiles.format({},kind),{width:1024,height:1024});
});

test('character and outfit can never inherit a panoramic project format despite its name',()=>{
  const project={formats:[{id:'character',name:'Personaje VN',width:1536,height:864},
    {id:'other',name:'Retrato',width:768,height:1152}]};
  const before=structuredClone(project);
  assert.deepEqual(Profiles.format(project,'character'),{width:768,height:1152});
  assert.deepEqual(Profiles.format(project,'outfit'),{width:768,height:1152});
  assert.deepEqual(project,before);
  assert.deepEqual(Profiles.format({formats:project.formats.slice(0,1)},'character'),{width:1024,height:1536});
});

test('invalid dimensions, pixel area and shape are rejected before semantic ranking',()=>{
  const formats=[
    {id:'character',width:256,height:4096},{id:'character',width:2048,height:2048},
    {id:'character',width:'1024',height:1536},{id:'character',width:1025,height:1536},
    {id:'character',width:128,height:192},{id:'character',width:true,height:1536},
    {id:'portrait',width:1536,height:2048},
  ];
  assert.deepEqual(Profiles.format({formats},'character'),{width:1536,height:2048});
  assert.deepEqual(Profiles.format({formats:[{id:'character',width:2048,height:2032}]},'background'),{width:1536,height:864});
});

test('valid project formats prefer the named type after enforcing its orientation',()=>{
  const project={formats:[{id:'portrait',width:768,height:1152},{id:'character',width:1024,height:1536},
    {id:'background',width:1280,height:720},{id:'item',width:512,height:512}]};
  assert.deepEqual(Profiles.format(project,'character'),{width:1024,height:1536});
  assert.deepEqual(Profiles.format(project,'background'),{width:1280,height:720});
  assert.deepEqual(Profiles.format(project,'item'),{width:512,height:512});
  assert.deepEqual(Profiles.format({settings:{formats:project.formats}},'background'),{width:1280,height:720});
});

test('negative defaults merge style, legacy piece and presentation without repeated tags',()=>{
  const style={id:'style',revision:3,negative:'bad quality, blurry\ntext'},piece={negative:' BLURRY, extra fingers'};
  const before=structuredClone({style,piece});
  assert.equal(Profiles.negative(style,piece,'text, watermark'),'bad quality, blurry, text, extra fingers, watermark');
  assert.deepEqual({style,piece},before);
});

test('legacy style-scoped trial negatives never become the piece contribution or erase the chosen style',()=>{
  const style={id:'style',revision:3,negative:'bad quality'},piece={negative:'legacy',definition:{trial_negative:{style_id:'style',style_revision:3,text:'  custom\nnegative  '}}};
  const before=structuredClone(piece);
  assert.equal(Profiles.negative(style,piece,'text'),'bad quality, legacy, text');
  piece.definition.trial_negative.text='';assert.equal(Profiles.negative(style,piece,'text'),'bad quality, legacy, text');
  piece.definition.trial_negative.style_revision=2;
  assert.equal(Profiles.negative(style,piece,'text'),'bad quality, legacy, text');
  piece.definition.trial_negative.style_revision=3;piece.definition.trial_negative.style_id='other';
  assert.equal(Profiles.negative(style,piece,'text'),'bad quality, legacy, text');
  assert.equal(before.definition.trial_negative.text,'  custom\nnegative  ');
});

test('malformed or unpinned negative overrides never erase inherited negatives',()=>{
  for(const override of [null,[],{}, {style_id:'style',style_revision:3,text:null},
    {style_id:'style',style_revision:'3',text:''},{style_id:'style',text:''}]){
    assert.equal(Profiles.negative({id:'style',revision:3,negative:'inherited'},{definition:{trial_negative:override}},'text'),'inherited, text');
  }
  assert.equal(Profiles.negative({id:'style',negative:'inherited'},{definition:{trial_negative:{style_id:'style',text:''}}},'text'),'inherited, text');
});

test('unknown kinds fail explicitly and browser import exposes the same UMD API',()=>{
  for(const method of [()=>Profiles.profile('nope'),()=>Profiles.values('nope'),()=>Profiles.context('nope'),()=>Profiles.format({},'nope')])assert.throws(method,/Tipo de elemento/);
  const scope=vm.createContext({});vm.runInContext(readFileSync(path.join(__dirname,'../static/anima_element_profiles.js'),'utf8'),scope);
  assert.equal(typeof scope.AnimaElementProfiles.context,'function');
  assert.deepEqual(JSON.parse(JSON.stringify(scope.AnimaElementProfiles.values('character'))),{framing:'full-body',view:'front'});
});
