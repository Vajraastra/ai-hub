const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const source = readFileSync(path.join(__dirname, '../static/anima_tags.js'), 'utf8');
const html = readFileSync(path.join(__dirname, '../static/anima_studio.html'), 'utf8');
const guide = require('../static/anima_prompt_guide.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
const profile = {id:'shared',tag_csv:'shared-csv'};

function harness(fetchOverride, saved='1') {
  const fields = [...html.matchAll(/<textarea id="([^"]+)" data-tag-autocomplete/g)].map(match => ({id:match[1]}));
  const nodes = new Map(), attached = [], calls = [], streams = [], windows = new Map();
  function element(id) {
    if(!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {checked:true,disabled:false,textContent:'',handlers:{},
        classList:{toggle(name,force){force?classes.add(name):classes.delete(name);},contains:name=>classes.has(name)},
        addEventListener(name,callback){this.handlers[name]=callback;},
      });
    }
    return nodes.get(id);
  }
  let options, closes=0;
  const response = (body,ok=true) => ({ok,status:ok?200:500,json:async()=>body});
  const defaultFetch = async url => response(url.endsWith('/profiles')?{default:'shared',profiles:[profile]}:{loaded:true,count:100,csv_present:true});
  class EventSource {
    constructor(url){this.url=url;this.closed=0;streams.push(this);}
    close(){this.closed++;}
  }
  vm.runInNewContext(source, {
    document:{getElementById:element,querySelectorAll:()=>fields},
    window:{AnimaPromptGuide:{...guide,init(){}},HubTagAutocomplete:{create(value){options=value;return{attach:field=>attached.push(field.id),close:()=>closes++};}},addEventListener:(name,fn)=>windows.set(name,fn)},
    localStorage:{getItem:()=>saved,setItem(){}},AbortController,EventSource,
    fetch:async(url,request={})=>{calls.push({url,request});return(fetchOverride||defaultFetch)(url,request,response);},
  });
  return {element,attached,calls,streams,windows,get options(){return options;},get closes(){return closes;}};
}

test('all prompt fields share the Painter corpus and metadata stays separate', async () => {
  const app=harness();await tick();
  assert.deepEqual(app.attached,['style-prompt','style-negative','test-prompt','prod-prompt','prod-negative','piece-prompt','piece-negative']);
  assert.equal(app.options.getCsv(),'shared-csv');
  assert.equal(app.options.formatName('Blue_Hair'),'blue hair');
  assert.equal(app.options.formatName('score_7'),'score_7');
  assert.equal(app.options.formatName('artist_name',{category:1}),'@artist name');
  assert.match(app.element('anima-tags-status').textContent,/100/);
  const controller=new AbortController();
  await app.options.search('blue hair','shared-csv',{signal:controller.signal});
  assert.equal(app.calls.at(-1).url,'/api/painter/tags/search?q=blue%20hair&csv=shared-csv&limit=8');
  assert.equal(app.calls.at(-1).request.signal,controller.signal);
  await app.options.search('@artist','shared-csv',{signal:controller.signal});
  assert.match(app.calls.at(-1).url,/q=artist&/);
  assert.ok(app.calls.every(call=>!call.url.includes('/generate')));
});

test('disabled suggestions do not load an index and can be enabled later', async () => {
  const app=harness(undefined,'0');await tick();
  assert.equal(app.calls.length,0);assert.equal(app.options.getCsv(),'');
  app.element('anima-tags-enabled').checked=true;
  app.element('anima-tags-enabled').handlers.change();await tick();
  assert.equal(app.options.getCsv(),'shared-csv');
});

test('late responses cannot reactivate suggestions after disabling them', async () => {
  let resolveStatus;
  const app=harness(async(url,request,response)=>url.endsWith('/profiles')?response({profiles:[profile]}):new Promise(resolve=>{resolveStatus=()=>resolve(response({loaded:true,count:50,csv_present:true}));}));
  await tick();const statusCall=app.calls.at(-1);
  app.element('anima-tags-enabled').checked=false;
  app.element('anima-tags-enabled').handlers.change();
  assert.equal(statusCall.request.signal.aborted,true);
  resolveStatus();await tick();
  assert.equal(app.options.getCsv(),'');assert.equal(app.element('anima-tags-status').textContent,'Sugerencias desactivadas.');
});

test('missing base offers an in-app download and failed SSE closes without reconnecting', async () => {
  const app=harness(async(url,request,response)=>response(url.endsWith('/profiles')?{profiles:[profile]}:{loaded:false,count:0,csv_present:false}));await tick();
  assert.equal(app.element('anima-tags-download').classList.contains('hidden'),false);
  app.element('anima-tags-download').handlers.click();
  const stream=app.streams[0];assert.match(stream.url,/profile_id=shared/);
  stream.onerror();assert.equal(stream.closed,1);
  assert.match(app.element('anima-tags-status').textContent,/interrumpió/);
  assert.equal(app.element('anima-tags-download').disabled,false);
  stream.onmessage({data:JSON.stringify({type:'done',count:100,csv:'shared-csv'})});
  assert.equal(app.options.getCsv(),'');
});

test('successful download enables the same corpus and pagehide closes pending work', async () => {
  const app=harness(async(url,request,response)=>response(url.endsWith('/profiles')?{profiles:[profile]}:{loaded:false,count:0,csv_present:false}));await tick();
  app.element('anima-tags-download').handlers.click();
  app.streams[0].onmessage({data:JSON.stringify({type:'done',count:42,csv:'shared-csv'})});
  assert.equal(app.options.getCsv(),'shared-csv');assert.equal(app.streams[0].closed,1);
  app.element('anima-tags-download').handlers.click();
  app.windows.get('pagehide')();assert.equal(app.streams[1].closed,1);
});

test('reload HTTP errors are visible without enabling autocomplete', async () => {
  const app=harness(async(url,request,response)=>{
    if(url.endsWith('/profiles'))return response({profiles:[profile]});
    return request.method==='POST'?response({detail:'CSV inválido'},false):response({loaded:true,count:7,csv_present:true});
  });await tick();
  app.element('anima-tags-reload').handlers.click();await tick();
  assert.match(app.element('anima-tags-status').textContent,/CSV inválido/);
  assert.equal(app.options.getCsv(),'');assert.equal(app.element('anima-tags-reload').disabled,false);
});
