"use strict";
// Ideogram 4 — lógica del módulo.
const $ = (id) => document.getElementById(id);
const API = "/api/ideogram";

const state = {
  caption: null,          // objeto caption completo (fuente para style/background)
  boxes: [],              // elements: {type,bbox:[ymin,xmin,ymax,xmax],desc,text,color_palette}
  sel: -1,                // índice de caja seleccionada
  jobTimer: null,
  models: null,
  mode: "auto",           // "auto" (LLM arma todo) | "manual" (usuario dibuja, LLM completa)
  refImg: null,           // Image de referencia (guía visual bajo las cajas; NO se envía a generar)
  refDataUri: null,       // data URI de la referencia (para el autoprompt del VLM)
  refOpacity: 0.45,       // opacidad de la referencia en el canvas
};

// ── Tabs ─────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".page").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("page-" + t.dataset.page).classList.add("active");
  if (t.dataset.page === "hist") loadHistory();
  if (t.dataset.page === "diag") loadDiag();
});

// ── Helpers ──────────────────────────────────────────────────────────────────
async function jget(p){ const r = await fetch(API+p); if(!r.ok) throw new Error((await r.json()).detail||r.status); return r.json(); }
async function jpost(p,b){ const r = await fetch(API+p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})});
  if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||r.status); return r.json(); }
async function jdel(p){ const r = await fetch(API+p,{method:"DELETE"}); if(!r.ok) throw new Error(r.status); return r.json(); }
function banner(el,msg,kind){ el.innerHTML = msg ? `<div class="banner ${kind||'warn'}">${msg}</div>` : ""; }
function fillSelect(sel,arr,cur){ sel.innerHTML=""; (arr||[]).forEach(v=>{ const o=document.createElement("option");
  o.value=typeof v==="string"?v:v.id; o.textContent=typeof v==="string"?v:v.id; sel.appendChild(o); });
  if(cur) sel.value=cur; }

// ── Init: estado + modelos ────────────────────────────────────────────────────
async function init(){
  try {
    const st = await jget("/status");
    $("hdrStatus").innerHTML = `ComfyUI ${st.comfyui?'<span class="ok">●</span>':'<span class="bad">●</span>'} · LM Studio ${st.lmstudio?'<span class="ok">●</span>':'<span class="bad">●</span>'}`;
    if(!st.comfyui) banner($("genBanner"),"ComfyUI no responde en :8188 — arráncalo desde el Hub.","err");
    else if(!st.nodes_ok) banner($("genBanner"),"Faltan nodos nativos de Ideogram 4. Actualiza ComfyUI (0.27+).","err");
  } catch(e){ $("hdrStatus").innerHTML=`<span class="bad">sin conexión</span>`; }

  try {
    const m = await jget("/models"); state.models = m;
    fillSelect($("unetCond"), m.cond);
    fillSelect($("unetUncond"), m.uncond);
    fillSelect($("clipName"), m.text_encoders);
    fillSelect($("vaeName"), m.vae);
    fillSelect($("psampler"), m.samplers, "euler");
    fillSelect($("llmModel"), m.llms.map(x=>x.id));
    // Preseleccionar la config óptima validada con GPU (batería, sesión 36d):
    // cond=int8_convrot · uncond=nvfp4_mixed · clip=qwen3vl_8b_fp8 · vae=flux2.
    // Sin esto los selectores caen en el 1º del desplegable — p.ej. el CLIP
    // por defecto sería gemma4 (orden alfabético), incompatible con el tipo
    // ideogram4 → nodo de ComfyUI revienta con un unpack de shape.
    const pick=(sel,arr,re)=>{ const v=(arr||[]).find(x=>re.test(x)); if(v) sel.value=v; };
    pick($("unetCond"),   m.cond,          /int8_convrot/i);
    pick($("unetUncond"), m.uncond,        /unconditional.*nvfp4/i);
    pick($("clipName"),   m.text_encoders, /qwen3vl.*fp8/i);
    pick($("vaeName"),    m.vae,           /flux2/i);
    if(m.lm_error) banner($("genBanner"), "LM Studio: "+m.lm_error+" (puedes pegar un JSON a mano).","warn");
  } catch(e){ banner($("genBanner"),"No se pudieron cargar los modelos: "+e.message,"err"); }
  refreshLoras();
  setMode("auto");
  drawCanvas();
}

// ── Caption (descripción → JSON vía LLM) ──────────────────────────────────────
$("btnCaption").onclick = async () => {
  const desc = $("desc").value.trim();
  if(!desc){ banner($("genBanner"),"Escribe una descripción primero.","warn"); return; }
  const llm = $("llmModel").value;
  if(!llm){ banner($("genBanner"),"No hay LLM en LM Studio. Pega un JSON a mano.","warn"); return; }
  $("btnCaption").disabled=true; $("btnCaption").textContent="Generando…";
  banner($("genBanner"),"","");
  try{
    const r = await jpost("/caption",{description:desc,llm_model:llm,
      width:+$("pw").value,height:+$("ph").value});
    applyCaption(r.caption);
  }catch(e){ banner($("genBanner"),"Fallo del LLM: "+e.message,"err"); }
  $("btnCaption").disabled=false; $("btnCaption").textContent="Generar JSON";
};

// ── Switch Automático / Manual ────────────────────────────────────────────────
function setMode(m){
  state.mode = m;
  const manual = m === "manual";
  $("modeAuto").className   = manual ? "ghost" : "act";
  $("modeManual").className = manual ? "act"   : "ghost";
  $("lblDesc").textContent  = manual ? "1 · Prompt general" : "1 · Descripción";
  $("desc").placeholder     = manual
    ? "Idea global de la escena (tema, estilo, ambiente). Tú dibujas las cajas abajo; el LLM solo completa el resto."
    : "Describe la imagen en lenguaje natural. Ej: un gato naranja dormido en un sofá de terciopelo azul junto a una ventana al atardecer.";
  $("btnCaption").style.display   = manual ? "none" : "";
  $("btnAssemble").style.display  = manual ? "" : "none";
  $("btnTranslate").style.display = manual ? "" : "none";
  $("btnRefine").style.display    = manual ? "" : "none";
  $("descHint").textContent = manual
    ? "Manual: dibuja las cajas (doble-clic / «+ Caja»), muévelas y escribe el desc de cada una + el prompt general. «Organizar JSON» arma el JSON sin LLM (respeta tus textos); «Traducir / corregir» pasa tus textos a inglés literal y corregido (sin inflar); «Configurar con LLM» además completa estilo y fondo."
    : "El LLM descompone la escena en sujeto + entorno (varias cajas = evita el filtro). Escribe en tu idioma: el JSON sale en inglés.";
}
$("modeAuto").onclick   = () => setMode("auto");
$("modeManual").onclick = () => setMode("manual");

// ── Organizar JSON (modo manual SIN LLM): estructura/valida respetando textos ──
$("btnAssemble").onclick = async () => {
  if(!state.caption || !state.boxes.length){ banner($("genBanner"),"Dibuja al menos una caja primero.","warn"); return; }
  const general=$("desc").value.trim();
  if(general) state.caption.high_level_description=general;   // el prompt general manda, sin LLM
  syncJsonFromState(); banner($("genBanner"),"","");
  try{
    const r=await jpost("/assemble",{caption:state.caption, general});
    applyCaption(r.caption);
    banner($("genBanner"),"JSON organizado sin LLM: tus textos intactos, estructura validada.","warn");
  }catch(e){ banner($("genBanner"),"No se pudo organizar: "+e.message,"err"); }
};

// ── Traducir / corregir (modo manual): lleva tus textos a inglés literal ──────
$("btnTranslate").onclick = async () => {
  if(!state.caption || !state.boxes.length){ banner($("genBanner"),"Dibuja al menos una caja primero.","warn"); return; }
  const llm=$("llmModel").value;
  if(!llm){ banner($("genBanner"),"No hay LLM en LM Studio. Escribe el JSON en inglés a mano.","warn"); return; }
  const general=$("desc").value.trim();
  if(general && !(state.caption.high_level_description||"").trim()) state.caption.high_level_description=general;
  syncJsonFromState();
  $("btnTranslate").disabled=true; $("btnTranslate").textContent="Traduciendo…"; banner($("genBanner"),"","");
  try{
    const r=await jpost("/translate",{caption:state.caption, llm_model:llm, general,
      width:+$("pw").value, height:+$("ph").value});
    applyCaption(r.caption);
    banner($("genBanner"),"Textos traducidos a inglés y corregidos (tus cajas se conservan). Revisa y edita si hace falta antes de renderizar.","warn");
  }catch(e){ banner($("genBanner"),"Fallo traduciendo: "+e.message,"err"); }
  $("btnTranslate").disabled=false; $("btnTranslate").textContent="Traducir / corregir";
};

// ── Configurar con LLM (modo manual): completa el borrador sin tocar las cajas ─
$("btnRefine").onclick = async () => {
  if(!state.caption || !state.boxes.length){ banner($("genBanner"),"Dibuja al menos una caja primero.","warn"); return; }
  const llm=$("llmModel").value;
  if(!llm){ banner($("genBanner"),"No hay LLM en LM Studio. Rellena el JSON a mano.","warn"); return; }
  const general=$("desc").value.trim();
  if(general && !(state.caption.high_level_description||"").trim()) state.caption.high_level_description=general;
  syncJsonFromState();
  $("btnRefine").disabled=true; $("btnRefine").textContent="Configurando…"; banner($("genBanner"),"","");
  try{
    const r=await jpost("/refine",{caption:state.caption, llm_model:llm, general,
      width:+$("pw").value, height:+$("ph").value});
    applyCaption(r.caption);
    banner($("genBanner"),"JSON configurado por el LLM (tus cajas se conservan). Revisa y renderiza.","warn");
  }catch(e){ banner($("genBanner"),"Fallo configurando: "+e.message,"err"); }
  $("btnRefine").disabled=false; $("btnRefine").textContent="Configurar con LLM";
};

function applyCaption(caption){
  state.caption = caption;
  state.boxes = (caption.compositional_deconstruction?.elements)||[];
  state.sel = state.boxes.length? 0 : -1;
  syncJsonFromState(); refreshBoxList(); drawCanvas();
}

function syncJsonFromState(){
  if(state.caption){
    state.caption.compositional_deconstruction = state.caption.compositional_deconstruction||{background:"",elements:[]};
    state.caption.compositional_deconstruction.elements = state.boxes;
    $("jsonPrompt").value = JSON.stringify(state.caption,null,2);
    $("jsonPrompt").classList.remove("invalid");
  }
}

// Aplica el JSON del textarea a state SIN reescribir el textarea (respeta el
// cursor del usuario). Conserva la selección si sigue en rango.
function applyJsonToState(obj){
  state.caption = obj;
  state.boxes = (obj.compositional_deconstruction?.elements)||[];
  state.sel = state.boxes.length ? Math.min(Math.max(state.sel,0), state.boxes.length-1) : -1;
  refreshBoxList(); drawCanvas();
}
$("btnSyncFromJson").onclick = () => {
  try{
    applyJsonToState(JSON.parse($("jsonPrompt").value));
    $("jsonPrompt").classList.remove("invalid");
    banner($("genBanner"),"","");
  }catch(e){ banner($("genBanner"),"JSON inválido: "+e.message,"err"); }
};
// Espejo bidireccional en vivo: editar el JSON a mano actualiza state al vuelo
// (parse seguro). Así las cajas y el JSON nunca divergen — tocar una caja ya
// reescribe el JSON, y escribir en el JSON ya mueve las cajas. Mientras el texto
// sea inválido (a medio teclear) se marca en rojo y NO se pisa el state.
$("jsonPrompt").addEventListener("input", () => {
  try{
    applyJsonToState(JSON.parse($("jsonPrompt").value));
    $("jsonPrompt").classList.remove("invalid");
  }catch{ $("jsonPrompt").classList.add("invalid"); }
});

// ── Lista/edición de la caja seleccionada ─────────────────────────────────────
function refreshBoxList(){
  const sel=$("selBox"); sel.innerHTML="";
  state.boxes.forEach((b,i)=>{ const o=document.createElement("option");
    o.value=i; o.textContent=`${i+1}. ${b.type} — ${(b.desc||b.text||"").slice(0,40)}`; sel.appendChild(o); });
  sel.value=state.sel;
  syncSelFields();
}
function syncSelFields(){
  const b=state.boxes[state.sel];
  $("selBoxType").value=b?b.type:"obj";
  $("selBoxText").value=b?(b.text||""):"";
  $("selBoxDesc").value=b?(b.desc||""):"";
}
$("selBox").onchange = () => { state.sel=+$("selBox").value; syncSelFields(); drawCanvas(); };
$("selBoxType").onchange = () => { const b=state.boxes[state.sel]; if(b){ b.type=$("selBoxType").value; if(b.type!=="text") delete b.text; syncJsonFromState(); refreshBoxList(); } };
$("selBoxText").oninput = () => { const b=state.boxes[state.sel]; if(b){ b.text=$("selBoxText").value; syncJsonFromState(); } };
$("selBoxDesc").oninput = () => { const b=state.boxes[state.sel]; if(b){ b.desc=$("selBoxDesc").value; syncJsonFromState(); } };
$("btnDelBox").onclick = () => delBox();
function delBox(){ if(state.sel<0) return; state.boxes.splice(state.sel,1);
  state.sel=Math.min(state.sel,state.boxes.length-1); syncJsonFromState(); refreshBoxList(); drawCanvas(); }

// ── Canvas de bounding boxes (grid 0-1000) ────────────────────────────────────
const cv=$("bboxCanvas"), ctx=cv.getContext("2d");
const G=1000; // rejilla lógica
let drag=null; // {mode:'move'|'resize', ox,oy, box}

function setCanvasAspect(){
  const w=+$("pw").value||1024, h=+$("ph").value||1024;
  cv.style.aspectRatio = `${w} / ${h}`;
}
["pw","ph"].forEach(id=>$(id).addEventListener("input",()=>{setCanvasAspect();drawCanvas();}));

// ── Anti-bloqueo (experimental): mostrar opciones + campo según método ─────────
$("bypassOn").addEventListener("change",()=>{ $("bypassOpts").style.display=$("bypassOn").checked?"block":"none"; });
$("bypassSplit").addEventListener("change",()=>{ $("fldBypassSplit").style.display=$("bypassSplit").checked?"block":"none"; });
$("bypassMethod").addEventListener("change",()=>{
  const sigma=$("bypassMethod").value==="sigma";
  $("fldBypassSigma").style.display=sigma?"":"none";
  $("fldBypassNoise").style.display=sigma?"none":"";
});

function toLogic(ev){
  const r=cv.getBoundingClientRect();
  return { x: Math.max(0,Math.min(G,(ev.clientX-r.left)/r.width*G)),
           y: Math.max(0,Math.min(G,(ev.clientY-r.top)/r.height*G)) };
}
function boxRect(b){ const [y0,x0,y1,x1]=b.bbox; return {x0,y0,x1,y1}; }
const COLORS=["#8b7bff","#4caf82","#e0a94c","#e05c5c","#4c9be0","#c86ac8","#6ad0c8","#d0a06a"];

// Zona de agarre y tamaño de handle van en PÍXELES de pantalla y se convierten a
// unidades lógicas (0-1000) según el tamaño real del canvas: así agarrar una
// esquina cuesta lo mismo con el canvas grande (2048²) o pequeño.
function px2logic(px){ const r=cv.getBoundingClientRect(); return {x:px/r.width*G, y:px/r.height*G}; }
const CURSOR={nw:"nwse-resize", se:"nwse-resize", ne:"nesw-resize", sw:"nesw-resize"};
// ¿Sobre qué esquina de la caja cae el puntero? (null si ninguna). Radio ~16px.
function cornerAt(p, b){
  const {x0,y0,x1,y1}=boxRect(b), g=px2logic(16);
  const near=(cx,cy)=>Math.abs(p.x-cx)<=g.x && Math.abs(p.y-cy)<=g.y;
  if(near(x0,y0)) return "nw";
  if(near(x1,y0)) return "ne";
  if(near(x0,y1)) return "sw";
  if(near(x1,y1)) return "se";
  return null;
}

function drawCanvas(){
  setCanvasAspect();
  ctx.clearRect(0,0,G,G);
  ctx.fillStyle="#101014"; ctx.fillRect(0,0,G,G);
  // imagen de referencia (fondo, bajo la rejilla y las cajas). El buffer es
  // cuadrado (G×G) pero el canvas se estira por CSS al aspect pw/ph; para que la
  // referencia conserve SU aspect ratio en pantalla se dibuja con letterbox
  // corrigiendo por el aspect del canvas: aspect_buffer = (iw/ih)/(pw/ph).
  if(state.refImg){
    const iw=state.refImg.naturalWidth||1, ih=state.refImg.naturalHeight||1;
    const Rc=(+$("pw").value||1)/(+$("ph").value||1);
    const ar=(iw/ih)/Rc;                         // aspect a usar dentro del buffer
    let bw,bh; if(ar>=1){ bw=G; bh=G/ar; } else { bh=G; bw=G*ar; }
    const bx=(G-bw)/2, by=(G-bh)/2;
    ctx.globalAlpha=state.refOpacity;
    ctx.drawImage(state.refImg,bx,by,bw,bh);
    ctx.globalAlpha=1;
  }
  // rejilla
  ctx.strokeStyle="rgba(255,255,255,.05)"; ctx.lineWidth=1;
  for(let i=1;i<10;i++){ ctx.beginPath(); ctx.moveTo(i*100,0); ctx.lineTo(i*100,G); ctx.moveTo(0,i*100); ctx.lineTo(G,i*100); ctx.stroke(); }
  state.boxes.forEach((b,i)=>{
    const {x0,y0,x1,y1}=boxRect(b); const c=COLORS[i%COLORS.length];
    const seld=i===state.sel;
    ctx.strokeStyle=c; ctx.lineWidth=seld?4:2;
    ctx.strokeRect(x0,y0,x1-x0,y1-y0);
    ctx.fillStyle=c+"22"; ctx.fillRect(x0,y0,x1-x0,y1-y0);
    // etiqueta
    ctx.fillStyle=c; ctx.fillRect(x0,y0,60,34); ctx.fillStyle="#000";
    ctx.font="bold 22px Inter,sans-serif"; ctx.fillText(String(i+1),x0+8,y0+25);
    // texto
    ctx.fillStyle="#ddd"; ctx.font="18px Inter,sans-serif";
    const t=(b.type==="text"&&b.text)?`"${b.text}"`:(b.desc||"");
    if(t) ctx.fillText(t.slice(0,34), x0+6, y0+52);
    // handles de resize en las 4 esquinas (solo la caja seleccionada), al frente
    if(seld){
      const h=px2logic(12);
      ctx.fillStyle=c; ctx.strokeStyle="#fff"; ctx.lineWidth=2;
      [[x0,y0],[x1,y0],[x0,y1],[x1,y1]].forEach(([hx,hy])=>{
        ctx.fillRect(hx-h.x/2, hy-h.y/2, h.x, h.y);
        ctx.strokeRect(hx-h.x/2, hy-h.y/2, h.x, h.y);
      });
    }
  });
}

function addBox(cy=500, cx=500, s=140){
  const nb={type:"obj",bbox:[Math.max(0,Math.round(cy-s)),Math.max(0,Math.round(cx-s)),
                             Math.min(G,Math.round(cy+s)),Math.min(G,Math.round(cx+s))],desc:"nuevo elemento"};
  if(!state.caption) state.caption={high_level_description:"",style_description:{aesthetics:"",lighting:"",medium:""},compositional_deconstruction:{background:"",elements:[]}};
  state.boxes.push(nb); state.sel=state.boxes.length-1;
  syncJsonFromState(); refreshBoxList(); drawCanvas();
}
cv.addEventListener("dblclick",(ev)=>{ const p=toLogic(ev); addBox(p.y,p.x,120); });
$("btnAddBox").onclick = () => addBox();

// ── Imagen de referencia (guía visual bajo las cajas; NO se envía a generar) ───
function refButtons(has){
  $("btnRefFit").style.display   = has ? "" : "none";
  $("refBase").style.display     = has ? "" : "none";
  $("btnAutoprompt").style.display = has ? "" : "none";
  $("btnRefClear").style.display = has ? "" : "none";
  $("refOpacityWrap").style.display = has ? "flex" : "none";
}
$("btnRefLoad").onclick = () => $("refFile").click();
$("refFile").onchange = (e) => {
  const f=e.target.files[0]; if(!f) return;
  const rd=new FileReader();
  rd.onload = () => { const img=new Image();
    img.onload = () => { state.refImg=img; state.refDataUri=rd.result; refButtons(true); drawCanvas(); };
    img.src=rd.result; };
  rd.readAsDataURL(f);
  e.target.value="";   // permite recargar el mismo archivo
};
$("btnRefClear").onclick = () => { state.refImg=null; state.refDataUri=null; refButtons(false); drawCanvas(); };

// ── Autoprompt: la referencia → prompt + cajas + JSON vía un VLM multimodal ────
$("btnAutoprompt").onclick = async () => {
  if(!state.refDataUri){ banner($("genBanner"),"Carga una imagen de referencia primero.","warn"); return; }
  const llm=$("llmModel").value;
  if(!llm){ banner($("genBanner"),"No hay modelo en LM Studio. Carga un VLM multimodal.","warn"); return; }
  const general=$("desc").value.trim();   // nota opcional (trigger, ajuste de estilo)
  $("btnAutoprompt").disabled=true; $("btnAutoprompt").textContent="Analizando…"; banner($("genBanner"),"","");
  try{
    const r=await jpost("/autoprompt",{image:state.refDataUri, llm_model:llm, general,
      width:+$("pw").value, height:+$("ph").value});
    applyCaption(r.caption);
    setMode("manual");   // el usuario retoca las cajas sobre la referencia de fondo
    banner($("genBanner"),"Autoprompt listo: revisa y ajusta las cajas (la referencia queda de fondo como guía) antes de renderizar.","warn");
  }catch(e){ banner($("genBanner"),"Fallo del autoprompt: "+e.message,"err"); }
  $("btnAutoprompt").disabled=false; $("btnAutoprompt").textContent="✨ Autoprompt";
};
$("refOpacity").oninput = () => { state.refOpacity=(+$("refOpacity").value)/100; drawCanvas(); };
// Ajustar la resolución de salida al aspect ratio de la referencia: el lado más
// largo → 2048, el corto proporcional redondeado a múltiplo de 16 (latente Flux2).
$("btnRefFit").onclick = () => {
  if(!state.refImg){ banner($("genBanner"),"Carga una imagen de referencia primero.","warn"); return; }
  const nw=state.refImg.naturalWidth, nh=state.refImg.naturalHeight;
  if(!nw||!nh) return;
  const LONG=+$("refBase").value||2048, long=Math.max(nw,nh);
  const short=Math.max(16, Math.round(LONG*Math.min(nw,nh)/long/16)*16);
  if(nw>=nh){ $("pw").value=LONG; $("ph").value=short; }
  else      { $("ph").value=LONG; $("pw").value=short; }
  setCanvasAspect(); drawCanvas();
  banner($("genBanner"),`Resolución ajustada a la referencia: ${$("pw").value}×${$("ph").value}.`,"warn");
};

cv.addEventListener("pointerdown",(ev)=>{
  const p=toLogic(ev);
  // 1) resize: ¿el puntero cae sobre una esquina de la caja seleccionada?
  const selB=state.boxes[state.sel];
  if(selB){
    const c=cornerAt(p, selB);
    if(c){ drag={mode:"resize",corner:c,box:selB}; cv.style.cursor=CURSOR[c];
           cv.setPointerCapture(ev.pointerId); return; }
  }
  // 2) mover / seleccionar: primera caja (de arriba) que contiene el puntero
  for(let i=state.boxes.length-1;i>=0;i--){
    const {x0,y0,x1,y1}=boxRect(state.boxes[i]);
    if(p.x>=x0&&p.x<=x1&&p.y>=y0&&p.y<=y1){
      state.sel=i; refreshBoxList(); drawCanvas();
      drag={mode:"move",box:state.boxes[i],ox:p.x,oy:p.y}; cv.style.cursor="move";
      cv.setPointerCapture(ev.pointerId); return; }
  }
});
cv.addEventListener("pointermove",(ev)=>{
  const p=toLogic(ev);
  if(!drag){ updateHoverCursor(p); return; }
  const b=drag.box;
  if(drag.mode==="resize"){
    // La esquina arrastrada mueve solo sus dos bordes; el opuesto queda fijo.
    let [y0,x0,y1,x1]=b.bbox; const c=drag.corner, MIN=20;
    if(c.includes("n")) y0=Math.min(p.y, y1-MIN);
    if(c.includes("s")) y1=Math.max(p.y, y0+MIN);
    if(c.includes("w")) x0=Math.min(p.x, x1-MIN);
    if(c.includes("e")) x1=Math.max(p.x, x0+MIN);
    b.bbox=[Math.round(Math.max(0,y0)),Math.round(Math.max(0,x0)),
            Math.round(Math.min(G,y1)),Math.round(Math.min(G,x1))];
  } else {
    const dx=p.x-drag.ox, dy=p.y-drag.oy; let [y0,x0,y1,x1]=b.bbox;
    const w=x1-x0,h=y1-y0; x0=Math.max(0,Math.min(G-w,x0+dx)); y0=Math.max(0,Math.min(G-h,y0+dy));
    b.bbox=[Math.round(y0),Math.round(x0),Math.round(y0+h),Math.round(x0+w)]; drag.ox=p.x; drag.oy=p.y;
  }
  drawCanvas();
});
// Feedback de cursor sin arrastrar: resize en esquinas, move dentro, crosshair fuera.
function updateHoverCursor(p){
  const b=state.boxes[state.sel];
  if(b){ const c=cornerAt(p,b); if(c){ cv.style.cursor=CURSOR[c]; return; } }
  for(let i=state.boxes.length-1;i>=0;i--){
    const {x0,y0,x1,y1}=boxRect(state.boxes[i]);
    if(p.x>=x0&&p.x<=x1&&p.y>=y0&&p.y<=y1){ cv.style.cursor="move"; return; }
  }
  cv.style.cursor="crosshair";
}
cv.addEventListener("pointerup",(ev)=>{ if(drag){ drag=null; syncJsonFromState(); updateHoverCursor(toLogic(ev)); } });
cv.addEventListener("pointerleave",()=>{ if(!drag) cv.style.cursor="crosshair"; });
document.addEventListener("keydown",(e)=>{ if(e.key==="Delete"&&state.sel>=0&&document.activeElement.tagName!=="INPUT"&&document.activeElement.tagName!=="TEXTAREA") delBox(); });

// ── Render (pipeline completo) ────────────────────────────────────────────────
$("btnGenerate").onclick = async () => {
  // En manual el textarea es el prompt general: se vuelca al resumen si aún no lo hay.
  if(state.mode==="manual" && state.caption){
    const g=$("desc").value.trim();
    if(g && !(state.caption.high_level_description||"").trim()) state.caption.high_level_description=g;
    syncJsonFromState();
  }
  const body = {
    description: $("desc").value.trim(),
    json_prompt: $("jsonPrompt").value.trim(),
    llm_model: $("llmModel").value,
    unet_cond: $("unetCond").value,
    unet_uncond: $("unetUncond").value,
    clip_name: $("clipName").value,
    vae_name: $("vaeName").value,
    width:+$("pw").value, height:+$("ph").value, steps:+$("psteps").value,
    cfg:+$("pcfg").value, mu:+$("pmu").value, std:+$("pstd").value,
    sampler:$("psampler").value, seed:+$("pseed").value,
    manage_vram: $("manageVram").checked,
    bypass_enabled: $("bypassOn").checked,
    bypass_method: $("bypassMethod").value,
    bypass_noise_scale: +$("bypassNoise").value,
    bypass_first_sigma: +$("bypassSigma").value,
    bypass_split: $("bypassSplit").checked,
    bypass_split_step: +$("bypassSplitStep").value,
    loras: loraPayload(),
    turbo: $("turboOn").checked,
  };
  if(!body.unet_cond){ banner($("genBanner"),"Selecciona el modelo condicional.","warn"); return; }
  if(!body.turbo && !body.unet_uncond){ banner($("genBanner"),"Selecciona el modelo incondicional (o activa el modo turbo).","warn"); return; }
  banner($("genBanner"),"","");
  $("btnGenerate").disabled=true;
  try{
    const {job_id}=await jpost("/generate",body);
    pollJob(job_id);
  }catch(e){ banner($("genBanner"),"No se pudo lanzar: "+e.message,"err"); $("btnGenerate").disabled=false; }
};

const PHASE={queued:"En cola",caption:"Generando JSON",unload:"Liberando VRAM",render:"Renderizando",check:"Verificando",save:"Guardando",done:"Listo"};
function pollJob(id){
  $("jobStatus").style.display="block"; $("resultBox").innerHTML="";
  clearInterval(state.jobTimer);
  state.jobTimer=setInterval(async ()=>{
    let j; try{ j=await jget("/jobs/"+id); }catch(e){ return; }
    $("jobPhase").textContent=PHASE[j.phase]||j.phase;
    if(j.phase==="render"&&j.steps_total){ $("jobStep").textContent=`${j.step}/${j.steps_total}`;
      $("jobBar").style.width=(100*j.step/j.steps_total)+"%"; }
    if(j.status==="done"){ clearInterval(state.jobTimer); $("btnGenerate").disabled=false;
      $("jobBar").style.width="100%"; $("jobPhase").textContent="Listo"; $("jobStep").textContent="";
      showResult(j.run_id,j.blocked); }
    if(j.status==="error"){ clearInterval(state.jobTimer); $("btnGenerate").disabled=false;
      $("jobStatus").style.display="none"; banner($("genBanner"),"Error: "+j.error,"err"); }
  },600);
}
function showResult(runId,blocked){
  const warn = blocked ? `<div class="banner warn">⚠ Posible banner de bloqueo del filtro. Reintenta con otra seed o añade más cajas de entorno.</div>` : "";
  $("resultBox").innerHTML = warn + `<img id="resultImg" src="${API}/history/${runId}/image?t=${Date.now()}">`;
}

$("btnUnload").onclick = async () => {
  $("btnUnload").disabled=true; $("btnUnload").textContent="Liberando…";
  try{ const r=await jpost("/unload"); banner($("genBanner"), r.ok?"VRAM del LLM liberada.":"No se pudo (¿lms CLI?): "+(r.reason||r.hint||""), r.ok?"warn":"err"); }
  catch(e){ banner($("genBanner"),"Error: "+e.message,"err"); }
  $("btnUnload").disabled=false; $("btnUnload").textContent="Liberar VRAM";
};

// ── Historial ─────────────────────────────────────────────────────────────────
$("btnReloadHist").onclick=loadHistory;
async function loadHistory(){
  try{
    const {runs}=await jget("/history");
    $("gallery").innerHTML = runs.length? runs.map(r=>`
      <div class="gcard">
        <img src="${API}/history/${r.id}/image" onclick="window.open('${API}/history/${r.id}/image')">
        <div class="meta">
          <div class="mono">${r.id}</div>
          <div>${(r.description||"—").slice(0,50)}</div>
          ${r.blocked?'<div class="blocked">⚠ posible bloqueo</div>':''}
          <div style="display:flex; gap:6px; margin-top:6px">
            <button class="ghost" style="padding:3px 8px" onclick="revealRun('${r.id}')" title="Abrir la carpeta contenedora en el explorador">Abrir carpeta</button>
            <button class="ghost" style="padding:3px 8px" onclick="delRun('${r.id}')">Borrar</button>
          </div>
        </div>
      </div>`).join("") : '<div class="dim">Sin generaciones todavía.</div>';
  }catch(e){ $("gallery").innerHTML='<div class="bad">'+e.message+'</div>'; }
}
window.delRun = async (id)=>{ if(!confirm("¿Borrar "+id+"?")) return; await jdel("/history/"+id); loadHistory(); };
window.revealRun = async (id)=>{ try{ await jpost("/history/"+id+"/reveal"); }
  catch(e){ alert("No se pudo abrir la carpeta: "+e.message); } };

// ── Diagnóstico ───────────────────────────────────────────────────────────────
$("btnReloadDiag").onclick=loadDiag;
async function loadDiag(){
  try{
    const st=await jget("/status");
    $("diagServices").innerHTML=`
      <div class="status-row"><span>ComfyUI (:8188)</span><span class="${st.comfyui?'ok':'bad'}">${st.comfyui?'en línea':'apagado'}</span></div>
      <div class="status-row"><span>LM Studio (:1234)</span><span class="${st.lmstudio?'ok':'bad'}">${st.lmstudio?'en línea':'apagado'}</span></div>`;
    $("diagNodes").innerHTML=Object.entries(st.nodes||{}).map(([n,ok])=>
      `<div class="status-row"><span class="mono">${n}</span><span class="${ok?'ok':'bad'}">${ok?'✓':'✗'}</span></div>`).join("")||'<div class="dim">Arranca ComfyUI para sondear.</div>';
  }catch(e){ $("diagServices").innerHTML='<div class="bad">'+e.message+'</div>'; }
}

// ── LoRAs: picker visual + stack montado (máx. 4) ─────────────────────────────
const escHtml = (s) => String(s).replace(/[&<>"]/g, c => (
  {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

const MAX_LORAS = 4;
let loraCatalog = [];            // catálogo del almacén (filtrado o show_all)
let loraStack = [];              // montados: {file,name,subfolder,rank,has_preview,strength,target}
let loraCollapsed = false;

const loraThumb = (l, cls) => l.has_preview
  ? `<img class="${cls}" loading="lazy" src="${API}/lora-preview?file=${encodeURIComponent(l.file)}">`
  : `<div class="${cls==='lp-thumb'?'lp-thumb-ph':'ph'}">🧩</div>`;

function loraPayload(){
  if(!$("lorasOn").checked) return [];
  return loraStack.map(l => ({name:l.file, strength:l.strength, target:l.target}));
}

async function refreshLoras(){
  try{
    const all = $("lp-all").checked;
    const data = await jget("/loras" + (all ? "?show_all=true" : ""));
    loraCatalog = data.loras || [];
  }catch(e){ loraCatalog = []; }
  renderLoraGrid();
}

function renderStack(){
  const on = $("lorasOn").checked;
  $("lorasBody").style.display = on ? "block" : "none";
  const names = loraStack.map(l => `<span class="tag" title="${escHtml(l.file)}">${escHtml(l.name)}${l.target!=='both'?' · '+l.target:''}</span>`).join("");
  $("lorasCollapse").textContent = loraCollapsed ? "▸" : "▾";
  $("loraStack").style.display = loraCollapsed ? "none" : "flex";
  if(loraCollapsed){
    $("lorasSummary").innerHTML = loraStack.length ? names : '<span class="dim">sin LoRAs montados</span>';
  }else{
    $("lorasSummary").textContent = `${loraStack.length}/${MAX_LORAS} montados`;
  }
  const stack = $("loraStack");
  stack.innerHTML = "";
  loraStack.forEach((l, i) => {
    const row = document.createElement("div");
    row.className = "lora-row";
    row.innerHTML =
      loraThumb(l, l.has_preview ? "thumb" : "ph") +
      `<div class="info"><div class="nm" title="${escHtml(l.file)}">${escHtml(l.name)}`
      + (l.rank ? ` <span class="dim">· r${l.rank}</span>` : "") + `</div>`
      + `<div class="ctl">peso <input type="number" data-i="${i}" data-k="strength" value="${l.strength}" step="0.05" min="-2" max="2">`
      + ($("turboOn").checked
          ? `<span class="dim">destino cond (turbo)</span>`
          : `destino <select data-i="${i}" data-k="target">`
            + ["both","cond","uncond"].map(t=>`<option value="${t}"${t===l.target?" selected":""}>${t==="uncond"?"incond":t}</option>`).join("")
            + `</select>`)
      + `</div></div>`
      + `<button class="rm" data-i="${i}" title="Quitar">✕</button>`;
    stack.appendChild(row);
  });
  stack.querySelectorAll("input,select").forEach(el => el.onchange = () => {
    const l = loraStack[+el.dataset.i];
    if(el.dataset.k==="strength") l.strength = +el.value; else l.target = el.value;
  });
  stack.querySelectorAll(".rm").forEach(b => b.onclick = () => {
    loraStack.splice(+b.dataset.i, 1); renderStack();
  });
}

// ── Picker overlay ────────────────────────────────────────────────────────────
function openLoraPicker(){
  if(loraStack.length >= MAX_LORAS){ banner($("genBanner"),`Máximo ${MAX_LORAS} LoRAs.`,"warn"); return; }
  $("lp-search").value = "";
  renderLoraGrid();
  $("lora-picker-overlay").classList.add("open");
  $("lp-search").focus();
}
function closeLoraPicker(){ $("lora-picker-overlay").classList.remove("open"); }

function renderLoraGrid(){
  const q = $("lp-search").value.trim().toLowerCase();
  const mounted = new Set(loraStack.map(l => l.file));
  const list = loraCatalog.filter(l => !q ||
    l.name.toLowerCase().includes(q) || (l.subfolder||"").toLowerCase().includes(q));
  $("lp-count").textContent = `${list.length} ${$("lp-all").checked ? "en total" : "compatibles"}`;
  const grid = $("lp-grid");
  grid.innerHTML = "";
  if(!list.length){
    grid.innerHTML = `<span class="dim" style="font-size:12px">No hay LoRAs${q?" que casen con la búsqueda":" de Ideogram 4 en el almacén"}.</span>`;
    return;
  }
  for(const l of list){
    const card = document.createElement("div");
    card.className = "lp-card" + (mounted.has(l.file) ? " dis" : "");
    card.innerHTML = loraThumb(l, "lp-thumb") +
      `<div class="lp-body"><div class="lp-name">${escHtml(l.name)}</div>`
      + `<div class="lp-meta">${escHtml(l.subfolder||"")}${l.rank?(l.subfolder?" · ":"")+"r"+l.rank:""}`
      + (l.arch_match ? "" : ' <span class="nomatch">no-Ideogram</span>') + `</div></div>`;
    card.title = l.file;
    card.onclick = () => {
      if(loraStack.length >= MAX_LORAS) return;
      loraStack.push({file:l.file, name:l.name, subfolder:l.subfolder, rank:l.rank,
                      has_preview:l.has_preview, strength:1.0, target:"both"});
      closeLoraPicker(); renderStack();
    };
    grid.appendChild(card);
  }
}

// Modo turbo: excluyente con el anti-bloqueo; bypasea el incondicional y el CFG.
$("turboOn").addEventListener("change", () => {
  const on = $("turboOn").checked;
  if(on){
    $("bypassOn").checked = false;
    $("bypassOpts").style.display = "none";
    if(+$("psteps").value > 8) $("psteps").value = 2;   // el turbo va en ~2 pasos
  }
  $("bypassOn").disabled = on;
  $("unetUncond").disabled = on;      // se bypasea (BasicGuider, sin negative)
  $("pcfg").disabled = on;            // BasicGuider no usa CFG
  renderStack();                      // los LoRAs pasan a mostrarse como "cond"
});

$("lorasOn").addEventListener("change", renderStack);
$("lorasCollapse").onclick = () => { loraCollapsed = !loraCollapsed; renderStack(); };
$("btnAddLora").onclick = openLoraPicker;
$("lp-close").onclick = closeLoraPicker;
$("lora-picker-overlay").onclick = (e) => { if(e.target.id==="lora-picker-overlay") closeLoraPicker(); };
$("lp-search").oninput = renderLoraGrid;
$("lp-all").addEventListener("change", refreshLoras);
renderStack();

init();
