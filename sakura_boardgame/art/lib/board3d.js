/* Station remake renderer. Original board coordinates and public API are preserved.
 * Static light/shadows are baked, no full-scene real-time shadow pass.
 * One coalesced animation frame while dirty/moving/following; 32ms timeout fallback for throttled WebViews.
 */
import * as T from 'three';
import { GLTFLoader } from './gltf_loader.js';
import { mergeGeometries } from './buffer_geometry_utils.js';
export const LIGHT_PRESETS = { showroom: { baked: true } };
export async function createBoard3D(container, opts={}) {
 const view=opts.boardView;if(!view?.orthoScale)throw Error('缺少棋盘相机参数');
 const renderer=new T.WebGLRenderer({antialias:true,powerPreference:'high-performance',preserveDrawingBuffer:!!opts.capture});
 renderer.outputColorSpace=T.SRGBColorSpace;renderer.shadowMap.enabled=false;
 // Limit high-DPI fill cost; CSS/world coordinates and baked texture resolution stay unchanged.
  renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));
 const canvas=renderer.domElement;Object.assign(canvas.style,{position:'absolute',inset:'0',width:'100%',height:'100%',display:'block',touchAction:'none'});container.appendChild(canvas);
 const scene=new T.Scene();scene.background=new T.Color('#c7e5f2');
 const camera=new T.OrthographicCamera(-1,1,1,-1,10,180);
 const worldToThree=(u,v,h)=>new T.Vector3(u,h||0,-v);
 const camState={target:worldToThree(...view.target),zoom:opts.zoom||opts.defaultZoom||1,baseWidth:view.orthoScale,followPt:null};
 const right=new T.Vector3(),up=new T.Vector3();
 const sourceR=new T.Vector3(view.right[0],view.right[2],-view.right[1]);
 const sourceU=new T.Vector3(view.up[0],view.up[2],-view.up[1]);
 const back=sourceR.clone().cross(sourceU).normalize();
 const sprites=new Map(),moves=new Map(),badges=new Map(),overlays=new Map(),textures=new Map(),materials=new Set(),geometries=new Set();
 const tickers=new Set(),animNodes=new Map();
 let life=null,trainTransition=null,cinematic=null,pendingFrame=null,frameSerial=0,interactionUntil=0;
  let dead=false,disposed=false,dirty=true,dragging=false,dragPos=null,zoomTouched=false,hoverCells=[],hoverRequest=null;
  let viewportW=800,viewportH=600,cameraDirty=true,cameraVersion=0,hoverVersion=-1,hoverPoints=[];
  const centre=worldToThree(8.5,10,2.0),delta=new T.Vector3(),projectPoint=new T.Vector3();
  const stats={frames:0,calls:0,triangles:0,lastFrameMs:0,cameraUpdates:0,hoverProjections:0,frameIntervals:[]};
function cameraUpdate(){
   if(!cameraDirty)return;cameraDirty=false;cameraVersion++;stats.cameraUpdates++;
   // Limit movement to an environment-safe projected rectangle, taking current viewport into account.
  delta.copy(camState.target).sub(centre);
  const maxPan=15;const r=T.MathUtils.clamp(delta.dot(sourceR),-maxPan,maxPan),u=T.MathUtils.clamp(delta.dot(sourceU),-maxPan,maxPan);
  camState.target.copy(centre).addScaledVector(sourceR,r).addScaledVector(sourceU,u).addScaledVector(back,delta.dot(back));
  camera.position.copy(camState.target).addScaledVector(back,65);camera.up.copy(sourceU);camera.lookAt(camState.target);camera.updateMatrixWorld();
  const width=camState.baseWidth/camState.zoom,aspect=Math.max(.1,viewportW/viewportH);
  camera.left=-width/2;camera.right=width/2;camera.top=width/2/aspect;camera.bottom=-width/2/aspect;camera.updateProjectionMatrix();
  right.setFromMatrixColumn(camera.matrixWorld,0);up.setFromMatrixColumn(camera.matrixWorld,1);
 }
 function project(u,v,h){cameraUpdate();const p=projectPoint.set(u,h||0,-v).project(camera);return[(p.x*.5+.5)*viewportW,(-p.y*.5+.5)*viewportH,p.z];}
 function overlayUpdate(){
  for(const [el,ov]of overlays){
   if(!el.isConnected){overlays.delete(el);continue;}
   const q=ov.get();if(!q){el.style.display='none';ov.last=null;continue;}
   const p=project(q.u,q.v,q.h);const tf=`translate(${p[0].toFixed(1)}px,${p[1].toFixed(1)}px)`;
   if(ov.last!==tf){el.style.display='';el.style.transform=tf;ov.last=tf;}
  }
 }
 function texture(url){
  if(!textures.has(url))textures.set(url,new Promise((resolve,reject)=>new T.TextureLoader().load(url,tex=>{tex.colorSpace=T.SRGBColorSpace;tex.anisotropy=renderer.capabilities.getMaxAnisotropy();resolve(tex);},undefined,reject)));
  return textures.get(url);
 }
function cancelFrame(){
   if(!pendingFrame)return;clearTimeout(pendingFrame.delay);clearTimeout(pendingFrame.fallback);cancelAnimationFrame(pendingFrame.raf);pendingFrame=null;frameSerial++;
  }
  function schedule(invalidate=true,delay=0){
   if(invalidate)dirty=true;if(disposed||dead)return;
   const hidden=document.hidden||(life&&!life.visible);
   if(hidden&&!moves.size&&!trainTransition?.busy)return;
   if(hidden)delay=Math.max(delay,120);
   const due=performance.now()+delay;
   if(pendingFrame&&pendingFrame.due<=due+1)return;
   cancelFrame();const ticket={id:++frameSerial,due,delay:null,fallback:null,raf:null};pendingFrame=ticket;
   const fire=()=>{if(pendingFrame!==ticket||disposed||dead)return;const late=Math.max(0,performance.now()-ticket.due);cancelFrame();frame(late);};
   const arm=()=>{if(pendingFrame!==ticket)return;if(hidden){fire();return;}ticket.raf=requestAnimationFrame(fire);ticket.fallback=setTimeout(fire,32);};
   if(delay>0)ticket.delay=setTimeout(arm,delay);else arm();
  }
  function nextFrame(){schedule(false);}
function setSpritePosition(s,p){s.group.position.copy(p);}
 function updateMoves(now){
  for(const [player,move]of [...moves]){
   const s=sprites.get(player);if(!s)continue;
   let elapsed=now-move.started,index=0;
   while(index<move.phases.length&&elapsed>=move.phases[index].ms){elapsed-=move.phases[index].ms;index++;}
   // Replay completed endpoints once, including frame gaps/throttled host timers.
   for(let j=move.completed;j<index;j++){const ph=move.phases[j];if(ph.arrival!=null)move.onHop?.(ph.arrival);}
   move.completed=index;
   if(index>=move.phases.length){
    moves.delete(player);setSpritePosition(s,move.end);s.group.scale.set(1,1,1);s.sprite.material.rotation=0;s.sprite.material.opacity=1;
    move.onDone?.();continue;
   }
   const ph=move.phases[index],k=T.MathUtils.clamp(elapsed/ph.ms,0,1);
   if(ph.kind==='hop'){s.group.position.copy(ph.a).lerp(ph.b,k);s.group.position.y+=Math.sin(Math.PI*k)*.30;}
   else{const incoming=ph.kind==='in',factor=incoming?.08+.92*k:1-.92*k;setSpritePosition(s,incoming?ph.b:ph.a);s.group.scale.setScalar(factor);s.sprite.material.opacity=incoming?k:1-k;s.sprite.material.rotation=(incoming?1-k:k)*Math.PI*320/180;}
   if(camState.followPt&&!api.takenOver)camState.followPt.copy(s.group.position);
  }
 }
     let previous=0,lastTick=0;
  function frame(late=0){
   if(disposed||dead)return;const now=performance.now();
   if(now-lastTick<15){schedule(false,15-(now-lastTick));return;}
   lastTick=now;const gap=now-previous,dt=previous&&gap<120?Math.min(.05,gap/1000):1/60;previous=now;
   updateMoves(now);trainTransition?.update(now);
   const hidden=document.hidden||(life&&!life.visible);
   let following=false;
   if(!hidden&&camState.followPt&&!dragging&&!cinematic){const distance=camState.target.distanceTo(camState.followPt);if(distance>.0005){camState.target.lerp(camState.followPt,1-Math.exp(-dt*8));following=true;dirty=true;cameraDirty=true;}else if(distance>0){camState.target.copy(camState.followPt);cameraDirty=true;}}
   const busy=dragging||moves.size>0||following||now<interactionUntil||!!opts.isAmbientBusy?.();
   const ambient=life?.update(now,busy)||false;
   for(const fn of tickers){try{fn(dt,now);}catch(e){console.error('board3d ticker',e);tickers.delete(fn);}}
   if(!hidden&&(dirty||moves.size||following||ambient)){
    cameraUpdate();overlayUpdate();const t0=performance.now();renderer.render(scene,camera);const cost=performance.now()-t0;
    stats.frames++;stats.calls=renderer.info.render.calls;stats.triangles=renderer.info.render.triangles;stats.lastFrameMs=cost;
    if(gap<250){stats.frameIntervals.push(gap);if(stats.frameIntervals.length>240)stats.frameIntervals.shift();}
    dirty=false;life?.observeCost(cost,late);
   }
   if(hoverRequest&&!dragging&&!hidden){const q=hoverRequest;hoverRequest=null;api.onHover?.(nearestCell(q.x,q.y),q.x,q.y);}
   if(moves.size||following||trainTransition?.busy)nextFrame();
   else if(life?.eligible&&!hidden){previous=0;schedule(false,busy?180:life.interval);}
   else previous=0;
  }
function resize(){viewportW=container.clientWidth||800;viewportH=container.clientHeight||600;renderer.setSize(viewportW,viewportH,false);cameraDirty=true;cameraUpdate();schedule();}
  const ro=new ResizeObserver(resize);ro.observe(container);resize();
const matCache=new Map(),groups=new Map(),glyphRecords=[],trainBatches=[],normalBatches=[];
 // Black-train mode uses cached independent artwork, not a recoloring shader.
 let blackTrainVisible=opts.blackTrainVisible===true;
 let blackTrainTiles=opts.blackTrainTiles||{},blackTrainCells=opts.blackTrainCells||[];
 // Re-sending the latest sync after asynchronous artwork loading is idempotent.
 function setBlackTrainTiles(tiles,cells){
  if(disposed)return false;
  blackTrainTiles=tiles||{};blackTrainCells=cells||[];
  return trainTransition?.setTiles(blackTrainTiles,blackTrainCells)||false;
 }
 let glyphCount=0,bakedCount=0;
 try {
  const gltf=await new GLTFLoader().loadAsync(opts.glbUrl||'/art/board/station_remake.glb');gltf.scene.updateMatrixWorld(true);
   if(opts.ambient!==false){try{const {createCityLife}=await import('./city_life.js');life=await createCityLife(scene,container,{capture:!!opts.capture});life.onChange=()=>{cancelFrame();previous=0;schedule();};}catch(e){console.warn('城市装饰动效不可用，保留静态棋盘',e);}}
  gltf.scene.traverse(n=>{
   if(!n.isMesh)return;const old=n.material;if(Array.isArray(old))throw Error('场景包含未拆分的多材质物体');
   let mat=matCache.get(old.uuid);
   if(!mat){
    if(n.userData.baked_lighting||old.userData.baked_lighting||old.name.endsWith('_baked')){mat=new T.MeshBasicMaterial({map:old.map,toneMapped:false,side:n.userData.double_sided_foliage?T.DoubleSide:T.FrontSide});bakedCount++;}
    else if(n.userData.role==='unlit_detail'){if(!n.geometry.attributes.color)throw Error('细部模型缺少顶点色');mat=new T.MeshBasicMaterial({color:0xffffff,vertexColors:true,toneMapped:false});mat.userData.vertexLitDetail=true;}
     else if(n.userData.role==='unlit_trim'){mat=new T.MeshBasicMaterial({color:old.color,toneMapped:false});mat.userData.unlitTrim=true;}
     else if(n.userData.role==='tile_icon'){mat=new T.MeshBasicMaterial({map:old.map,transparent:true,alphaTest:.015,depthWrite:false,toneMapped:false,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1});}
    else mat=new T.MeshBasicMaterial({color:old.emissive?.getHex()?old.emissive:old.color,map:old.emissiveMap||old.map,toneMapped:false});
    mat.name=old.name;
     if(mat.map){mat.map.anisotropy=renderer.capabilities.getMaxAnisotropy();mat.map.needsUpdate=true;}matCache.set(old.uuid,mat);materials.add(mat);
   }
   if(n.userData.role==='tile_icon')glyphCount++;
   const geo=n.geometry.clone();geo.applyMatrix4(n.matrixWorld);const flat=geo.index?geo.toNonIndexed():geo;
     // Erase the old train's baked receiver shadow, not the surrounding station AO.
     if(opts.blackTrainTheme&&old.name.startsWith('station_static')){
      const p=flat.attributes.position,clean=[];
      for(let i=0;i<p.count;i+=3){const xs=[p.getX(i),p.getX(i+1),p.getX(i+2)];if([0,1,2].every(j=>Math.abs(p.getY(i+j)+.64)<.0003)&&Math.max(...xs)-Math.min(...xs)>28){for(let j=0;j<3;j++)clean.push(p.getX(i+j),p.getY(i+j),p.getZ(i+j));for(let j=1;j<3;j++)p.setXYZ(i+j,p.getX(i),p.getY(i),p.getZ(i));}}
      if(clean.length){const cg=new T.BufferGeometry();cg.setAttribute('position',new T.Float32BufferAttribute(clean,3));const cm=new T.MeshBasicMaterial({color:0x667681,toneMapped:false});materials.add(cm);groups.set('clean-train-receiver',{mat:cm,geos:[cg],train:false});}
     }
    if(n.userData.role==='tile_icon'){flat.computeBoundingBox();const c=flat.boundingBox.getCenter(new T.Vector3());flat.userData.glyphKey=`${Math.round(c.x)},${Math.round(-c.z)}`;}
   for(const name of Object.keys(flat.attributes))if(!['position','uv','color'].includes(name))flat.deleteAttribute(name);
   life?.attach(flat,mat,old.name,n.geometry,n.matrixWorld);
    let train=false;for(let p=n;p;p=p.parent)if(p.userData?.asset_kind==='train'){train=true;break;}
     const groupKey=mat.uuid+(train?':train':':board');
     if(!groups.has(groupKey))groups.set(groupKey,{mat,geos:[],train});groups.get(groupKey).geos.push(flat);
  });
  for(const {mat,geos,train}of groups.values()){
    // Real alternative atlases replace the former luminance-to-gold filter.
   if(!geos.every(g=>g.attributes.uv))for(const g of geos)g.deleteAttribute('uv');
   const merged=mergeGeometries(geos);if(!merged)throw Error('静态场景合批失败');merged.computeBoundingSphere();geometries.add(merged);const batch=new T.Mesh(merged,mat);batch.name=mat.name||'city batch';if(mat.userData.cityLife){merged.boundingSphere.radius+=6;batch.userData.cityLife=true;}if(train){batch.visible=opts.blackTrainTheme?false:blackTrainVisible;trainBatches.push(batch);}else normalBatches.push(batch);scene.add(batch);
    let vertexOffset=0;for(const g of geos){const count=g.attributes.position.count;if(g.userData.glyphKey)glyphRecords.push({key:g.userData.glyphKey,position:merged.attributes.position,offset:vertexOffset,count,original:new Float32Array(merged.attributes.position.array.slice(vertexOffset*3,(vertexOffset+count)*3)),hidden:false});vertexOffset+=count;g.dispose();}
  }
  gltf.scene.traverse(n=>{if(n.isMesh)n.geometry.dispose();});
   if(opts.blackTrainTheme){
     const {createBlackTrainTransition}=await import('./black_train_transition.js');
     const smooth=t=>{t=T.MathUtils.clamp(t,0,1);return t*t*(3-2*t);};
     trainTransition=await createBlackTrainTransition(scene,normalBatches,{...opts.blackTrainTheme,
      onStart({bounds}){
       const points=hoverCells.map(c=>worldToThree(c.u,c.v,(c.h||0)+.1));
       if(bounds&&!bounds.isEmpty())for(const x of [bounds.min.x,bounds.max.x])for(const z of [bounds.min.z,bounds.max.z])points.push(new T.Vector3(x,bounds.max.y,z));
       const box=new T.Box3().setFromPoints(points.length?points:[centre]);const wide=box.getCenter(new T.Vector3());
       const spanR=points.map(p=>p.clone().sub(wide).dot(sourceR)),spanU=points.map(p=>p.clone().sub(wide).dot(sourceU));
       const width=Math.max(35,(Math.max(...spanR)-Math.min(...spanR))*1.22,(Math.max(...spanU)-Math.min(...spanU))*viewportW/viewportH*1.30);
       cinematic={target:camState.target.clone(),zoom:camState.zoom,wide,wideZoom:Math.min(camState.zoom,camState.baseWidth/width),takenOver:api.takenOver};
       dragging=false;api.onHover?.(null);
      },
      onFrame(t){if(!cinematic)return;const c=cinematic;
       if(t<.20){const a=smooth(t/.20);camState.target.copy(c.target).lerp(c.wide,a);camState.zoom=T.MathUtils.lerp(c.zoom,c.wideZoom,a);}
       else if(t<.79){camState.target.copy(c.wide);camState.zoom=c.wideZoom;}
       else {const a=smooth((t-.79)/.21);camState.target.copy(c.wide).lerp(!c.takenOver&&camState.followPt?camState.followPt:c.target,a);camState.zoom=T.MathUtils.lerp(c.wideZoom,c.zoom,a);}
       cameraDirty=true;
      },
      onFinish(){if(cinematic){camState.zoom=cinematic.zoom;camState.target.copy(!cinematic.takenOver&&camState.followPt?camState.followPt:cinematic.target);cinematic=null;cameraDirty=true;}}
     },()=>schedule());setBlackTrainTiles(blackTrainTiles,blackTrainCells);if(blackTrainVisible)trainTransition.set(true,true);
    }
 }catch(e){cancelFrame();life?.dispose();ro.disconnect();renderer.dispose();canvas.remove();disposed=true;throw e;}
 const blank=new T.DataTexture(new Uint8Array([255,255,255,0]),1,1);blank.needsUpdate=true;
 function makeSprite(player){
  const group=new T.Group(),mat=new T.SpriteMaterial({map:blank,transparent:true,alphaTest:.015,depthTest:false,depthWrite:false,toneMapped:false});materials.add(mat);
  const sprite=new T.Sprite(mat);sprite.center.set(.5,0);sprite.scale.set(.8,1.3,1);sprite.renderOrder=999;group.userData.player=player;group.add(sprite);scene.add(group);
  const s={group,sprite,key:null,sameCell:false,textureTicket:0};sprites.set(player,s);return s;
 }
 function setPieces(list){
  for(const spec of list){
   const s=sprites.get(spec.player)||makeSprite(spec.player);s.sameCell=!!spec.sameCell;
   if(s.key!==spec.key){s.key=spec.key;const ticket=++s.textureTicket;texture(`/art/chibi/${spec.key}.png`).then(tex=>{if(disposed||ticket!==s.textureTicket)return;s.sprite.material.map=tex;s.sprite.material.needsUpdate=true;const h=s.sameCell?1.05:1.3;s.sprite.scale.set(h*tex.image.width/tex.image.height,h,1);schedule();}).catch(e=>console.warn('棋子纹理加载失败',e));}
   if(!moves.has(spec.player)&&!spec.freeze){const p=worldToThree(spec.u,spec.v,(spec.h||0)+.061);if(spec.sameCell)p.addScaledVector(right,spec.player==='user'?-.16:.16);s.group.position.copy(p);}
   const image=s.sprite.material.map?.image;if(image?.width>1){const h=spec.sameCell?1.05:1.3;s.sprite.scale.set(h*image.width/image.height,h,1);}
   s.group.visible=true;
  }schedule();
 }
 const badgeMaterials=new Map();
 function badgeMaterial(kind){
  if(!badgeMaterials.has(kind)){
   const m=new T.MeshBasicMaterial({map:blank,transparent:true,depthWrite:false,toneMapped:false,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2});materials.add(m);badgeMaterials.set(kind,m);
   texture(`/art/icons/property_owned_${kind}.png`).then(tex=>{if(disposed)return;tex.flipY=false;tex.needsUpdate=true;m.map=tex;m.needsUpdate=true;schedule();}).catch(e=>console.warn('产权图标纹理加载失败',e));
  }return badgeMaterials.get(kind);
 }
 function setBadges(cells,holdId){
  const want=new Set(),ownedUV=new Set();
  for(const c of cells){if(!c.owner)continue;const id=String(c.id);want.add(id);
   // Preserve an existing badge while landing is held. Only genuinely new ownership is delayed.
   if(holdId!=null&&id===String(holdId)&&!badges.has(id))continue;
   const kind=c.mortgaged?'mortgaged':c.owner;let mesh=badges.get(id);
   if(!mesh){const geo=new T.PlaneGeometry(.80,.80);geometries.add(geo);mesh=new T.Mesh(geo,badgeMaterial(kind));mesh.rotation.x=-Math.PI/2;mesh.renderOrder=3;scene.add(mesh);badges.set(id,mesh);}
   if(mesh.userData.kind!==kind){mesh.material=badgeMaterial(kind);mesh.userData.kind=kind;}
   mesh.visible=!(trainTransition&&blackTrainVisible);mesh.position.set(c.x,(c.h||0)+.083,-c.y);ownedUV.add(`${c.x},${c.y}`);
  }
  for(const [id,mesh]of badges)if(!want.has(id)){scene.remove(mesh);mesh.geometry.dispose();geometries.delete(mesh.geometry);badges.delete(id);}
  // Only owner changes update vertices. Collapsing triangles hides the old glyph without extra draw calls.
  for(const rec of glyphRecords){const hide=ownedUV.has(rec.key);if(hide===rec.hidden)continue;rec.hidden=hide;for(let i=0;i<rec.count;i++){const k=hide?0:i*3;rec.position.setXYZ(rec.offset+i,rec.original[k],rec.original[k+1],rec.original[k+2]);}rec.position.needsUpdate=true;}schedule();
 }
 function playMove(player,points,gates,onDone,onHop){
  const s=sprites.get(player)||makeSprite(player);moves.delete(player);s.group.scale.set(1,1,1);s.sprite.material.opacity=1;s.sprite.material.rotation=0;
  if(!points||points.length<2){onDone?.();schedule();return;}
  const pts=points.map(p=>worldToThree(p.u,p.v,(p.h||0)+.061)),phases=[];
  for(let i=0;i<pts.length-1;i++){const a=pts[i],b=pts[i+1];if(gates?.[i]){phases.push({a,b,ms:430,kind:'out',arrival:null},{a,b,ms:460,kind:'in',arrival:i+1});}else phases.push({a,b,ms:430,kind:'hop',arrival:i+1});}
  setSpritePosition(s,pts[0]);moves.set(player,{phases,started:performance.now(),completed:0,end:pts.at(-1),onDone,onHop});api.takenOver=false;camState.followPt=pts[0].clone();schedule();
 }
 function nearestCell(px,py){
   cameraUpdate();if(hoverVersion!==cameraVersion){hoverPoints=hoverCells.map(c=>({cell:c,p:project(c.u,c.v,(c.h||0)+.06)}));hoverVersion=cameraVersion;stats.hoverProjections+=hoverPoints.length;}
   let best=null,min=44*44;for(const {cell,p}of hoverPoints){const d=(p[0]-px)**2+(p[1]-py)**2;if(p[2]>=-1&&p[2]<=1&&d<min){best=cell;min=d;}}return best;
  }
 const listeners=[];function listen(type,fn,options){canvas.addEventListener(type,fn,options);listeners.push([type,fn,options]);}
 listen('pointerdown',e=>{if(e.button!==0||cinematic)return;dragging=true;hoverRequest=null;dragPos=[e.clientX,e.clientY];api.onHover?.(null);canvas.setPointerCapture(e.pointerId);});
 listen('pointermove',e=>{if(dragging){const dx=e.clientX-dragPos[0],dy=e.clientY-dragPos[1];dragPos=[e.clientX,e.clientY];api.panByPixels(dx,dy);}else if(api.onHover){const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;hoverRequest={x,y};schedule(false);}});
 listen('pointerup',()=>{dragging=false;});listen('pointercancel',()=>{dragging=false;});listen('pointerleave',()=>{hoverRequest=null;api.onHover?.(null);});
 listen('wheel',e=>{e.preventDefault();if(cinematic)return;zoomTouched=true;api.setZoom(camState.zoom*(e.deltaY<0?1.12:.89));},{passive:false});
 const api={renderer,scene,camera,camState,tickers,animNodes,worldToThree,preset:'station_baked',takenOver:false,get dead(){return dead;},get moving(){return moves.size>0;},get zoom(){return camState.zoom;},get zoomTouched(){return zoomTouched;},get dragging(){return dragging;},onHover:null,
  project,setPieces,setBadges,playMove,setBlackTrainTiles,
   setBlackTrainVisible(value){const visible=!!value;if(blackTrainVisible===visible)return;blackTrainVisible=visible;if(trainTransition){trainTransition.set(visible);for(const b of badges.values())b.visible=!visible;}else for(const batch of trainBatches)batch.visible=visible;schedule();},
   get transitioning(){return !!trainTransition?.busy;},
   getBlackTrainStats(){return trainTransition?.stats()||{active:blackTrainVisible,transitioning:false};},
  focusOn(u,v,h,instant=false){const p=worldToThree(u,v,(h||0)+.061);if(!instant&&camState.followPt?.distanceToSquared(p)<1e-12)return;camState.followPt=p;if(instant){camState.target.copy(p);cameraDirty=true;}schedule();},
  setZoom(z){interactionUntil=performance.now()+220;const next=T.MathUtils.clamp(z,.60,3);if(next===camState.zoom)return;camState.zoom=next;cameraDirty=true;schedule();},
  panByPixels(dx,dy){interactionUntil=performance.now()+180;const k=camState.baseWidth/camState.zoom/Math.max(1,viewportW);camState.target.addScaledVector(right,-dx*k).addScaledVector(up,dy*k);camState.followPt=null;api.takenOver=true;cameraDirty=true;schedule();},
  setHoverCells(cells){hoverCells=cells||[];hoverVersion=-1;life?.setPortals(hoverCells);},
   setAmbient(enabled){life?.setEnabled(enabled);},
   getAmbientStats(){return life?.getStats()||{enabled:false};},
  addOverlay(el,get,scope='effect'){overlays.set(el,{get,scope,last:null});cameraUpdate();overlayUpdate();},
  removeOverlay(el){overlays.delete(el);},
  clearOverlays(scope='route'){for(const[el,ov]of overlays)if(ov.scope===scope)overlays.delete(el);},
  getPiecePosition(player){const s=sprites.get(player);return s?{u:s.group.position.x,v:-s.group.position.z,h:s.group.position.y}:null;},
  cancelMoves(){moves.clear();for(const s of sprites.values()){s.group.scale.set(1,1,1);s.sprite.material.opacity=1;s.sprite.material.rotation=0;}camState.followPt=null;schedule();},
  getStats(){return{...stats,blackTrainVisible,trainBatches:trainBatches.length,ambient:life?.getStats()||null,pendingFrames:pendingFrame?1:0,liveMoves:moves.size,overlays:overlays.size,staticBatches:groups.size,unlitTrimBatches:[...groups.values()].filter(g=>g.mat.userData.unlitTrim).length,vertexLitDetailBatches:[...groups.values()].filter(g=>g.mat.userData.vertexLitDetail).length,bakedMaterials:bakedCount,glyphCount,hiddenOwnedGlyphs:glyphRecords.filter(r=>r.hidden).length,shadowMapEnabled:renderer.shadowMap.enabled};},
  dispose(){if(disposed)return;disposed=true;cancelFrame();life?.dispose();trainTransition?.dispose();document.removeEventListener('visibilitychange',visibilityChanged);ro.disconnect();for(const[t,fn,o]of listeners)canvas.removeEventListener(t,fn,o);moves.clear();overlays.clear();for(const g of geometries)g.dispose();for(const m of materials)m.dispose();for(const p of textures.values())p.then(t=>t.dispose()).catch(()=>{});for(const m of matCache.values())m.map?.dispose();blank.dispose();renderer.dispose();canvas.remove();}
 };
 listen('webglcontextlost',e=>{e.preventDefault();dead=true;cancelFrame();life?.dispose();
   // Finish visual arrivals once so a lost GPU context cannot strand a live turn/tower.
   for(const [player,move]of [...moves]){moves.delete(player);const s=sprites.get(player);if(s){setSpritePosition(s,move.end);s.group.scale.set(1,1,1);s.sprite.material.opacity=1;s.sprite.material.rotation=0;}for(let j=move.completed;j<move.phases.length;j++){const ph=move.phases[j];if(ph.arrival!=null)move.onHop?.(ph.arrival);}move.onDone?.();}
   console.warn('棋盘WebGL上下文丢失，已完成可见落点');window.dispatchEvent(new Event('board3d:lost'));});
   function visibilityChanged(){cancelFrame();previous=0;life?.update(performance.now(),true);schedule();}
  document.addEventListener('visibilitychange',visibilityChanged);
  cameraUpdate();schedule();return api;
}
export default {createBoard3D,LIGHT_PRESETS};
