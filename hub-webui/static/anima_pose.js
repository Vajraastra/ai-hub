/* Independent pose editor. Normalized joints remain editable across canvases. */
(function(root) {
  'use strict';
  const DEFAULT_POINTS = [[.5,.13],[.5,.22],[.38,.24],[.32,.39],[.29,.53],
    [.62,.24],[.68,.39],[.71,.53],[.43,.51],[.42,.70],[.40,.91],
    [.57,.51],[.58,.70],[.60,.91],[.47,.12],[.53,.12],[.44,.14],[.56,.14]];
  const LIMBS = [[1,2],[1,5],[2,3],[3,4],[5,6],[6,7],[1,8],[8,9],[9,10],
    [1,11],[11,12],[12,13],[1,0],[0,14],[14,16],[0,15],[15,17]];
  const COLORS = ['#ff0000','#ff5500','#ffaa00','#ffff00','#aaff00','#55ff00',
    '#00ff00','#00ff55','#00ffaa','#00ffff','#00aaff','#0055ff','#0000ff',
    '#5500ff','#aa00ff','#ff00ff','#ff00aa','#ff0055'];
  const clone = value => JSON.parse(JSON.stringify(value));
  const defaultSkeleton = () => ({format:'openpose18',points:clone(DEFAULT_POINTS)});
  function validateSkeleton(input) {
    if(input?.format !== 'openpose18' || !Array.isArray(input.points) || input.points.length !== 18)
      throw new Error('Usa un esqueleto OpenPose de 18 articulaciones.');
    for(const point of input.points) if(!Array.isArray(point) || point.length !== 2 ||
      point.some(n=>typeof n !== 'number' || !Number.isFinite(n) || n < 0 || n > 1))
      throw new Error('Las articulaciones deben estar dentro del lienzo.');
    return {format:'openpose18',points:clone(input.points)};
  }
  function importSkeleton(input) {
    if(input?.format === 'openpose18') return validateSkeleton(input);
    const flat = input?.people?.[0]?.pose_keypoints_2d;
    const width = Number(input?.canvas_width), height = Number(input?.canvas_height);
    if(!Array.isArray(flat) || flat.length !== 54 || !(width > 0 && height > 0))
      throw new Error('Importa openpose18 o JSON OpenPose con una persona, 18 puntos y canvas_width/canvas_height.');
    if(input.people.length !== 1) throw new Error('El editor de personaje admite una sola persona.');
    const points = Array.from({length:18},(_,i)=>{
      const [x,y,confidence] = flat.slice(i*3,i*3+3);
      if(typeof confidence !== 'number' || !Number.isFinite(confidence) || confidence <= 0)
        throw new Error('El esqueleto importado debe incluir todas las articulaciones visibles.');
      return [x / width, y / height];
    });
    return validateSkeleton({format:'openpose18',points});
  }
  function draw(canvas,skeleton) {
    const ctx = canvas.getContext('2d');
    if(!ctx) throw new Error('No se pudo abrir el lienzo de pose.');
    const width = canvas.width, height = canvas.height, thickness = Math.max(2,Math.round(Math.min(width,height)/128));
    ctx.fillStyle = '#000'; ctx.fillRect(0,0,width,height);
    const points = skeleton.points.map(([x,y])=>[x*(width-1),y*(height-1)]);
    ctx.lineWidth = thickness; ctx.lineCap = 'round';
    LIMBS.forEach(([start,end],i)=>{
      ctx.strokeStyle = COLORS[i];ctx.beginPath();ctx.moveTo(...points[start]);ctx.lineTo(...points[end]);ctx.stroke();
    });
    points.forEach(([x,y],i)=>{ctx.fillStyle=COLORS[i];ctx.beginPath();ctx.arc(x,y,thickness,0,Math.PI*2);ctx.fill();});
  }
  function create(container, options={}) {
    if(!container) throw new Error('Falta el espacio del editor de pose.');
    const doc = container.ownerDocument || root.document;
    const element = (tag, text, attrs={}) => {
      const node = doc.createElement(tag); if(text) node.textContent = text;
      Object.assign(node,attrs);return node;
    };
    const wrap = element('div','',{className:'pose-editor'});
    const enabled = element('input','',{type:'checkbox'}), enabledLabel = element('label','',{className:'check'});
    enabledLabel.append(enabled,doc.createTextNode(' Guiar la pose con ControlNet'));wrap.append(enabledLabel);
    const body = element('div');body.hidden = true;wrap.append(body);
    const modelLabel = element('label','Modelo de control'), model = element('select');modelLabel.append(model);body.append(modelLabel);
    const install = element('button','Instalar control de esqueleto',{type:'button',className:'secondary'});
    const status = element('p','',{className:'hint'});body.append(install,status);
    const canvas = element('canvas','',{width:512,height:768});
    canvas.setAttribute('aria-label','Pose de cuerpo. Arrastra las articulaciones de colores.');
    canvas.style.cssText = 'display:block;width:100%;max-width:300px;aspect-ratio:2/3;touch-action:none;background:black;margin:8px auto';
    body.append(canvas);
    body.append(element('p','Arrastra articulaciones. Esqueleto corporal de 18 puntos; manos y rostro detallados pueden importarse como mapa PNG.',{className:'hint'}));
    const row = element('div','',{className:'button-row'});
    const reset = element('button','Restablecer',{type:'button',className:'subtle'});
    const exportButton = element('button','Guardar esqueleto',{type:'button',className:'subtle'});
    const importFile = element('input','',{type:'file',accept:'.json,application/json'});
    const importLabel = element('label','Importar esqueleto',{className:'file-field'});importLabel.append(importFile);
    row.append(reset,exportButton);body.append(row,importLabel);
    const upload = element('input','',{type:'file',accept:'image/png'});
    const uploadLabel = element('label','Usar mapa PNG',{className:'file-field'});uploadLabel.append(upload);body.append(uploadLabel);
    const strength = element('input','',{type:'number',min:'0',max:'3',step:'.05',value:'1'});
    const strengthLabel = element('label','Fuerza');strengthLabel.append(strength);body.append(strengthLabel);
    const period = element('div','',{className:'field-grid two'});
    const start = element('input','',{type:'number',min:'0',max:'1',step:'.05',value:'0'});
    const end = element('input','',{type:'number',min:'0',max:'1',step:'.05',value:'1'});
    const startLabel = element('label','Inicio del control'), endLabel = element('label','Final del control');
    startLabel.append(start);endLabel.append(end);period.append(startLabel,endLabel);body.append(period);
    container.append(wrap);
    let skeleton = defaultSkeleton(), importedImage = null, importedData = null, representation = 'openpose';
    let catalog = [], selectedName = '', drag = null, loading = false, catalogEpoch = 0, imageEpoch = 0;
    const changed = () => options.onChange?.();
    function repaint() {
      if(importedImage){const ctx=canvas.getContext('2d');ctx.fillStyle='#000';ctx.fillRect(0,0,canvas.width,canvas.height);
        const scale=Math.min(canvas.width/importedImage.width,canvas.height/importedImage.height);
        const w=importedImage.width*scale,h=importedImage.height*scale;ctx.drawImage(importedImage,(canvas.width-w)/2,(canvas.height-h)/2,w,h);
      } else draw(canvas,skeleton);
    }
    function say(message,error=false){status.textContent=message;status.setAttribute('role',error?'alert':'status');}
    function populate(){
      const previous = selectedName || model.value;model.replaceChildren();
      for(const entry of catalog){const option=element('option',entry.name+(entry.installed?'':' · falta instalar'),{value:entry.file});model.append(option);}
      if(previous && !catalog.some(entry=>entry.file===previous))model.append(element('option',previous,{value:previous}));
      if(previous)model.value=previous;
      install.disabled=loading||catalog.some(entry=>entry.id==='kohya-pose'&&entry.installed);
    }
    async function request(path,method='GET'){
      const response=await root.fetch('/api/anima-studio/pose'+path,{method});
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'No se pudo preparar el control de pose.');return data;
    }
    async function loadCatalog(){
      const epoch=++catalogEpoch;
      try{const result=await request('/catalog');if(epoch!==catalogEpoch)return;
        catalog=result.models||[];populate();say(catalog.find(entry=>entry.file===model.value)?.note||'Control de pose experimental.');
      }catch(error){if(epoch===catalogEpoch)say(error.message,true);}
    }
    enabled.onchange=()=>{body.hidden=!enabled.checked;changed();if(enabled.checked&&!catalog.length)loadCatalog();};
    model.onchange=()=>{selectedName=model.value;representation=catalog.find(entry=>entry.file===model.value)?.representation||'openpose';changed();};
    for(const input of [strength,start,end])input.onchange=changed;
    reset.onclick=()=>{imageEpoch++;skeleton=defaultSkeleton();importedImage=importedData=null;repaint();changed();};
    install.onclick=async()=>{
      loading=true;populate();say('Descargando control de esqueleto…');
      try{await request('/install/kohya-pose','POST');await loadCatalog();say('Control instalado. Puedes generar con esta pose.');}
      catch(error){say(error.message,true);}finally{loading=false;populate();}
    };
    canvas.onpointerdown=event=>{
      if(importedData)return;
      const rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left)/rect.width,y=(event.clientY-rect.top)/rect.height;
      let best=-1,distance=.06; skeleton.points.forEach(([px,py],i)=>{const d=Math.hypot(px-x,py-y);if(d<distance){best=i;distance=d;}});
      if(best>=0){drag=best;canvas.setPointerCapture?.(event.pointerId);event.preventDefault();}
    };
    canvas.onpointermove=event=>{
      if(drag===null)return;const rect=canvas.getBoundingClientRect();
      skeleton.points[drag]=[Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];repaint();
    };
    canvas.onpointerup=canvas.onpointercancel=()=>{if(drag!==null){drag=null;changed();}};
    importFile.onchange=async()=>{
      const epoch=++imageEpoch;
      try{const file=importFile.files?.[0];if(!file)return;if(file.size>256*1024)throw new Error('El esqueleto JSON supera 256 KiB.');
        const loaded=importSkeleton(JSON.parse(await file.text()));if(epoch!==imageEpoch)return;
        skeleton=loaded;importedImage=importedData=null;repaint();changed();say('Esqueleto importado.');
      }catch(error){say(error.message,true);}finally{importFile.value='';}
    };
    exportButton.onclick=()=>{
      const blob=new root.Blob([JSON.stringify(skeleton,null,2)],{type:'application/json'}),url=root.URL.createObjectURL(blob);
      const link=element('a','',{href:url,download:'pose.json'});link.click();root.URL.revokeObjectURL(url);
    };
    async function loadImage(data,epoch=++imageEpoch){
      const image=new root.Image();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error('No se pudo abrir el mapa PNG.'));image.src=data;});
      if(epoch!==imageEpoch)return;
      if(Math.max(image.width,image.height)>2048||image.width*image.height>3145728||Math.min(image.width,image.height)<16)
        throw new Error('El mapa PNG debe tener hasta 2048 píxeles por lado y 3 megapíxeles.');
      importedImage=image;importedData=data;repaint();
    }
    upload.onchange=async()=>{
      const epoch=++imageEpoch;
      try{const file=upload.files?.[0];if(!file)return;if(file.size>4*1024*1024)throw new Error('El mapa PNG supera 4 MiB.');
        const data=await new Promise((resolve,reject)=>{const reader=new root.FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('No se pudo leer el PNG.'));reader.readAsDataURL(file);});
        if(epoch!==imageEpoch)return;
        if(!String(data).startsWith('data:image/png;base64,'))throw new Error('Usa un archivo PNG.');
        await loadImage(data,epoch);if(epoch!==imageEpoch)return;
        changed();say('Mapa PNG cargado. Restablecer vuelve al esqueleto editable.');
      }catch(error){say(error.message,true);}finally{upload.value='';}
    };
    repaint();
    function getValue(){
      if(!enabled.checked)return null;
      const control={enabled:true,model_patch:model.value||selectedName,
        image_base64:importedData||canvas.toDataURL('image/png'),strength:Number(strength.value),
        start_percent:Number(start.value),end_percent:Number(end.value),representation};
      if(!control.model_patch)throw new Error('Selecciona e instala un modelo de control de pose.');
      if(!importedData)control.skeleton=clone(skeleton);
      return control;
    }
    function setValue(value){
      imageEpoch++;
      enabled.checked=!!value?.enabled;body.hidden=!enabled.checked;
      strength.value=String(value?.strength??1);start.value=String(value?.start_percent??0);end.value=String(value?.end_percent??1);
      selectedName=value?.model_patch||'';representation=value?.representation||'openpose';
      skeleton=value?.skeleton?validateSkeleton(value.skeleton):defaultSkeleton();importedImage=importedData=null;
      if(value?.image_base64&&!value.skeleton){
        // Preserve the exact map while its preview decodes asynchronously.
        importedData=value.image_base64;loadImage(value.image_base64).catch(error=>say(error.message,true));
      }else repaint();
      populate();if(enabled.checked&&!catalog.length)loadCatalog();
    }
    return {getValue,setValue,loadCatalog};
  }
  const api={create,defaultSkeleton,validateSkeleton,importSkeleton,draw,LIMBS,COLORS};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.AnimaPose=api;
})(typeof globalThis!=='undefined'?globalThis:this);
