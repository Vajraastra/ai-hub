/* Calibration records are immutable sources, independent of the active editor. */
(function (root) {
  'use strict';
  const CONFIG_KEYS = ['model','text_encoder','vae','loras','steps','cfg','sampler','scheduler','width','height','seed','denoise'];
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function snapshot(record) {
    const metadata=record.metadata||{}, source=record.config||metadata.config||record.snapshot?.config||metadata.snapshot?.config||{};
    const config={};
    for(const key of CONFIG_KEYS)if(source[key]!==undefined)config[key]=clone(source[key]);
    const seed=record.seed??metadata.seed??config.seed;
    if(seed!==undefined&&seed!==null)config.seed=String(seed);
    const calibration=record.calibration||metadata.calibration;
    return {config,prompt:calibration?.style_prompt||'',negative:calibration?.style_negative??record.negative??metadata.negative??source.negative??'',
      testPrompt:calibration?.test_prompt??record.prompt??metadata.prompt??source.prompt??'',hasCalibration:!!calibration};
  }
  function details(record) {
    const saved=snapshot(record),c=saved.config;
    const entry=(label,value,wide=false)=>`<div${wide?' class="calibration-wide"':''}><dt>${esc(label)}</dt><dd>${esc(value??'No registrado')}</dd></div>`;
    const loras=Array.isArray(c.loras)?(c.loras.length?`<ul class="calibration-loras">${c.loras.map(l=>`<li><span>${esc(l.file??l.name??'No registrado')}</span><strong>Peso ${esc(l.strength??'No registrado')}</strong></li>`).join('')}</ul>`:'<span>Sin LoRAs</span>'):'<span>No registrados</span>';
    const prompt=(label,value)=>`<div><strong>${esc(label)}</strong><p>${esc(value||'Vacío')}</p></div>`;
    return `<dl class="calibration-facts">${entry('Modelo',c.model,true)}${entry('Seed',c.seed,true)}${entry('Pasos',c.steps)}${entry('CFG',c.cfg)}${entry('Sampler',c.sampler)}${entry('Scheduler',c.scheduler)}${entry('Resolución',c.width&&c.height?`${c.width} × ${c.height}`:undefined,true)}<div class="calibration-wide"><dt>LoRAs y pesos</dt><dd>${loras}</dd></div></dl><details class="calibration-extra"><summary>Prompts y ajustes completos</summary><dl class="calibration-facts">${entry('Encoder',c.text_encoder,true)}${entry('VAE',c.vae,true)}${c.denoise!==undefined?entry('Denoise',c.denoise):''}</dl>${!saved.hasCalibration?'<p class="hint">Muestra antigua: el prompt de estilo no se registró separado de la escena.</p>':''}${prompt('Prompt de estilo',saved.prompt)}${prompt('Negativo',saved.negative)}${prompt(saved.hasCalibration?'Escena de prueba':'Prompt original',saved.testPrompt)}</details>`;
  }
  function stylePayload(record,{projectId,name,shared=false,prompt,negative,targetStyle=null}) {
    if(!projectId||record.project_id!==projectId)throw new Error('La muestra no pertenece al proyecto activo.');
    name=String(name??'').trim();
    if(!name)throw new Error('Escribe un nombre para el estilo.');
    if(record.purged_at||record.purged)throw new Error('Esta muestra ya no está disponible.');
    const saved=snapshot(record);
    if(!record.id||!saved.config.model)throw new Error('La muestra no conserva el modelo necesario para guardar un estilo.');
    const scope=shared?null:projectId;
    if(targetStyle&&(targetStyle.project_id||null)!==scope)throw new Error('La revisión debe conservar el proyecto del estilo original.');
    return {...(targetStyle?{id:targetStyle.id}:{}),project_id:scope,name,prompt:String(prompt??saved.prompt).trim(),
      negative:String(negative??saved.negative).trim(),config:saved.config,sample_ids:[...new Set([record.id,...(targetStyle?.sample_ids||[])])]};
  }
  const api={snapshot,details,stylePayload};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.AnimaCalibration=api;
})(globalThis);
