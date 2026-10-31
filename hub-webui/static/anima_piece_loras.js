/* LoRAs belong to the content of a piece; this editor never changes a style. */
(function(root) {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const normalized = value => String(value ?? '').replace(/\\/g, '/').toLowerCase();
  const fileOf = item => typeof item === 'string' ? item : String(item?.file ?? '');
  const nameOf = item => typeof item === 'string' ? item.split(/[\\/]/).pop()
    : String(item?.display_name || item?.name || item?.label || fileOf(item).split(/[\\/]/).pop());

  function create({container,catalog={},onPick,onChange=()=>{},prefix='piece-loras'}={}) {
    if(!container?.ownerDocument)throw new Error('Indica el contenedor de los LoRAs de la pieza.');
    const doc=container.ownerDocument;
    let source=catalog,rows=[],disposed=false,epoch=0;
    const listeners=[],rowListeners=[];
    function el(tag,attrs={},text) {
      const node=doc.createElement(tag);
      for(const [key,value] of Object.entries(attrs)) {
        if(key==='class')node.className=value;
        else node.setAttribute(key,String(value));
      }
      if(text!==undefined)node.textContent=text;
      return node;
    }
    function listen(node,type,handler,group=rowListeners) {
      node.addEventListener(type,handler);group.push([node,type,handler]);
    }
    function clearListeners(group) {
      for(const [node,type,handler] of group)node.removeEventListener(type,handler);
      group.length=0;
    }
    function changed(){if(!disposed)onChange();}
    function entries(){return Array.isArray(source)?source:Array.isArray(source?.loras)?source.loras:[];}
    function entry(file){return entries().find(item=>normalized(fileOf(item))===normalized(file));}
    function filesExcept(row){return rows.filter(other=>other!==row).map(other=>other.data.file);}
    function duplicate(file,row){return rows.some(other=>other!==row&&normalized(other.data.file)===normalized(file));}

    const panel=el('section',{class:'piece-loras','aria-label':'LoRAs de la pieza'});
    const toolbar=el('div',{class:'section-title small'});
    const add=el('button',{type:'button',id:`${prefix}-add`,class:'text-button'},'+ Añadir LoRA');
    const list=el('div',{id:`${prefix}-list`,class:'piece-lora-list'});
    const empty=el('p',{class:'hint'},'Sin LoRAs adicionales.');
    toolbar.append(add);panel.append(toolbar,list,empty);container.append(panel);

    function choice(row) {
      const item=entry(row.data.file),thumbnail=item?.thumbnail_url;
      row.button.replaceChildren();
      if(typeof thumbnail==='string'&&/^\/api\/(?:anima-studio|vault)\//.test(thumbnail)) {
        const image=el('img',{src:thumbnail,alt:'',loading:'lazy',decoding:'async'});
        image.style.width='54px';image.style.height='68px';image.style.objectFit='contain';
        row.button.append(image);
      }
      const text=el('span');
      text.append(el('strong',{},item?nameOf(item):row.data.file?'Archivo no disponible':'Elegir LoRA'));
      text.append(el('small',{},row.data.file||'Explorar la biblioteca de LoRAs'));
      row.button.append(text);
      row.button.title=row.data.file||'Elegir LoRA';
      row.button.setAttribute('data-selected-file',row.data.file);
      row.button.setAttribute('aria-label',item?`Cambiar LoRA: ${nameOf(item)}`:row.data.file?`LoRA no disponible: ${row.data.file}`:'Elegir LoRA');
      if(row.fallback) {
        row.fallback.replaceChildren(el('option',{value:''},'Selecciona un LoRA'));
        for(const candidate of entries()) {
          const file=fileOf(candidate);
          if(file&&!duplicate(file,row))row.fallback.append(el('option',{value:file},nameOf(candidate)));
        }
        if(row.data.file&&!entry(row.data.file))row.fallback.append(el('option',{value:row.data.file},`No disponible: ${row.data.file}`));
        const matched=entries().find(candidate=>normalized(fileOf(candidate))===normalized(row.data.file));
        row.fallback.value=matched?fileOf(matched):row.data.file;
      }
    }
    function raw(row) {
      const value={...row.data,strength:row.weight.value,enabled:row.enabled.checked};
      if(row.trigger.value!==''||Object.hasOwn(row.data,'trigger'))value.trigger=row.trigger.value;
      return value;
    }
    function capture(){for(const row of rows)if(row.weight)row.data=raw(row);}
    function pick(row=null) {
      if(disposed||(!row&&rows.length>=12)||row&&!rows.includes(row))return;
      const stamp=epoch;
      const select=selected=>{
        if(disposed||stamp!==epoch||row&&!rows.includes(row))return;
        const file=fileOf(selected);
        if(!file||duplicate(file,row)||!row&&rows.length>=12)return;
        capture();
        if(row)row.data.file=file;
        else rows.push({data:{file,strength:1,enabled:true}});
        render();changed();
      };
      if(onPick)onPick({kind:'loras',value:row?.data.file||'',used:filesExcept(row),select});
      else if(row)row.fallback?.focus();
      else {
        const first=entries().find(item=>!duplicate(fileOf(item),null));
        if(first)select(fileOf(first));
      }
    }
    function render() {
      clearListeners(rowListeners);list.replaceChildren();
      empty.hidden=rows.length>0;empty.style.display=rows.length?'none':'';add.disabled=rows.length>=12;
      rows.forEach((row,index)=>{
        const element=el('div',{class:'piece-lora-row'});
        row.button=el('button',{type:'button',class:'catalog-choice','data-lora-pick':index});
        const controls=el('div',{class:'field-grid two'});
        const weightLabel=el('label');
        row.weight=el('input',{type:'number',min:-3,max:3,step:0.05,id:`${prefix}-${index}-weight`,'data-lora-weight':index});
        row.weight.value=String(row.data.strength===undefined?1:row.data.strength);
        weightLabel.append(el('span',{},'Influencia'),row.weight);
        const enabledLabel=el('label',{class:'check'});
        row.enabled=el('input',{type:'checkbox',id:`${prefix}-${index}-enabled`,'data-lora-enabled':index});
        row.enabled.checked=row.data.enabled!==false;enabledLabel.append(row.enabled,el('span',{},'Activo'));
        const triggerLabel=el('label');
        row.trigger=el('input',{type:'text',id:`${prefix}-${index}-trigger`,'data-lora-trigger':index,placeholder:'Si el LoRA lo necesita'});
        row.trigger.value=row.data.trigger||'';triggerLabel.append(el('span',{},'Activador (opcional)'),row.trigger);
        const remove=el('button',{type:'button',class:'text-button danger','data-lora-remove':index},'Quitar LoRA');
        row.fallback=null;
        element.append(row.button);
        if(!onPick) {
          row.fallback=el('select',{'aria-label':'Archivo de LoRA','data-lora-file':index});
          listen(row.fallback,'change',()=>{
            const file=row.fallback.value;
            if(!file||duplicate(file,row)){choice(row);return;}
            capture();row.data.file=file;for(const item of rows)choice(item);changed();
          });
          element.append(row.fallback);
        }
        controls.append(weightLabel,enabledLabel);element.append(controls,triggerLabel,remove);list.append(element);choice(row);
        listen(row.button,'click',()=>pick(row));
        listen(row.weight,'input',changed);listen(row.weight,'change',changed);
        listen(row.enabled,'change',changed);listen(row.trigger,'input',changed);
        listen(remove,'click',()=>{capture();rows=rows.filter(item=>item!==row);render();changed();});
      });
    }
    listen(add,'click',()=>pick(),listeners);
    function setValue(value=[]) {
      if(disposed)return;
      if(!Array.isArray(value)||value.length>12)throw new Error('Puedes añadir hasta 12 LoRAs por pieza.');
      const seen=new Set();
      const next=value.map(item=>{
        if(!item||typeof item!=='object'||Array.isArray(item)||typeof item.file!=='string'||!item.file.trim())throw new Error('El archivo de un LoRA no es válido.');
        const key=normalized(item.file);
        if(seen.has(key))throw new Error('No repitas el mismo LoRA en esta pieza.');
        if(item.enabled!==undefined&&typeof item.enabled!=='boolean')throw new Error('La activación del LoRA debe ser verdadera o falsa.');
        if(item.trigger!==undefined&&typeof item.trigger!=='string')throw new Error('El activador del LoRA debe ser texto.');
        seen.add(key);return {data:clone(item)};
      });
      rows=next;epoch++;render();
    }
    function getValue() {
      if(disposed)throw new Error('Este editor de LoRAs ya se cerró.');
      return rows.map(row=>{
        const value=raw(row),weight=String(value.strength).trim(),strength=Number(weight);
        if(!weight||!Number.isFinite(strength)||strength< -3||strength>3)throw new Error('La influencia de cada LoRA debe estar entre -3 y 3.');
        return clone({...value,strength});
      });
    }
    function setCatalog(value={}) {
      if(disposed)return;
      source=value;for(const row of rows)choice(row);
    }
    function destroy() {
      if(disposed)return;
      disposed=true;epoch++;clearListeners(rowListeners);clearListeners(listeners);panel.remove();rows=[];
    }
    render();return {setValue,getValue,setCatalog,destroy};
  }
  const api={create};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.AnimaPieceLoras=api;
})(globalThis);
