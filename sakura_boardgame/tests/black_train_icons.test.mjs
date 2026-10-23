// Run with: node --test sakura_boardgame/tests/black_train_icons.test.mjs
// Browser modules are loaded in memory; no packages, DOM, WebGL or server needed.
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {test} from 'node:test';
const lib=new URL('../art/lib/',import.meta.url);
const moduleURL=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const threeURL=moduleURL(await readFile(new URL('three_module.js',lib),'utf8'));
const T=await import(threeURL);
const utilsURL=moduleURL((await readFile(new URL('buffer_geometry_utils.js',lib),'utf8')).replace("from 'three'",`from '${threeURL}'`));
const loaderURL=moduleURL('export class GLTFLoader {async loadAsync(){return {scene:globalThis.__blackTrainFixture()};}}');
const source=(await readFile(new URL('black_train_transition.js',lib),'utf8'))
 .replace("from 'three'",`from '${threeURL}'`)
 .replace("from './gltf_loader.js'",`from '${loaderURL}'`)
 .replace("from './buffer_geometry_utils.js'",`from '${utilsURL}'`);
const {mapBlackTrainIcons,createBlackTrainTransition}=await import(moduleURL(source));
const cells=[{id:71,x:2,y:3,h:0},{id:'start',x:4,y:3,h:0},{id:8,x:6,y:3,h:2},{id:99,x:8,y:3,h:0},{id:4,x:10,y:3,h:0}];
const tiles={'71':{color:'black',category:'黑色异常'},'8':{color:'gold',category:'金色车票'},'99':{color:'white',category:'白色乞讨'},'4':{color:'blue',category:'蓝色契约'}};
const center=c=>({x:c.x,y:c.h+.07,z:-c.y});

test('world centers map shuffled ids, cells, and glyphs to authoritative color/category',()=>{
 const order=[cells[4],cells[2],cells[0],cells[3],cells[1]];
 const mapped=mapBlackTrainIcons(order.map(center),[...cells].reverse(),tiles);
 assert.deepEqual(mapped.map(m=>m?.color??null),['blue','gold','black','white',null]);
 assert.deepEqual(mapped.slice(0,4).map(m=>m.category),['蓝色契约','金色车票','黑色异常','白色乞讨']);
 assert.deepEqual(mapped.slice(0,4).map(m=>m.cellId),['4','8','71','99']);
 assert.deepEqual(mapBlackTrainIcons(order.map(center),cells,tiles),mapped);
});

test('height disambiguates overlapping floors; unknown/missing/ambiguous mappings hide',()=>{
 const floors=[{id:'low',x:1,y:2,h:0},{id:'high',x:1,y:2,h:3}];
 assert.equal(mapBlackTrainIcons([{x:1,y:3.07,z:-2}],floors,{high:tiles[71]})[0].cellId,'high');
 assert.deepEqual(mapBlackTrainIcons([{x:50,y:0,z:50}],cells,tiles),[null]);
 assert.deepEqual(mapBlackTrainIcons([center(cells[0])],cells,{'71':{color:'purple',category:'金色车票'}}),[null]);
 assert.deepEqual(mapBlackTrainIcons([center(cells[0])],[cells[0],{...cells[0],id:'duplicate'}],tiles),[null]);
 assert.deepEqual(mapBlackTrainIcons([center(cells[0])],cells,{}),[null]);
});

function glyph(c,oldName='Icon gold_ticket'){
 const mesh=new T.Mesh(new T.PlaneGeometry(.8,.8),new T.MeshBasicMaterial());
 mesh.geometry.rotateX(-Math.PI/2);mesh.position.set(c.x,c.h+.07,-c.y);
 mesh.material.name=oldName;mesh.userData.role='tile_icon';return mesh;
}
function fixture(){
 const root=new T.Group();
 // Deliberately wrong legacy textures/order, including a start arrow with no event.
 for(const c of [cells[3],cells[0],cells[4],cells[1],cells[2]])root.add(glyph(c));
 const station=new T.Mesh(new T.BoxGeometry(),new T.MeshBasicMaterial({color:0x123456}));station.material.name='station';root.add(station);
 const train=new T.Mesh(new T.BoxGeometry(4,1,2),new T.MeshBasicMaterial());train.userData.asset_kind='train';train.material.name='train';root.add(train);return root;
}

test('four category batches, idempotent resync, dissolve/normal restore, and disposal',async()=>{
 const originalLoader=T.TextureLoader.prototype.loadAsync;
 T.TextureLoader.prototype.loadAsync=async url=>{const t=new T.Texture();t.name=url;return t;};
 globalThis.__blackTrainFixture=fixture;
 const scene=new T.Scene();scene.background=new T.Color('#c7e5f2');
 const normal=glyph(cells[0]);scene.add(normal);
 const normalPositions=Array.from(normal.geometry.attributes.position.array);
 const oldCompile=normal.material.onBeforeCompile,oldKey=normal.material.customProgramCacheKey;
 let invalidations=0,starts=0,finishes=0;
 try{
  const transition=await createBlackTrainTransition(scene,[normal],{baseUrl:'/theme',onStart(){starts++;},onFinish(){finishes++;}},()=>invalidations++);
  assert.equal(transition.stats().iconBatches,4);
  assert.equal(transition.stats().mappedIcons,0,'legacy icons hidden until authoritative data arrives');
  assert.equal(transition.setTiles(tiles,cells),true);
  assert.equal(transition.stats().mappedIcons,4);
  assert.equal(transition.stats().unmatchedIcons,1,'start arrow must not invent an event');
  const batches=scene.children.filter(m=>m.material?.name.startsWith('Black train icon '));
  const oldGeometries=batches.map(m=>m.geometry);
  for(const batch of batches){
   const color=batch.material.name.split(' ').at(-1);
   assert.equal(batch.material.map.name,`/theme/icons_unified/${{gold:'gold_ticket',blue:'blue_contract',white:'white_beg',black:'black_anomaly'}[color]}.png`);
   const p=batch.geometry.boundingSphere.center;
   assert.equal(mapBlackTrainIcons([p],cells,tiles)[0].color,color);
   const shader={uniforms:{},fragmentShader:'void main() { }'};batch.material.onBeforeCompile(shader);
   assert.match(shader.fragmentShader,/btNoise >= btMix/);
  }
  for(let i=0;i<5;i++)assert.equal(transition.setTiles(structuredClone(tiles),[...cells].reverse()),false);
  assert.deepEqual(batches.map(m=>m.geometry),oldGeometries,'polling sync must not rebuild geometry');
  assert.equal(transition.stats().iconRebuilds,1);
  transition.set(true,true);assert.equal(normal.visible,false);assert.ok(batches.every(m=>m.visible));
  transition.set(false,true);assert.equal(normal.visible,true);assert.ok(batches.every(m=>!m.visible));
  assert.deepEqual(Array.from(normal.geometry.attributes.position.array),normalPositions);
  assert.equal(scene.background.getHexString(),'c7e5f2');
  transition.set(true);assert.equal(starts,1);assert.equal(transition.busy,true);
  const changed={...tiles,'71':{color:'gold',category:'金色车票'}};
  assert.equal(transition.setTiles(changed,cells),true);
  assert.equal(transition.stats().iconBatches,4);assert.equal(transition.stats().iconRebuilds,2);
  transition.update(performance.now()+7000);assert.equal(transition.active,true);assert.equal(transition.busy,false);
  transition.set(false,true);
  assert.ok(finishes>=3);assert.ok(invalidations>0);
  let disposed=0;for(const batch of batches)batch.geometry.addEventListener('dispose',()=>disposed++);
  transition.dispose();assert.equal(disposed,4);assert.deepEqual(scene.children,[normal]);
  assert.equal(normal.material.onBeforeCompile,oldCompile);assert.equal(normal.material.customProgramCacheKey,oldKey);
  assert.equal(transition.setTiles(tiles,cells),false);
 }finally{T.TextureLoader.prototype.loadAsync=originalLoader;delete globalThis.__blackTrainFixture;}
});

test('missing glyphs are synthesized once per authoritative cell, including tower entry',async()=>{
 const originalLoader=T.TextureLoader.prototype.loadAsync;
 T.TextureLoader.prototype.loadAsync=async()=>new T.Texture();
 const entry={id:'entry',x:12,y:10,h:1.9966},end={id:'end',x:16,y:16,h:1.9858};
 globalThis.__blackTrainFixture=()=>{const root=fixture();root.add(glyph(cells[0]));return root;};
 const scene=new T.Scene();scene.background=new T.Color('#c7e5f2');
 let transition;
 try{
  transition=await createBlackTrainTransition(scene,[],{baseUrl:'/theme'},()=>{});
  const authority={...tiles,entry:{color:'blue',category:'契约'},end:{color:'white',category:'乞讨'}};
  const all=[...cells,entry,end];
  assert.equal(transition.setTiles(authority,all),true);
  assert.equal(transition.stats().mappedIcons,6,'duplicate source glyph not counted twice');
  assert.equal(transition.stats().supplementalIcons,2);
  const batches=scene.children.filter(m=>m.material?.name.startsWith('Black train icon '));
  assert.equal(batches.reduce((sum,m)=>sum+m.geometry.attributes.position.count,0),6*6);
  assert.equal(transition.setTiles(authority,[...all].reverse()),false);
  const previous=batches.map(m=>m.geometry);let disposed=0;
  for(const g of previous)g.addEventListener('dispose',()=>disposed++);
  assert.equal(transition.setTiles(tiles,cells),true);assert.equal(disposed,4);
  assert.equal(transition.stats().mappedIcons,4);assert.equal(transition.stats().supplementalIcons,0);
  assert.equal(transition.setTiles({},all),true);assert.equal(transition.stats().mappedIcons,0);
  assert.equal(transition.setTiles({},all),false);
 }finally{transition?.dispose();T.TextureLoader.prototype.loadAsync=originalLoader;delete globalThis.__blackTrainFixture;}
});

// Read the real shipped binary, not guessed object names or a mocked gate.
// Decode uncompressed GLB attributes directly; no browser, image decoder or server.
async function realGLB(){
 const bytes=await readFile(new URL('../art/black_train/station_black_gold.glb',import.meta.url));
 assert.equal(bytes.toString('ascii',0,4),'glTF');
 const length=bytes.readUInt32LE(12),doc=JSON.parse(bytes.toString('utf8',20,20+length));
 const bin=20+length+8,types={5126:Float32Array,5125:Uint32Array,5123:Uint16Array,5121:Uint8Array},sizes={SCALAR:1,VEC2:2,VEC3:3,VEC4:4};
 function attribute(index){
  const a=doc.accessors[index],v=doc.bufferViews[a.bufferView],Type=types[a.componentType],size=sizes[a.type];
  assert.ok(Type&&size&&!a.sparse,'fixture decoder only supports ordinary shipped accessors');
  const array=new Type(a.count*size),stride=v.byteStride||size*Type.BYTES_PER_ELEMENT;
  for(let i=0;i<a.count;i++){
   const offset=bin+(v.byteOffset||0)+(a.byteOffset||0)+i*stride;
   const chunk=bytes.subarray(offset,offset+size*Type.BYTES_PER_ELEMENT);
   array.set(new Type(chunk.buffer.slice(chunk.byteOffset,chunk.byteOffset+chunk.byteLength)),i*size);
  }
  return new T.BufferAttribute(array,size,a.normalized||false);
 }
 const materials=doc.materials.map(m=>{const mat=new T.MeshBasicMaterial();mat.name=m.name;return mat;});
 const nodes=doc.nodes.map(n=>{
  const group=new T.Group();group.name=n.name;group.userData={...n.extras};
  if(n.mesh!=null)for(const p of doc.meshes[n.mesh].primitives){
   const g=new T.BufferGeometry();
   for(const [key,value]of Object.entries(p.attributes)){const name={POSITION:'position',NORMAL:'normal',TEXCOORD_0:'uv',COLOR_0:'color'}[key];if(name)g.setAttribute(name,attribute(value));}
   if(p.indices!=null)g.setIndex(attribute(p.indices));
   const mesh=new T.Mesh(g,materials[p.material]);mesh.userData={...n.extras};group.add(mesh);
  }
  if(n.matrix)group.applyMatrix4(new T.Matrix4().fromArray(n.matrix));
  else {if(n.translation)group.position.fromArray(n.translation);if(n.rotation)group.quaternion.fromArray(n.rotation);if(n.scale)group.scale.fromArray(n.scale);}
  return group;
 });
 doc.nodes.forEach((n,i)=>{for(const child of n.children||[])nodes[i].add(nodes[child]);});
 const root=new T.Group();for(const index of doc.scenes[doc.scene||0].nodes)root.add(nodes[index]);root.updateMatrixWorld(true);
 return root;
}

test('real GLB keeps ordinary slab, removes only tower-entry civic portal, and fills both missing glyphs',async()=>{
 const root=await realGLB(),layout=JSON.parse(await readFile(new URL('../board_layout.json',import.meta.url),'utf8'));
 assert.deepEqual(layout.towerGate,[12,10],'review verified spatial clipping if the map changes');
 const route=[...layout.main_path,...Object.values(layout.branches).flat()];
 const unique=[...new Map(route.map(uv=>[uv.join(','),uv])).values()];
 const realCells=unique.map(([x,y],id)=>({id,x,y,h:layout.heights[`${x},${y}`]??0}));
 const realTiles=Object.fromEntries(realCells.filter(c=>!(c.x===0&&c.y===2)).map((c,i)=>[c.id,{color:['gold','blue','white','black'][i%4],category:'authoritative fixture'}]));
 const portal=[],slab=[];let glyphCount=0;
 root.traverse(n=>{if(!n.isMesh)return;const g=n.geometry.clone().applyMatrix4(n.matrixWorld),p=g.attributes.position;
  if(n.userData.asset_kind==='civic'&&n.userData.asset_variant==='portal')portal.push(g.index?g.toNonIndexed():g);
  if(n.material.name.startsWith('Civic tile modules'))slab.push(g.index?g.toNonIndexed():g);
  if(n.userData.role==='tile_icon')glyphCount++;
 });
 const inEntry=(p,i)=>Math.abs(p.getX(i)-12)<=.44&&Math.abs(p.getZ(i)+10)<=.43;
 const countEntry=geometries=>geometries.reduce((sum,g)=>{const p=g.attributes.position;for(let i=0;i<p.count;i+=3)if([i,i+1,i+2].every(j=>inEntry(p,j)))sum++;return sum;},0);
 const countAt=(geometries,x,z)=>geometries.reduce((sum,g)=>{const p=g.attributes.position;for(let i=0;i<p.count;i+=3)if([i,i+1,i+2].every(j=>Math.abs(p.getX(j)-x)<=.44&&Math.abs(p.getZ(j)-z)<=.43))sum++;return sum;},0);
 const portalTriangles=countEntry(portal);assert.equal(portalTriangles,324,'one complete plinth/support/ring portal');
 const endTriangles=countAt(portal,16,-16);assert.equal(endTriangles,324);
 const entry=realCells.find(c=>c.x===12&&c.y===10);assert.ok(entry);
 let topTriangles=0;
 for(const g of slab){const p=g.attributes.position;for(let i=0;i<p.count;i+=3){
  if([i,i+1,i+2].every(j=>Math.abs(p.getX(j)-12)<=.501&&Math.abs(p.getZ(j)+10)<=.501&&Math.abs(p.getY(j)-(entry.h+.06))<.002))topTriangles++;
 }}
 assert.ok(topTriangles>=2,'ordinary tile top already exists underneath portal');
 const originalLoader=T.TextureLoader.prototype.loadAsync;
 T.TextureLoader.prototype.loadAsync=async()=>new T.Texture();globalThis.__blackTrainFixture=()=>root;
 const scene=new T.Scene();scene.background=new T.Color('#c7e5f2');const normal=glyph(entry);scene.add(normal);
 let transition;
 try{
  transition=await createBlackTrainTransition(scene,[normal],{baseUrl:'/theme'},()=>{});
  assert.equal(transition.stats().removedPortalTriangles,portalTriangles);
  const civic=scene.children.filter(n=>n.material?.name==='civic modules_baked');
  assert.equal(civic.length,1);assert.equal(countEntry(civic.map(n=>n.geometry)),0);
  assert.equal(countAt(civic.map(n=>n.geometry),16,-16),endTriangles,'ordinary end portal geometry unchanged');
  assert.ok(scene.children.some(n=>n.material?.name.startsWith('tower modules')),'distant tower landmark remains');
  assert.equal(civic[0].geometry.attributes.position.count,portal.reduce((sum,g)=>sum+g.attributes.position.count,0)-portalTriangles*3,'all other civic geometry, including end portal, retained');
  transition.setTiles(realTiles,realCells);
  assert.equal(glyphCount,55);assert.equal(transition.stats().supplementalIcons,2);
  assert.equal(transition.stats().mappedIcons,56);
  assert.equal(transition.stats().unmatchedIcons,1);
  const renderedIds=[];
  for(const batch of scene.children.filter(n=>n.material?.name.startsWith('Black train icon '))){
   const p=batch.geometry.attributes.position,color=batch.material.name.split(' ').at(-1);
   for(let i=0;i<p.count;i+=6){
    const box=new T.Box3();for(let j=i;j<i+6;j++)box.expandByPoint(new T.Vector3().fromBufferAttribute(p,j));
    const m=mapBlackTrainIcons([box.getCenter(new T.Vector3())],realCells,realTiles)[0];
    assert.ok(m,'every rendered quad belongs to a real authoritative cell');assert.equal(m.color,color);renderedIds.push(m.cellId);
   }
  }
  assert.equal(new Set(renderedIds).size,56,'no duplicated cell icons in real GLB');
  assert.ok(renderedIds.includes(String(entry.id)));
  assert.ok(renderedIds.includes(String(realCells.find(c=>c.x===16&&c.y===16).id)));
  assert.equal(transition.setTiles(realTiles,[...realCells].reverse()),false);
  transition.set(true,true);assert.equal(normal.visible,false);
  transition.set(false,true);assert.equal(normal.visible,true);assert.equal(scene.background.getHexString(),'c7e5f2');
  console.log('REAL_GLB',JSON.stringify({glyphCount,ordinaryEntryTopTriangles:topTriangles,removedPortalTriangles:portalTriangles,legacyMapped:54,...transition.stats()}));
 }finally{transition?.dispose();T.TextureLoader.prototype.loadAsync=originalLoader;delete globalThis.__blackTrainFixture;for(const g of [...portal,...slab])g.dispose();}
});

test('board exposes and forwards the sync API after async theme loading',async()=>{
 const board=await readFile(new URL('board3d.js',lib),'utf8');
 assert.match(board,/function setBlackTrainTiles\(tiles,cells\)/);
 assert.match(board,/trainTransition\?\.setTiles\(blackTrainTiles,blackTrainCells\)/);
 assert.match(board,/createBlackTrainTransition[\s\S]*setBlackTrainTiles\(blackTrainTiles,blackTrainCells\)/);
 assert.match(board,/project,setPieces,setBadges,playMove,setBlackTrainTiles/);
});
