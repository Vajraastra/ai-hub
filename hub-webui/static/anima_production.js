/* Resource-specific production choices. Category IDs remain owned by the project. */
(function (root) {
  'use strict';
  const ALL_KINDS = ['character', 'personality', 'outfit', 'pose', 'expression', 'scene'];
  const PROFILES = {
    character: {
      label: 'Personajes', description: 'Define la identidad, ropa, pose y expresión del personaje.',
      kinds: ALL_KINDS, expression: true,
      promptPlaceholder: 'Describe la toma, el encuadre y los detalles de este personaje…',
    },
    background: {
      label: 'Fondos', description: 'Construye el entorno, la composición y la iluminación.',
      kinds: ['background', 'scene'], expression: false,
      promptPlaceholder: 'Describe el lugar, la perspectiva, la iluminación y la atmósfera…',
    },
    item: {
      label: 'Objetos', description: 'Describe el objeto y el entorno en el que quieres mostrarlo.',
      kinds: ['item', 'scene'], expression: false,
      promptPlaceholder: 'Describe el objeto, sus materiales, sus detalles y cómo se presenta…',
    },
    card: {
      label: 'Cartas', description: 'Combina el sujeto y el escenario para la ilustración de la carta.',
      kinds: ALL_KINDS, expression: true,
      promptPlaceholder: 'Describe el sujeto, la composición y los detalles de la ilustración…',
    },
    custom: {
      label: 'Otros recursos', description: 'Combina las piezas que necesite este recurso.',
      kinds: [...ALL_KINDS, 'background', 'item'], expression: false,
      promptPlaceholder: 'Describe el recurso que quieres generar…',
    },
  };
  const ALIASES = {
    character: ['character', 'characters', 'personaje', 'personajes'],
    background: ['background', 'backgrounds', 'fondo', 'fondos', 'escenario', 'escenarios'],
    item: ['item', 'items', 'object', 'objects', 'objeto', 'objetos', 'prop', 'props'],
    card: ['card', 'cards', 'carta', 'cartas', 'tarjeta', 'tarjetas'],
  };
  const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const categoryId = category => typeof category === 'object' && category !== null
    ? category.id ?? category.file ?? category.name ?? category.label ?? '' : category;
  const categoryLabel = category => typeof category === 'object' && category !== null
    ? category.name ?? category.label ?? category.id ?? '' : category;
  function semanticId(value) {
    const tokens = normalize(value).split(/[^a-z0-9]+/).filter(Boolean);
    return Object.keys(ALIASES).find(id => ALIASES[id].some(alias => tokens.includes(alias)));
  }
  function profile(category) {
    const id = semanticId(categoryId(category)) || semanticId(categoryLabel(category)) || 'custom';
    const result = PROFILES[id];
    return {id, ...result, label: id === 'custom' ? String(categoryLabel(category) || result.label) : result.label,
      kinds: [...result.kinds]};
  }
  function matchesCategory(a, b) {
    const aId = normalize(categoryId(a)), bId = normalize(categoryId(b));
    if (aId === bId) return true;
    const left = profile(a), right = profile(b);
    return left.id !== 'custom' && left.id === right.id;
  }
  function preferredFormat(profileOrCategory, formats) {
    const kind = profile(profileOrCategory).id;
    if (kind === 'custom' || !Array.isArray(formats)) return null;
    const candidates = formats.filter(format => format && format.id != null);
    if (!candidates.length) return null;
    const score = format => {
      const label = `${format.id} ${format.name || ''}`;
      const semantic = semanticId(label);
      const width = Number(format.width), height = Number(format.height);
      const ratio = width > 0 && height > 0 ? width / height : 0;
      const portrait = ratio > 0 && ratio < .9;
      const landscape = ratio > 1.2;
      const square = ratio > 0 && Math.abs(ratio - 1) < .15;
      const sameKind = semantic === kind;
      const shape = kind === 'background' ? landscape : kind === 'item' ? square : portrait;
      return (sameKind ? 100 : 0) + (shape ? 10 : 0);
    };
    return candidates.reduce((best, candidate) => score(candidate) > score(best) ? candidate : best).id;
  }
  const api = {profile, preferredFormat, matchesCategory};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AnimaProduction = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
