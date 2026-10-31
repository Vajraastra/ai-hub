/* Presentation choices for specialised element editors; styles own generation settings. */
(function(root) {
  'use strict';
  const option=(value,label,prompt='')=>({value,label,prompt});
  const control=(key,label,options)=>({key,label,options});
  const views=()=>[
    option('front','Frente','Front view.'),
    option('threequarter','Tres cuartos','Three-quarter view.'),
    option('profile','Perfil','Side profile view.'),
    option('back','Espalda','Back view.'),
  ];
  const PROFILES={
    character:{label:'Personaje',help:'Describe su identidad, rasgos y proporciones. El estilo ya aporta el acabado.',
      placeholder:'Rostro, cabello, especie, cuerpo y rasgos distintivos…',orientation:'portrait',
      prompt:'Single character design study, neutral posture, plain fitted base clothing, simple neutral background.',
      negative:'text, watermark, collage, multiple views',
      controls:[control('framing','Encuadre',[
        option('full-body','Cuerpo entero','Full body, entire figure visible including feet.'),
        option('waist-up','Medio cuerpo','Waist-up portrait, framed from the waist to the head.'),
        option('portrait','Retrato','Close-up portrait, framed around the head and shoulders.'),
      ]),control('view','Vista',views())]},
    outfit:{label:'Outfit',help:'Describe las prendas, materiales y accesorios. La prueba usa un maniquí neutro.',
      placeholder:'Prendas, capas, materiales, colores y accesorios…',orientation:'portrait',
      prompt:'Clothing design displayed on one featureless neutral mannequin, neutral standing posture, entire outfit visible including shoes and accessories, simple neutral background.',
      negative:'detailed face, hair, text, watermark, collage, multiple views',
      controls:[control('view','Vista del atuendo',views().filter(o=>o.value!=='profile'))]},
    pose:{label:'Pose',help:'Ajusta el esqueleto o describe la postura. El acabado se hereda del estilo.',
      placeholder:'Postura, orientación, manos y apoyos…',orientation:'portrait',
      prompt:'Single featureless neutral mannequin demonstrating the selected posture, entire figure visible, simple neutral background.',
      negative:'text, watermark, collage, multiple views',controls:[control('view','Vista de la pose',views())]},
    expression:{label:'Expresión',help:'Describe los cambios de ojos, cejas y boca. Prueba la expresión en un retrato.',
      placeholder:'Emoción y cambios concretos del rostro…',orientation:'square',
      prompt:'Single front-facing portrait, facial expression study, simple neutral background.',
      negative:'text, watermark, collage, multiple views',
      controls:[control('intensity','Intensidad',[
        option('described','Según la descripción'),
        option('subtle','Sutil','Subtle restrained facial expression.'),
        option('strong','Marcada','Strong clearly readable facial expression.'),
      ])]},
    background:{label:'Fondo',help:'Diseña el lugar, la luz y el espacio de la escena. La salida es horizontal.',
      placeholder:'Lugar, arquitectura, perspectiva y distribución de la escena…',orientation:'landscape',
      prompt:'Environment illustration, readable large shapes, no people, no text or interface.',
      negative:'people, characters, text, watermark, interface',
      controls:[control('light','Iluminación',[
        option('described','Según la descripción'),option('day','Día','Daylight.'),
        option('dusk','Atardecer','Warm dusk lighting.'),option('night','Noche','Night-time lighting.'),
      ]),control('weather','Clima',[
        option('described','Según la descripción'),option('clear','Despejado','Clear weather.'),
        option('rain','Lluvia','Rainy weather.'),option('snow','Nieve','Snowy weather.'),
        option('fog','Niebla','Foggy weather.'),
      ]),control('space','Espacio para personajes',[
        option('none','Sin reserva específica'),
        option('left','A la izquierda','Leave uncluttered foreground space on the left for a character.'),
        option('right','A la derecha','Leave uncluttered foreground space on the right for a character.'),
        option('center','Al centro','Leave uncluttered foreground space in the center for characters.'),
      ])]},
    item:{label:'Objeto',help:'Diseña un recurso aislado con una silueta clara. La salida es cuadrada.',
      placeholder:'Objeto, construcción, materiales y detalles distintivos…',orientation:'square',
      prompt:'One object, fully visible and centered with generous margins, simple plain background, readable silhouette at small size.',
      negative:'person, mannequin, text, watermark, interface, collage',
      controls:[control('view','Vista del objeto',views().filter(o=>o.value!=='back')),
        control('presentation','Presentación',[
          option('isolated','Objeto aislado','Isolated object illustration.'),
          option('icon','Icono de inventario','Inventory icon composition, large clear silhouette, simplified small details.'),
        ])]},
    personality:{label:'Personalidad',help:'Describe la actitud y la presencia que debe comunicar el personaje.',
      placeholder:'Actitud, presencia y manera de actuar…',orientation:'square',
      prompt:'Single character portrait expressing the described personality, simple neutral background.',
      negative:'text, watermark, collage, multiple views',controls:[]},
  };
  PROFILES.scene={...PROFILES.background,label:'Escenario'};
  const clone=value=>JSON.parse(JSON.stringify(value));
  function entry(kind){if(!Object.hasOwn(PROFILES,kind))throw new Error('Tipo de elemento no reconocido.');return PROFILES[kind];}
  function profile(kind){const {prompt,negative,...result}=entry(kind);return clone(result);}
  function normalise(kind,input){
    const source=input&&typeof input==='object'&&!Array.isArray(input)?input:{};
    return Object.fromEntries(entry(kind).controls.map(c=>[c.key,c.options.some(o=>o.value===source[c.key])?source[c.key]:c.options[0].value]));
  }
  function values(kind,definition={}){return normalise(kind,definition?.presentation);}
  function context(kind,input={}){
    const data=entry(kind),chosen=normalise(kind,input);
    return {prompt:[data.prompt,...data.controls.map(c=>c.options.find(o=>o.value===chosen[c.key]).prompt)].filter(Boolean).join('\n'),negative:data.negative};
  }
  const DIMENSIONS={portrait:{width:1024,height:1536},landscape:{width:1536,height:864},square:{width:1024,height:1024}};
  const ALIASES={character:['personaje','character','portrait','retrato','vertical','sprite'],
    outfit:['outfit','vestuario','atuendo','ropa','personaje','portrait','vertical'],
    pose:['pose','postura','personaje','portrait','vertical'],
    expression:['expression','expresion','rostro','cara','portrait','retrato','square','cuadrado'],
    personality:['personalidad','personality','rostro','retrato','square','cuadrado'],
    background:['background','fondo','escenario','landscape','horizontal'],
    scene:['scene','escenario','fondo','background','landscape','horizontal'],
    item:['item','objeto','object','asset','square','cuadrado','icon']};
  const SPECIFIC={character:['personaje','character','sprite'],outfit:['outfit','vestuario','atuendo','ropa'],
    pose:['pose','postura'],expression:['expression','expresion','rostro','cara'],
    personality:['personality','personalidad'],background:['background','fondo'],
    scene:['scene','escenario','fondo','background'],item:['item','objeto','object','asset','icon']};
  const normalText=text=>String(text??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  function format(project,kind){
    const orientation=entry(kind).orientation,fallback=DIMENSIONS[orientation];
    const formats=project?.formats??project?.settings?.formats;
    const valid=(Array.isArray(formats)?formats:[]).filter(f=>{
      if(!f||!['width','height'].every(key=>Number.isInteger(f[key])&&f[key]>=256&&f[key]<=2048&&f[key]%16===0)||f.width*f.height>3145728)return false;
      return orientation==='portrait'?f.height>f.width:orientation==='landscape'?f.width>f.height:f.width===f.height;
    });
    const score=f=>{
      const tokens=normalText(`${f.id??''} ${f.name??''}`).split(/[^a-z0-9]+/);
      const nameScore=SPECIFIC[kind].some(word=>tokens.includes(word))?200:ALIASES[kind].some(word=>tokens.includes(word))?100:0;
      return nameScore-Math.abs(f.width/f.height-fallback.width/fallback.height);
    };
    const chosen=valid.reduce((best,f)=>!best||score(f)>score(best)?f:best,null)||fallback;
    return {width:chosen.width,height:chosen.height};
  }
  function negative(style,piece,contextNegative=''){
    const seen=new Set(),tokens=[];
    for(const block of [style?.negative,piece?.negative,contextNegative]){
      if(typeof block!=='string')continue;
      for(const part of block.split(/[,\r\n]+/)){
        const token=part.trim(),key=token.replace(/\s+/g,' ').toLowerCase();
        if(token&&!seen.has(key)){seen.add(key);tokens.push(token);}
      }
    }
    return tokens.join(', ');
  }
  const api={profile,values,context,format,negative};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.AnimaElementProfiles=api;
})(globalThis);
