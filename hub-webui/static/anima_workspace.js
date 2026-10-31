/* Visual authoring vocabulary; rendering never mutates a composition. */
(function (root) {
  'use strict';
  const TYPES = [
    {kind:'character',name:'Personaje',description:'Identidad, rostro y proporciones',path:'M8 21v-2a4 4 0 0 1 8 0v2M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8'},
    {kind:'personality',name:'Personalidad',description:'Actitud y presencia del personaje',path:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M8 9h.01M16 9h.01M8 14q4 4 8 0'},
    {kind:'outfit',name:'Outfit',description:'Prendas y accesorios sobre maniquí',path:'m8 3-6 4 3 5 3-2v11h8V10l3 2 3-5-6-4c-2 3-6 3-8 0'},
    {kind:'pose',name:'Pose',description:'Postura y control de articulaciones',path:'M12 3v7m-7 3 7-3 7 3m-7-3-3 6-3 5m6-11 3 6 3 5M12 2v1'},
    {kind:'expression',name:'Expresión',description:'Emoción, ojos, cejas y boca',path:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M8 9h.01M16 9h.01M8 14q4 5 8 0'},
    {kind:'background',name:'Fondo',description:'Localización, luz y ambiente',path:'M3 4h18v16H3zM3 17l6-7 5 5 3-4 4 6M16 7h.01'},
    {kind:'item',name:'Objeto',description:'Assets, equipo y consumibles',path:'m12 2 9 5v10l-9 5-9-5V7zm0 10 9-5m-9 5L3 7m9 5v10'},
    {kind:'scene',name:'Escenario',description:'Entorno y contexto de una toma',path:'M3 4h18v16H3zM3 17l5-6 5 4 4-7 4 9M8 7h.01'},
  ];
  const LABELS = Object.fromEntries(TYPES.map(type=>[type.kind,type.name]));
  Object.assign(LABELS,{personality:'Personalidad',scene:'Escenario'});
  const esc = value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const normalize = value=>String(value??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const label = kind=>LABELS[kind]||kind;
  function icon(kind) {
    const type=TYPES.find(t=>t.kind===kind)||TYPES.find(t=>t.kind==='background');
    return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="${type.path}"/></svg>`;
  }
  function creators(disabled=false) {
    return TYPES.map(type=>`<button type="button" class="element-type-card" data-create-element="${type.kind}" ${disabled?'disabled':''}><span class="element-type-icon">${icon(type.kind)}</span><strong>${type.name}</strong><span>${type.description}</span><small>Crear ${type.name.toLowerCase()}</small></button>`).join('');
  }
  function inventory(pieces,{projectId,kind='',query='',onlyFavorites=false,favorites=new Set(),limit=24}={}) {
    const term=normalize(query.trim());
    const filtered=pieces.filter(piece=>(!piece.project_id||piece.project_id===projectId)
      &&(!kind||piece.kind===kind)&&(!onlyFavorites||favorites.has(piece.id))
      &&normalize(`${piece.name} ${piece.prompt||''} ${(piece.tags||[]).join(' ')}`).includes(term));
    return {items:filtered.slice(0,Math.max(1,limit)),total:filtered.length};
  }
  function preview(piece,{imageUrl,isAvailable=()=>true,pinnedRevision}={}) {
    const mismatch=pinnedRevision!=null&&pinnedRevision!==piece.revision;
    const id=!mismatch&&piece.sample_asset_id&&isAvailable(piece.sample_asset_id)?piece.sample_asset_id:null;
    return id?`<img src="${esc(imageUrl(id))}" alt="Prueba de ${esc(piece.name)}" loading="lazy" data-workspace-image="${esc(id)}">`
      :`<span class="inventory-placeholder">${icon(piece.kind)}<small>${mismatch?'Versión guardada':piece.master_id?'Variante por probar':'Sin prueba vinculada'}</small></span>`;
  }
  function card(piece,options={}) {
    const {selected=false,locked=false,favorite=false,pinnedRevision,lockedReason=''}=options;
    const stale=selected&&pinnedRevision!=null&&pinnedRevision!==piece.revision;
    return `<article class="inventory-card${selected?' selected':''}" data-element-id="${esc(piece.id)}">
      <button type="button" class="inventory-preview" data-add-element="${esc(piece.id)}" aria-label="${selected?'Seleccionado:':'Añadir'} ${esc(piece.name)}" aria-pressed="${selected}" ${locked?'disabled':''}>${preview(piece,options)}${selected?'<span class="inventory-selected">✓ En la imagen</span>':''}</button>
      <div class="inventory-card-body"><span class="inventory-kind">${esc(label(piece.kind))} · ${piece.master_id?'Variante':'Maestro'}</span><h4 class="inventory-name">${esc(piece.name)}</h4><small>v${esc(pinnedRevision??piece.revision??1)}</small>${lockedReason?`<p class="hint">${esc(lockedReason)}</p>`:''}
      <div class="inventory-card-actions"><button type="button" class="${selected?'secondary':'primary'}" data-add-element="${esc(piece.id)}" ${locked?'disabled':''}>${stale&&!locked?'Usar versión actual':selected?'Seleccionado':'Añadir a imagen'}</button><button type="button" class="subtle" data-edit-element="${esc(piece.id)}" aria-label="Editar ${esc(piece.name)}">Editar</button><button type="button" class="subtle" data-variant-element="${esc(piece.id)}" aria-label="Crear variante de ${esc(piece.name)}">Variante</button><button type="button" class="favorite ${favorite?'active':''}" data-inventory-favorite="${esc(piece.id)}" aria-label="${favorite?'Quitar de':'Añadir a'} favoritos: ${esc(piece.name)}" aria-pressed="${favorite}">${favorite?'★':'☆'}</button></div></div></article>`;
  }
  function slot(piece,options={}) {
    const revision=options.pinnedRevision??piece.revision??1;
    return `<article class="composition-slot"><div class="composition-slot-preview">${preview(piece,options)}</div><div class="composition-slot-info"><small>${esc(label(piece.kind))}</small><strong>${esc(piece.name)}</strong><span>v${esc(revision)}${options.locked?' · Fijado':''}</span></div><button type="button" class="subtle" data-remove-element="${esc(piece.kind)}" aria-label="Quitar ${esc(piece.name)}" ${options.locked?'disabled':''}>Quitar</button></article>`;
  }
  function readiness({category,profile,pieces=[],hasStyle=false,hasConfig=false,source=false,mode='txt2img',masterReady=false,expressionPrompt='',additionalPrompt=''}={}) {
    if(!category)return {ready:false,message:'Elige qué imagen quieres componer.'};
    if(!hasStyle&&!source)return {ready:false,message:'Elige un estilo calibrado para esta imagen.'};
    if(mode==='expression'){
      if(!masterReady)return {ready:false,message:'Elige la imagen maestra para trabajar su expresión.'};
      if(!expressionPrompt.trim()&&!pieces.some(p=>p.kind==='expression'&&p.prompt?.trim()))return {ready:false,message:'Añade una expresión del inventario o descríbela en los ajustes.'};
    }else if(!source){
      if(!pieces.length&&!additionalPrompt.trim())return {ready:false,message:'Describe la imagen que quieres probar o añade elementos del inventario.'};
    }
    return {ready:true,message:source?'Composición recuperada. Conserva sus ajustes y revisiones.':pieces.length?`${pieces.length} elemento${pieces.length===1?'':'s'} seleccionado${pieces.length===1?'':'s'}. Listo para generar.`:'Prueba libre a partir de tu descripción. No necesitas guardar elementos antes.'};
  }
  const api={TYPES,LABELS,label,icon,creators,inventory,preview,card,slot,readiness};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.AnimaWorkspace=api;
})(typeof globalThis!=='undefined'?globalThis:this);
