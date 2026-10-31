/* Unsaved element trials and recovery from the chosen image. */
(function(root) {
  'use strict';
  const clone=value=>JSON.parse(JSON.stringify(value));
  const labels={character:'personaje',outfit:'outfit',pose:'pose',expression:'expresión',background:'fondo',item:'objeto',personality:'personalidad',scene:'escenario'};
  const pieceNegative=piece=>piece?.negative??'';
  function payload({project,draft,token,style,seed,dimensions,negative,contextPrompt='',contextNegative='',sourcePiece}) {
    if(!project?.id)throw new Error('Abre un proyecto para guardar tus pruebas.');
    if(!labels[draft?.kind])throw new Error('Elige el tipo de elemento que quieres probar.');
    if(!style?.id||!Number.isInteger(style.revision)||style.revision<1||!style.config?.model)throw new Error('Elige un estilo calibrado. Puedes prepararlo en Estilos.');
    const exactSeed=String(seed??style.config.seed??'42');
    if(!/^\d+$/.test(exactSeed)||BigInt(exactSeed)>18446744073709551615n)throw new Error('La variación de la prueba no conserva una seed válida.');
    if(typeof pieceNegative(draft)!=='string')throw new Error('El negativo de la pieza debe ser texto.');
    const piece=clone(draft);
    for(const key of ['id','revision','generation_snapshot','created_at','updated_at'])delete piece[key];
    piece.project_id=project.id;piece.seed=exactSeed;
    if(piece.definition)delete piece.definition.trial_negative;
    const category=project.categories?.[0];
    const config={seed:exactSeed,width:dimensions.width,height:dimensions.height};
    if(piece.kind==='pose'&&piece.pose_control)config.pose_control=clone(piece.pose_control);
    return {project_id:project.id,category:typeof category==='string'?category:category?.id,
      mode:'txt2img',purpose:'piece_test',piece_ids:[],style_id:style.id,style_revision:style.revision,
      config,prompt:'',negative:'',
      piece_test:{kind:piece.kind,draft:piece,token,specialized:true,reuse_master_settings:false,
        ...(sourcePiece?.id?{source_piece_id:sourcePiece.id,source_piece_revision:sourcePiece.revision}:{}),
        context_prompt:contextPrompt,context_negative:contextNegative}};
  }
  function matches(record,token) {
    const trial=record.piece_test||record.metadata?.piece_test;
    return !!token&&(trial?.token===token||trial?.id===token);
  }
  function sample(record) {
    const metadata=record.metadata||{},trial=record.piece_test||metadata.piece_test||{};
    const snapshot=record.snapshot||metadata.snapshot||{},kind=trial.kind;
    const piece=snapshot.pieces?.find(p=>p.kind===kind)||trial.draft;
    if(!piece)throw new Error('Esta prueba no conserva la descripción del elemento.');
    const config=clone(record.config||metadata.config||{});
    config.seed=String(record.seed??metadata.seed??config.seed??'42');
    const identity=new Set((piece.loras||[]).map(l=>l.file.replace(/\\/g,'/').toLowerCase()));
    config.loras=(config.loras||[]).filter(l=>!identity.has(l.file.replace(/\\/g,'/').toLowerCase()));
    const draft=clone(piece);
    if(draft.draft){delete draft.id;delete draft.revision;delete draft.draft;draft.name=trial.draft?.name||'';}
    delete draft.generation_snapshot;
    if(draft.definition)delete draft.definition.trial_negative;
    draft.sample_asset_id=record.id;draft.seed=config.seed;
    return {draft,config,style:snapshot.style?clone(snapshot.style):null,negative:draft.negative||'',effectiveNegative:record.negative??metadata.negative??'',stylePrompt:trial.style_prompt??snapshot.style?.prompt??'',
      styleNegative:trial.style_negative??snapshot.style?.negative??'',
      contextPrompt:trial.context_prompt,contextNegative:trial.context_negative};
  }
  const api={payload,matches,sample};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.AnimaElementLab=api;
})(typeof globalThis!=='undefined'?globalThis:this);
