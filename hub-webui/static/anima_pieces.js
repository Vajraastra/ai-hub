/* Piece definitions and immutable generation recovery, shared by UI and CPU checks. */
(function (root) {
  'use strict';
  const PROFILES = {
    character: {label:'Personaje', help:'Define rostro, cabello, especie, cuerpo y proporciones. El vestuario y la pose se eligen por separado.', placeholder:'Identidad, rasgos distintivos y proporciones…'},
    personality: {label:'Personalidad', help:'Describe la actitud que debe comunicar el personaje, sin añadir ropa ni sustituir su identidad.', placeholder:'Actitud, presencia y manera de actuar…'},
    outfit: {label:'Outfit', help:'Define prendas, capas, materiales, colores y accesorios. La prueba lo presenta sobre un maniquí neutro.', placeholder:'Prendas y cómo se construyen y combinan…'},
    pose: {label:'Pose', help:'Guarda la postura y su control visual. El esqueleto y la cámara pertenecen a esta pieza.', placeholder:'Postura, orientación, manos y apoyos…'},
    expression: {label:'Expresión', help:'Describe ojos, cejas, boca e intensidad. Al derivar de una imagen sólo se sustituye este bloque.', placeholder:'Emoción y cambios concretos del rostro…'},
    background: {label:'Fondo', help:'Define lugar, arquitectura, cámara y zonas despejadas. Crea variantes de hora, clima o estado desde este maestro.', placeholder:'Lugar, composición, iluminación y estado…'},
    item: {label:'Objeto', help:'Define un objeto aislado, su construcción, materiales y orientación. Sus variantes conservan la familia del diseño.', placeholder:'Objeto, materiales, construcción y vista…'},
    scene: {label:'Escenario', help:'Describe el entorno de una toma. Puedes conservar estas piezas existentes o crear un maestro de Fondo.', placeholder:'Entorno, iluminación y composición…'},
  };
  const clone = value => JSON.parse(JSON.stringify(value));
  function profile(kind) { return {...(PROFILES[kind] || PROFILES.scene)}; }
  function variant(master) {
    const result={kind:master.kind, name:`${master.name} · variante`, prompt:master.prompt||'', negative:master.negative||'',
      tags:[...(master.tags||[])], project_id:master.project_id??null, master_id:master.id, master_revision:master.revision};
    for(const key of ['seed','loras','pose_control','generation_snapshot']) if(master[key] !== undefined) result[key]=clone(master[key]);
    return result;
  }
  function composition(record) {
    const snapshot=record.snapshot||record.metadata?.snapshot||{}, saved=record.composition||record.metadata?.composition||{};
    const config=record.config||record.metadata?.config||{};
    const storedSeed=record.seed??config.seed??record.metadata?.seed??'';
    if(typeof storedSeed==='number'&&!Number.isSafeInteger(storedSeed))throw new Error('La generación no conserva una seed exacta válida.');
    const seed=String(storedSeed);
    if(!/^\d+$/.test(seed)||BigInt(seed)>18446744073709551615n)throw new Error('La generación no conserva una seed exacta válida.');
    if(!snapshot.style?.id)throw new Error('La generación no conserva un estilo para recuperar su composición.');
    const pieces=Array.isArray(snapshot.pieces)?snapshot.pieces:[];
    return {source_asset_id:record.id,style_id:snapshot.style.id,style_revision:snapshot.style.revision,
      pieces:clone(pieces),piece_revisions:Object.fromEntries(pieces.map(piece=>[piece.id,piece.revision])),
      config:clone({...config,seed}),additional_prompt:saved.additional_prompt??'',additional_negative:saved.additional_negative??'',
      additional_known:typeof saved.additional_prompt==='string',category:record.category,
      format_snapshot:saved.format_snapshot?clone(saved.format_snapshot):null};
  }
  const api={profile,variant,composition};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.AnimaPieces=api;
})(typeof globalThis!=='undefined'?globalThis:this);
