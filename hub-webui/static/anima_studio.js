'use strict';

(() => {
  const API = '/api/anima-studio';
  const $ = id => document.getElementById(id);
  const KINDS = {character:'Personaje', personality:'Personalidad', outfit:'Outfit', pose:'Pose', expression:'Expresión', background:'Fondo', item:'Objeto', scene:'Escenario'};
  const STATES = {pending:'Pendiente', accepted:'Aceptado', rejected:'Rechazado'};
  const state = {projects:[], styles:[], pieces:[], recipes:[], generations:[], catalog:{models:[],loras:[],text_encoders:[],vaes:[]}, projectId:'', productionCategory:'', productionMode:'txt2img', sampleIds:new Set(), purgeIds:new Set(), activeJob:null, masterId:null, masterBase64:null, masterReady:false, masterToken:0, currentStyle:null, recipeStyleRevision:null, recipePieceRevisions:{}, recipeFormatSnapshot:null};
  const session={id:null,active:false,status:'starting',error:'',timer:null,inFlight:null,controller:null,socket:null,epoch:0,generationPending:false};
  const catalogPicker={kind:'models',row:null,limit:48,folder:'*'};
  const deleted = Object.fromEntries(['projects','styles','pieces','recipes','generations'].map(kind=>[kind,new Set()]));
  let storageRetryAt=0,storageCleanupBusy=false,storagePending=false;
  let productionPose=null,piecePose=null,pieceDraft=null;
  const pieceProfiles=globalThis.AnimaPieces;
  const workspace=globalThis.AnimaWorkspace;
  let visualStyles=null;
  const inventoryView={kind:'',limit:24};
  const pinnedPieceViews=new Map();
  const unavailablePreviews=new Set();
  let favorites;
  try { favorites = new Set(JSON.parse(localStorage.getItem('anima-studio-favorites') || '[]')); } catch { favorites = new Set(); }
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const list = value => Array.isArray(value) ? value : (value?.items || value?.records || []);
  const currentProject = () => state.projects.find(p => p.id === state.projectId);
  const inProject = item => !item.project_id || item.project_id === state.projectId;
  const projectGenerations = () => state.generations.filter(g => g.project_id === state.projectId);
  const imageUrl = id => `${API}/images/${encodeURIComponent(id)}`;
  const optionValue = item => typeof item === 'string' ? item : (item?.file ?? item?.id ?? item?.name ?? '');
  const optionName = item => typeof item === 'string' ? item : (item?.name ?? item?.label ?? item?.file ?? item?.id ?? '');

  async function api(path, method='GET', body, options={}) {
    const response = await fetch(API + path, {method, cache:'no-store', headers: body === undefined ? {} : {'Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)}),...options});
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!response.ok) {
      let detail = data?.detail ?? data?.error ?? data ?? `Error ${response.status}`;
      if (typeof detail !== 'string') detail = JSON.stringify(detail);
      throw new Error(detail);
    }
    return data;
  }
  function notice(message, error=false) {
    const node = $('notice');
    const dialog=document.querySelector?.('dialog[open]');
    if(dialog)dialog.append(node);else document.body?.append(node);
    node.setAttribute('role',error?'alert':'status');node.setAttribute('aria-live',error?'assertive':'polite');
    node.className = `notice${error ? ' error' : ''}`;
    node.innerHTML = `<span>${esc(message)}</span><button aria-label="Cerrar mensaje">✕</button>`;
    node.querySelector('button').onclick = () => node.classList.add('hidden');
  }
  const action = fn => async event => { try { await fn(event); } catch (error) { console.error(error); notice(error.message || String(error), true); } };
  function sampleAvailable(id) {
    const record=state.generations.find(g=>g.id===id);
    return !deleted.generations.has(id)&&!record?.purged_at&&!record?.purged;
  }
  function availableRecords(kind,items) {
    if(kind==='generations')for(const item of list(items))if(item.purged_at||item.purged)deleted.generations.add(item.id);
    return list(items).filter(item=>!deleted[kind].has(item.id)).map(item=>kind==='styles'?{...item,sample_ids:(item.sample_ids||[]).filter(sampleAvailable)}:item);
  }
  function forgetDeleted(kind,id) {
    deleted[kind].add(id);state[kind]=availableRecords(kind,state[kind]);
    if(kind==='generations'){
      state.sampleIds.delete(id);state.purgeIds.delete(id);
      state.styles=availableRecords('styles',state.styles);
      if(state.currentStyle)state.currentStyle={...state.currentStyle,sample_ids:(state.currentStyle.sample_ids||[]).filter(sample=>sample!==id)};
      if($('image-dialog-image').getAttribute('src')===imageUrl(id)){$('image-dialog').close();$('image-dialog-image').removeAttribute('src');}
    }
    renderVisualWorkspace();
  }
  async function refreshAfterDelete(refresh,message) {
    notice(message);
    try{await refresh();}catch(error){console.error(error);notice(`${message} No se pudo actualizar el resto de la biblioteca: ${error.message}. Usa Actualizar para reintentarlo.`,true);}
  }
  function selectOptions(element, items, emptyLabel, preferred) {
    const previous = preferred ?? element.value;
    element.innerHTML = (emptyLabel === undefined ? '' : `<option value="">${esc(emptyLabel)}</option>`) + items.map(item => `<option value="${esc(optionValue(item))}">${esc(optionName(item))}</option>`).join('');
    // ComfyUI returns Windows separators; keep the local catalog's exact value.
    const normalized=String(previous??'').replace(/\\/g,'/');
    const match=[...element.options].find(o=>o.value===previous)
      || [...element.options].find(o=>o.value.replace(/\\/g,'/')===normalized);
    if (match) element.value = match.value;
    else if (preferred) { const option = new Option(`No disponible: ${preferred}`, preferred); option.disabled = true; option.selected = true; element.add(option); }
    visualStyles?.refresh();
  }
  function assertProject() { if (!currentProject()) throw new Error('Crea o selecciona un proyecto primero.'); }
  function seedValue(id) {
    const value = $(id).value.trim();
    if (!/^\d+$/.test(value) || BigInt(value) > 18446744073709551615n) throw new Error('La seed debe ser un entero entre 0 y 18446744073709551615.');
    return BigInt(value).toString();
  }
  function freshSeed() {
    const values = new Uint32Array(2); crypto.getRandomValues(values);
    return ((BigInt(values[0]) << 32n) | BigInt(values[1])).toString();
  }
  function numeric(id, min, max) {
    const value = Number($(id).value);
    if (!Number.isFinite(value) || value < min || value > max) throw new Error(`Revisa el valor de ${$(id).closest('label')?.childNodes[0]?.textContent.trim() || id}.`);
    return value;
  }
  function setTab(name) {
    document.querySelectorAll('.tab').forEach(tab => { const active=tab.dataset.tab===name; tab.classList.toggle('active',active); tab.setAttribute('aria-selected',String(active)); });
    document.querySelectorAll('.page').forEach(page => page.classList.toggle('active',page.id===`tab-${name}`));
  }
  function renderProjects() {
    selectOptions($('project-select'),state.projects,'Selecciona un proyecto',state.projectId);
    $('edit-project').disabled = !currentProject();
    const project = currentProject();
    renderFormatOptions();
    selectOptions($('category-select'),project?.categories || [],'Selecciona categoría');
    $('purge-days').value = project?.retention_days ?? 30;
    renderProductionMenu();
  }
  function productionCategories() { return currentProject()?.categories || []; }
  const sameCategory = (a,b) => String(optionValue(a)??'')===String(optionValue(b)??'');
  function productionLabel(category) {
    const name=String(optionName(category)||'');
    return typeof category==='string'&&/^(characters?|personajes?|backgrounds?|fondos?|items?|objects?|objetos?|props?|cards?|cartas?|tarjetas?)$/i.test(name)?AnimaProduction.profile(category).label:name;
  }
  function productionProfile() { return AnimaProduction.profile(productionCategories().find(c=>String(optionValue(c))===state.productionCategory)||state.productionCategory); }
  function renderProductionMenu() {
    const categories=productionCategories();
    if(state.productionCategory&&!categories.some(c=>String(optionValue(c))===state.productionCategory))state.productionCategory='';
    let expressionCategories=categories.filter(c=>AnimaProduction.profile(c).id==='character');
    if(!expressionCategories.length)expressionCategories=categories.filter(c=>AnimaProduction.profile(c).expression);
    $('production-menu').innerHTML=categories.map(c=>{
      const p=AnimaProduction.profile(c),label=productionLabel(c);
      return `<button class="production-choice" data-production-category="${esc(optionValue(c))}" data-production-mode="txt2img" aria-pressed="${state.productionCategory===String(optionValue(c))&&state.productionMode==='txt2img'}"><strong>${esc(label)}</strong><span>${esc(p.description)}</span></button>`;
    }).join('')+expressionCategories.map(c=>`<button class="production-choice" data-production-category="${esc(optionValue(c))}" data-production-mode="expression"><strong>Expresiones${expressionCategories.length>1?` · ${esc(productionLabel(c))}`:''}</strong><span>Cambia la expresión de un personaje a partir de un maestro y una máscara facial.</span></button>`).join('');
    if(!categories.length)$('production-menu').innerHTML=empty('Configura las categorías del proyecto','Añade personajes, fondos, objetos u otras categorías desde Configurar.');
    const chosen=!!state.productionCategory,p=productionProfile();
    $('production-workspace').classList.toggle('hidden',!chosen);
    $('production-menu').classList.remove('hidden');
    $('change-production').classList.add('hidden');
    $('production-title').textContent=chosen?(state.productionMode==='expression'?`Expresiones · ${productionLabel(categories.find(c=>String(optionValue(c))===state.productionCategory))}`:productionLabel(categories.find(c=>String(optionValue(c))===state.productionCategory))):'¿Qué quieres generar?';
    $('production-description').textContent=chosen?(state.productionMode==='expression'?'Elige un maestro, pinta la zona facial y describe la expresión.':p.description):'Cada recurso tiene su propio espacio y las herramientas que necesita.';
    $('prod-prompt').placeholder=p.promptPlaceholder;
    $('expression-controls').classList.toggle('hidden',state.productionMode!=='expression');
    $('production-pose-tools').classList.toggle('hidden',p.id!=='character'||state.productionMode==='expression');
    $('generate-production').textContent=state.productionMode==='expression'?'Generar expresión':'Generar imagen';
    $('production-results-help').textContent=p.expression?'Acepta una imagen para organizarla. Usa «Como maestro» para derivar expresiones.':'Acepta una imagen para organizarla o borra los resultados que no quieras conservar.';
    updateCompositionSource();
    renderVisualWorkspace();
  }
  function chooseProduction(category,mode='txt2img') {
    const available=productionCategories().find(c=>String(optionValue(c))===String(category));
    if(!available)throw new Error('Esta categoría ya no está en el proyecto. Revisa su configuración.');
    if(mode==='expression'&&!AnimaProduction.profile(available).expression)throw new Error('Las expresiones requieren una categoría de personajes.');
    const changed=state.productionCategory!==String(category)||state.productionMode!==mode;
    state.productionCategory=String(category);state.productionMode=mode;
    if(changed){
      releaseComposition(false);productionPose?.setValue(null);
      state.derivedConfig=null;
      $('piece-pickers').replaceChildren();$('recipe-select').value='';state.recipeStyleRevision=null;state.recipePieceRevisions={};state.recipeFormatSnapshot=null;
      for(const id of ['prod-prompt','prod-negative','recipe-name','expression-name'])$(id).value='';
      clearMaster();renderFormatOptions();
      const preferred=AnimaProduction.preferredFormat(productionProfile(),currentProject()?.formats||[]);
      if(preferred)$('format-select').value=preferred;
      $('composed-output').textContent='Aquí aparecerá la combinación resuelta.';$('assembled-preview').open=false;
    }
    $('category-select').value=String(category);
    document.querySelectorAll('[name="mode"]').forEach(r=>r.checked=r.value===mode);
    renderProductionMenu();renderPieces();renderGalleries();
  }
  function renderFormatOptions() {
    const formats=currentProject()?.formats||[], snapshot=state.recipeFormatSnapshot;
    selectOptions($('format-select'),formats,formats.length?undefined:'Configura los formatos del proyecto');
    if(snapshot){
      if(![...$('format-select').options].some(option=>option.value===snapshot.id))$('format-select').add(new Option(`${snapshot.name||snapshot.id} · guardado`,snapshot.id));
      $('format-select').value=snapshot.id;
    }
    updateRecipeNote();
  }
  function updateRecipeNote() {
    const parts=[];
    if(state.recipeStyleRevision!==null)parts.push(`estilo v${state.recipeStyleRevision}`);
    if(state.recipeFormatSnapshot){const format=state.recipeFormatSnapshot;parts.push(`${format.name||'formato'} ${format.width} × ${format.height}${format.alpha?' · alfa requerido':''}`);}
    $('recipe-revision-note').textContent=parts.length?`Configuración guardada: ${parts.join(' · ')}.`:'';
    $('recipe-revision-note').classList.toggle('hidden',!parts.length);
  }
  function renderStyles() {
    state.styles=availableRecords('styles',state.styles);
    const term=$('style-search').value.trim().toLowerCase();
    const allStyles=state.styles.filter(inProject);
    const styles = allStyles.filter(s=>`${s.name} ${s.prompt}`.toLowerCase().includes(term));
    const options = styles.map(s => ({id:s.id,name:`${favorites.has(s.id)?'★ ':''}${s.name} · v${s.revision || 1}${s.project_id?'':' · Compartido'}`}));
    selectOptions($('cal-style'),options,'Sin estilo guardado');
    selectOptions($('prod-style'),allStyles.map(s=>({id:s.id,name:`${favorites.has(s.id)?'★ ':''}${s.name} · v${s.revision||1}${s.project_id?'':' · Compartido'}`})),'Selecciona un estilo guardado');
    $('favorite-style').disabled=!$('cal-style').value;
    $('favorite-style').textContent=favorites.has($('cal-style').value)?'★':'☆';
    $('delete-style').disabled=!$('cal-style').value;
    $('style-shelf').innerHTML=styles.length?styles.slice(0,12).map(style=>`<div class="style-entry"><button class="style-tile ${style.id===$('cal-style').value?'selected':''}" data-load-style="${esc(style.id)}">${style.sample_ids?.length?`<img src="${imageUrl(style.sample_ids[0])}" alt="Muestra de ${esc(style.name)}" loading="lazy">`:'<span class="style-swatch">A</span>'}<span><strong>${favorites.has(style.id)?'★ ':''}${esc(style.name)}</strong><small>v${style.revision||1}${style.project_id?'':' · Compartido'}</small></span></button><button class="text-button" data-delete-resource="styles" data-id="${esc(style.id)}">Borrar estilo</button></div>`).join(''):'<p class="hint">Guarda tu primera calibración para verla aquí.</p>';
    $('style-count').textContent = `${allStyles.length} estilo${allStyles.length === 1 ? '' : 's'} disponible${allStyles.length === 1 ? '' : 's'}`;
    if(styles.length>12)$('style-shelf').innerHTML+=`<button class="secondary" data-open-styles="cal-style">Ver los ${styles.length} estilos</button>`;
    renderVisualWorkspace();
  }
  function renderCatalog() {
    const previousModel=$('model-select').value;
    selectOptions($('model-select'),state.catalog.models || [],state.catalog.models?.length ? undefined : 'No hay modelos Anima compatibles',previousModel||undefined);
    selectOptions($('encoder-select'),state.catalog.text_encoders || [],state.catalog.text_encoders?.length ? undefined : 'Encoder no disponible');
    selectOptions($('vae-select'),state.catalog.vaes || [],state.catalog.vaes?.length ? undefined : 'VAE no disponible');
    renderModelChoice();
    for (const row of $('lora-list').children) {
      const select=row.querySelector('select'), previous=select.value;
      selectOptions(select,state.catalog.loras || [],'Selecciona un LoRA',previous);
      renderLoraChoice(row);
    }
    $('catalog-note').textContent = `${state.catalog.models?.length || 0} modelos y ${state.catalog.loras?.length || 0} LoRAs Anima en la biblioteca del Hub. Los pesos no se copian al taller.`;
    if($('catalog-dialog').open)renderCatalogPicker();
  }
  const catalogName = item => item?.display_name || item?.name || item?.file || '';
  const catalogFilename = item => item?.relative_path || item?.backend_file || item?.file || '';
  const normalizeCatalogText = value => String(value??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase();
  const catalogTags = item => Array.isArray(item?.tags)?item.tags.map(String):[];
  function catalogFolder(item) {
    const backend=item?.backend_file||item?.file;
    const parts=String(backend||item?.relative_path||'').replace(/\\/g,'/').split('/').filter(Boolean);
    if(!backend&&parts[0]?.toLowerCase()==='loras')parts.shift();
    if(parts[0]?.toLowerCase()==='anima')parts.shift();
    return parts.slice(0,-1).join('/');
  }
  function catalogFolders() {
    const entries=state.catalog.loras||[],counts=new Map();
    for(const item of entries){const folder=catalogFolder(item);counts.set(folder,(counts.get(folder)||0)+1);}
    return [{value:'*',label:'Todos',count:entries.length},...[...counts].sort(([a],[b])=>a.localeCompare(b,'es',{numeric:true})).map(([value,count])=>({value,label:value||'Sin carpeta',count}))];
  }
  function renderCatalogFolders() {
    const tabs=$('catalog-folders'),grid=$('catalog-grid'),isLora=catalogPicker.kind==='loras';
    tabs.classList.toggle('hidden',!isLora);
    grid.setAttribute('role',isLora?'tabpanel':'region');
    if(!isLora){tabs.innerHTML='';grid.setAttribute('aria-labelledby','catalog-title');return;}
    const folders=catalogFolders();
    if(!folders.some(folder=>folder.value===catalogPicker.folder)){catalogPicker.folder='*';catalogPicker.limit=48;grid.scrollTop=0;}
    tabs.innerHTML=folders.map((folder,index)=>`<button id="catalog-folder-${index}" type="button" role="tab" class="catalog-folder-tab" data-catalog-folder="${esc(folder.value)}" aria-selected="${folder.value===catalogPicker.folder}" aria-controls="catalog-grid" tabindex="${folder.value===catalogPicker.folder?0:-1}" title="${esc(folder.label)}">${esc(folder.label)}<span>${folder.count}</span></button>`).join('');
    grid.setAttribute('aria-labelledby',`catalog-folder-${folders.findIndex(folder=>folder.value===catalogPicker.folder)}`);
  }
  function selectCatalogFolder(folder,focus=false) {
    if(catalogPicker.kind!=='loras'||!catalogFolders().some(item=>item.value===folder))return;
    catalogPicker.folder=folder;catalogPicker.limit=48;renderCatalogPicker();$('catalog-grid').scrollTop=0;
    if(focus)$(`catalog-folder-${catalogFolders().findIndex(item=>item.value===folder)}`).focus();
  }
  function catalogThumbnail(item) {
    const url=item?.thumbnail_url;
    return typeof url==='string'&&/^\/api\/(?:anima-studio|vault)\//.test(url)?url:'';
  }
  function catalogEntry(kind,value) {
    const normalized=String(value||'').replace(/\\/g,'/');
    return (state.catalog[kind]||[]).find(item=>String(optionValue(item)).replace(/\\/g,'/')===normalized);
  }
  function catalogPreview(item) {
    const url=catalogThumbnail(item);
    return `<span class="catalog-preview${url?' has-image':''}">${url?`<img src="${esc(url)}" alt="Vista previa de ${esc(catalogName(item))}" loading="lazy" decoding="async">`:''}<span class="catalog-no-image"><span aria-hidden="true">▧</span>Sin miniatura</span></span>`;
  }
  function markBrokenCatalogImage(event) {
    const image=event.target;
    if(image.tagName==='IMG'&&image.closest('.catalog-preview'))image.parentElement.classList.remove('has-image');
  }
  function renderCatalogChoice(button,kind,value) {
    const item=catalogEntry(kind,value),label=kind==='models'?'modelo':'LoRA';
    button.classList.toggle('unavailable',!!value&&!item);
    button.innerHTML=catalogPreview(item)+`<span class="catalog-choice-info"><strong>${esc(item?catalogName(item):value?'Archivo no disponible':`Escoge un ${label}`)}</strong><small>${esc(catalogFilename(item)||value||'Explora la galería de Anima')}</small><span class="catalog-choice-action">${value?'Cambiar':'Explorar'} ${label}</span></span>`;
    button.setAttribute('aria-label',`${value?'Cambiar':'Escoger'} ${label}${item?`: ${catalogName(item)}`:''}`);
  }
  function renderModelChoice() {renderCatalogChoice($('choose-model'),'models',$('model-select').value);}
  function renderLoraChoice(row) {renderCatalogChoice(row.querySelector('.choose-lora'),'loras',row.querySelector('select').value);}
  function openCatalogPicker(kind,row=null) {
    catalogPicker.kind=kind;catalogPicker.row=row;catalogPicker.limit=48;catalogPicker.folder='*';
    $('catalog-title').textContent=kind==='models'?'Modelos Anima':'LoRAs Anima';
    $('catalog-description').textContent=kind==='models'?'Escoge el modelo que dará forma al estilo.':'Combina LoRAs de estilo, personaje u outfit; después ajusta su peso.';
    $('catalog-search').value='';$('catalog-images-only').checked=false;
    renderCatalogPicker();$('catalog-grid').scrollTop=0;$('catalog-dialog').showModal();$('catalog-search').focus();
  }
  function matchingCatalogEntries(kind,query,imagesOnly=false,folder='*') {
    const terms=normalizeCatalogText(query).trim().split(/\s+/).filter(Boolean);
    return (state.catalog[kind]||[]).filter(item=>{
      if(kind==='loras'&&folder!=='*'&&catalogFolder(item)!==folder)return false;
      if(imagesOnly&&!catalogThumbnail(item))return false;
      const text=normalizeCatalogText([catalogName(item),catalogFilename(item),item.file,item.name,...catalogTags(item)].join(' '));
      return terms.every(term=>text.includes(term));
    });
  }
  function pickerValue() {return catalogPicker.kind==='models'?$('model-select').value:catalogPicker.row?.querySelector('select').value||'';}
  function renderCatalogPicker() {
    renderCatalogFolders();
    const kind=catalogPicker.kind,selected=pickerValue();
    const used=new Set(kind==='loras'?[...$('lora-list').children].filter(row=>row!==catalogPicker.row).map(row=>row.querySelector('select').value):[]);
    const matches=matchingCatalogEntries(kind,$('catalog-search').value,$('catalog-images-only').checked,catalogPicker.folder).sort((a,b)=>
      Number(optionValue(b)===selected)-Number(optionValue(a)===selected)||Number(!!catalogThumbnail(b))-Number(!!catalogThumbnail(a))||catalogName(a).localeCompare(catalogName(b),'es'));
    const visible=matches.slice(0,catalogPicker.limit);
    $('catalog-results').textContent=`${matches.length} ${kind==='models'?'modelos':'LoRAs'}${visible.length<matches.length?` · mostrando ${visible.length}`:''}`;
    $('catalog-grid').innerHTML=visible.length?visible.map(item=>{
      const file=optionValue(item),isSelected=file===selected,alreadyUsed=used.has(file),tags=catalogTags(item);
      return `<button type="button" class="catalog-card${isSelected?' selected':''}" data-catalog-file="${esc(file)}" aria-pressed="${isSelected}" aria-label="${esc(`${alreadyUsed?'Ya añadido':isSelected?'Seleccionado':'Elegir'}: ${catalogName(item)}`)}" ${alreadyUsed?'disabled':''}>${catalogPreview(item)}<span class="catalog-card-info"><span class="catalog-card-type">${kind==='models'?'Modelo':'LoRA'} Anima${isSelected?' · Seleccionado':alreadyUsed?' · Ya añadido':''}</span><strong>${esc(catalogName(item))}</strong><small title="${esc(catalogFilename(item))}">${esc(catalogFilename(item))}</small>${tags.length?`<span class="catalog-tags">${tags.slice(0,4).map(tag=>`<span>${esc(tag)}</span>`).join('')}</span>`:''}</span></button>`;
    }).join(''):empty('No hay coincidencias','Prueba otro nombre, archivo o etiqueta, o desactiva el filtro de miniaturas.');
    $('catalog-more').classList.toggle('hidden',visible.length>=matches.length);
  }
  function chooseCatalogFile(file) {
    const kind=catalogPicker.kind,item=(state.catalog[kind]||[]).find(entry=>optionValue(entry)===file);
    if(!item)return;
    if(kind==='models'){$('model-select').value=file;renderModelChoice();}
    else {
      const used=[...$('lora-list').children].some(row=>row!==catalogPicker.row&&row.querySelector('select').value===file);
      if(used)return;
      if(catalogPicker.row){catalogPicker.row.querySelector('select').value=file;renderLoraChoice(catalogPicker.row);}
      else addLora(file);
    }
    $('catalog-dialog').close();
  }
  function renderPieces() {
    const previous = selectedPieces();
    const pieces = state.pieces.filter(inProject);
    const source=state.sourceRecord?.snapshot||state.sourceRecord?.metadata?.snapshot;
    for(const piece of source?.pieces||[])if(!pieces.some(p=>p.id===piece.id))pieces.push(piece);
    $('piece-pickers').innerHTML = Object.entries(KINDS).filter(([kind])=>state.productionCategory&&productionProfile().kinds.includes(kind)).map(([kind,label]) => `<label>${label}<select data-piece-kind="${kind}"><option value="">Sin ${label.toLowerCase()}</option>${pieces.filter(p=>p.kind===kind).map(p=>`<option value="${esc(p.id)}">${esc(p.name)}${p.project_id?'':' · Compartido'}</option>`).join('')}</select></label>`).join('');
    for (const select of $('piece-pickers').querySelectorAll('select')) {
      const old = previous.find(id => pieces.some(p=>p.id===id && p.kind===select.dataset.pieceKind));
      if (old) select.value=old;
      select.dataset.previousValue=select.value;
    }
    renderPieceLibrary();
    selectOptions($('recipe-select'),state.recipes.filter(inProject).filter(r=>r.category&&sameCategory(r.category,state.productionCategory)&&(r.mode||'txt2img')===state.productionMode).map(r=>({id:r.id,name:r.name})),'Nueva combinación');
    $('delete-recipe').disabled=!$('recipe-select').value;
    renderRecipeLibrary();
    updateCompositionSource();
    renderVisualWorkspace();
  }
  function renderPieceLibrary() {
    const term=$('piece-search').value.trim().toLowerCase(), kind=$('piece-filter').value, onlyFavorites=$('favorites-only').checked;
    const pieces=state.pieces.filter(inProject).filter(p=>(!kind || p.kind===kind) && (!onlyFavorites || favorites.has(p.id)) && `${p.name} ${p.prompt} ${(p.tags||[]).join(' ')}`.toLowerCase().includes(term));
    $('pieces-library').innerHTML=pieces.length ? pieces.map(p=>`<article class="piece-card">${p.sample_asset_id?`<img class="piece-thumbnail" src="${imageUrl(p.sample_asset_id)}" alt="Prueba de ${esc(p.name)}" loading="lazy">`:''}<div class="piece-top"><div><div class="piece-kind">${esc(KINDS[p.kind]||p.kind)} · ${p.master_id?'Variante':'Maestro'} · v${p.revision||1}${p.project_id?'':' · Compartida'}</div><h4>${esc(p.name)}</h4></div><button class="favorite ${favorites.has(p.id)?'active':''}" data-favorite="${esc(p.id)}" aria-label="${favorites.has(p.id)?'Quitar de':'Añadir a'} favoritos">${favorites.has(p.id)?'★':'☆'}</button></div><p>${esc(p.prompt)}</p>${p.seed!==undefined?`<small class="muted">Seed de prueba ${esc(p.seed)}</small>`:''}<div class="asset-actions"><button data-edit-piece="${esc(p.id)}">Editar</button><button data-variant-piece="${esc(p.id)}">Crear variante</button><button data-use-piece="${esc(p.id)}">Usar en producción</button><button class="danger" data-delete-resource="pieces" data-id="${esc(p.id)}">Borrar</button></div></article>`).join('') : empty('Aún no hay piezas en esta vista','Guarda un maestro de personaje, outfit, fondo u objeto y crea variantes desde él.');
  }
  function renderRecipeLibrary() {
    const recipes=state.recipes.filter(inProject);
    $('recipes-library').innerHTML=recipes.length?recipes.map(r=>`<article class="piece-card"><h4>${esc(r.name)}</h4><p>${esc(r.category||'Sin categoría')} · v${r.revision||1}</p><div class="asset-actions"><button data-use-recipe="${esc(r.id)}">Usar receta</button><button class="danger" data-delete-resource="recipes" data-id="${esc(r.id)}">Borrar</button></div></article>`).join(''):empty('Sin recetas guardadas','Guarda una combinación desde el espacio del recurso.');
  }
  function empty(title,description) { return `<div class="empty-state"><strong>${esc(title)}</strong><p>${esc(description)}</p></div>`; }
  function recordInfo(g) {
    const config=g.config || g.metadata?.config || g.snapshot?.config || g.metadata?.snapshot?.config || {};
    return {name:g.name || [g.character_name||g.character,g.outfit_name||g.outfit,g.expression_name||g.expression].filter(Boolean).join(' · ') || g.category || 'Prueba de generación',seed:String(g.seed ?? g.edit_seed ?? config.seed ?? g.metadata?.seed ?? '—'),config};
  }
  function assetCard(g,context) {
    const info=recordInfo(g), selected=context==='cal'?state.sampleIds.has(g.id):state.purgeIds.has(g.id);
    const choose=context==='cal' ? `<label class="asset-check"><input type="checkbox" data-sample="${esc(g.id)}" ${selected?'checked':''}>Muestra del estilo</label>` : context==='review' && g.status==='rejected' ? `<label class="asset-check"><input type="checkbox" data-purge="${esc(g.id)}" ${selected?'checked':''}>Seleccionar</label>` : '';
    const status=g.status||'pending';
    const pieceTest=(g.purpose||g.metadata?.purpose)==='piece_test';
    const designActions=`<button data-capture-piece="${esc(g.id)}">${pieceTest?'Elegir imagen del elemento':'Crear maestro de pieza'}</button>${!pieceTest&&(g.snapshot||g.metadata?.snapshot)?.style?.id?`<button data-use-composition="${esc(g.id)}">Usar composición</button>`:''}`;
    return `<article class="asset-card"><div class="asset-image"><img src="${imageUrl(g.id)}" loading="lazy" alt="${esc(info.name)}" data-open-image="${esc(g.id)}">${choose}</div><div class="asset-content"><div class="asset-title">${esc(info.name)}</div><div class="asset-meta">Seed ${esc(info.seed)}${info.config.steps?` · ${esc(info.config.steps)} pasos`:''}</div><span class="asset-state ${esc(status)}">${esc(STATES[status]||status)}</span><div class="asset-actions">${status!=='accepted'?`<button data-review="accepted" data-id="${esc(g.id)}">Aceptar</button>`:''}${status!=='rejected'?`<button data-review="rejected" data-id="${esc(g.id)}">Rechazar</button>`:`<button data-review="pending" data-id="${esc(g.id)}">Restaurar</button>`}${!pieceTest&&productionCategories().some(c=>AnimaProduction.profile(c).expression&&sameCategory(c,g.category))?`<button data-master="${esc(g.id)}">Como maestro</button>`:''}${designActions}<a href="${imageUrl(g.id)}?download=true" download>PNG</a><a href="${API}/generations/${encodeURIComponent(g.id)}" download="${esc(g.id)}.json">Ficha</a>${context==='cal'?`<button data-load-config="${esc(g.id)}">Recuperar ajustes</button>`:''}<button class="danger" data-delete-image="${esc(g.id)}">Borrar imagen</button></div></div></article>`;
  }
  function renderGalleries() {
    const generations=availableRecords('generations',projectGenerations()).slice().reverse();
    if(workspace){
      const tests=generations.filter(g=>(g.piece_test||g.metadata?.piece_test)?.id===state.elementTestId&&state.elementTestId);
      $('element-tests').classList.toggle('hidden',!state.elementTestId);
      $('element-test-gallery').innerHTML=tests.length?tests.slice(0,8).map(g=>assetCard(g,'element')).join(''):empty('Preparando la prueba del elemento','Sus resultados aparecerán aquí; después elige la imagen para tu inventario.');
    }
    const calibration=generations.filter(g=>g.purpose==='calibration' || g.metadata?.purpose==='calibration' || g.category==='calibration' || g.category==='calibracion' || state.sampleIds.has(g.id));
    $('cal-gallery').innerHTML=calibration.length ? calibration.map(g=>assetCard(g,'cal')).join('') : empty('Aquí empieza tu biblioteca de estilos','Genera una prueba para comparar el acabado y guardar sus ajustes.');
    const production=generations.filter(g=>!['calibration','piece_test'].includes(g.purpose) && !['calibration','piece_test'].includes(g.metadata?.purpose) && !['calibration','calibracion'].includes(g.category)&&sameCategory(g.category,state.productionCategory)&&(g.mode||g.metadata?.mode||'txt2img')===state.productionMode);
    $('production-gallery').innerHTML=production.length ? production.map(g=>assetCard(g,'production')).join('') : empty('Tu próximo recurso aparecerá aquí','Selecciona un estilo y las piezas que formarán la imagen.');
    const filter=$('review-filter').value, term=$('image-search').value.trim().toLowerCase();
    const review=generations.filter(g=>(!filter||g.status===filter) && JSON.stringify([g.name,g.category,g.character_name||g.character,g.outfit_name||g.outfit,g.expression_name||g.expression,g.id]).toLowerCase().includes(term));
    $('review-gallery').innerHTML=review.length ? review.map(g=>assetCard(g,'review')).join('') : empty('No hay imágenes en esta vista','Cambia el filtro o genera un recurso para revisarlo.');
    $('review-count').textContent=`${generations.length} imágenes · ${generations.filter(g=>g.status==='accepted').length} aceptadas · ${generations.filter(g=>g.status==='rejected').length} rechazadas`;
    $('purge-controls').classList.toggle('hidden',filter!=='rejected');
    updatePurgeCount();
    selectOptions($('master-select'),generations.filter(g=>g.status!=='rejected'&&sameCategory(g.category,state.productionCategory)).map(g=>({id:g.id,name:`${recordInfo(g).name} · ${g.id.slice(0,8)}`})),'Selecciona una imagen o carga un PNG',state.masterId || '');
    renderVisualWorkspace();
  }
  function updatePurgeCount() {
    const selected=projectGenerations().filter(g=>state.purgeIds.has(g.id)&&g.status==='rejected');
    const bytes=selected.reduce((sum,g)=>sum+(g.size_bytes||g.bytes||0),0);
    $('purge-count').textContent=`${selected.length} seleccionadas${bytes?` · ${(bytes/1048576).toFixed(1)} MB`:''}. Los maestros utilizados quedan protegidos.`;
    $('purge-rejected').disabled=!selected.length;
  }
  function renderAll() { renderProjects(); renderStyles(); renderCatalog(); renderPieces(); renderGalleries(); }
  async function reload() {
    const data=await api('/bootstrap');
    for (const kind of ['generations','projects','styles','pieces','recipes']) state[kind]=availableRecords(kind,data[kind]);
    if (data.catalog) state.catalog=data.catalog;
    storagePending=!!data.storage_cleanup?.cleanup_pending;
    if (!state.projects.some(p=>p.id===state.projectId)) {
      let remembered=''; try { remembered=localStorage.getItem('anima-studio-project')||''; } catch {}
      state.projectId=state.projects.find(p=>p.id===remembered)?.id || state.projects[0]?.id || '';
    }
    updateStatus(data.status || {}); renderAll();
    if(data.catalog_error)notice(data.catalog_error,true);
    if(data.storage_cleanup?.errors?.length&&!data.storage_cleanup.deferred)notice(`Limpieza pendiente: ${data.storage_cleanup.errors.join(' ')}`,true);
  }
  function updateStatus(status) {
    $('capability-note').textContent=(status.limitations||[]).join(' ');
    updateSessionView();
  }
  function sessionReady() {return session.active&&session.status==='ready'&&session.socket?.readyState===WebSocket.OPEN;}
  function updateGenerationAvailability() {
    const busy=session.generationPending||!!state.activeJob;
    document.querySelectorAll('.generate-action').forEach(button=>button.disabled=busy||!sessionReady());
    if(workspace)$('generate-production').disabled=busy||!sessionReady()||!visualReadiness().ready;
    $('project-select').disabled=busy;
  }
  function updateSessionView() {
    const ready=sessionReady(),failed=session.status==='error';
    $('backend-status').className=`status-pill ${ready?'up':'down'}`;
    $('backend-status').textContent=ready?'ComfyUI disponible':failed?'ComfyUI no disponible':'Iniciando ComfyUI…';
    $('backend-status').title=failed?session.error:'El backend se prepara automáticamente para esta pestaña.';
    $('backend-session').classList.toggle('hidden',ready);
    $('backend-session').classList.toggle('error',failed);
    $('backend-session-title').textContent=failed?'No se pudo preparar ComfyUI':'Iniciando ComfyUI…';
    $('backend-session-message').textContent=failed?(session.error||'Reintenta para volver a preparar el backend.'):'El taller prepara el backend automáticamente. Puedes organizar tus estilos y piezas mientras inicia.';
    $('retry-backend').classList.toggle('hidden',!failed);
    $('retry-backend').disabled=!!session.inFlight;
    updateGenerationAvailability();
    if(ready&&storagePending&&!storageCleanupBusy&&!state.activeJob&&!session.generationPending&&Date.now()>=storageRetryAt)void cleanupStorage(true);
  }
  async function cleanupStorage(silent=false) {
    if(storageCleanupBusy)return;
    storageCleanupBusy=true;storageRetryAt=Date.now()+30000;
    try{
      const result=await api('/storage/cleanup','POST',{include_orphans:!silent});storagePending=!!result.cleanup_pending;
      for(const id of result.deleted_images||[])forgetDeleted('generations',id);
      renderStyles();renderGalleries();
      if(result.errors?.length&&!result.deferred)notice(`Limpieza pendiente: ${result.errors.join(' ')}`,true);
      else if(!silent)notice(result.deferred?'La limpieza se reintentará cuando ComfyUI esté libre.':`Limpieza completada: ${result.removed_files||0} archivos temporales retirados.`);
    }catch(error){storagePending=true;if(!silent)notice(error.message,true);}
    finally{storageCleanupBusy=false;}
  }
  function scheduleHeartbeat() {
    if(session.timer!==null)clearTimeout(session.timer);
    session.timer=null;
    if(session.active&&session.socket?.readyState<WebSocket.CLOSING)session.timer=setTimeout(()=>{session.timer=null;void requestSession('heartbeat');},10000);
  }
  function sendSessionClose(id) {
    const body=JSON.stringify({session_id:id});
    let sent=false;
    try{sent=navigator.sendBeacon(`${API}/session/close`,new Blob([body],{type:'application/json'}));}catch{}
    if(!sent)void fetch(`${API}/session/close`,{method:'POST',headers:{'Content-Type':'application/json'},body,keepalive:true}).catch(()=>{});
  }
  function connectSessionSocket() {
    const epoch=session.epoch,id=session.id;
    let socket;
    try{socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}${API}/session/socket/${encodeURIComponent(id)}`);}
    catch(error){session.status='error';session.error=`No se pudo conectar la sesión del taller: ${error.message||error}`;sendSessionClose(id);updateSessionView();return false;}
    session.socket=socket;
    let disconnected=false;
    const current=()=>session.active&&epoch===session.epoch&&session.socket===socket;
    const disconnect=()=>{
      if(disconnected||!current())return;
      disconnected=true;session.status='error';session.error='Se perdió la conexión de esta pestaña con ComfyUI. Reintenta para abrir una nueva sesión.';
      if(session.timer!==null)clearTimeout(session.timer);
      session.timer=null;session.controller?.abort();
      try{socket.close();}catch{}
      sendSessionClose(id);updateSessionView();
    };
    socket.onopen=()=>{if(current())updateSessionView();};
    socket.onmessage=event=>{
      if(!current()||disconnected)return;
      let result;try{result=JSON.parse(event.data);}catch{return;}
      if(result?.session_id&&result.session_id!==id)return;
      if(result?.status==='closed'){session.status='error';session.error=result.error||'La sesión de ComfyUI se cerró. Reintenta para abrir una nueva.';}
      else if(['starting','ready','error'].includes(result?.status)){session.status=result.status;session.error=result.error||'';}
      else return;
      updateSessionView();
    };
    socket.onclose=disconnect;socket.onerror=disconnect;
    return true;
  }
  async function requestSession(action='heartbeat') {
    if(!session.active||!session.socket||session.socket.readyState>=WebSocket.CLOSING)return null;
    if(session.inFlight)return session.inFlight;
    const epoch=session.epoch,controller=new AbortController();
    session.controller=controller;
    const timeout=setTimeout(()=>controller.abort(),15000);
    const request=(async()=>{
      try {
        const result=await api(`/session/${action}`,'POST',{session_id:session.id},{signal:controller.signal});
        if(!session.active||epoch!==session.epoch||!session.socket||session.socket.readyState>=WebSocket.CLOSING)return null;
        if(result?.status==='closed'){session.status='error';session.error=result.error||'La sesión de ComfyUI se cerró. Reintenta para abrir una nueva.';return result;}
        if(!['starting','ready','error'].includes(result?.status))throw new Error('El servidor no devolvió un estado válido para ComfyUI.');
        session.status=result.status;session.error=result.error||'';
        return result;
      } catch(error) {
        if(session.active&&epoch===session.epoch&&session.socket?.readyState<WebSocket.CLOSING){session.status='error';session.error=error.name==='AbortError'?'El servidor tardó demasiado en responder. Puedes reintentar la conexión.':error.message||String(error);}
        return null;
      } finally {
        clearTimeout(timeout);
        if(epoch===session.epoch){session.inFlight=null;session.controller=null;updateSessionView();scheduleHeartbeat();}
      }
    })();
    session.inFlight=request;updateSessionView();
    return request;
  }
  function openSession() {
    if(session.inFlight)return session.inFlight;
    if(session.active&&session.status!=='error'&&session.socket?.readyState<WebSocket.CLOSING)return Promise.resolve({session_id:session.id,status:session.status});
    if(session.active)closeSession();
    session.id=crypto.randomUUID();session.active=true;session.epoch++;
    if(session.timer!==null)clearTimeout(session.timer);
    session.timer=null;session.status='starting';session.error='';updateSessionView();
    if(!connectSessionSocket())return Promise.resolve(null);
    return requestSession('open');
  }
  function closeSession() {
    if(!session.active)return;
    const closingId=session.id,closingSocket=session.socket;
    session.active=false;session.epoch++;
    if(session.timer!==null)clearTimeout(session.timer);
    session.timer=null;session.controller?.abort();session.controller=null;session.inFlight=null;session.socket=null;
    try{closingSocket?.close();}catch{}
    sendSessionClose(closingId);
    updateGenerationAvailability();
  }
  async function refreshGenerations() {
    const projectId=state.projectId,items=await api(`/generations?project_id=${encodeURIComponent(projectId)}`);
    if(state.projectId!==projectId)return;
    state.generations=availableRecords('generations',items);renderGalleries();
  }
  function addLora(file='',strength=1) {
    const row=document.createElement('div'); row.className='lora-row';
    row.innerHTML='<select hidden aria-hidden="true" tabindex="-1"></select><button type="button" class="catalog-choice choose-lora" aria-haspopup="dialog" aria-controls="catalog-dialog"></button><label>Peso<input type="number" min="-3" max="3" step="0.05" value="1"></label><button type="button" class="subtle remove-lora" title="Quitar LoRA" aria-label="Quitar LoRA">✕</button>';
    selectOptions(row.querySelector('select'),state.catalog.loras || [],'Selecciona un LoRA',file);
    row.querySelector('input').value=strength;
    row.querySelector('.choose-lora').onclick=()=>openCatalogPicker('loras',row);
    row.querySelector('.remove-lora').onclick=()=>{row.remove();$('empty-loras').classList.toggle('hidden',$('lora-list').children.length>0);};
    row.querySelector('.choose-lora').addEventListener('error',markBrokenCatalogImage,true);renderLoraChoice(row);
    $('lora-list').append(row); $('empty-loras').classList.add('hidden');
    return row;
  }
  function calibrationConfig() {
    if (!$('model-select').value) throw new Error('No hay un modelo Anima seleccionado. Actualiza el catálogo del Hub.');
    const loras=[...$('lora-list').children].map(row=>({file:row.querySelector('select').value,strength:Number(row.querySelector('input').value)}));
    if (loras.some(l=>!l.file || !Number.isFinite(l.strength))) throw new Error('Selecciona el archivo y peso de cada LoRA, o elimina la fila vacía.');
    if (new Set(loras.map(l=>l.file)).size!==loras.length) throw new Error('Un LoRA aparece más de una vez. Ajusta su peso en una sola fila.');
    return {model:$('model-select').value,text_encoder:$('encoder-select').value,vae:$('vae-select').value,loras,steps:numeric('steps',1,100),cfg:numeric('cfg',0,30),sampler:$('sampler').value,scheduler:$('scheduler').value,width:numeric('width',256,2048),height:numeric('height',256,2048),seed:seedValue('cal-seed')};
  }
  function loadConfig(config) {
    selectOptions($('model-select'),state.catalog.models||[],undefined,config.model);
    renderModelChoice();
    selectOptions($('encoder-select'),state.catalog.text_encoders||[],undefined,config.text_encoder);
    selectOptions($('vae-select'),state.catalog.vaes||[],undefined,config.vae);
    for(const [key,id] of Object.entries({steps:'steps',cfg:'cfg',width:'width',height:'height',seed:'cal-seed'})) if(config[key]!==undefined) $(id).value=String(config[key]);
    for(const key of ['sampler','scheduler']) if(config[key]) { if(![...$(key).options].some(o=>o.value===config[key])) $(key).add(new Option(config[key],config[key])); $(key).value=config[key]; }
    $('lora-list').replaceChildren(); for(const lora of config.loras||[]) addLora(lora.file??lora.name,lora.strength??1);
    $('empty-loras').classList.toggle('hidden',(config.loras||[]).length>0);
  }
  function loadStyle(id) {
    const style=state.styles.find(s=>s.id===id); state.currentStyle=style || null;
    $('favorite-style').disabled=!style;$('favorite-style').textContent=favorites.has(id)?'★':'☆';
    if(!style)return;
    $('style-name').value=style.name; $('style-shared').checked=!style.project_id; $('style-prompt').value=style.prompt||''; $('style-negative').value=style.negative||'';
    state.sampleIds=new Set((style.sample_ids||[]).filter(sampleAvailable)); loadConfig(style.config||{}); $('save-style').textContent='Guardar nueva revisión'; renderGalleries();
  }
  async function saveStyle(copy=false) {
    assertProject(); const name=$('style-name').value.trim(); if(!name) throw new Error('Escribe un nombre para el estilo.');
    const projectId=$('style-shared').checked?null:state.projectId;
    const sameScope=!state.currentStyle || (state.currentStyle.project_id||null)===projectId;
    const payload={...(state.currentStyle && !copy && sameScope?{id:state.currentStyle.id}:{}),project_id:projectId,name,prompt:$('style-prompt').value.trim(),negative:$('style-negative').value.trim(),config:calibrationConfig(),sample_ids:[...state.sampleIds].filter(sampleAvailable)};
    const result=await api('/styles','POST',payload); $('style-search').value='';await reload(); $('cal-style').value=result.id; $('prod-style').value=result.id; productionStyleChanged();loadStyle(result.id);renderStyles();notice(`Estilo «${result.name}» guardado · revisión ${result.revision||1}.`);
  }
  function productionStyleChanged() {
    releaseComposition(false);
    state.derivedConfig=null;
    state.recipeStyleRevision=null;
    $('recipe-select').value='';updateRecipeNote();
    const style=state.styles.find(s=>s.id===$('prod-style').value);
    if(style?.config?.seed!==undefined)$('prod-seed').value=String(style.config.seed);
    renderVisualWorkspace();
  }
  function productionPieceChanged(select) {
    if(select.dataset.pieceKind!=='expression')releaseComposition(false);
    delete state.recipePieceRevisions[select.dataset.previousValue];
    delete state.recipePieceRevisions[select.value];
    select.dataset.previousValue=select.value;
    const piece=state.pieces.find(p=>p.id===select.value);
    if(select.dataset.pieceKind==='character'&&piece?.seed!==undefined&&!state.sourceAssetId&&!state.derivedConfig){$('prod-seed').value=String(piece.seed);$('seed-lock').checked=true;}
    if(select.dataset.pieceKind==='pose')productionPose?.setValue(piece?.pose_control||null);
    renderVisualWorkspace();
  }
  function selectedPieces() { return [...$('piece-pickers').querySelectorAll('select')].map(s=>s.value).filter(Boolean); }
  function selectedVisualPieces() {
    const source=state.sourceRecord?.snapshot||state.sourceRecord?.metadata?.snapshot;
    return [...$('piece-pickers').querySelectorAll('select')].filter(select=>select.value).map(select=>{
      const pin=state.recipePieceRevisions[select.value];
      const historical=source?.pieces?.find(piece=>piece.id===select.value&&pin!=null&&piece.revision===pin)
        ||pinnedPieceViews.get(`${select.value}:${pin}`);
      return historical||state.pieces.find(piece=>piece.id===select.value)
        ||{id:select.value,kind:select.dataset.pieceKind,name:`${KINDS[select.dataset.pieceKind]} guardado`,revision:pin};
    });
  }
  function visualReadiness() {
    if(!workspace)return {ready:true,message:''};
    return workspace.readiness({category:state.productionCategory,profile:productionProfile().id,
      pieces:selectedVisualPieces(),hasStyle:!!$('prod-style').value,source:!!state.sourceAssetId,
      mode:state.productionMode,masterReady:state.masterReady,expressionPrompt:$('prod-prompt').value});
  }
  function renderVisualWorkspace() {
    if(!workspace)return;
    const scoped=state.pieces.filter(inProject),selected=new Set(selectedPieces()),locked=!!state.sourceAssetId;
    $('element-type-cards').innerHTML=workspace.creators(!currentProject());
    $('element-create-empty-message').textContent=!currentProject()?'Crea un proyecto para comenzar.':!scoped.length?'Empieza creando un personaje, outfit, pose, expresión, fondo u objeto. Después elige su mejor prueba para identificarlo en el inventario.':'Cada elemento se diseña y prueba por separado. Sus variantes conservan el maestro.';
    const kinds=[['','Todos'],...Object.entries(KINDS)];
    $('inventory-kind-tabs').innerHTML=kinds.map(([kind,name])=>`<button type="button" class="inventory-kind-tab" data-inventory-kind="${kind}" aria-pressed="${inventoryView.kind===kind}">${esc(name)} <small>${scoped.filter(p=>!kind||p.kind===kind).length}</small></button>`).join('');
    const page=workspace.inventory(state.pieces,{projectId:state.projectId,kind:inventoryView.kind,
      query:$('inventory-search').value,onlyFavorites:$('inventory-favorites').checked,favorites,limit:inventoryView.limit});
    const preview={imageUrl,isAvailable:id=>sampleAvailable(id)&&!unavailablePreviews.has(id)};
    $('inventory-grid').innerHTML=page.items.length?page.items.map(piece=>workspace.card(piece,{...preview,
      selected:selected.has(piece.id),pinnedRevision:selected.has(piece.id)?state.recipePieceRevisions[piece.id]:undefined,
      locked:(locked&&piece.kind!=='expression')||(selected.size>0&&!productionProfile().kinds.includes(piece.kind)),
      lockedReason:locked&&piece.kind!=='expression'?'Composición fijada':selected.size>0&&!productionProfile().kinds.includes(piece.kind)?'Cambia el tipo de imagen para añadirlo':'',favorite:favorites.has(piece.id)})).join('')
      :empty(scoped.length?'No hay elementos con estos filtros':'Tu inventario empieza aquí',scoped.length?'Cambia el tipo o la búsqueda.':'Elige arriba qué elemento quieres crear. Sus pruebas elegidas aparecerán como miniaturas.');
    $('inventory-count').textContent=`${page.items.length} de ${page.total} elemento${page.total===1?'':'s'}`;
    $('inventory-more').classList.toggle('hidden',page.items.length>=page.total);
    const pieces=selectedVisualPieces();
    $('composition-slots').innerHTML=pieces.map(piece=>workspace.slot(piece,{...preview,
      pinnedRevision:state.recipePieceRevisions[piece.id],locked:locked&&piece.kind!=='expression'})).join('');
    $('composition-empty-message').classList.toggle('hidden',pieces.length>0);
    $('composition-summary').textContent=visualReadiness().message;
    visualStyles?.refresh();
    updateGenerationAvailability();
  }
  function createVisualElement(kind) {
    assertProject();openPiece();pieceDraft={kind,project_id:state.projectId};
    fillPieceFields(pieceDraft);renderPieceTestStyles();renderPieceEditor();
    $('piece-dialog-title').textContent=`Crear ${workspace.label(kind).toLowerCase()}`;
  }
  function addVisualElement(id) {
    const piece=state.pieces.find(p=>p.id===id&&inProject(p));
    if(!piece)throw new Error('Este elemento ya no está disponible en el inventario.');
    if(state.sourceAssetId&&piece.kind!=='expression')throw new Error('Pulsa «Editar como nueva composición» para cambiar sus elementos fijados.');
    if(state.productionCategory&&selectedPieces().length&&!productionProfile().kinds.includes(piece.kind))throw new Error('Este elemento pertenece a otro tipo de imagen. Cambia el objetivo de composición antes de añadirlo.');
    let category=productionCategories().find(c=>String(optionValue(c))===state.productionCategory&&AnimaProduction.profile(c).kinds.includes(piece.kind));
    if(!category)category=productionCategories().find(c=>AnimaProduction.profile(c).kinds.includes(piece.kind));
    if(!category)throw new Error('Añade una categoría compatible desde Configurar proyecto.');
    if(String(optionValue(category))!==state.productionCategory)chooseProduction(optionValue(category));
    const picker=$('piece-pickers').querySelector(`[data-piece-kind="${piece.kind}"]`);
    if(!picker)throw new Error('El objetivo elegido no admite este tipo de elemento.');
    if(picker.value===piece.id&&(state.recipePieceRevisions[piece.id]==null||state.recipePieceRevisions[piece.id]===piece.revision))return;
    picker.value=piece.id;productionPieceChanged(picker);renderVisualWorkspace();
    notice(`${piece.name} añadido a la imagen.`);
  }
  function removeVisualElement(kind) {
    if(state.sourceAssetId&&kind!=='expression')throw new Error('Libera la composición para quitar elementos fijados.');
    const picker=$('piece-pickers').querySelector(`[data-piece-kind="${kind}"]`);
    if(picker){picker.value='';productionPieceChanged(picker);renderVisualWorkspace();}
  }
  function pieceByKind(kind) { const id=$('piece-pickers').querySelector(`[data-piece-kind="${kind}"]`)?.value;return state.pieces.find(p=>p.id===id); }
  function productionPayload() {
    if(!state.productionCategory)throw new Error('Elige primero qué recurso quieres generar.');
    assertProject(); const style=state.styles.find(s=>s.id===$('prod-style').value && inProject(s));
    if(!style&&!state.sourceAssetId)throw new Error('Selecciona un estilo guardado para producir el recurso.');
    const format=state.recipeFormatSnapshot||(currentProject().formats||[]).find(f=>String(f.id??f.name)===$('format-select').value);
    if(!format&&!state.sourceAssetId)throw new Error('Selecciona un formato del proyecto.');
    const ids=selectedPieces(), revisions=Object.fromEntries(Object.entries(state.recipePieceRevisions).filter(([id])=>ids.includes(id)));
    const config={...(state.sourceConfig||state.derivedConfig||{}),width:Number(format?.width??state.sourceConfig?.width),height:Number(format?.height??state.sourceConfig?.height),seed:seedValue('prod-seed')};
    const pose=productionPose?.getValue();if(pose)config.pose_control=pose;
    const payload={project_id:state.projectId,style_id:style?.id||state.sourceStyleId,style_revision:state.recipeStyleRevision??style?.revision,piece_ids:ids,piece_revisions:Object.keys(revisions).length?revisions:undefined,prompt:$('prod-prompt').value.trim(),negative:$('prod-negative').value.trim(),config,format_id:format?.id,format_snapshot:state.recipeFormatSnapshot?{...state.recipeFormatSnapshot}:undefined,category:state.productionCategory,mode:state.productionMode,character:pieceByKind('character')?.name||'',outfit:pieceByKind('outfit')?.name||'',expression:pieceByKind('expression')?.name||'',recipe_id:$('recipe-select').value||undefined};
    if(state.sourceAssetId){
      payload.source_asset_id=state.sourceAssetId;
      const expression=pieceByKind('expression');
      if(expression){payload.expression_piece_id=expression.id;payload.expression_piece_revision=state.recipePieceRevisions[expression.id]??expression.revision;}
      if(state.productionMode==='expression'&&payload.prompt){payload.expression_prompt=payload.prompt;delete payload.expression_piece_id;delete payload.expression_piece_revision;}
      if(state.productionMode==='expression')payload.expression_negative=payload.negative;
    }
    return payload;
  }
  async function previewPrompt() {
    const data=await api('/compose','POST',productionPayload()); $('composed-output').textContent=JSON.stringify(data,null,2); $('assembled-preview').open=true;
  }
  async function startGeneration(payload) {
    if(state.activeJob)throw new Error('Ya hay una generación del taller en curso.');
    if(!sessionReady())throw new Error(session.status==='error'?'ComfyUI no está disponible. Usa Reintentar para preparar el backend.':'ComfyUI está iniciando. La generación se habilitará cuando esté listo.');
    assertProject();
    session.generationPending=true;updateGenerationAvailability();$('interrupt-job').disabled=false;
    try {
      const job=await api('/generate','POST',{...payload,session_id:session.id}); if(!job?.job_id)throw new Error('El servidor no devolvió el identificador del trabajo.');
      state.activeJob=job.job_id; $('job-status').classList.remove('hidden'); $('job-title').textContent=payload.purpose==='calibration'?'Generando prueba de estilo':'Generando recurso'; $('job-progress').value=0;
      await pollJob(job.job_id,payload);
    } finally { state.activeJob=null;session.generationPending=false;updateGenerationAvailability();$('interrupt-job').disabled=true; }
  }
  async function pollJob(id,payload) {
    while(state.activeJob===id) {
      let job;
      try {job=await api(`/jobs/${encodeURIComponent(id)}`);} catch(error) {$('job-phase').textContent='No se pudo consultar el trabajo';throw error;}
      const progress=job.progress;
      const percent=typeof progress==='object'&&progress ? 100*Number(progress.value||0)/Math.max(Number(progress.max||1),1) : Number(progress||0)*(Number(progress)>0&&Number(progress)<=1?100:1);
      $('job-progress').value=Math.max(0,Math.min(100,percent)); $('job-phase').textContent=job.phase||job.message||job.status;
      if(['done','completed','success'].includes(job.status)) {
        $('job-progress').value=100; $('job-phase').textContent='Completado';
        const result=job.result||{};
        if(payload.purpose==='calibration'&&result.id&&sampleAvailable(result.id))state.sampleIds.add(result.id);
        await refreshGenerations();
        if(payload.purpose==='piece_test')$('element-tests').scrollIntoView?.({behavior:'smooth',block:'start'});
        if(job.warning){storagePending=true;notice(job.warning,true);}
        else notice(payload.mode==='expression'?'Expresión generada. Revisa la cara y su transición antes de aceptarla.':'Generación terminada. Revisa el resultado para aceptarlo o rechazarlo.');
        return;
      }
      if(['error','failed','cancelled','canceled'].includes(job.status)) {$('job-phase').textContent='Finalizado con error';throw new Error(job.error||job.message||'No se pudo completar la generación.');}
      await new Promise(resolve=>setTimeout(resolve,1100));
    }
  }
  async function generateTest() {
    const prompt=$('test-prompt').value.trim();if(!prompt)throw new Error('Describe una escena de prueba para comparar el estilo.');
    const stylePrompt=$('style-prompt').value.trim();
    await startGeneration({project_id:state.projectId,config:calibrationConfig(),prompt:[stylePrompt,prompt].filter(Boolean).join('\n'),negative:$('style-negative').value.trim(),mode:'txt2img',purpose:'calibration',category:optionValue(currentProject()?.categories?.[0]||'characters'),calibration:{style_prompt:stylePrompt,style_negative:$('style-negative').value.trim(),test_prompt:prompt}});
  }
  async function generateProduction() {
    const ready=visualReadiness();if(!ready.ready)throw new Error(ready.message);
    if(!state.sourceAssetId&&!$('seed-lock').checked)$('prod-seed').value=freshSeed();
    const payload=productionPayload();payload.purpose='production';
    if(payload.mode==='expression') {
      if(!state.masterReady)throw new Error('Selecciona o carga el maestro de la expresión.');
      if(!maskHasPaint())throw new Error('Pinta la región facial que puede cambiar.');
      if(state.masterId)payload.master_asset_id=state.masterId;else payload.master_base64=state.masterBase64;
      payload.mask_base64=maskPng();payload.config.denoise=numeric('denoise',.05,1);payload.expression=$('expression-name').value.trim()||pieceByKind('expression')?.name||'';
      payload.config.width=$('master-canvas').width;payload.config.height=$('master-canvas').height;
      if(!payload.prompt&&!pieceByKind('expression'))throw new Error('Selecciona una expresión guardada o descríbela en las instrucciones.');
    }
    await startGeneration(payload);
  }
  async function saveRecipe() {
    if(state.sourceAssetId&&state.productionMode==='expression')throw new Error('Esta receta no puede conservar todavía el maestro y la máscara facial. Conserva la imagen maestra y recupérala desde «Como maestro» para repetir sus expresiones.');
    if(state.sourceAssetId&&!state.sourceAdditionalKnown)throw new Error('La ficha antigua no separa las instrucciones adicionales. Libera la composición y revisa el prompt antes de guardar una receta.');
    const payload=productionPayload(),name=$('recipe-name').value.trim();if(!name)throw new Error('Escribe un nombre para la receta.');
    if(state.sourceAssetId){const snapshot=state.sourceRecord?.snapshot||state.sourceRecord?.metadata?.snapshot;payload.config.loras=JSON.parse(JSON.stringify(snapshot?.style?.config?.loras||[]));}
    if(payload.mode!=='expression')for(const key of ['source_asset_id','expression_piece_id','expression_piece_revision','expression_prompt','expression_negative'])delete payload[key];
    const saved=await api('/recipes','POST',{...payload,name,id:$('recipe-select').value||undefined});await reload();$('recipe-select').value=saved.id;loadRecipe(saved.id);notice(`Receta «${name}» guardada.`);
  }
  function loadRecipe(id) {
    releaseComposition(false);
    state.derivedConfig=null;
    const recipe=state.recipes.find(r=>r.id===id);
    if(recipe){
      const category=productionCategories().find(c=>sameCategory(c,recipe.category))||(!recipe.category?productionCategories().find(c=>AnimaProduction.profile(c).id==='character'):null);
      if(!category)throw new Error('La categoría de esta receta ya no está en el proyecto. Añádela desde Configurar para recuperarla.');
      chooseProduction(optionValue(category),recipe.mode||'txt2img');$('recipe-select').value=id;
    }
    $('delete-recipe').disabled=!recipe;
    state.recipeStyleRevision=recipe?.style_revision??null;state.recipePieceRevisions={...(recipe?.piece_revisions||{})};state.recipeFormatSnapshot=recipe?.format_snapshot?{...recipe.format_snapshot}:null;renderFormatOptions();if(!recipe)return;
    state.derivedConfig=JSON.parse(JSON.stringify(recipe.config||{}));delete state.derivedConfig.pose_control;
    $('recipe-name').value=recipe.name;$('prod-style').value=recipe.style_id||recipe.snapshot?.style?.id||'';$('prod-prompt').value=recipe.prompt||'';$('prod-negative').value=recipe.negative||'';
    if(recipe.config?.seed!==undefined)$('prod-seed').value=String(recipe.config.seed);
    if(recipe.format_id)$('format-select').value=recipe.format_id;
    const ids=Array.isArray(recipe.piece_ids)?recipe.piece_ids:Object.values(recipe.piece_ids||{});
    for(const select of $('piece-pickers').querySelectorAll('select')){select.value=ids.find(id=>[...select.options].some(o=>o.value===id))||'';select.dataset.previousValue=select.value;}
    productionPose?.setValue(recipe.config?.pose_control||null);
    renderVisualWorkspace();
  }
  function updateCompositionSource() {
    const frozen=!!state.sourceAssetId;
    $('composition-source').classList.toggle('hidden',!frozen);
    $('composition-source-note').textContent=frozen?`Seed, estilo y piezas de ${state.sourceAssetId.slice(0,8)} fijados. Puedes cambiar el bloque de expresión. Para cambiar otra pieza, libera la composición.`:'';
    for(const id of ['prod-style','format-select','prod-seed','seed-lock','new-seed'])$(id).disabled=frozen;
    for(const picker of $('piece-pickers').querySelectorAll('select'))picker.disabled=frozen&&picker.dataset.pieceKind!=='expression';
    for(const id of ['prod-prompt','prod-negative'])$(id).disabled=frozen&&state.productionMode!=='expression';
    $('production-pose-tools').classList.toggle('composition-frozen',frozen);
    renderVisualWorkspace();
  }
  function releaseComposition(showNotice=true) {
    const hadSource=!!state.sourceAssetId;
    if(hadSource&&state.sourceConfig){
      state.derivedConfig=JSON.parse(JSON.stringify(state.sourceConfig));
      const snapshot=state.sourceRecord?.snapshot||state.sourceRecord?.metadata?.snapshot;
      state.derivedConfig.loras=JSON.parse(JSON.stringify(snapshot?.style?.config?.loras||[]));
      for(const key of ['pose_control','prompt','negative'])delete state.derivedConfig[key];
    }
    state.sourceAssetId=null;state.sourceConfig=null;state.sourceRecord=null;state.sourceStyleId=null;state.sourceAdditionalKnown=false;
    updateCompositionSource();
    if(showNotice&&hadSource)notice('Nueva composición: conserva los ajustes, la seed y las revisiones de sus piezas. Los cambios no modifican la imagen original.');
  }
  function restoreComposition(record,mode=state.productionMode) {
    if((record.purpose||record.metadata?.purpose)==='piece_test')throw new Error('Esta imagen es una prueba de pieza. Vincúlala al maestro y utiliza la pieza desde la biblioteca para componer el recurso.');
    if(!pieceProfiles)throw new Error('Recarga el taller para cargar las herramientas de piezas.');
    const saved=pieceProfiles.composition(record),category=productionCategories().find(c=>sameCategory(c,saved.category));
    if(!category)throw new Error('La categoría de la generación ya no está en el proyecto.');
    chooseProduction(optionValue(category),mode);
    state.sourceAssetId=record.id;state.sourceConfig=saved.config;state.sourceRecord=record;state.sourceStyleId=saved.style_id;state.sourceAdditionalKnown=saved.additional_known;state.derivedConfig=null;
    state.recipeStyleRevision=saved.style_revision;state.recipePieceRevisions=saved.piece_revisions;
    for(const piece of saved.pieces)pinnedPieceViews.set(`${piece.id}:${piece.revision}`,piece);
    state.recipeFormatSnapshot=saved.format_snapshot||{id:`source-${record.id}`,name:'Lienzo del maestro',width:saved.config.width,height:saved.config.height};
    selectOptions($('prod-style'),state.styles.filter(inProject).map(s=>({id:s.id,name:s.name})),'Selecciona un estilo guardado',saved.style_id);
    renderFormatOptions();
    for(const picker of $('piece-pickers').querySelectorAll('select')){
      const piece=saved.pieces.find(p=>p.kind===picker.dataset.pieceKind);
      if(piece&&!Array.from(picker.options).some(option=>option.value===piece.id))picker.add(new Option(`${piece.name} · guardado`,piece.id));
      picker.value=piece?.id||'';picker.dataset.previousValue=picker.value;
    }
    $('prod-seed').value=saved.config.seed;$('seed-lock').checked=true;
    $('prod-prompt').value=mode==='expression'?'':saved.additional_prompt;
    $('prod-negative').value=mode==='expression'?'':saved.additional_negative;
    productionPose?.setValue(saved.config.pose_control||saved.pieces.find(p=>p.kind==='pose')?.pose_control||null);
    updateCompositionSource();setTab('production');
    if(!saved.additional_known)notice('Composición recuperada. Esta ficha antigua no separa las instrucciones adicionales; el backend conserva el prompt original mientras siga fijada.');
  }
  function renderPieceEditor() {
    const kind=$('piece-kind').value,profile=pieceProfiles?.profile(kind)||{help:'Define el contenido de esta pieza.',placeholder:'Describe esta pieza…'};
    $('piece-editor-help').textContent=profile.help;$('piece-prompt').placeholder=profile.placeholder;
    $('piece-character-tools').classList.toggle('hidden',kind!=='character');$('piece-pose-tools').classList.toggle('hidden',kind!=='pose');
    const selected=$('piece-master').value||pieceDraft?.master_id;
    const masters=state.pieces.filter(inProject).filter(p=>p.kind===kind&&p.id!==$('piece-id').value);
    selectOptions($('piece-master'),masters.map(p=>({id:p.id,name:`${p.name} · v${p.revision||1}`})),'Maestro independiente',selected);
    $('piece-master').disabled=!!$('piece-id').value||!!pieceDraft?.sample_asset_id;
    $('piece-kind').disabled=!!pieceDraft?.master_id||!!$('piece-id').value;
    $('piece-master-note').textContent=pieceDraft?.master_id?`Variante de un maestro fijado en v${pieceDraft.master_revision}. Conserva los valores heredados y edita sólo lo que quieras cambiar.`:'';
    $('piece-master-note').classList.toggle('hidden',!pieceDraft?.master_id);
    const sample=$('piece-sample-id').value;
    $('piece-sample').classList.toggle('hidden',!sample);
    if(sample){$('piece-sample-preview').src=imageUrl(sample);$('piece-sample-note').textContent='Al guardar se captura la seed exacta y la configuración de esta prueba. El prompt de pieza sigue siendo editable.';}
    renderPieceSettings();
  }
  function renderPieceSettings() {
    const snapshot=pieceDraft?.generation_snapshot,style=snapshot?.snapshot?.style;
    const available=!!snapshot&&!!style?.id&&$('piece-test-style').value===style.id;
    $('piece-reuse-settings').disabled=!available;
    if(!available)$('piece-reuse-settings').checked=false;
    $('piece-settings-note').textContent=available?`La prueba puede conservar el estilo v${style.revision} y el sampling de la prueba maestra. El formato y la seed elegidos se mantienen explícitos.`:'Al vincular una prueba, el maestro conserva su estilo, sampling y seed para reutilizarlos en variantes.';
  }
  function renderPieceTestStyles(preferred) {
    const styles=state.styles.filter(inProject).map(s=>({id:s.id,name:`${s.name} · v${s.revision}`}));
    const historical=pieceDraft?.generation_snapshot?.snapshot?.style;
    if(historical?.id&&!styles.some(style=>style.id===historical.id))styles.push({id:historical.id,name:`${historical.name||'Estilo guardado'} · v${historical.revision} · del maestro`});
    selectOptions($('piece-test-style'),styles,'Selecciona un estilo',preferred??historical?.id??$('prod-style').value);
  }
  function fillPieceFields(piece={}) {
    $('piece-kind').value=piece.kind||'character';$('piece-name').value=piece.name||'';$('piece-prompt').value=piece.prompt||'';$('piece-negative').value=piece.negative||'';
    $('piece-tags').value=(piece.tags||[]).join(', ');$('piece-shared').checked=piece.project_id===null;
    $('piece-seed').value=piece.seed??'';$('piece-sample-id').value=piece.sample_asset_id||'';
    const lora=piece.loras?.[0];selectOptions($('piece-lora'),state.catalog.loras||[],'Sin LoRA propio',lora?.file||'');$('piece-lora-strength').value=String(lora?.strength??1);
    piecePose?.setValue(piece.pose_control||null);
  }
  function openPiece(piece,variant=false) {
    $('piece-form').reset();
    pieceDraft=variant&&piece?pieceProfiles.variant(piece):piece?JSON.parse(JSON.stringify(piece)):{};
    if(variant)delete pieceDraft.sample_asset_id;
    $('piece-id').value=variant?'':piece?.id||'';
    const kind=pieceDraft.kind||(state.productionCategory?productionProfile().kinds[0]:'character');
    fillPieceFields({...pieceDraft,kind,project_id:Object.hasOwn(pieceDraft,'project_id')?pieceDraft.project_id:state.projectId});
    $('piece-dialog-title').textContent=variant?'Nueva variante':piece?'Editar pieza':'Nuevo maestro';
    renderPieceTestStyles();
    selectOptions($('piece-test-format'),currentProject()?.formats||[],undefined,$('format-select').value);
    const testConfig=pieceDraft.generation_snapshot?.config;
    if(testConfig){const format=(currentProject()?.formats||[]).find(f=>f.width===testConfig.width&&f.height===testConfig.height);if(format)$('piece-test-format').value=format.id;}
    $('piece-reuse-settings').checked=!!pieceDraft.generation_snapshot;
    $('piece-master').value=pieceDraft.master_id||'';renderPieceEditor();$('piece-dialog').showModal();
  }
  function choosePieceMaster() {
    const master=state.pieces.find(p=>p.id===$('piece-master').value);
    if(!master){pieceDraft={};renderPieceEditor();return;}
    pieceDraft=pieceProfiles.variant(master);fillPieceFields(pieceDraft);renderPieceTestStyles();$('piece-reuse-settings').checked=!!pieceDraft.generation_snapshot;renderPieceEditor();
  }
  async function capturePieceSample(id) {
    const record=await api(`/generations/${encodeURIComponent(id)}`);
    const composition=record.composition||{},kind=record.piece_test?.kind||record.metadata?.piece_test?.kind||(AnimaProduction.profile(record.category).id==='background'?'background':AnimaProduction.profile(record.category).id==='item'?'item':'character');
    const snapshot=record.snapshot||record.metadata?.snapshot||{},piece=(snapshot.pieces||[]).find(p=>p.kind===kind);
    const existing=record.purpose==='piece_test'&&piece?state.pieces.find(p=>p.id===piece.id):null;
    if(existing){openPiece(existing);pieceDraft.sample_asset_id=id;$('piece-sample-id').value=id;$('piece-seed').value=recordInfo(record).seed;$('piece-dialog-title').textContent='Elegir imagen del elemento';renderPieceEditor();return;}
    openPiece();pieceDraft={kind,sample_asset_id:id};
    fillPieceFields({kind,name:piece?.name||recordInfo(record).name,prompt:piece?.prompt||composition.additional_prompt||record.prompt||'',negative:piece?.negative||'',seed:recordInfo(record).seed,sample_asset_id:id,project_id:state.projectId});
    $('piece-dialog-title').textContent='Guardar prueba como maestro';renderPieceEditor();
  }
  async function savePiece(event) {
    event.preventDefault();assertProject();
    const projectId=$('piece-shared').checked?null:state.projectId,old=state.pieces.find(p=>p.id===$('piece-id').value);
    const sameScope=!old||(old.project_id||null)===projectId;
    const kind=$('piece-kind').value,seed=$('piece-seed').value.trim();
    if(seed)seedValue('piece-seed');
    const lora=$('piece-lora').value,previous=pieceDraft?.loras?.[0],loras=kind==='character'&&lora?[{...(previous?.file===lora?previous:{}),file:lora,strength:numeric('piece-lora-strength',-3,3)},...(pieceDraft?.loras||[]).slice(1)]:[];
    const payload={id:sameScope?($('piece-id').value||undefined):undefined,project_id:projectId,kind,name:$('piece-name').value.trim(),prompt:$('piece-prompt').value.trim(),negative:$('piece-negative').value.trim(),tags:$('piece-tags').value.split(',').map(t=>t.trim()).filter(Boolean),seed:seed||null,loras,
      master_id:pieceDraft?.master_id||undefined,master_revision:pieceDraft?.master_revision||undefined,sample_asset_id:$('piece-sample-id').value||null};
    if(kind==='pose')payload.pose_control=piecePose?.getValue()||null;
    const testStyle=$('piece-test-style').value,testFormat=$('piece-test-format').value,reuseSettings=$('piece-reuse-settings').checked,testing=event.submitter?.id==='save-test-piece';
    if(testing&&!testStyle)throw new Error('Selecciona un estilo guardado para probar la pieza.');
    const saved=await api('/pieces','POST',payload);
    $('piece-dialog').close();await reload();notice(`Elemento «${saved.name}» guardado en el inventario.`);
    if(testing)await testPiece(saved,testStyle,testFormat,reuseSettings);
  }
  async function testPiece(piece,styleId,formatId,reuseSettings=true) {
    const category=productionCategories().find(c=>AnimaProduction.profile(c).kinds.includes(piece.kind));
    if(!category)throw new Error('Pieza guardada. Añade una categoría compatible para generar su prueba.');
    const historical=piece.generation_snapshot?.snapshot?.style;
    const style=state.styles.find(s=>s.id===styleId&&inProject(s))||(reuseSettings&&historical?.id===styleId?historical:null);
    const format=(currentProject()?.formats||[]).find(f=>f.id===formatId);
    if(!style)throw new Error('Pieza guardada. Elige un estilo disponible o conserva los ajustes del maestro para usar su estilo histórico.');
    if(!format)throw new Error('Pieza guardada. Selecciona un formato para generar su prueba.');
    const seed=String(piece.seed??style.config?.seed??'42');
    if(!/^\d+$/.test(seed)||BigInt(seed)>18446744073709551615n)throw new Error('La seed de prueba no es válida.');
    state.elementTestId=piece.id;renderGalleries();
    const payload={project_id:state.projectId,style_id:styleId,style_revision:reuseSettings&&historical?.id===styleId?historical.revision:style.revision,
      piece_ids:[piece.id],piece_revisions:{[piece.id]:piece.revision},piece_test:{kind:piece.kind,id:piece.id,reuse_master_settings:reuseSettings},purpose:'piece_test',prompt:'',negative:'',
      config:{width:Number(format.width),height:Number(format.height),seed:BigInt(seed).toString()},format_id:format.id,category:optionValue(category),mode:'txt2img'};
    if(piece.kind==='pose'&&piece.pose_control)payload.config.pose_control=piece.pose_control;
    setTab('production');await startGeneration(payload);
  }
  function addFormat(format={}) {
    const row=document.createElement('div');row.className='format-row';row.dataset.id=format.id||'';
    row.innerHTML=`<label>Nombre<input data-field="name" required value="${esc(format.name||'')}" placeholder="Ej. Retrato"></label><label>Ancho<input data-field="width" type="number" min="256" max="4096" step="16" value="${format.width||1024}" required></label><label>Alto<input data-field="height" type="number" min="256" max="4096" step="16" value="${format.height||1024}" required></label><label class="check" title="Transparencia requerida"><input data-field="alpha" type="checkbox" ${format.alpha?'checked':''}>Alfa</label><button type="button" class="subtle" aria-label="Quitar formato">✕</button>`;
    row.querySelector('button').onclick=()=>row.remove();$('format-editor').append(row);
  }
  function openProject(project) {
    $('project-form').reset();$('project-id').value=project?.id||'';$('project-name').value=project?.name||'';$('project-dialog-title').textContent=project?'Configurar proyecto':'Nuevo proyecto';$('project-categories').value=(project?.categories||['personajes','fondos','objetos','cartas']).map(optionValue).join(', ');$('project-retention').value=project?.retention_days??30;$('project-naming').value=project?.naming||'{project}_{category}_{character}_{outfit}_{expression}_{revision}';$('format-editor').replaceChildren();
    (project?.formats||[{id:'portrait',name:'Personaje',width:1024,height:1536,alpha:true},{id:'square',name:'Cuadrado',width:1024,height:1024,alpha:false},{id:'landscape',name:'Fondo',width:1536,height:864,alpha:false}]).forEach(addFormat);
    $('export-project').disabled=!project;$('delete-project').disabled=!project;$('project-dialog').showModal();
  }
  async function saveProject(event) {
    event.preventDefault();const old=state.projects.find(p=>p.id===$('project-id').value)||{};
    const formats=[...$('format-editor').children].map((row,index)=>({id:row.dataset.id||`format-${index+1}-${crypto.randomUUID().slice(0,8)}`,name:row.querySelector('[data-field="name"]').value.trim(),width:Number(row.querySelector('[data-field="width"]').value),height:Number(row.querySelector('[data-field="height"]').value),alpha:row.querySelector('[data-field="alpha"]').checked}));
    if(!formats.length)throw new Error('Añade al menos un formato al proyecto.');
    const saved=await api('/projects','POST',{...old,id:$('project-id').value||undefined,name:$('project-name').value.trim(),categories:$('project-categories').value.split(',').map(c=>c.trim()).filter(Boolean),formats,retention_days:Number($('project-retention').value),naming:$('project-naming').value.trim()});
    const switching=state.projectId!==saved.id;state.projectId=saved.id;if(switching)resetWorkspace();$('project-dialog').close();await reload();notice(`Proyecto «${saved.name}» guardado.`);
  }
  async function reviewGeneration(id,status) {
    const reason=status==='rejected' ? window.prompt('Motivo del rechazo (opcional):','') : '';
    if(reason===null)return;
    await api(`/generations/${encodeURIComponent(id)}/review`,'POST',{status,reason});state.purgeIds.delete(id);await refreshGenerations();
  }
  async function deleteImage(id) {
    const image=state.generations.find(g=>g.id===id);if(!image)return;
    if(state.masterId===id)throw new Error('Esta imagen es el maestro que estás editando. Quita el maestro antes de borrarla.');
    if(!window.confirm(`¿Borrar definitivamente la imagen «${recordInfo(image).name}», sus variantes y su ficha? El estilo conserva sus ajustes. Los maestros utilizados por otras imágenes quedan protegidos.`))return;
    const result=await api(`/generations/${encodeURIComponent(id)}`,'DELETE');storagePending=!!result.cleanup_pending;forgetDeleted('generations',id);renderStyles();renderGalleries();
    await refreshAfterDelete(refreshGenerations,result.cleanup_pending?'Imagen retirada. Queda una limpieza de archivos pendiente que se reintentará al actualizar.':'Imagen borrada.');
  }
  async function deleteResource(kind,id) {
    const item=state[kind]?.find(r=>r.id===id);if(!item)return;
    const label={styles:'estilo',pieces:'pieza',recipes:'receta',projects:'proyecto'}[kind];
    const scope=item.project_id?'':kind!=='projects'?' Es un recurso compartido con otros proyectos.':'';
    if(!window.confirm(`¿Borrar definitivamente ${label} «${item.name}» y los archivos de todas sus revisiones, sin respaldo?${scope} Las imágenes generadas se conservan. Si otro recurso lo utiliza, el taller indicará cuál debes quitar primero.`))return;
    await api(`/${kind}/${encodeURIComponent(id)}`,'DELETE');forgetDeleted(kind,id);favorites.delete(id);
    try{localStorage.setItem('anima-studio-favorites',JSON.stringify([...favorites]));}catch{}
    if(kind==='styles'&&state.currentStyle?.id===id)resetStyle();
    if(kind==='recipes'&&$('recipe-select').value===id){$('recipe-select').value='';$('recipe-name').value='';state.recipeStyleRevision=null;state.recipePieceRevisions={};state.recipeFormatSnapshot=null;}
    if(kind==='pieces'){delete state.recipePieceRevisions[id];$('recipe-select').value='';}
    if(kind==='projects'){$('project-dialog').close();resetWorkspace();}
    renderAll();await refreshAfterDelete(reload,`${label[0].toUpperCase()+label.slice(1)} borrado.`);
  }
  function resetStyle() {
    state.currentStyle=null;state.sampleIds.clear();$('cal-style').value='';
    for(const id of ['style-name','style-prompt','style-negative'])$(id).value='';
    $('style-shared').checked=false;$('save-style').textContent='Guardar estilo';renderStyles();renderGalleries();
  }
  async function purgeRejected() {
    const ids=[...state.purgeIds].filter(id=>projectGenerations().some(g=>g.id===id&&g.status==='rejected'));
    if(!ids.length)return;
    const days=numeric('purge-days',0,3650);
    if(!window.confirm(`Borrar definitivamente hasta ${ids.length} imágenes rechazadas con al menos ${days} días de antigüedad. Los maestros utilizados por otros resultados se conservarán; los estilos mantienen sus ajustes. ¿Continuar?`))return;
    const result=await api('/rejected/purge','POST',{ids,min_age_days:days});
    storagePending=!!result.cleanup_pending;
    for(const id of result.purged||[])forgetDeleted('generations',id);
    state.purgeIds.clear();renderStyles();renderGalleries();
    await refreshAfterDelete(refreshGenerations,`Limpieza terminada: ${result.purged?.length||0} imágenes retiradas y ${result.protected?.length||0} protegidas por otros usos.${result.cleanup_pending?' Queda una limpieza de archivos pendiente.':''}`);
  }
  async function selectMaster(id) {
    state.masterId=id||null;state.masterBase64=null;$('master-upload').value='';
    if(!id){clearMaster();return;}
    const record=state.generations.find(g=>g.id===id);
    if(record&&(record.snapshot||record.metadata?.snapshot)?.style?.id)restoreComposition(record,'expression');
    state.masterId=id;
    const seed=recordInfo(record||{}).seed;if(seed!=='—')$('prod-seed').value=seed;
    $('seed-lock').checked=true;
    await loadMasterImage(imageUrl(id));
  }
  function clearMaster() { releaseComposition(false);state.masterReady=false;state.masterId=null;state.masterBase64=null;state.masterToken++;$('master-canvas').getContext('2d').clearRect(0,0,$('master-canvas').width,$('master-canvas').height);$('mask-canvas').getContext('2d').clearRect(0,0,$('mask-canvas').width,$('mask-canvas').height);$('mask-placeholder').classList.remove('hidden');$('mask-info').textContent='La máscara se guarda con la generación.'; }
  async function loadMasterImage(src) {
    const token=++state.masterToken;state.masterReady=false;
    const image=new Image();image.src=src;
    await image.decode();if(token!==state.masterToken)return;
    if(image.naturalWidth*image.naturalHeight>16777216)throw new Error('El maestro supera 16 megapíxeles. Utiliza una imagen de menor tamaño para el PoC.');
    for(const id of ['master-canvas','mask-canvas']){$(id).width=image.naturalWidth;$(id).height=image.naturalHeight;}
    $('master-canvas').getContext('2d').drawImage(image,0,0);state.masterReady=true;$('mask-placeholder').classList.add('hidden');$('mask-info').textContent=`${image.naturalWidth} × ${image.naturalHeight} · Pinta sólo la región facial que puede cambiar.`;
  }
  function maskHasPaint() { const canvas=$('mask-canvas'),pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;for(let i=3;i<pixels.length;i+=4)if(pixels[i])return true;return false; }
  function maskPng() { const source=$('mask-canvas'),canvas=document.createElement('canvas');canvas.width=source.width;canvas.height=source.height;const context=canvas.getContext('2d');context.fillStyle='black';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(source,0,0);return canvas.toDataURL('image/png').split(',')[1]; }
  function setupMask() {
    const canvas=$('mask-canvas'),context=()=>canvas.getContext('2d');let painting=false,last=null;
    function point(event){const rect=canvas.getBoundingClientRect();return {x:(event.clientX-rect.left)*canvas.width/rect.width,y:(event.clientY-rect.top)*canvas.height/rect.height};}
    function draw(event){if(!painting||!state.masterReady)return;const p=point(event),ctx=context();ctx.globalCompositeOperation=$('mask-erase').checked?'destination-out':'source-over';ctx.strokeStyle='white';ctx.fillStyle='white';ctx.lineWidth=Number($('brush-size').value);ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();ctx.moveTo(last?.x??p.x,last?.y??p.y);ctx.lineTo(p.x,p.y);ctx.stroke();if(!last){ctx.beginPath();ctx.arc(p.x,p.y,ctx.lineWidth/2,0,Math.PI*2);ctx.fill();}last=p;}
    canvas.onpointerdown=event=>{if(!state.masterReady)return;event.preventDefault();painting=true;last=null;canvas.setPointerCapture(event.pointerId);draw(event);};canvas.onpointermove=draw;
    const stop=()=>{painting=false;last=null;};canvas.onpointerup=stop;canvas.onpointercancel=stop;canvas.onlostpointercapture=stop;
  }
  function downloadJson(data,name) {const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function resetWorkspace() {
    state.elementTestId=null;
    inventoryView.kind='';inventoryView.limit=24;pinnedPieceViews.clear();
    $('inventory-search').value='';$('inventory-favorites').checked=false;
    state.productionCategory='';state.productionMode='txt2img';$('piece-pickers').replaceChildren();
    state.sampleIds.clear();state.purgeIds.clear();state.currentStyle=null;state.recipeStyleRevision=null;state.recipePieceRevisions={};state.recipeFormatSnapshot=null;clearMaster();state.derivedConfig=null;
    for(const id of ['style-name','style-prompt','style-negative','test-prompt','prod-prompt','prod-negative','recipe-name','expression-name','style-search'])$(id).value='';
    $('style-shared').checked=false;$('cal-style').value='';$('prod-style').value='';$('recipe-select').value='';$('save-style').textContent='Guardar estilo';$('recipe-revision-note').classList.add('hidden');$('prod-seed').value='42';
    $('lora-list').replaceChildren();$('empty-loras').classList.remove('hidden');$('composed-output').textContent='Aquí aparecerá la combinación resuelta.';
  }
  function wireEvents() {
    $('close-element-tests').onclick=()=>{state.elementTestId=null;renderGalleries();};
    document.addEventListener('click',action(async event=>{
      const button=event.target.closest('[data-create-element],[data-inventory-kind],[data-add-element],[data-remove-element],[data-edit-element],[data-variant-element],[data-inventory-favorite],[data-open-styles]');
      if(!button)return;
      if(button.dataset.createElement)return createVisualElement(button.dataset.createElement);
      if(Object.hasOwn(button.dataset,'inventoryKind')){inventoryView.kind=button.dataset.inventoryKind;inventoryView.limit=24;renderVisualWorkspace();return;}
      if(button.dataset.addElement)return addVisualElement(button.dataset.addElement);
      if(button.dataset.removeElement)return removeVisualElement(button.dataset.removeElement);
      if(button.dataset.editElement||button.dataset.variantElement){
        const piece=state.pieces.find(p=>p.id===(button.dataset.editElement||button.dataset.variantElement)&&inProject(p));
        if(!piece)throw new Error('Este elemento ya no está en el inventario.');
        return openPiece(piece,!!button.dataset.variantElement);
      }
      if(button.dataset.inventoryFavorite){const id=button.dataset.inventoryFavorite;favorites.has(id)?favorites.delete(id):favorites.add(id);try{localStorage.setItem('anima-studio-favorites',JSON.stringify([...favorites]));}catch{}renderVisualWorkspace();renderPieceLibrary();return;}
      if(button.dataset.openStyles)return visualStyles?.open(button.dataset.openStyles);
    }));
    $('inventory-search').oninput=()=>{inventoryView.limit=24;renderVisualWorkspace();};
    $('inventory-favorites').onchange=()=>{inventoryView.limit=24;renderVisualWorkspace();};
    $('inventory-more').onclick=()=>{inventoryView.limit+=24;renderVisualWorkspace();};
    $('prod-prompt').addEventListener('input',renderVisualWorkspace);
    document.addEventListener('error',event=>{
      const image=event.target;if(!image?.dataset?.workspaceImage)return;
      unavailablePreviews.add(image.dataset.workspaceImage);renderVisualWorkspace();
    },true);
    document.querySelectorAll('.tab').forEach(tab=>tab.onclick=()=>setTab(tab.dataset.tab));
    document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>$(button.dataset.close).close());
    $('project-select').onchange=action(async()=>{state.projectId=$('project-select').value;resetWorkspace();try{localStorage.setItem('anima-studio-project',state.projectId);}catch{}await reload();});
    $('new-project').onclick=()=>openProject();$('edit-project').onclick=()=>openProject(currentProject());$('project-form').onsubmit=action(saveProject);$('add-format').onclick=()=>addFormat();
    $('delete-project').onclick=action(()=>deleteResource('projects',$('project-id').value));
    $('export-project').onclick=action(async()=>{const id=$('project-id').value;if(id)downloadJson(await api(`/projects/${encodeURIComponent(id)}/export`),`${currentProject()?.name||'proyecto'}-perfil.json`);});
    $('import-project').onchange=action(async()=>{const file=$('import-project').files[0];if(!file)return;let profile;try{profile=JSON.parse(await file.text());}catch{throw new Error('El archivo de perfil no contiene JSON válido.');}const result=await api('/projects/import','POST',profile);state.projectId=result.id;resetWorkspace();$('project-dialog').close();await reload();notice('Perfil importado.');});
    $('refresh').onclick=action(reload);$('refresh-gallery').onclick=action(refreshGenerations);$('refresh-catalog').onclick=action(async()=>{state.catalog=await api('/catalog?refresh=true');renderCatalog();notice('Catálogo actualizado.');});
    $('retry-backend').onclick=action(openSession);
    $('interrupt-job').onclick=action(async()=>{await api('/interrupt','POST',{session_id:session.id});$('interrupt-job').disabled=true;$('job-phase').textContent='Interrupción solicitada; esperando al backend…';});
    $('style-search').oninput=renderStyles;
    $('favorite-style').onclick=()=>{const id=$('cal-style').value;if(!id)return;favorites.has(id)?favorites.delete(id):favorites.add(id);try{localStorage.setItem('anima-studio-favorites',JSON.stringify([...favorites]));}catch{}renderStyles();};
    $('choose-model').onclick=()=>openCatalogPicker('models');
    $('choose-model').addEventListener('error',markBrokenCatalogImage,true);
    $('catalog-grid').addEventListener('error',markBrokenCatalogImage,true);
    $('catalog-folders').onclick=event=>{const tab=event.target.closest('[data-catalog-folder]');if(tab)selectCatalogFolder(tab.dataset.catalogFolder,true);};
    $('catalog-folders').onkeydown=event=>{
      if(!event.target.closest('[data-catalog-folder]'))return;
      const folders=catalogFolders(),index=folders.findIndex(folder=>folder.value===catalogPicker.folder);
      const next={ArrowRight:(index+1)%folders.length,ArrowLeft:(index+folders.length-1)%folders.length,Home:0,End:folders.length-1}[event.key];
      if(next!==undefined){event.preventDefault();selectCatalogFolder(folders[next].value,true);}
    };
    $('catalog-grid').onclick=event=>{const button=event.target.closest('[data-catalog-file]');if(button&&!button.disabled)chooseCatalogFile(button.dataset.catalogFile);};
    const filterCatalog=()=>{catalogPicker.limit=48;renderCatalogPicker();$('catalog-grid').scrollTop=0;};
    $('catalog-search').oninput=filterCatalog;$('catalog-images-only').onchange=filterCatalog;
    $('catalog-more').onclick=()=>{catalogPicker.limit+=48;renderCatalogPicker();};
    $('catalog-dialog').onclose=()=>{catalogPicker.row=null;};
    $('add-lora').onclick=()=>openCatalogPicker('loras');$('cal-style').onchange=()=>{loadStyle($('cal-style').value);renderStyles();};$('save-style').onclick=action(()=>saveStyle());$('duplicate-style').onclick=action(()=>saveStyle(true));$('generate-test').onclick=action(generateTest);
    $('reset-style').onclick=resetStyle;
    $('delete-style').onclick=action(()=>deleteResource('styles',$('cal-style').value));
    $('delete-recipe').onclick=action(()=>deleteResource('recipes',$('recipe-select').value));
    $('change-production').onclick=()=>{$('production-workspace').classList.add('hidden');$('production-menu').classList.remove('hidden');$('change-production').classList.add('hidden');$('production-title').textContent='¿Qué quieres generar?';$('production-description').textContent='Cada recurso tiene su propio espacio y las herramientas que necesita.';};
    $('production-menu').onclick=action(event=>{const choice=event.target.closest('[data-production-category]');if(choice)chooseProduction(choice.dataset.productionCategory,choice.dataset.productionMode);});
    $('prod-style').onchange=productionStyleChanged;
    $('format-select').onchange=()=>{releaseComposition(false);state.recipeFormatSnapshot=null;renderFormatOptions();};
    $('release-composition').onclick=()=>releaseComposition();
    $('preview-prompt').onclick=action(previewPrompt);$('generate-production').onclick=action(generateProduction);$('new-seed').onclick=()=>$('prod-seed').value=freshSeed();$('save-recipe').onclick=action(saveRecipe);$('recipe-select').onchange=()=>loadRecipe($('recipe-select').value);
    $('open-piece-form').onclick=()=>openPiece();$('add-piece').onclick=()=>openPiece();$('piece-form').onsubmit=action(savePiece);$('piece-search').oninput=renderPieceLibrary;$('piece-filter').onchange=renderPieceLibrary;$('favorites-only').onchange=renderPieceLibrary;
    $('piece-kind').onchange=()=>{pieceDraft={...pieceDraft,kind:$('piece-kind').value};$('piece-master').value='';renderPieceEditor();};
    $('piece-master').onchange=choosePieceMaster;
    $('piece-test-style').onchange=()=>{$('piece-reuse-settings').checked=false;renderPieceSettings();};
    $('piece-clear-sample').onclick=()=>{$('piece-sample-id').value='';if(pieceDraft)pieceDraft.sample_asset_id=null;renderPieceEditor();};
    $('review-filter').onchange=renderGalleries;$('image-search').oninput=renderGalleries;$('purge-rejected').onclick=action(purgeRejected);
    $('cleanup-storage').onclick=()=>void cleanupStorage();
    $('select-rejected').onclick=()=>{for(const box of $('review-gallery').querySelectorAll('[data-purge]')){box.checked=true;state.purgeIds.add(box.dataset.purge);}updatePurgeCount();};
    document.querySelectorAll('[name="mode"]').forEach(radio=>radio.onchange=()=>$('expression-controls').classList.toggle('hidden',radio.value!=='expression'||!radio.checked));
    $('master-select').onchange=action(()=>selectMaster($('master-select').value));
    $('clear-master').onclick=()=>{clearMaster();$('master-select').value='';$('master-upload').value='';};
    $('master-upload').onchange=action(async()=>{const file=$('master-upload').files[0];if(!file)return;if(file.size>20*1024*1024)throw new Error('Utiliza un PNG de menos de 20 MB.');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('No se pudo leer el PNG.'));reader.readAsDataURL(file);});await loadMasterImage(data);state.masterId=null;state.masterBase64=data.split(',')[1];$('master-select').value='';});
    $('clear-mask').onclick=()=>$('mask-canvas').getContext('2d').clearRect(0,0,$('mask-canvas').width,$('mask-canvas').height);
    document.addEventListener('click',action(async event=>{
      const button=event.target.closest('[data-review],[data-master],[data-open-image],[data-edit-piece],[data-variant-piece],[data-capture-piece],[data-use-composition],[data-use-piece],[data-favorite],[data-load-config],[data-load-style],[data-delete-image],[data-delete-resource],[data-use-recipe]');if(!button)return;
      if(button.dataset.deleteImage)return deleteImage(button.dataset.deleteImage);
      if(button.dataset.deleteResource)return deleteResource(button.dataset.deleteResource,button.dataset.id);
      if(button.dataset.useRecipe){loadRecipe(button.dataset.useRecipe);setTab('production');return;}
      if(button.dataset.capturePiece)return capturePieceSample(button.dataset.capturePiece);
      if(button.dataset.useComposition){const record=await api(`/generations/${encodeURIComponent(button.dataset.useComposition)}`);return restoreComposition(record,'txt2img');}
      if(button.dataset.variantPiece)return openPiece(state.pieces.find(p=>p.id===button.dataset.variantPiece),true);
      if(button.dataset.loadStyle){$('cal-style').value=button.dataset.loadStyle;loadStyle(button.dataset.loadStyle);renderStyles();return;}
      if(button.dataset.review)return reviewGeneration(button.dataset.id,button.dataset.review);
      if(button.dataset.master){
        const master=state.generations.find(g=>g.id===button.dataset.master),category=productionCategories().find(c=>AnimaProduction.profile(c).expression&&sameCategory(c,master?.category));
        if(!category)throw new Error('Este recurso no pertenece a una categoría compatible con expresiones. Abre Expresiones y carga un maestro de personaje.');
        chooseProduction(optionValue(category),'expression');setTab('production');$('master-select').value=button.dataset.master;return selectMaster(button.dataset.master);
      }
      if(button.dataset.openImage){const id=button.dataset.openImage;$('image-dialog-image').src=imageUrl(id);$('image-dialog-title').textContent=recordInfo(state.generations.find(g=>g.id===id)||{}).name;$('image-dialog').showModal();}
      if(button.dataset.editPiece)openPiece(state.pieces.find(p=>p.id===button.dataset.editPiece));
      if(button.dataset.usePiece){
        const piece=state.pieces.find(p=>p.id===button.dataset.usePiece);if(!piece)return;
        if(!state.productionCategory||!productionProfile().kinds.includes(piece.kind)){
          const category=productionCategories().find(c=>AnimaProduction.profile(c).kinds.includes(piece.kind));
          if(!category)throw new Error('Añade una categoría compatible con esta pieza desde Configurar.');
          chooseProduction(optionValue(category));
        }
        const picker=$('piece-pickers').querySelector(`[data-piece-kind="${piece.kind}"]`);if(picker){picker.value=piece.id;productionPieceChanged(picker);}setTab('production');renderProductionMenu();
      }
      if(button.dataset.favorite){const id=button.dataset.favorite;favorites.has(id)?favorites.delete(id):favorites.add(id);try{localStorage.setItem('anima-studio-favorites',JSON.stringify([...favorites]));}catch{}renderPieceLibrary();renderStyles();}
      if(button.dataset.loadConfig){const g=await api(`/generations/${encodeURIComponent(button.dataset.loadConfig)}`),info=recordInfo(g),calibration=g.calibration||g.metadata?.calibration;loadConfig(info.config);if(calibration){$('style-prompt').value=calibration.style_prompt||'';$('style-negative').value=calibration.style_negative||'';$('test-prompt').value=calibration.test_prompt||'';}else{$('style-prompt').value='';$('style-negative').value=g.negative||g.metadata?.negative||'';$('test-prompt').value=g.prompt||g.metadata?.prompt||'';}notice('Ajustes recuperados de la generación. Revisa el prompt antes de repetir.');}
    }));
    document.addEventListener('change',event=>{const input=event.target;if(input.dataset.sample){input.checked?state.sampleIds.add(input.dataset.sample):state.sampleIds.delete(input.dataset.sample);}if(input.dataset.purge){input.checked?state.purgeIds.add(input.dataset.purge):state.purgeIds.delete(input.dataset.purge);updatePurgeCount();}if(input.dataset.pieceKind)productionPieceChanged(input);});
    setupMask();
  }
  async function init() {
    for(const id of ['piece-kind','piece-filter'])selectOptions($(id),Object.entries(KINDS).map(([id,name])=>({id,name})),id==='piece-filter'?'Todos':undefined);
    if(globalThis.AnimaPose){productionPose=AnimaPose.create($('pose-control-editor'),{onChange:()=>{if(state.sourceAssetId)releaseComposition(false);}});piecePose=AnimaPose.create($('piece-pose-editor'));}
    wireEvents();
    if(globalThis.AnimaStyleBrowser){
      visualStyles=AnimaStyleBrowser.create({getStyles:()=>state.styles.filter(inProject),imageUrl,getFavorites:()=>favorites,onChoose:()=>renderVisualWorkspace()});
      for(const id of ['cal-style','prod-style','piece-test-style'])visualStyles.attach(id);
    }
    setTab('production');
    window.addEventListener('pagehide',closeSession);
    window.addEventListener('pageshow',event=>{if(event.persisted)void openSession();});
    await openSession();
    try {await reload();const sampling=await api('/sampling-options');selectOptions($('sampler'),sampling.samplers||[],undefined);selectOptions($('scheduler'),sampling.schedulers||[],undefined);if(!state.projects.length)openProject();}catch(error){notice(`No se pudo abrir el taller: ${error.message}`,true);updateStatus({});}
  }
  init();
})();
