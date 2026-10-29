const assert = require('node:assert/strict');
const {test} = require('node:test');
const Browser = require('../static/anima_style_browser.js');

function fixture() {
  const document = {activeElement:null};
  class Node {
    constructor(tag) {
      this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this.listeners={};
      this.className='';this.textContent='';this.id='';this.hidden=false;this.disabled=false;this.open=false;
      this.style={values:{},setProperty(key,value){this.values[key]=value;}};
      this._value='';this.classList={add:()=>{},toggle:()=>{}};
    }
    get options() { return this.children.filter(child=>child.tagName==='OPTION'); }
    get value() { return this._value; }
    set value(value) {
      const text=String(value);
      this._value=this.tagName==='SELECT'&&!this.options.some(option=>option.value===text)?'':text;
    }
    get isConnected() { return !!this.parentElement || this===document.body; }
    append(...nodes) { for(const node of nodes){this.children.push(node);node.parentElement=this;} }
    replaceChildren(...nodes) { for(const child of this.children)child.parentElement=null;this.children=[];this.append(...nodes); }
    setAttribute(key,value) { this.attributes[key]=String(value); }
    getAttribute(key) { return this.attributes[key]??null; }
    addEventListener(type,listener) { (this.listeners[type]??=[]).push(listener); }
    dispatchEvent(event) {
      this['on'+event.type]?.(event);
      for(const listener of this.listeners[event.type]||[])listener(event);
      return true;
    }
    insertAdjacentElement(position,node) {
      assert.equal(position,'afterend');const parent=this.parentElement;
      parent.children.splice(parent.children.indexOf(this)+1,0,node);node.parentElement=parent;
    }
    remove() { const parent=this.parentElement;if(parent)parent.children=parent.children.filter(child=>child!==this);this.parentElement=null; }
    focus() { document.activeElement=this; }
    showModal() { this.open=true; }
    close() { this.open=false;this.dispatchEvent(new Event('close')); }
  }
  function descendants(node) { return [node,...node.children.flatMap(descendants)]; }
  document.body=new Node('body');
  document.createElement=tag=>new Node(tag);
  document.getElementById=id=>descendants(document.body).find(node=>node.id===id)||null;
  document.querySelectorAll=selector=>descendants(document.body).filter(node=>node.className.split(' ').includes(selector.slice(1)));
  function select(id,entries) {
    const node=new Node('select');node.id=id;
    for(const [value,text] of entries){const option=new Node('option');option.value=value;option.textContent=text;node.append(option);}
    document.body.append(node);return node;
  }
  global.document=document;
  const controls={styles:[],favorites:new Set(),chosen:[]};
  const api=Browser.create({getStyles:()=>controls.styles,imageUrl:id=>`/images/${id}`,
    getFavorites:()=>controls.favorites,onChoose:value=>controls.chosen.push(value)});
  const get=className=>descendants(document.body).find(node=>node.className.split(' ').includes(className));
  const all=className=>descendants(document.body).filter(node=>node.className.split(' ').includes(className));
  return {document,select,controls,api,get,all};
}

test('search covers names, accents, tags, prompts, model and LoRA files', () => {
  const styles=[{id:'a',name:'Ilustración suave',prompt:'painted ink',tags:['acuarela'],
    config:{model:'anima/aesthetic.safetensors',loras:[{file:'styles/watercolor.safetensors'}]}},
    {id:'b',name:'Cartoon',prompt:'cel shading',tags:['limpio']}];
  for(const query of ['ilustracion','acuarela','painted ink','aesthetic','watercolor'])
    assert.deepEqual(Browser.matchingStyles(styles,{query}).map(style=>style.id),['a']);
  assert.deepEqual(Browser.matchingStyles(styles,{query:'no existe'}),[]);
});

test('a library of 300 styles renders 24 initially and preserves all matches while paging', () => {
  const styles=Array.from({length:300},(_,index)=>({id:`s${index}`,name:`Style ${index}`}));
  const first=Browser.stylePage(styles),second=Browser.stylePage(styles,{limit:first.nextLimit});
  assert.equal(first.items.length,24);assert.equal(first.total,300);assert.equal(first.hasMore,true);
  assert.equal(second.items.length,48);assert.equal(second.total,300);
  assert.deepEqual(first.items,second.items.slice(0,24));
  assert.equal(Browser.stylePage(styles,{limit:312}).hasMore,false);
});

test('favorite and sample filters do not mutate the original library', () => {
  const styles=[{id:'a',sample_ids:['one']},{id:'b',sample_ids:[]},{id:'c',sample_ids:['two']}];
  const before=JSON.stringify(styles);
  assert.deepEqual(Browser.matchingStyles(styles,{filter:'favorites',favorites:new Set(['b'])}).map(s=>s.id),['b']);
  assert.deepEqual(Browser.matchingStyles(styles,{filter:'samples'}).map(s=>s.id),['a','c']);
  assert.equal(JSON.stringify(styles),before);
});

test('only explicitly supplied styles and historic options are available', () => {
  const entries=Browser.selectEntries([{id:'local',name:'Local'}],[{value:'',text:'Sin estilo'},
    {value:'local',text:'Local'},{value:'historic',text:'Estilo original · del maestro'}]);
  assert.deepEqual(entries.map(style=>style.id),['local','historic']);
  assert.equal(entries[1].historic,true);assert.equal(entries[1].sample_ids.length,0);
});

test('choosing a visual card preserves the native form contract and returns focus', () => {
  const f=fixture();f.controls.styles=[{id:'a',name:'Acuarela',revision:3,sample_ids:['sample']}];
  const select=f.select('prod-style',[['','Sin estilo'],['a','Acuarela']]);let changes=0;
  select.onchange=()=>changes++;
  const button=f.api.attach('prod-style');assert.equal(select.hidden,true);
  button.onclick();assert.equal(f.get('asb-dialog').open,true);
  f.get('asb-card').onclick();
  assert.equal(select.value,'a');assert.equal(changes,1);
  assert.equal(f.controls.chosen[0].selectId,'prod-style');assert.equal(f.controls.chosen[0].style.id,'a');
  assert.equal(f.get('asb-dialog').open,false);assert.equal(f.document.activeElement,button);
  assert.equal(button.getAttribute('aria-label'),'Cambiar estilo: Acuarela');
});

test('filtering a 300-style dialog keeps an off-page selection intact', () => {
  const f=fixture();f.controls.styles=Array.from({length:300},(_,i)=>({id:`s${i}`,name:`Style ${i}`}));
  const select=f.select('cal-style',f.controls.styles.map(s=>[s.id,s.name]));select.value='s299';
  f.api.attach('cal-style');f.api.open('cal-style');
  assert.equal(f.all('asb-card').length,24);assert.equal(select.value,'s299');
  f.get('asb-search').value='Style 3';f.get('asb-search').oninput();
  assert.equal(select.value,'s299');assert.match(f.get('asb-selected-note').textContent,/Style 299/);
  f.get('asb-more').onclick();assert.equal(select.value,'s299');
});

test('refresh follows disabled/frozen selects and does not recreate attached controls', () => {
  const f=fixture();f.controls.styles=[{id:'a',name:'Paint'}];
  const select=f.select('prod-style',[['a','Paint']]);
  const button=f.api.attach('prod-style');assert.equal(f.api.attach('prod-style'),button);
  f.api.open('prod-style');select.disabled=true;f.api.refresh();
  assert.equal(button.disabled,true);assert.equal(f.get('asb-dialog').open,false);
  button.onclick();assert.equal(f.get('asb-dialog').open,false);
});

test('historic styles can be selected without inventing a preview image', () => {
  const f=fixture();const select=f.select('piece-test-style',[['','Sin estilo'],['old','Acabado · del maestro']]);
  f.api.attach('piece-test-style');f.api.open('piece-test-style');
  const card=f.get('asb-card');assert.equal(card.getAttribute('aria-pressed'),'false');
  assert.equal(f.all('asb-art').some(frame=>frame.children.some(node=>node.tagName==='IMG')),false);
  card.onclick();assert.equal(select.value,'old');
  assert.match(f.get('asb-choice').getAttribute('aria-label'),/Acabado/);
});

test('a missing thumbnail falls back to a visible labelled placeholder', () => {
  const f=fixture();f.controls.styles=[{id:'a',name:'Paint',sample_ids:['gone']}];
  f.select('prod-style',[['a','Paint']]).value='a';f.api.attach('prod-style');
  const frame=f.get('asb-art'),image=frame.children.find(node=>node.tagName==='IMG');
  image.onload();assert.equal(frame.children[0].hidden,true);
  image.onerror();assert.equal(frame.children[0].hidden,false);assert.equal(frame.children.length,1);
  f.api.refresh();assert.equal(f.get('asb-art').children.length,1);
});

test('choosing from search may restore a valid option removed by another native filter', () => {
  const f=fixture();f.controls.styles=[{id:'a',name:'Paint'},{id:'b',name:'Ink'}];
  const select=f.select('cal-style',[['','Sin estilo'],['a','Paint']]);
  f.api.attach('cal-style');f.api.open('cal-style');
  f.all('asb-card')[1].onclick();assert.equal(select.value,'b');
  assert.equal(select.options.some(option=>option.value==='b'),true);
});

test('clearing a style dispatches change while retaining the other gallery state', () => {
  const f=fixture();f.controls.styles=[{id:'a',name:'Paint'}];
  const select=f.select('prod-style',[['','Sin estilo'],['a','Paint']]);select.value='a';
  let changes=0;select.onchange=()=>changes++;
  f.api.attach('prod-style');f.api.open('prod-style');f.get('asb-clear').onclick();
  assert.equal(select.value,'');assert.equal(changes,1);assert.equal(f.controls.styles.length,1);
});
