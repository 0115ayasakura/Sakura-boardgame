/* Cached alternative artwork; no camera, pieces, gameplay state or screen filter. */
import * as T from 'three';
import {GLTFLoader} from './gltf_loader.js';
import {mergeGeometries} from './buffer_geometry_utils.js';
const ICONS={gold:'gold_ticket',blue:'blue_contract',white:'white_beg',black:'black_anomaly'};
// Pure world-space mapping: GLB coordinates are (cell.x, cell.h, -cell.y).
// Never infer an event from mesh order, cell id/order, or the old GLB material.
export function mapBlackTrainIcons(centers, cells=[], tiles={}) {
 const valid=cells.filter(c=>c&&c.id!=null&&Number.isFinite(c.x)&&Number.isFinite(c.y)&&Number.isFinite(c.h??0));
 return centers.map(p=>{
  let best=null,distance=Infinity,ambiguous=false;
  for(const c of valid){const planar=(p.x-c.x)**2+(-p.z-c.y)**2,vertical=p.y-(c.h??0);
   // A missing cell must not borrow an unrelated neighbour's event.
   if(planar>.75**2||Math.abs(vertical)>1)continue;
   const d=planar+vertical**2;
   if(d<distance-1e-9){best=c;distance=d;ambiguous=false;}else if(Math.abs(d-distance)<=1e-9)ambiguous=true;
  }
  const entry=best&&!ambiguous&&Object.hasOwn(tiles,String(best.id))?tiles[String(best.id)]:null;
  return entry&&Object.hasOwn(ICONS,entry.color)?{cellId:String(best.id),color:entry.color,category:entry.category??null}:null;
 });
}
// The shipped GLB joins both portal rings AND civic furniture into one mesh.
// Remove only complete triangles inside the verified tower-entry footprint;
// its ordinary Civic tile modules slab already exists underneath. Never hide
// asset_kind=tower (the distant landmark), or the whole civic/portal mesh.
const TOWER_ENTRY={x:12,z:-10,halfX:.44,halfZ:.43};
function withoutTowerPortal(g) {
 const p=g.attributes.position,keep=[];
 for(let i=0;i<p.count;i+=3){
  const inside=[i,i+1,i+2].every(j=>Math.abs(p.getX(j)-TOWER_ENTRY.x)<=TOWER_ENTRY.halfX&&Math.abs(p.getZ(j)-TOWER_ENTRY.z)<=TOWER_ENTRY.halfZ);
  if(!inside)keep.push(i,i+1,i+2);
 }
 if(keep.length===p.count)return g;
 const result=new T.BufferGeometry();
 for(const [name,a]of Object.entries(g.attributes)){
  const array=new a.array.constructor(keep.length*a.itemSize);
  for(let i=0;i<keep.length;i++)for(let k=0;k<a.itemSize;k++)array[i*a.itemSize+k]=a.array[keep[i]*a.itemSize+k];
  result.setAttribute(name,new T.BufferAttribute(array,a.itemSize,a.normalized));
 }
 g.dispose();return result;
}
export async function createBlackTrainTransition(scene, normalBatches, opts, invalidate) {
 const root=opts.baseUrl, loader=new T.TextureLoader(), textures=[], mats=[], geos=new Set();
 async function tex(url){const t=await loader.loadAsync(url);t.flipY=false;t.colorSpace=T.SRGBColorSpace;textures.push(t);return t;}
 const replacements=new Map();
 for(const k of ['gold_ticket','blue_contract','white_beg','black_anomaly'])replacements.set('Icon '+k,await tex(root+'/icons_unified/'+k+'.png'));
 const tile=await tex(root+'/tile_black_gold.png');
 const gltf=await new GLTFLoader().loadAsync(root+'/station_black_gold.glb');gltf.scene.updateMatrixWorld(true);
 const groups=new Map(), dark=[], trains=[], iconParts=[], iconCenters=[], iconBatches=new Map();
 let removedPortalTriangles=0;
 gltf.scene.traverse(n=>{if(!n.isMesh)return;const old=n.material;let train=false;for(let p=n;p;p=p.parent)if(p.userData?.asset_kind==='train')train=true;
 const g=n.geometry.clone().applyMatrix4(n.matrixWorld);let flat=g.index?g.toNonIndexed():g;if(flat!==g)g.dispose();for(const a of Object.keys(flat.attributes))if(!['position','uv','color'].includes(a))flat.deleteAttribute(a);
 if(!train&&n.userData.asset_kind==='civic'&&n.userData.asset_variant==='portal'){
  const before=flat.attributes.position.count;flat=withoutTowerPortal(flat);removedPortalTriangles+=(before-flat.attributes.position.count)/3;
 }
 // Keep source glyphs independently until authoritative gameplay tiles arrive.
 // This includes arrows/start glyphs: no tile entry means no invented event.
 const icon=!train&&(n.userData.role==='tile_icon'||old.name.startsWith('Icon'));
 if(icon){flat.computeBoundingBox();iconCenters.push(flat.boundingBox.getCenter(new T.Vector3()));flat.deleteAttribute('color');iconParts.push(flat);return;}
 const key=old.uuid+':'+train;if(!groups.has(key)){
 const map=old.name.startsWith('Civic tile modules')?tile:old.map;
 const mat=new T.MeshBasicMaterial({map,color:map?0xffffff:old.color,vertexColors:!map&&!!n.geometry.attributes.color,side:n.userData.double_sided_foliage?T.DoubleSide:T.FrontSide,toneMapped:false});mat.name=old.name;mats.push(mat);groups.set(key,{mat,train,parts:[]});}
 groups.get(key).parts.push(flat);
 });
 for(const {mat,train,parts}of groups.values()){
 if(!parts.every(p=>p.attributes.uv))for(const p of parts)p.deleteAttribute('uv');
 const g=mergeGeometries(parts);if(!g)throw Error('黑列车场景合批失败');geos.add(g);for(const p of parts)p.dispose();const mesh=new T.Mesh(g,mat);mesh.visible=false;scene.add(mesh);(train?trains:dark).push(mesh);
 }
 // Four persistent material/mesh batches participate in the same dissolve hooks.
 function emptyGeometry(){const g=new T.BufferGeometry();g.setAttribute('position',new T.Float32BufferAttribute([],3));return g;}
 for(const [color,name]of Object.entries(ICONS)){
  const mat=new T.MeshBasicMaterial({map:replacements.get('Icon '+name),transparent:true,alphaTest:.015,depthWrite:false,side:T.DoubleSide,toneMapped:false,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1});mat.name='Black train icon '+color;mats.push(mat);
  const g=emptyGeometry(),mesh=new T.Mesh(g,mat);geos.add(g);mesh.visible=false;scene.add(mesh);dark.push(mesh);iconBatches.set(color,mesh);
 }
 let iconSignature='',iconRebuilds=0,mappedIcons=0,supplementalIcons=0,unmatchedIcons=iconParts.length;
 function setTiles(tiles={},cells=[]){
  if(disposed)return false;
  cells=cells||[];tiles=tiles||{};
  const mapping=mapBlackTrainIcons(iconCenters,cells,tiles),records=new Map();
  // A cell can have duplicated legacy glyph meshes; draw its event only once.
  for(let i=0;i<mapping.length;i++){const m=mapping[i];if(m&&!records.has(m.cellId))records.set(m.cellId,{...m,index:i});}
  // Gates and other authored omissions have no glyph at all. Only actual
  // authoritative tile entries may create an icon (never a start/end guess).
  const centers=cells.map(c=>({x:c?.x,y:(c?.h??0)+.068,z:-c?.y}));
  const missing=mapBlackTrainIcons(centers,cells,tiles);
  for(let i=0;i<missing.length;i++){const m=missing[i],p=centers[i];if(m&&!records.has(m.cellId))records.set(m.cellId,{...m,p});}
  const ordered=[...records.values()].sort((a,b)=>a.cellId.localeCompare(b.cellId));
  const signature=ordered.map(m=>JSON.stringify([m.cellId,m.color,m.index??null,m.p?.x,m.p?.y,m.p?.z])).join(',');
  if(signature===iconSignature)return false;
  const pending=new Map(),generated=[];
  try{
   for(const m of ordered)if(m.p){
    // Same .72-square footprint and +.068 height as the real source glyphs.
    const g=new T.PlaneGeometry(.72,.72);g.rotateX(-Math.PI/2);g.translate(m.p.x,m.p.y,m.p.z);
    const flat=g.toNonIndexed();g.dispose();flat.deleteAttribute('normal');m.geometry=flat;generated.push(flat);
   }
   for(const color of iconBatches.keys()){
    const parts=ordered.filter(m=>m.color===color).map(m=>m.geometry||iconParts[m.index]);
    const g=parts.length?mergeGeometries(parts):emptyGeometry();
    if(!g)throw Error('黑列车图标合批失败');
    g.computeBoundingSphere();pending.set(color,g);
   }
  }catch(error){for(const g of pending.values())g.dispose();throw error;}
  finally{for(const g of generated)g.dispose();}
  for(const [color,g]of pending){const mesh=iconBatches.get(color),old=mesh.geometry;mesh.geometry=g;geos.delete(old);old.dispose();geos.add(g);}
  iconSignature=signature;iconRebuilds++;mappedIcons=ordered.length;supplementalIcons=generated.length;unmatchedIcons=mapping.filter(m=>!m).length;invalidate();return true;
 }
 gltf.scene.traverse(n=>{if(n.isMesh)n.geometry.dispose();});
 const bounds=new T.Box3();for(const m of trains)bounds.expandByObject(m);const size=bounds.getSize(new T.Vector3());const axis=size.x>=size.z?'x':'z',travel=Math.max(size.x,size.z)+38;
 const originals=new Map([...normalBatches,...dark,...trains].map(m=>[m.material,m.material.color.clone()]));
 // Complementary screen-space coverage dissolves actual artwork without alpha
 // sorting/z-fighting or a full-screen colour filter. No new render targets.
 const mixUniform={value:0}, hooks=[];
 for(const [list,isDark]of [[normalBatches,false],[dark,true]])for(const mat of new Set(list.map(m=>m.material))){
  const compile=mat.onBeforeCompile,key=mat.customProgramCacheKey;
  mat.onBeforeCompile=function(shader,...rest){compile?.call(this,shader,...rest);shader.uniforms.btMix=mixUniform;
   shader.fragmentShader='uniform float btMix;\n'+shader.fragmentShader.replace('void main() {',`void main() {\nfloat btNoise=fract(52.9829189*fract(dot(gl_FragCoord.xy,vec2(0.06711056,0.00583715))));\nif (${isDark?'btNoise >= btMix':'btNoise < btMix'}) discard;`);};
  mat.customProgramCacheKey=()=>String(key?.call(mat)||'')+':bt-dissolve-'+isDark;mat.needsUpdate=true;hooks.push({mat,compile,key});
 }
 let wanted=false, displayed=false, animation=null,disposed=false;
 const bgNormal=scene.background.clone(),bgDark=new T.Color('#252a3d');
 const reduced=()=>globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
 const ease=t=>t*t*(3-2*t);
 function tint(factor){for(const [m,c]of originals)m.color.copy(c).multiplyScalar(factor);}
 function show(on){displayed=on;for(const m of normalBatches)m.visible=!on;for(const m of dark)m.visible=on;}
 function place(offset){for(const m of trains)m.position[axis]=offset;}
 function finish(){mixUniform.value=wanted?1:0;show(wanted);for(const m of trains)m.visible=wanted;place(0);tint(1);scene.background.copy(wanted?bgDark:bgNormal);animation=null;opts.onFinish?.();invalidate();}
 function set(on,instant=false){on=!!on;if(disposed||wanted===on&&!animation)return;wanted=on;if(instant||reduced()){finish();return;}
 // Reverse safely from the current coverage/position, including rapid toggles.
 const offset=animation?(trains[0]?.position[axis]||0):(on?-travel:0);
 animation={start:performance.now(),to:on,offset,mix:mixUniform.value,bg:scene.background.clone()};
 for(const m of [...normalBatches,...dark,...trains])m.visible=true;opts.onStart?.({on,duration:6800,bounds});invalidate();}
 function update(now){if(!animation)return false;const t=Math.min(1,(now-animation.start)/6800),on=animation.to;
 // First establish the wide camera. Train takes 3.8 seconds, then camera returns.
 const phase=ease(T.MathUtils.clamp((t-.20)/.57,0,1));
 mixUniform.value=T.MathUtils.lerp(animation.mix,on?1:0,ease(T.MathUtils.clamp((t-.19)/.48,0,1)));
 scene.background.copy(animation.bg).lerp(on?bgDark:bgNormal,ease(T.MathUtils.clamp((t-.19)/.48,0,1)));
 place(T.MathUtils.lerp(animation.offset,on?0:travel,phase));opts.onFrame?.(t);
 if(t>=1)finish();else invalidate();return true;
 }
 return {set,setTiles,update,get busy(){return !!animation;},get active(){return wanted;},stats(){return {active:wanted,transitioning:!!animation,themeBatches:dark.length,trainBatches:trains.length,trainAxis:axis,iconBatches:iconBatches.size,mappedIcons,supplementalIcons,unmatchedIcons,removedPortalTriangles,iconRebuilds};},dispose(){disposed=true;animation=null;for(const {mat,compile,key}of hooks){mat.onBeforeCompile=compile;mat.customProgramCacheKey=key;mat.needsUpdate=true;}for(const m of [...dark,...trains])scene.remove(m);for(const g of [...geos,...iconParts])g.dispose();for(const m of mats){if(m.map&&!textures.includes(m.map))m.map.dispose();m.dispose();}for(const t of textures)t.dispose();tint(1);}};
}
