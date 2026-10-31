const assert=require('node:assert/strict');
const {test}=require('node:test');
const Loras=require('../static/anima_piece_loras.js');

class Node {
  constructor(tag,ownerDocument) {
    Object.assign(this,{tagName:tag.toUpperCase(),ownerDocument,children:[],parentElement:null,
      attributes:new Map(),listeners:new Map(),style:{},hidden:false,disabled:false,checked:false,_value:'',_text:''});
  }
  setAttribute(key,value){this.attributes.set(key,String(value));if(key==='value')this._value=String(value);}
  getAttribute(key){return this.attributes.get(key)??null;}
  set id(value){this.setAttribute('id',value);}
  get id(){return this.getAttribute('id');}
  set value(value){this._value=String(value);}
  get value(){return this._value;}
  set textContent(value){this._text=String(value);this.replaceChildren();}
  get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
  append(...nodes){for(const node of nodes){node.remove();node.parentElement=this;this.children.push(node);}}
  replaceChildren(...nodes){for(const node of this.children)node.parentElement=null;this.children=[];this.append(...nodes);}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(node=>node!==this);this.parentElement=null;}
  addEventListener(type,handler){if(!this.listeners.has(type))this.listeners.set(type,new Set());this.listeners.get(type).add(handler);}
  removeEventListener(type,handler){this.listeners.get(type)?.delete(handler);}
  dispatchEvent(event){for(const handler of [...(this.listeners.get(event.type)||[])])handler({...event,target:this});}
  click(){if(!this.disabled)this.dispatchEvent({type:'click'});}
  focus(){this.ownerDocument.activeElement=this;}
  querySelectorAll(selector){
    const matches=node=>selector.startsWith('#')?node.id===selector.slice(1):selector.startsWith('[')
      ?(()=>{const [,key,value]=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);return node.attributes.has(key)&&(value===undefined||node.getAttribute(key)===value);})()
      :node.tagName.toLowerCase()===selector;
    const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
    return descendants(this).filter(matches);
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
class Document {createElement(tag){return new Node(tag,this);}}
const catalog={loras:[
  {file:'content/hero.safetensors',name:'Héroe',thumbnail_url:'/api/anima-studio/catalog/thumbnail/hero'},
  {file:'outfit/dress.safetensors',name:'Vestido'},
  {file:'poses/running.safetensors',name:'Carrera'},
]};
function setup(options={}) {
  const doc=new Document(),container=doc.createElement('div'),picks=[];
  let count=0;
  const editor=Loras.create({container,catalog,prefix:'test-piece',onPick:request=>picks.push(request),onChange:()=>count++,...options});
  const field=(name,index=0)=>container.querySelector(`[data-lora-${name}="${index}"]`);
  const add=()=>container.querySelector('#test-piece-add').click();
  return {editor,container,doc,picks,field,add,changes:()=>count};
}

test('a piece starts without extra LoRAs or generation controls',()=>{
  const {editor,container,changes}=setup();
  assert.deepEqual(editor.getValue(),[]);assert.equal(changes(),0);
  assert.match(container.textContent,/Sin LoRAs/);assert.equal(container.querySelectorAll('input').length,0);
  for(const button of container.querySelectorAll('button'))assert.equal(button.getAttribute('type'),'button');
});

test('reopening preserves exact paths, zero weights, disabled entries and unknown metadata independently',()=>{
  const {editor,field,changes}=setup();
  const original=[{file:'CONTENT\\Hero.safetensors',strength:0,enabled:false,trigger:'hero tag',metadata:{source:'local'},custom:['a']}];
  const before=structuredClone(original);editor.setValue(original);
  assert.deepEqual(editor.getValue(),before);assert.equal(changes(),0);
  assert.equal(field('weight').value,'0');assert.equal(field('enabled').checked,false);assert.equal(field('trigger').value,'hero tag');
  const returned=editor.getValue();returned[0].metadata.source='changed';returned[0].custom.push('b');
  assert.deepEqual(editor.getValue(),before);assert.deepEqual(original,before);
});

test('all piece-owned fields are editable and only user events notify their owner',()=>{
  const {editor,field,changes}=setup();editor.setValue([{file:'content/hero.safetensors',strength:.7,notes:'keep'}]);
  field('weight').value='-0.25';field('weight').dispatchEvent({type:'input'});
  field('enabled').checked=false;field('enabled').dispatchEvent({type:'change'});
  field('trigger').value='new tag';field('trigger').dispatchEvent({type:'input'});
  assert.equal(changes(),3);assert.deepEqual(editor.getValue(),[{file:'content/hero.safetensors',strength:-.25,enabled:false,trigger:'new tag',notes:'keep'}]);
  assert.equal(field('trigger').getAttribute('data-tag-autocomplete'),null);
});

test('gallery requests list other used files and replace one row without losing its edits',()=>{
  const {editor,picks,field,add,changes}=setup();
  add();assert.deepEqual(picks[0].used,[]);assert.equal(picks[0].kind,'loras');picks.shift().select('content/hero.safetensors');
  field('weight').value='0';field('trigger').value='identity';field('enabled').checked=false;
  add();assert.deepEqual(picks[0].used,['content/hero.safetensors']);picks.shift().select('outfit/dress.safetensors');
  field('pick',1).click();const request=picks.shift();
  assert.equal(request.value,'outfit/dress.safetensors');assert.deepEqual(request.used,['content/hero.safetensors']);request.select('poses/running.safetensors');
  assert.equal(changes(),3);
  assert.deepEqual(editor.getValue(),[
    {file:'content/hero.safetensors',strength:0,enabled:false,trigger:'identity'},
    {file:'poses/running.safetensors',strength:1,enabled:true},
  ]);
});

test('normalized duplicates are rejected transactionally and ignored by stale gallery choices',()=>{
  const {editor,picks,field,add,changes}=setup();editor.setValue([{file:'content/hero.safetensors',strength:0}]);
  const before=editor.getValue();
  assert.throws(()=>editor.setValue([{file:'CONTENT\\HERO.safetensors'},{file:'content/hero.safetensors'}]),/repitas/);
  assert.deepEqual(editor.getValue(),before);
  add();picks.shift().select('CONTENT\\HERO.safetensors');
  assert.equal(changes(),0);assert.deepEqual(editor.getValue(),before);
  add();picks.shift().select('outfit/dress.safetensors');
  field('pick',1).click();picks.shift().select('CONTENT\\HERO.safetensors');
  assert.equal(editor.getValue()[1].file,'outfit/dress.safetensors');assert.equal(changes(),1);
});

test('catalog refresh preserves in-progress fields and never substitutes a missing file',()=>{
  const {editor,field,container,changes}=setup();
  editor.setValue([{file:'content\\hero.safetensors',strength:0,enabled:false,trigger:'hero',unknown:{keep:true}}]);
  assert.match(field('pick').textContent,/Héroe/);
  field('weight').value='';field('trigger').value='typing now';
  const weight=field('weight');editor.setCatalog({loras:[{file:'new.safetensors',name:'Otro'}]});
  assert.equal(field('weight'),weight);assert.equal(field('weight').value,'');assert.equal(field('trigger').value,'typing now');
  assert.match(container.textContent,/Archivo no disponible/);assert.equal(field('pick').getAttribute('data-selected-file'),'content\\hero.safetensors');
  assert.equal(changes(),0);assert.throws(()=>editor.getValue(),/influencia/);
  field('weight').value='0';assert.deepEqual(editor.getValue(),[{file:'content\\hero.safetensors',strength:0,enabled:false,trigger:'typing now',unknown:{keep:true}}]);
});

test('removing a row preserves edits on the remaining rows and invalidates that row picker',()=>{
  const {editor,field,picks,changes}=setup();editor.setValue([{file:'content/hero.safetensors'},{file:'outfit/dress.safetensors',strength:.5}]);
  field('pick').click();const oldPicker=picks.shift();field('weight',1).value='-3';field('trigger',1).value='dress tag';
  field('remove').click();oldPicker.select('poses/running.safetensors');
  assert.equal(changes(),1);assert.deepEqual(editor.getValue(),[{file:'outfit/dress.safetensors',strength:-3,enabled:true,trigger:'dress tag'}]);
});

test('setValue invalidates pending gallery results and keeps the next editor state silent',()=>{
  const {editor,picks,add,changes}=setup();add();const pending=picks.shift();
  editor.setValue([{file:'outfit/dress.safetensors',strength:3}]);pending.select('content/hero.safetensors');
  assert.equal(changes(),0);assert.deepEqual(editor.getValue(),[{file:'outfit/dress.safetensors',strength:3,enabled:true}]);
});

test('the twelve-LoRA limit applies to repeated callbacks as well as programmatic state',()=>{
  const {editor,picks,add,container,changes}=setup();add();const request=picks.shift();
  for(let index=0;index<13;index++)request.select(`piece/lora-${index}.safetensors`);
  assert.equal(editor.getValue().length,12);assert.equal(changes(),12);assert.equal(container.querySelector('#test-piece-add').disabled,true);
  assert.throws(()=>editor.setValue(Array.from({length:13},(_,i)=>({file:`item-${i}.safetensors`}))),/12/);
  assert.equal(editor.getValue().length,12);
});

test('weight validation rejects blanks, non-finite values and out-of-range values even for disabled rows',()=>{
  const {editor,field}=setup();editor.setValue([{file:'poses/running.safetensors',enabled:false}]);
  for(const value of ['', 'NaN','Infinity','3.01','-3.01']){field('weight').value=value;assert.throws(()=>editor.getValue(),/influencia/);}
  for(const value of ['-3','0','3']){field('weight').value=value;assert.equal(editor.getValue()[0].strength,Number(value));}
  for(const value of [null,{},[{file:4}],[{file:'ok.safetensors',enabled:'yes'}],[{file:'ok.safetensors',trigger:12}]])assert.throws(()=>editor.setValue(value));
});

test('independent instances do not share data, events or picker callbacks',()=>{
  const a=setup(),b=setup({prefix:'other-piece'});
  a.editor.setValue([{file:'content/hero.safetensors',strength:.8}]);b.editor.setValue([{file:'outfit/dress.safetensors',strength:.2}]);
  a.field('pick').click();a.picks.shift().select('poses/running.safetensors');
  assert.equal(a.editor.getValue()[0].file,'poses/running.safetensors');assert.equal(b.editor.getValue()[0].file,'outfit/dress.safetensors');
  assert.equal(a.changes(),1);assert.equal(b.changes(),0);
  assert.ok(a.container.querySelector('#test-piece-0-weight'));assert.ok(b.container.querySelector('#other-piece-0-weight'));
});

test('destroy removes only its own controls and stops live inputs and pending gallery callbacks',()=>{
  const {editor,container,doc,field,picks,changes}=setup();
  const unrelated=doc.createElement('span');container.append(unrelated);
  editor.setValue([{file:'content/hero.safetensors'}]);field('pick').click();const pending=picks.shift(),weight=field('weight');
  editor.destroy();weight.dispatchEvent({type:'input'});pending.select('outfit/dress.safetensors');editor.setCatalog(catalog);editor.setValue([]);
  assert.equal(changes(),0);assert.deepEqual(container.children,[unrelated]);assert.throws(()=>editor.getValue(),/cerró/);
});

test('fallback native selectors operate without a gallery and do not create duplicates',()=>{
  const {editor,field,add}=setup({onPick:undefined});add();add();
  assert.deepEqual(editor.getValue().map(value=>value.file),['content/hero.safetensors','outfit/dress.safetensors']);
  field('file',1).value='content/hero.safetensors';field('file',1).dispatchEvent({type:'change'});
  assert.equal(editor.getValue()[1].file,'outfit/dress.safetensors');
  field('file',1).value='poses/running.safetensors';field('file',1).dispatchEvent({type:'change'});
  assert.equal(editor.getValue()[1].file,'poses/running.safetensors');
});

test('catalog names are literal text and thumbnails use only local Hub endpoints',()=>{
  const {editor,container,field}=setup({catalog:{loras:[
    {file:'x.safetensors',name:'<img src=x onerror=alert(1)>',thumbnail_url:'https://remote.example/track'},
    {file:'y.safetensors',name:'Local',thumbnail_url:'/api/vault/thumbnail/y'},
  ]}});
  editor.setValue([{file:'x.safetensors'},{file:'y.safetensors'}]);
  assert.match(field('pick').textContent,/<img src=x/);assert.equal(field('pick').querySelectorAll('img').length,0);
  assert.equal(container.querySelectorAll('img').length,1);assert.equal(container.querySelector('img').getAttribute('src'),'/api/vault/thumbnail/y');
  for(const button of container.querySelectorAll('button'))assert.equal(button.getAttribute('type'),'button');
});
