'use strict';

(() => {
  const API = '/api/anima-studio';
  const $ = id => document.getElementById(id);
  const KINDS = {character:'Personaje', personality:'Personalidad', outfit:'Outfit', pose:'Pose', expression:'Expresión', scene:'Escenario'};
  const STATES = {pending:'Pendiente', accepted:'Aceptado', rejected:'Rechazado'};
  const state = {projects:[], styles:[], pieces:[], recipes:[], generations:[], catalog:{models:[],loras:[],text_encoders:[],vaes:[]}, projectId:'', sampleIds:new Set(), purgeIds:new Set(), activeJob:null, masterId:null, masterBase64:null, masterReady:false, masterToken:0, currentStyle:null, recipeStyleRevision:null, recipePieceRevisions:{}, recipeFormatSnapshot:null};
  const session={id:null,active:false,status:'starting',error:'',timer:null,inFlight:null,controller:null,socket:null,epoch:0,generationPending:false};
  let favorites;
  try { favorites = new Set(JSON.parse(localStorage.getItem('anima-studio-favorites') || '[]')); } catch { favorites = new Set(); }
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const list = value => Array.isArray(value) ? value : (value?.items || value?.records || []);
  const currentProject = () => state.projects.find(p => p.id === state.projectId);
  const inProject = item => !item.project_id || item.project_id === state.projectId;
  const projectGenerations = () => state.generations.filter(g => g.project_id === state.projectId);
  const imageUrl = id => `${API}/images/${encodeURIComponent(id)}`;
  const optionValue = item => typeof item === 'string' ? item : (item.file ?? item.id ?? item.name);
  const optionName = item => typeof item === 'string' ? item : (item.name ?? item.label ?? item.file ?? item.id);

  async function api(path, method='GET', body, options={}) {
    const response = await fetch(API + path, {method, headers: body === undefined ? {} : {'Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)}),...options});
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
    node.className = `notice${error ? ' error' : ''}`;
    node.innerHTML = `<span>${esc(message)}</span><button aria-label="Cerrar mensaje">✕</button>`;
    node.querySelector('button').onclick = () => node.classList.add('hidden');
  }
  const action = fn => async event => { try { await fn(event); } catch (error) { console.error(error); notice(error.message || String(error), true); } };
  function selectOptions(element, items, emptyLabel, preferred) {
    const previous = preferred ?? element.value;
    element.innerHTML = (emptyLabel === undefined ? '' : `<option value="">${esc(emptyLabel)}</option>`) + items.map(item => `<option value="${esc(optionValue(item))}">${esc(optionName(item))}</option>`).join('');
    // ComfyUI returns Windows separators; keep the local catalog's exact value.
    const normalized=String(previous??'').replace(/\\/g,'/');
    const match=[...element.options].find(o=>o.value===previous)
      || [...element.options].find(o=>o.value.replace(/\\/g,'/')===normalized);
    if (match) element.value = match.value;
    else if (preferred) { const option = new Option(`No disponible: ${preferred}`, preferred); option.disabled = true; option.selected = true; element.add(option); }
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
    const term=$('style-search').value.trim().toLowerCase();
    const allStyles=state.styles.filter(inProject);
    const styles = allStyles.filter(s=>`${s.name} ${s.prompt}`.toLowerCase().includes(term));
    const options = styles.map(s => ({id:s.id,name:`${favorites.has(s.id)?'★ ':''}${s.name} · v${s.revision || 1}${s.project_id?'':' · Compartido'}`}));
    selectOptions($('cal-style'),options,'Sin estilo guardado');
    selectOptions($('prod-style'),allStyles.map(s=>({id:s.id,name:`${favorites.has(s.id)?'★ ':''}${s.name} · v${s.revision||1}${s.project_id?'':' · Compartido'}`})),'Selecciona un estilo guardado');
    $('favorite-style').disabled=!$('cal-style').value;
    $('favorite-style').textContent=favorites.has($('cal-style').value)?'★':'☆';
    $('style-shelf').innerHTML=styles.length?styles.map(style=>`<button class="style-tile ${style.id===$('cal-style').value?'selected':''}" data-load-style="${esc(style.id)}">${style.sample_ids?.length?`<img src="${imageUrl(style.sample_ids[0])}" alt="Muestra de ${esc(style.name)}" loading="lazy">`:'<span class="style-swatch">A</span>'}<span><strong>${favorites.has(style.id)?'★ ':''}${esc(style.name)}</strong><small>v${style.revision||1}${style.project_id?'':' · Compartido'}</small></span></button>`).join(''):'<p class="hint">Guarda tu primera calibración para verla aquí.</p>';
    $('style-count').textContent = `${allStyles.length} estilo${allStyles.length === 1 ? '' : 's'} disponible${allStyles.length === 1 ? '' : 's'}`;
  }
  function renderCatalog() {
    selectOptions($('model-select'),state.catalog.models || [],state.catalog.models?.length ? undefined : 'No hay modelos Anima compatibles');
    selectOptions($('encoder-select'),state.catalog.text_encoders || [],state.catalog.text_encoders?.length ? undefined : 'Encoder no disponible');
    selectOptions($('vae-select'),state.catalog.vaes || [],state.catalog.vaes?.length ? undefined : 'VAE no disponible');
    for (const row of $('lora-list').children) selectOptions(row.querySelector('select'),state.catalog.loras || [],'Selecciona un LoRA');
    $('catalog-note').textContent = `${state.catalog.models?.length || 0} modelos y ${state.catalog.loras?.length || 0} LoRAs Anima en la biblioteca del Hub. Los pesos no se copian al taller.`;
  }
  function renderPieces() {
    const previous = selectedPieces();
    const pieces = state.pieces.filter(inProject);
    $('piece-pickers').innerHTML = Object.entries(KINDS).map(([kind,label]) => `<label>${label}<select data-piece-kind="${kind}"><option value="">Sin ${label.toLowerCase()}</option>${pieces.filter(p=>p.kind===kind).map(p=>`<option value="${esc(p.id)}">${esc(p.name)}${p.project_id?'':' · Compartido'}</option>`).join('')}</select></label>`).join('');
    for (const select of $('piece-pickers').querySelectorAll('select')) {
      const old = previous.find(id => pieces.some(p=>p.id===id && p.kind===select.dataset.pieceKind));
      if (old) select.value=old;
      select.dataset.previousValue=select.value;
    }
    renderPieceLibrary();
    selectOptions($('recipe-select'),state.recipes.filter(inProject).map(r=>({id:r.id,name:r.name})),'Nueva combinación');
  }
  function renderPieceLibrary() {
    const term=$('piece-search').value.trim().toLowerCase(), kind=$('piece-filter').value, onlyFavorites=$('favorites-only').checked;
    const pieces=state.pieces.filter(inProject).filter(p=>(!kind || p.kind===kind) && (!onlyFavorites || favorites.has(p.id)) && `${p.name} ${p.prompt} ${(p.tags||[]).join(' ')}`.toLowerCase().includes(term));
    $('pieces-library').innerHTML=pieces.length ? pieces.map(p=>`<article class="piece-card"><div class="piece-top"><div><div class="piece-kind">${esc(KINDS[p.kind]||p.kind)} · v${p.revision||1}${p.project_id?'':' · Compartida'}</div><h4>${esc(p.name)}</h4></div><button class="favorite ${favorites.has(p.id)?'active':''}" data-favorite="${esc(p.id)}" aria-label="${favorites.has(p.id)?'Quitar de':'Añadir a'} favoritos">${favorites.has(p.id)?'★':'☆'}</button></div><p>${esc(p.prompt)}</p><div class="asset-actions"><button data-edit-piece="${esc(p.id)}">Editar</button><button data-use-piece="${esc(p.id)}">Usar en producción</button></div></article>`).join('') : empty('Aún no hay piezas en esta vista','Guarda un personaje, outfit o pose para empezar a combinarlos.');
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
    return `<article class="asset-card"><div class="asset-image"><img src="${imageUrl(g.id)}" loading="lazy" alt="${esc(info.name)}" data-open-image="${esc(g.id)}">${choose}</div><div class="asset-content"><div class="asset-title">${esc(info.name)}</div><div class="asset-meta">Seed ${esc(info.seed)}${info.config.steps?` · ${esc(info.config.steps)} pasos`:''}</div><span class="asset-state ${esc(status)}">${esc(STATES[status]||status)}</span><div class="asset-actions">${status!=='accepted'?`<button data-review="accepted" data-id="${esc(g.id)}">Aceptar</button>`:''}${status!=='rejected'?`<button data-review="rejected" data-id="${esc(g.id)}">Rechazar</button>`:`<button data-review="pending" data-id="${esc(g.id)}">Restaurar</button>`}<button data-master="${esc(g.id)}">Como maestro</button><a href="${imageUrl(g.id)}?download=true" download>PNG</a><a href="${API}/generations/${encodeURIComponent(g.id)}" download="${esc(g.id)}.json">Ficha</a>${context==='cal'?`<button data-load-config="${esc(g.id)}">Recuperar ajustes</button>`:''}</div></div></article>`;
  }
  function renderGalleries() {
    const generations=projectGenerations().filter(g=>!g.purged).slice().reverse();
    const calibration=generations.filter(g=>g.purpose==='calibration' || g.metadata?.purpose==='calibration' || g.category==='calibration' || g.category==='calibracion' || state.sampleIds.has(g.id));
    $('cal-gallery').innerHTML=calibration.length ? calibration.map(g=>assetCard(g,'cal')).join('') : empty('Aquí empieza tu biblioteca de estilos','Genera una prueba para comparar el acabado y guardar sus ajustes.');
    const production=generations.filter(g=>g.purpose!=='calibration' && g.metadata?.purpose!=='calibration' && !['calibration','calibracion'].includes(g.category));
    $('production-gallery').innerHTML=production.length ? production.map(g=>assetCard(g,'production')).join('') : empty('Tu próximo recurso aparecerá aquí','Selecciona un estilo y las piezas que formarán la imagen.');
    const filter=$('review-filter').value, term=$('image-search').value.trim().toLowerCase();
    const review=generations.filter(g=>(!filter||g.status===filter) && JSON.stringify([g.name,g.category,g.character_name||g.character,g.outfit_name||g.outfit,g.expression_name||g.expression,g.id]).toLowerCase().includes(term));
    $('review-gallery').innerHTML=review.length ? review.map(g=>assetCard(g,'review')).join('') : empty('No hay imágenes en esta vista','Cambia el filtro o genera un recurso para revisarlo.');
    $('review-count').textContent=`${generations.length} imágenes · ${generations.filter(g=>g.status==='accepted').length} aceptadas · ${generations.filter(g=>g.status==='rejected').length} rechazadas`;
    $('purge-controls').classList.toggle('hidden',filter!=='rejected');
    updatePurgeCount();
    selectOptions($('master-select'),generations.filter(g=>g.status!=='rejected').map(g=>({id:g.id,name:`${recordInfo(g).name} · ${g.id.slice(0,8)}`})),'Selecciona una imagen o carga un PNG',state.masterId || '');
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
    for (const kind of ['projects','styles','pieces','recipes','generations']) state[kind]=list(data[kind]);
    if (data.catalog) state.catalog=data.catalog;
    if (!state.projects.some(p=>p.id===state.projectId)) {
      let remembered=''; try { remembered=localStorage.getItem('anima-studio-project')||''; } catch {}
      state.projectId=state.projects.find(p=>p.id===remembered)?.id || state.projects[0]?.id || '';
    }
    updateStatus(data.status || {}); renderAll();
    if(data.catalog_error)notice(data.catalog_error,true);
  }
  function updateStatus(status) {
    $('capability-note').textContent=(status.limitations||[]).join(' ');
    updateSessionView();
  }
  function sessionReady() {return session.active&&session.status==='ready'&&session.socket?.readyState===WebSocket.OPEN;}
  function updateGenerationAvailability() {
    const busy=session.generationPending||!!state.activeJob;
    document.querySelectorAll('.generate-action').forEach(button=>button.disabled=busy||!sessionReady());
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
  async function refreshGenerations() { state.generations=list(await api(`/generations?project_id=${encodeURIComponent(state.projectId)}`)); renderGalleries(); }
  function addLora(file='',strength=1) {
    const row=document.createElement('div'); row.className='lora-row';
    row.innerHTML='<label>LoRA<select></select></label><label>Peso<input type="number" min="-3" max="3" step="0.05" value="1"></label><button class="subtle" title="Quitar LoRA" aria-label="Quitar LoRA">✕</button>';
    selectOptions(row.querySelector('select'),state.catalog.loras || [],'Selecciona un LoRA',file);
    row.querySelector('input').value=strength; row.querySelector('button').onclick=()=>{row.remove();$('empty-loras').classList.toggle('hidden',$('lora-list').children.length>0);};
    $('lora-list').append(row); $('empty-loras').classList.add('hidden');
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
    state.sampleIds=new Set(style.sample_ids||[]); loadConfig(style.config||{}); $('save-style').textContent='Guardar nueva revisión'; renderGalleries();
  }
  async function saveStyle(copy=false) {
    assertProject(); const name=$('style-name').value.trim(); if(!name) throw new Error('Escribe un nombre para el estilo.');
    const projectId=$('style-shared').checked?null:state.projectId;
    const sameScope=!state.currentStyle || (state.currentStyle.project_id||null)===projectId;
    const payload={...(state.currentStyle && !copy && sameScope?{id:state.currentStyle.id}:{}),project_id:projectId,name,prompt:$('style-prompt').value.trim(),negative:$('style-negative').value.trim(),config:calibrationConfig(),sample_ids:[...state.sampleIds]};
    const result=await api('/styles','POST',payload); $('style-search').value='';await reload(); $('cal-style').value=result.id; $('prod-style').value=result.id; productionStyleChanged();loadStyle(result.id);renderStyles();notice(`Estilo «${result.name}» guardado · revisión ${result.revision||1}.`);
  }
  function productionStyleChanged() {
    state.recipeStyleRevision=null;
    $('recipe-select').value='';updateRecipeNote();
    const style=state.styles.find(s=>s.id===$('prod-style').value);
    if(style?.config?.seed!==undefined)$('prod-seed').value=String(style.config.seed);
  }
  function productionPieceChanged(select) {
    delete state.recipePieceRevisions[select.dataset.previousValue];
    delete state.recipePieceRevisions[select.value];
    select.dataset.previousValue=select.value;
  }
  function selectedPieces() { return [...$('piece-pickers').querySelectorAll('select')].map(s=>s.value).filter(Boolean); }
  function pieceByKind(kind) { const id=$('piece-pickers').querySelector(`[data-piece-kind="${kind}"]`)?.value;return state.pieces.find(p=>p.id===id); }
  function productionPayload() {
    assertProject(); const style=state.styles.find(s=>s.id===$('prod-style').value && inProject(s));
    if(!style)throw new Error('Selecciona un estilo guardado para producir el recurso.');
    const format=state.recipeFormatSnapshot||(currentProject().formats||[]).find(f=>String(f.id??f.name)===$('format-select').value);
    if(!format)throw new Error('Selecciona un formato del proyecto.');
    const ids=selectedPieces(), revisions=Object.fromEntries(Object.entries(state.recipePieceRevisions).filter(([id])=>ids.includes(id)));
    return {project_id:state.projectId,style_id:style.id,style_revision:state.recipeStyleRevision??style.revision,piece_ids:ids,piece_revisions:Object.keys(revisions).length?revisions:undefined,prompt:$('prod-prompt').value.trim(),negative:$('prod-negative').value.trim(),config:{width:Number(format.width),height:Number(format.height),seed:seedValue('prod-seed')},format_id:format.id,format_snapshot:state.recipeFormatSnapshot?{...state.recipeFormatSnapshot}:undefined,category:$('category-select').value||optionValue(currentProject().categories?.[0]||'personajes'),character:pieceByKind('character')?.name||'',outfit:pieceByKind('outfit')?.name||'',expression:pieceByKind('expression')?.name||'',recipe_id:$('recipe-select').value||undefined};
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
        if(payload.purpose==='calibration'&&result.id)state.sampleIds.add(result.id);
        await refreshGenerations();
        notice(payload.mode==='expression'?'Expresión generada. Revisa la cara y su transición antes de aceptarla.':'Generación terminada. Revisa el resultado para aceptarlo o rechazarlo.');
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
    if(!$('seed-lock').checked)$('prod-seed').value=freshSeed();
    const payload=productionPayload();payload.mode=document.querySelector('[name="mode"]:checked').value;payload.purpose='production';
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
    const payload=productionPayload(),name=$('recipe-name').value.trim();if(!name)throw new Error('Escribe un nombre para la receta.');
    const saved=await api('/recipes','POST',{...payload,name,id:$('recipe-select').value||undefined});await reload();$('recipe-select').value=saved.id;loadRecipe(saved.id);notice(`Receta «${name}» guardada.`);
  }
  function loadRecipe(id) {
    const recipe=state.recipes.find(r=>r.id===id);state.recipeStyleRevision=recipe?.style_revision??null;state.recipePieceRevisions={...(recipe?.piece_revisions||{})};state.recipeFormatSnapshot=recipe?.format_snapshot?{...recipe.format_snapshot}:null;renderFormatOptions();if(!recipe)return;
    $('recipe-name').value=recipe.name;$('prod-style').value=recipe.style_id||recipe.snapshot?.style?.id||'';$('prod-prompt').value=recipe.prompt||'';$('prod-negative').value=recipe.negative||'';
    if(recipe.config?.seed!==undefined)$('prod-seed').value=String(recipe.config.seed);
    if(recipe.format_id)$('format-select').value=recipe.format_id;if(recipe.category)$('category-select').value=recipe.category;
    const ids=recipe.piece_ids||[];
    for(const select of $('piece-pickers').querySelectorAll('select')){select.value=ids.find(id=>[...select.options].some(o=>o.value===id))||'';select.dataset.previousValue=select.value;}
  }
  function openPiece(piece) {
    $('piece-form').reset();$('piece-id').value=piece?.id||'';$('piece-dialog-title').textContent=piece?'Editar pieza':'Nueva pieza';
    if(piece){$('piece-kind').value=piece.kind;$('piece-name').value=piece.name;$('piece-prompt').value=piece.prompt||'';$('piece-negative').value=piece.negative||'';$('piece-tags').value=(piece.tags||[]).join(', ');$('piece-shared').checked=!piece.project_id;}
    $('piece-dialog').showModal();
  }
  async function savePiece(event) {
    event.preventDefault();assertProject();
    const projectId=$('piece-shared').checked?null:state.projectId,old=state.pieces.find(p=>p.id===$('piece-id').value);
    const sameScope=!old||(old.project_id||null)===projectId;
    const saved=await api('/pieces','POST',{id:sameScope?($('piece-id').value||undefined):undefined,project_id:projectId,kind:$('piece-kind').value,name:$('piece-name').value.trim(),prompt:$('piece-prompt').value.trim(),negative:$('piece-negative').value.trim(),tags:$('piece-tags').value.split(',').map(t=>t.trim()).filter(Boolean)});
    $('piece-dialog').close();await reload();const picker=$('piece-pickers').querySelector(`[data-piece-kind="${saved.kind}"]`);if(picker){picker.value=saved.id;productionPieceChanged(picker);}notice(`Pieza «${saved.name}» guardada.`);
  }
  function addFormat(format={}) {
    const row=document.createElement('div');row.className='format-row';row.dataset.id=format.id||'';
    row.innerHTML=`<label>Nombre<input data-field="name" required value="${esc(format.name||'')}" placeholder="Ej. Retrato"></label><label>Ancho<input data-field="width" type="number" min="256" max="4096" step="16" value="${format.width||1024}" required></label><label>Alto<input data-field="height" type="number" min="256" max="4096" step="16" value="${format.height||1024}" required></label><label class="check" title="Transparencia requerida"><input data-field="alpha" type="checkbox" ${format.alpha?'checked':''}>Alfa</label><button type="button" class="subtle" aria-label="Quitar formato">✕</button>`;
    row.querySelector('button').onclick=()=>row.remove();$('format-editor').append(row);
  }
  function openProject(project) {
    $('project-form').reset();$('project-id').value=project?.id||'';$('project-name').value=project?.name||'';$('project-dialog-title').textContent=project?'Configurar proyecto':'Nuevo proyecto';$('project-categories').value=(project?.categories||['personajes','fondos','objetos','cartas']).map(optionValue).join(', ');$('project-retention').value=project?.retention_days??30;$('project-naming').value=project?.naming||'{project}_{category}_{character}_{outfit}_{expression}_{revision}';$('format-editor').replaceChildren();
    (project?.formats||[{id:'portrait',name:'Personaje',width:1024,height:1536,alpha:true},{id:'square',name:'Cuadrado',width:1024,height:1024,alpha:false},{id:'landscape',name:'Fondo',width:1536,height:864,alpha:false}]).forEach(addFormat);
    $('export-project').disabled=!project;$('project-dialog').showModal();
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
  async function purgeRejected() {
    const ids=[...state.purgeIds].filter(id=>projectGenerations().some(g=>g.id===id&&g.status==='rejected'));
    if(!ids.length)return;
    const days=numeric('purge-days',0,3650);
    if(!window.confirm(`Borrar definitivamente hasta ${ids.length} imágenes rechazadas con al menos ${days} días de antigüedad. Los recursos utilizados como maestro o referencia se conservarán. ¿Continuar?`))return;
    const result=await api('/rejected/purge','POST',{ids,min_age_days:days});state.purgeIds.clear();await refreshGenerations();notice(`Limpieza terminada. ${JSON.stringify(result)}`);
  }
  async function selectMaster(id) {
    state.masterId=id||null;state.masterBase64=null;$('master-upload').value='';
    if(!id){clearMaster();return;}
    const record=state.generations.find(g=>g.id===id);
    const seed=recordInfo(record||{}).seed;if(seed!=='—')$('prod-seed').value=seed;
    $('seed-lock').checked=true;
    await loadMasterImage(imageUrl(id));
  }
  function clearMaster() { state.masterReady=false;state.masterId=null;state.masterBase64=null;state.masterToken++;$('master-canvas').getContext('2d').clearRect(0,0,$('master-canvas').width,$('master-canvas').height);$('mask-canvas').getContext('2d').clearRect(0,0,$('mask-canvas').width,$('mask-canvas').height);$('mask-placeholder').classList.remove('hidden');$('mask-info').textContent='La máscara se guarda con la generación.'; }
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
    state.sampleIds.clear();state.purgeIds.clear();state.currentStyle=null;state.recipeStyleRevision=null;state.recipePieceRevisions={};state.recipeFormatSnapshot=null;clearMaster();
    for(const id of ['style-name','style-prompt','style-negative','test-prompt','prod-prompt','prod-negative','recipe-name','expression-name','style-search'])$(id).value='';
    $('style-shared').checked=false;$('cal-style').value='';$('prod-style').value='';$('recipe-select').value='';$('save-style').textContent='Guardar estilo';$('recipe-revision-note').classList.add('hidden');$('prod-seed').value='42';
    $('lora-list').replaceChildren();$('empty-loras').classList.remove('hidden');$('composed-output').textContent='Aquí aparecerá la combinación resuelta.';
  }
  function wireEvents() {
    document.querySelectorAll('.tab').forEach(tab=>tab.onclick=()=>setTab(tab.dataset.tab));
    document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>$(button.dataset.close).close());
    $('project-select').onchange=action(async()=>{state.projectId=$('project-select').value;resetWorkspace();try{localStorage.setItem('anima-studio-project',state.projectId);}catch{}await reload();});
    $('new-project').onclick=()=>openProject();$('edit-project').onclick=()=>openProject(currentProject());$('project-form').onsubmit=action(saveProject);$('add-format').onclick=()=>addFormat();
    $('export-project').onclick=action(async()=>{const id=$('project-id').value;if(id)downloadJson(await api(`/projects/${encodeURIComponent(id)}/export`),`${currentProject()?.name||'proyecto'}-perfil.json`);});
    $('import-project').onchange=action(async()=>{const file=$('import-project').files[0];if(!file)return;let profile;try{profile=JSON.parse(await file.text());}catch{throw new Error('El archivo de perfil no contiene JSON válido.');}const result=await api('/projects/import','POST',profile);state.projectId=result.id;resetWorkspace();$('project-dialog').close();await reload();notice('Perfil importado.');});
    $('refresh').onclick=action(reload);$('refresh-gallery').onclick=action(refreshGenerations);$('refresh-catalog').onclick=action(async()=>{state.catalog=await api('/catalog?refresh=true');renderCatalog();notice('Catálogo actualizado.');});
    $('retry-backend').onclick=action(openSession);
    $('interrupt-job').onclick=action(async()=>{await api('/interrupt','POST',{session_id:session.id});$('interrupt-job').disabled=true;$('job-phase').textContent='Interrupción solicitada; esperando al backend…';});
    $('style-search').oninput=renderStyles;
    $('favorite-style').onclick=()=>{const id=$('cal-style').value;if(!id)return;favorites.has(id)?favorites.delete(id):favorites.add(id);try{localStorage.setItem('anima-studio-favorites',JSON.stringify([...favorites]));}catch{}renderStyles();};
    $('add-lora').onclick=()=>addLora();$('cal-style').onchange=()=>{loadStyle($('cal-style').value);renderStyles();};$('save-style').onclick=action(()=>saveStyle());$('duplicate-style').onclick=action(()=>saveStyle(true));$('generate-test').onclick=action(generateTest);
    $('reset-style').onclick=()=>{state.currentStyle=null;state.sampleIds.clear();$('cal-style').value='';$('style-name').value='';$('style-prompt').value='';$('style-negative').value='';$('style-shared').checked=false;$('save-style').textContent='Guardar estilo';renderGalleries();};
    $('prod-style').onchange=productionStyleChanged;
    $('format-select').onchange=()=>{state.recipeFormatSnapshot=null;renderFormatOptions();};
    $('preview-prompt').onclick=action(previewPrompt);$('generate-production').onclick=action(generateProduction);$('new-seed').onclick=()=>$('prod-seed').value=freshSeed();$('save-recipe').onclick=action(saveRecipe);$('recipe-select').onchange=()=>loadRecipe($('recipe-select').value);
    $('open-piece-form').onclick=()=>openPiece();$('add-piece').onclick=()=>openPiece();$('piece-form').onsubmit=action(savePiece);$('piece-search').oninput=renderPieceLibrary;$('piece-filter').onchange=renderPieceLibrary;$('favorites-only').onchange=renderPieceLibrary;
    $('review-filter').onchange=renderGalleries;$('image-search').oninput=renderGalleries;$('purge-rejected').onclick=action(purgeRejected);
    $('select-rejected').onclick=()=>{for(const box of $('review-gallery').querySelectorAll('[data-purge]')){box.checked=true;state.purgeIds.add(box.dataset.purge);}updatePurgeCount();};
    document.querySelectorAll('[name="mode"]').forEach(radio=>radio.onchange=()=>$('expression-controls').classList.toggle('hidden',radio.value!=='expression'||!radio.checked));
    $('master-select').onchange=action(()=>selectMaster($('master-select').value));
    $('master-upload').onchange=action(async()=>{const file=$('master-upload').files[0];if(!file)return;if(file.size>20*1024*1024)throw new Error('Utiliza un PNG de menos de 20 MB.');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('No se pudo leer el PNG.'));reader.readAsDataURL(file);});await loadMasterImage(data);state.masterId=null;state.masterBase64=data.split(',')[1];$('master-select').value='';});
    $('clear-mask').onclick=()=>$('mask-canvas').getContext('2d').clearRect(0,0,$('mask-canvas').width,$('mask-canvas').height);
    document.addEventListener('click',action(async event=>{
      const button=event.target.closest('[data-review],[data-master],[data-open-image],[data-edit-piece],[data-use-piece],[data-favorite],[data-load-config],[data-load-style]');if(!button)return;
      if(button.dataset.loadStyle){$('cal-style').value=button.dataset.loadStyle;loadStyle(button.dataset.loadStyle);renderStyles();return;}
      if(button.dataset.review)return reviewGeneration(button.dataset.id,button.dataset.review);
      if(button.dataset.master){setTab('production');document.querySelector('[name="mode"][value="expression"]').checked=true;$('expression-controls').classList.remove('hidden');$('master-select').value=button.dataset.master;return selectMaster(button.dataset.master);}
      if(button.dataset.openImage){const id=button.dataset.openImage;$('image-dialog-image').src=imageUrl(id);$('image-dialog-title').textContent=recordInfo(state.generations.find(g=>g.id===id)||{}).name;$('image-dialog').showModal();}
      if(button.dataset.editPiece)openPiece(state.pieces.find(p=>p.id===button.dataset.editPiece));
      if(button.dataset.usePiece){const piece=state.pieces.find(p=>p.id===button.dataset.usePiece),picker=$('piece-pickers').querySelector(`[data-piece-kind="${piece.kind}"]`);if(picker){picker.value=piece.id;productionPieceChanged(picker);}setTab('production');}
      if(button.dataset.favorite){const id=button.dataset.favorite;favorites.has(id)?favorites.delete(id):favorites.add(id);try{localStorage.setItem('anima-studio-favorites',JSON.stringify([...favorites]));}catch{}renderPieceLibrary();renderStyles();}
      if(button.dataset.loadConfig){const g=await api(`/generations/${encodeURIComponent(button.dataset.loadConfig)}`),info=recordInfo(g),calibration=g.calibration||g.metadata?.calibration;loadConfig(info.config);if(calibration){$('style-prompt').value=calibration.style_prompt||'';$('style-negative').value=calibration.style_negative||'';$('test-prompt').value=calibration.test_prompt||'';}else{$('style-prompt').value='';$('style-negative').value=g.negative||g.metadata?.negative||'';$('test-prompt').value=g.prompt||g.metadata?.prompt||'';}notice('Ajustes recuperados de la generación. Revisa el prompt antes de repetir.');}
    }));
    document.addEventListener('change',event=>{const input=event.target;if(input.dataset.sample){input.checked?state.sampleIds.add(input.dataset.sample):state.sampleIds.delete(input.dataset.sample);}if(input.dataset.purge){input.checked?state.purgeIds.add(input.dataset.purge):state.purgeIds.delete(input.dataset.purge);updatePurgeCount();}if(input.dataset.pieceKind)productionPieceChanged(input);});
    setupMask();
  }
  async function init() {
    for(const id of ['piece-kind','piece-filter'])selectOptions($(id),Object.entries(KINDS).map(([id,name])=>({id,name})),id==='piece-filter'?'Todos':undefined);
    wireEvents();
    window.addEventListener('pagehide',closeSession);
    window.addEventListener('pageshow',event=>{if(event.persisted)void openSession();});
    await openSession();
    try {await reload();const sampling=await api('/sampling-options');selectOptions($('sampler'),sampling.samplers||[],undefined);selectOptions($('scheduler'),sampling.schedulers||[],undefined);if(!state.projects.length)openProject();}catch(error){notice(`No se pudo abrir el taller: ${error.message}`,true);updateStatus({});}
  }
  init();
})();
