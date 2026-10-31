/* Named prompt combinations persist independently of calibration styles/projects. */
(function (root) {
  'use strict';
  const KEYS = ['style_prompt','style_negative','test_prompt'];
  const clone = value => JSON.parse(JSON.stringify(value));
  const textFields = value => Object.fromEntries(KEYS.map(key=>[key,String(value?.[key]??'')]));
  const normalize = value => String(value??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase();

  function create({api,notice=()=>{},document:doc=root.document,confirm:confirmDelete=root.confirm?.bind(root),getFields,setFields}) {
    const $=id=>doc.getElementById(id);
    let items=[],selectedId='',draft=null,saving=false,deleting=false,refreshing=false,readEpoch=0,wired=false;
    const selected=()=>items.find(item=>item.id===selectedId);
    function status(message,error=false) {
      const node=$('prompt-library-status');node.textContent=message;
      node.setAttribute('role',error?'alert':'status');
    }
    function report(error) {status(error.message||String(error),true);notice(error.message||String(error),true);}
    const action=fn=>async event=>{try{return await fn(event);}catch(error){report(error);}};
    function option(value,text) {const node=doc.createElement('option');node.value=value;node.textContent=text;return node;}
    function render() {
      const terms=normalize($('prompt-library-search').value).trim().split(/\s+/).filter(Boolean);
      const matches=items.filter(item=>terms.every(term=>normalize([item.name,...KEYS.map(key=>item[key])].join(' ')).includes(term)));
      const current=selected();
      if(current&&!matches.some(item=>item.id===current.id))matches.unshift(current);
      const select=$('prompt-library-select');
      select.replaceChildren(option('',items.length?'Selecciona una configuración':'Sin prompts guardados'),
        ...matches.map(item=>option(item.id,item.name)));
      select.value=current?.id||'';selectedId=select.value;
      select.disabled=deleting;
      const busy=saving||deleting;
      for(const id of ['prompt-library-apply','prompt-library-update','prompt-library-delete'])$(id).disabled=!current||busy;
      $('prompt-library-save').disabled=busy;
      $('prompt-library-refresh').disabled=refreshing;
      $('prompt-save-submit').disabled=saving;
    }
    function invalidateReads() {readEpoch++;refreshing=false;}
    async function refresh() {
      const epoch=++readEpoch;refreshing=true;render();status('Cargando biblioteca de prompts…');
      try {
        const result=await api('/prompts');
        if(epoch!==readEpoch)return;
        if(!Array.isArray(result)||result.some(item=>!item||typeof item.id!=='string'||typeof item.name!=='string'))throw new Error('No se pudo leer la biblioteca de prompts.');
        items=result.map(item=>({...clone(item),...textFields(item)}));
        if(!selected())selectedId='';
        status(`${items.length} ${items.length===1?'configuración guardada':'configuraciones guardadas'} · disponibles en todos los proyectos.`);
      } catch(error) {
        if(epoch!==readEpoch)return;
        status(`No se pudo cargar la biblioteca. Usa Recargar para reintentarlo. ${error.message||error}`,true);
        throw error;
      } finally {if(epoch===readEpoch){refreshing=false;render();}}
    }
    function apply() {
      const current=selected();if(!current)throw new Error('Selecciona una configuración de prompts.');
      setFields(textFields(current));notice(`Prompts «${current.name}» aplicados. Los ajustes de generación se conservan.`);
    }
    function openSave(mode='create') {
      if(saving||deleting)return;
      const current=mode==='update'?selected():null;
      if(mode==='update'&&!current)throw new Error('Selecciona la configuración que quieres actualizar.');
      const fields=textFields(getFields());
      if(!KEYS.some(key=>fields[key].trim()))throw new Error('Escribe al menos un prompt antes de guardar.');
      draft={fields,...(current?{id:current.id,expected_revision:current.revision}:{}),mode};
      $('prompt-save-name').value=current?.name||'';
      $('prompt-save-title').textContent=current?'Actualizar configuración de prompts':'Guardar configuración de prompts';
      $('prompt-save-submit').textContent=current?'Actualizar configuración':'Guardar configuración nueva';
      $('prompt-save-preview').textContent=`Prompt de estilo\n${fields.style_prompt||'(Vacío)'}\n\nNegativo del estilo\n${fields.style_negative||'(Vacío)'}\n\nEscena de prueba\n${fields.test_prompt||'(Vacío)'}`;
      $('prompt-save-dialog').showModal();$('prompt-save-name').focus();
    }
    async function submit(event) {
      event?.preventDefault();if(saving)return;
      const source=draft;if(!source)throw new Error('Abre el guardado de prompts antes de continuar.');
      const name=$('prompt-save-name').value.trim();
      if(!name)throw new Error('Escribe un nombre para la configuración de prompts.');
      const payload={name,...source.fields,...(source.id?{id:source.id,expected_revision:source.expected_revision}:{})};
      invalidateReads();saving=true;render();
      try {
        const saved=await api('/prompts','POST',payload);
        invalidateReads();items=[{...clone(saved),...textFields(saved)},...items.filter(item=>item.id!==saved.id)];selectedId=saved.id;
        if(draft===source){draft=null;$('prompt-save-dialog').close();}
        status(`«${saved.name}» guardado en la biblioteca de prompts.`);notice(`Prompts «${saved.name}» guardados.`);
      } finally {saving=false;render();}
    }
    async function remove() {
      if(deleting||saving)return;
      const record=selected();if(!record)throw new Error('Selecciona la configuración que quieres borrar.');
      if(!confirmDelete?.(`¿Borrar la configuración de prompts «${record.name}» de la biblioteca? Los textos abiertos y las imágenes se conservan.`))return;
      invalidateReads();deleting=true;render();
      try {
        await api(`/prompts/${encodeURIComponent(record.id)}?expected_revision=${encodeURIComponent(record.revision)}`,'DELETE');
        invalidateReads();items=items.filter(item=>item.id!==record.id);if(selectedId===record.id)selectedId='';
        status(`Configuración «${record.name}» borrada. Los textos abiertos se conservan.`);notice(`Configuración «${record.name}» borrada.`);
      } finally {deleting=false;render();}
    }
    async function init() {
      if(!wired){
        wired=true;
        $('prompt-library-search').oninput=render;
        $('prompt-library-select').onchange=()=>{selectedId=$('prompt-library-select').value;render();};
        $('prompt-library-refresh').onclick=action(refresh);
        $('prompt-library-apply').onclick=action(apply);
        $('prompt-library-save').onclick=action(()=>openSave());
        $('prompt-library-update').onclick=action(()=>openSave('update'));
        $('prompt-library-delete').onclick=action(remove);
        $('prompt-save-form').onsubmit=action(submit);
        $('prompt-save-dialog').onclose=()=>{draft=null;};
      }
      try{await refresh();}catch{/* The local status offers a retry; calibration stays available. */}
    }
    return {init,refresh,apply,openSave,submit,remove};
  }
  const api={create};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.AnimaPromptLibrary=api;
})(globalThis);
