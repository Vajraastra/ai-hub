/* Author-sourced prompting guidance. Suggestions never alter a prompt until clicked. */
(function (root) {
  'use strict';
  const ANIMA_SOURCE = 'https://huggingface.co/circlestone-labs/Anima/blob/main/README.md';
  const WAI_SOURCE = 'https://tensor.art/models/1003360262834670526';
  const QUALITY = ['masterpiece', 'best quality'];
  const GENERAL_NEGATIVE = ['worst quality', 'low quality', 'artist name', 'blurry', 'jpeg artifacts', 'chromatic aberration'];
  const SCORED_NEGATIVE = ['worst quality', 'low quality', 'score_1', 'score_2', 'score_3', 'artist name', 'blurry', 'jpeg artifacts', 'chromatic aberration'];
  const ORDER = 'Calidad / metadatos / época / clasificación → cantidad de sujetos → personaje y serie → @artista (opcional) → apariencia, ropa, pose, composición, fondo y luz. Dentro de cada grupo el orden es libre.';
  const bindings = new WeakMap();

  function formatTag(name, result = {}, csv = '') {
    const value = String(name ?? '').trim().toLowerCase();
    const formatted = value.startsWith('score_') ? value : value.replace(/_/g, ' ');
    const artist = result.category === 1 || (result.category === 8 && csv.includes('danbooru_e621_merged'));
    return artist && !formatted.startsWith('@') ? '@' + formatted : formatted;
  }
  function example(quality) {
    return [...quality, 'safe', '1girl', 'solo', 'long blue hair', 'green eyes', 'blue coat', 'full body', 'standing', 'castle courtyard', 'soft morning light'].join(', ')
      + '\nA young adventurer stands beside a stone fountain. Her blue coat moves gently in the breeze while the distant castle remains softly lit.';
  }
  function profileFor(modelName) {
    const name = String(modelName ?? '').normalize('NFKD').toLowerCase();
    const anima = /anima/.test(name), wai = /wai/.test(name);
    let profile;
    if (anima && /aesthetic/.test(name)) {
      profile = {id:'aesthetic',label:'Anima Aesthetic',quality:[...QUALITY],negative:[...GENERAL_NEGATIVE],optionalQuality:true,
        note:'La calidad es opcional. El autor desaconseja score_* en positivos y negativos; este preset adapta su negativo general omitiendo scores.'};
    } else if (anima && wai && /preview|(?:^|[^a-z])pw\s*\d/.test(name)) {
      profile = {id:'wai-preview',label:'WAI Anima Preview · guía general',quality:[...QUALITY],negative:[...GENERAL_NEGATIVE],
        note:'Versión preview: confirma su ficha específica. Esta guía común no aplica los scores de WAI Anima 1.0.'};
    } else if (anima && wai && /v?1[._ ]0\b|base[._ ]?1[._ ]?0\b|v10base10\b/.test(name)) {
      profile = {id:'wai-1.0',label:'WAI Anima 1.0',quality:[...QUALITY,'score_7'],
        negative:['worst quality','low quality','score_1','score_2','score_3','artist name','blurry','jpeg artifacts','lowres','censor'],
        source:WAI_SOURCE,sourceLabel:'Recomendaciones de WAI0731',note:'Prefijo publicado por el autor para la versión basada en Anima Base 1.0.'};
    } else if (anima && !wai && /base/.test(name)) {
      profile = {id:'base',label:'Anima Base',quality:[...QUALITY,'score_7'],negative:[...SCORED_NEGATIVE],
        note:'Prefijo recomendado por CircleStone Labs. Más tags o un score mayor no garantizan un resultado mejor.'};
    } else {
      profile = {id:'generic',label:'Anima · guía general',quality:[...QUALITY],negative:[...GENERAL_NEGATIVE],
        note:'No se identificó una variante conocida. Recomendación general sin scores; consulta la ficha de tu modelo.'};
    }
    return {...profile,order:ORDER,example:example(profile.quality),source:profile.source||ANIMA_SOURCE,sourceLabel:profile.sourceLabel||'Guía oficial de Anima'};
  }
  function normalizedTag(value) {
    let tag = String(value).trim();
    // Group delimiters and a trailing numeric weight are syntax, not part of a tag.
    let previous;
    do {
      previous=tag;
      tag=tag.replace(/^[\s([]+/, '').replace(/[\s)\]]+$/, '');
      tag=tag.replace(/:\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*$/, '');
    } while(tag!==previous);
    return tag.trim().toLowerCase().replace(/_/g, ' ').replace(/\s+/g, ' ');
  }
  function addTags(text, tags) {
    const original = String(text ?? '');
    const present = new Set(original.split(/[,\r\n]/).map(normalizedTag).filter(Boolean));
    const missing = [];
    for (const tag of Array.isArray(tags) ? tags : []) {
      const formatted = formatTag(tag), normalized = normalizedTag(formatted);
      if (normalized && !present.has(normalized)) {missing.push(formatted);present.add(normalized);}
    }
    if (!missing.length) return original;
    return missing.join(', ') + (original.trim() ? ', ' : '') + original;
  }
  function init() {
    const doc = root.document, container = doc?.getElementById('anima-prompt-guide');
    if (!container) return null;
    if (bindings.has(container)) return bindings.get(container);
    const $ = id => doc.getElementById(id), choose = $('choose-model');
    const quality = $('anima-add-quality'), negative = $('anima-add-negative');
    let current;
    function refresh() {
      const title = choose?.querySelector('.catalog-choice-info strong')?.textContent || '';
      const file = choose?.querySelector('.catalog-choice-info small')?.textContent || '';
      current = profileFor(`${title} ${file}`);
      const values = {'anima-prompt-variant':`${current.label}. ${current.note}`,'anima-quality-tags':current.quality.join(', '),'anima-prompt-order':current.order,'anima-prompt-example':current.example};
      for (const [id, value] of Object.entries(values)) if ($(id)) $(id).textContent = value;
      const link = $('anima-prompt-source');
      if (link) {link.href=current.source;link.textContent=current.sourceLabel;link.rel='noopener noreferrer';}
    }
    function insert(fieldId, tags, label) {
      const field = $(fieldId), status = $('anima-prompt-guide-status');
      if (!field) return;
      const before = field.value, result = addTags(before, tags);
      if (result === before) {if(status)status.textContent=`Los tags de ${label} ya estaban presentes.`;return;}
      const offset = result.length - before.length, start = field.selectionStart ?? 0, end = field.selectionEnd ?? start;
      if (typeof field.setRangeText === 'function') field.setRangeText(result.slice(0,offset),0,0,'preserve');
      else field.value = result;
      field.focus();field.setSelectionRange?.(start+offset,end+offset);
      field.dispatchEvent(new root.Event('input',{bubbles:true}));
      if(status)status.textContent=`Tags de ${label} añadidos al estilo. Revisa el resultado con la misma seed.`;
    }
    const addQuality = () => insert('style-prompt',current.quality,'calidad');
    const addNegative = () => insert('style-negative',current.negative,'negativo');
    quality?.addEventListener('click',addQuality);negative?.addEventListener('click',addNegative);
    const observer = choose && root.MutationObserver ? new root.MutationObserver(refresh) : null;
    observer?.observe(choose,{childList:true,subtree:true,characterData:true});
    refresh();
    const api = {refresh,destroy(){observer?.disconnect();quality?.removeEventListener('click',addQuality);negative?.removeEventListener('click',addNegative);bindings.delete(container);}};
    bindings.set(container,api);
    return api;
  }
  const api = {formatTag,profileFor,addTags,init};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnimaPromptGuide = api;
})(typeof window !== 'undefined' ? window : globalThis);
