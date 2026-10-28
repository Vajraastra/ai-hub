const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const sourcePath = path.join(__dirname,'../static/anima_prompt_guide.js');
const {formatTag,profileFor,addTags,init} = require(sourcePath);

test('Anima formatting uses spaces and lowercase while score syntax remains intact', () => {
  assert.equal(formatTag(' BLUE_HAIR '),'blue hair');
  assert.equal(formatTag('SCORE_7'),'score_7');
  assert.equal(formatTag('@Artist_Name'),'@artist name');
  assert.equal(formatTag('Artist_Name',{category:1}),'@artist name');
  assert.equal(formatTag('Artist_Name',{category:8},'danbooru_e621_merged_2024'),'@artist name');
  assert.equal(formatTag('Contributor_Name',{category:9},'danbooru_e621_merged_2024'),'contributor name');
  assert.equal(formatTag('Other_Tag',{category:8},'e621_2026'),'other tag');
  assert.equal(formatTag('foo_\\(series_name\\)'),'foo \\(series name\\)');
});

test('Base follows the official scored preset without inserting content classification as quality', () => {
  const profile=profileFor('anima-base-v1.0.safetensors');
  assert.equal(profile.id,'base');
  assert.deepEqual(profile.quality,['masterpiece','best quality','score_7']);
  assert.deepEqual(profile.negative,['worst quality','low quality','score_1','score_2','score_3','artist name','blurry','jpeg artifacts','chromatic aberration']);
  assert.ok(profile.source.startsWith('https://huggingface.co/circlestone-labs/Anima/'));
  assert.match(profile.order,/@artista/);assert.match(profile.example,/safe/);
  assert.equal((profile.example.split('\n')[1].match(/\./g)||[]).length,2);
});

test('Aesthetic has optional quality and no scores in either recommendation', () => {
  const profile=profileFor('Anima Aesthetic v1.0b');
  assert.equal(profile.id,'aesthetic');assert.equal(profile.optionalQuality,true);
  assert.deepEqual(profile.quality,['masterpiece','best quality']);
  assert.ok([...profile.quality,...profile.negative].every(tag=>!tag.startsWith('score_')));
});

test('WAI 1.0 uses its current author recipe and preview remains explicitly distinct', () => {
  for(const name of ['WAI Anima · v1.0','waiANIMA_v10Base10.safetensors','WAI Anima base 1.0']) {
    const profile=profileFor(name);assert.equal(profile.id,'wai-1.0');
    assert.equal(profile.source,'https://tensor.art/models/1003360262834670526');
    assert.deepEqual(profile.quality,['masterpiece','best quality','score_7']);
    assert.deepEqual(profile.negative,['worst quality','low quality','score_1','score_2','score_3','artist name','blurry','jpeg artifacts','lowres','censor']);
  }
  for(const name of ['WAI Anima preview3 v1.0','WAI_Anima_pw3']) {
    const profile=profileFor(name);assert.equal(profile.id,'wai-preview');
    assert.match(profile.label,/guía general/);assert.ok(!profile.quality.includes('score_7'));
  }
});

test('opaque and unknown model identities keep a clearly marked generic recommendation', () => {
  for(const name of ['','checkpoints::7d9.safetensors','WAI Anima','Anima custom mix']) {
    const profile=profileFor(name);assert.equal(profile.id,'generic');
    assert.match(profile.label,/guía general/);assert.ok(!profile.quality.includes('score_7'));
  }
});

test('adding quality is idempotent across underscores, whitespace and numeric weights', () => {
  const original='(best_quality:1.2), ((masterpiece:1.1):1.3), score 7, blue_hair';
  assert.equal(addTags(original,['masterpiece','best quality','score_7']),original);
  assert.equal(addTags(' [best quality], blue_hair',['best_quality','masterpiece','masterpiece']),'masterpiece,  [best quality], blue_hair');
});

test('adding suggestions preserves original text byte for byte after the missing prefix', () => {
  const original='  A blue-haired hero stands beside a fountain.\n(blue_hair:1.3), soft lighting  ';
  const result=addTags(original,['masterpiece','best quality']);
  assert.equal(result,'masterpiece, best quality, '+original);
  assert.equal(addTags(result,['masterpiece','best quality']),result);
  assert.equal(addTags('',['score_7','score 7']),'score_7');
  assert.equal(addTags(original,[]),original);
});

function browserHarness() {
  const nodes=new Map();let mutation;
  function node(id) {
    if(!nodes.has(id))nodes.set(id,{textContent:'',value:'',selectionStart:0,selectionEnd:0,handlers:{},events:[],
      addEventListener(name,fn){this.handlers[name]=fn;},removeEventListener(name){delete this.handlers[name];},
      dispatchEvent(event){this.events.push(event.type);},focus(){},setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},
    });
    return nodes.get(id);
  }
  node('choose-model').querySelector=selector=>node(selector.endsWith('strong')?'model-title':'model-file');
  class MutationObserver {constructor(callback){mutation=callback;}observe(){}disconnect(){mutation=null;}}
  const window={document:{getElementById:node},MutationObserver,Event:class Event {constructor(type){this.type=type;}}};
  vm.runInNewContext(readFileSync(sourcePath,'utf8'),{window});
  return {node,guide:window.AnimaPromptGuide,mutate:()=>mutation?.()};
}

test('model changes update guidance without touching prompts until a button is clicked', () => {
  const app=browserHarness();app.node('model-title').textContent='Anima Base';
  app.node('style-prompt').value='blue_hair';app.node('style-negative').value='old negative';
  const controller=app.guide.init();assert.equal(app.guide.init(),controller);
  assert.match(app.node('anima-quality-tags').textContent,/score_7/);
  app.node('model-title').textContent='Anima Aesthetic';app.mutate();
  assert.doesNotMatch(app.node('anima-quality-tags').textContent,/score_/);
  assert.equal(app.node('style-prompt').value,'blue_hair');assert.equal(app.node('style-negative').value,'old negative');
  app.node('anima-add-quality').handlers.click();
  assert.equal(app.node('style-prompt').value,'masterpiece, best quality, blue_hair');
  assert.deepEqual(app.node('style-prompt').events,['input']);
  app.node('anima-add-quality').handlers.click();
  assert.equal(app.node('style-prompt').value,'masterpiece, best quality, blue_hair');
  assert.match(app.node('anima-prompt-guide-status').textContent,/ya estaban/);
  controller.destroy();assert.equal(app.node('anima-add-quality').handlers.click,undefined);
});

test('initialization is harmless outside a page containing the guide', () => {
  assert.equal(init(),null);
});
