const assert=require('node:assert/strict');
const {test}=require('node:test');
const Pose=require('../static/anima_pose.js');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function editorHarness() {
  const ctx={fillRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},arc(){},fill(){},drawImage(){}};
  const images=[];
  const document={createTextNode(text){return {textContent:text};},createElement(tag){
    return {tag,children:[],style:{},value:'',append(...children){this.children.push(...children);},
      replaceChildren(){this.children=[];},setAttribute(){},getContext(){return ctx;},
      toDataURL(){return 'data:image/png;base64,SKELETON';},ownerDocument:document};
  }};
  const context=vm.createContext({document,Image:class {constructor(){this.width=128;this.height=192;images.push(this);}},
    fetch:async()=>({ok:true,json:async()=>({models:[]})})});
  vm.runInContext(readFileSync(path.join(__dirname,'../static/anima_pose.js'),'utf8'),context);
  const container=document.createElement('div');let changes=0;
  const editor=context.AnimaPose.create(container,{onChange(){changes++;}});
  return {editor,container,images,changes:()=>changes};
}

test('independent skeleton copies preserve normalized joints',()=>{
  const first=Pose.defaultSkeleton(),second=Pose.defaultSkeleton();
  first.points[0][0]=.1;
  assert.equal(second.points[0][0],.5);
  assert.equal(Pose.LIMBS.length,17);
  assert.equal(Pose.COLORS.length,18);
  const copy=Pose.validateSkeleton(second);copy.points[0][0]=.2;
  assert.equal(second.points[0][0],.5);
});

test('OpenPose import scales exactly one complete body to the canvas',()=>{
  const source={canvas_width:1000,canvas_height:1500,people:[{pose_keypoints_2d:
    Pose.defaultSkeleton().points.flatMap(([x,y])=>[x*1000,y*1500,1])}]};
  const restored=Pose.importSkeleton(source);
  assert.deepEqual(restored,Pose.defaultSkeleton());
  source.people[0].pose_keypoints_2d[2]=0;
  assert.throws(()=>Pose.importSkeleton(source),/articulaciones/);
  assert.throws(()=>Pose.importSkeleton({...source,people:[...source.people,...source.people]}),/persona/);
});

test('invalid or off-canvas skeleton never reaches rendering',()=>{
  const bad=Pose.defaultSkeleton();bad.points[0][0]=NaN;
  assert.throws(()=>Pose.validateSkeleton(bad),/dentro del lienzo/);
  assert.throws(()=>Pose.importSkeleton({people:[]}),/18 puntos/);
});

test('drawing produces a black control map and every body limb',()=>{
  let strokes=0,circles=0;
  const ctx={fillRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){strokes++;},arc(){circles++;},fill(){}};
  Pose.draw({width:512,height:768,getContext(){return ctx;}},Pose.defaultSkeleton());
  assert.equal(strokes,17);assert.equal(circles,18);
});

test('restoring a saved control is silent and preserves every effective setting',()=>{
  const {editor,changes}=editorHarness();
  assert.equal(editor.getValue(),null);
  const value={enabled:true,model_patch:'anima/control.safetensors',strength:.75,start_percent:.2,
    end_percent:.9,representation:'openpose',skeleton:Pose.defaultSkeleton()};
  editor.setValue(value);const restored=JSON.parse(JSON.stringify(editor.getValue()));
  assert.equal(restored.model_patch,value.model_patch);
  assert.equal(restored.strength,.75);assert.equal(restored.start_percent,.2);assert.equal(restored.end_percent,.9);
  assert.deepEqual(restored.skeleton,value.skeleton);
  assert.equal(changes(),0);
});

test('a late PNG preview cannot replace a newer skeleton selection',async()=>{
  const {editor,images}=editorHarness();
  editor.setValue({enabled:true,model_patch:'anima/control.safetensors',image_base64:'data:image/png;base64,OLD'});
  const fresh={enabled:true,model_patch:'anima/control.safetensors',skeleton:Pose.defaultSkeleton()};
  fresh.skeleton.points[0][0]=.3;editor.setValue(fresh);
  images[0].onload();await Promise.resolve();await Promise.resolve();
  const value=JSON.parse(JSON.stringify(editor.getValue()));
  assert.equal(value.skeleton.points[0][0],.3);
  assert.equal(value.image_base64,'data:image/png;base64,SKELETON');
});
