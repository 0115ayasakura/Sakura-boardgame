import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as Three from '../sakura_boardgame/art/lib/three_module.js';
const utilsSource=(await readFile(new URL('../sakura_boardgame/art/lib/buffer_geometry_utils.js',import.meta.url),'utf8')).replace("'three'",JSON.stringify(new URL('../sakura_boardgame/art/lib/three_module.js',import.meta.url).href));
const {mergeGeometries}=await import('data:text/javascript;base64,'+Buffer.from(utilsSource).toString('base64'));
let now=0,invalidations=0;
const source=(await readFile(new URL('../sakura_boardgame/art/lib/black_train_transition.js',import.meta.url),'utf8')).replace(/^import .*;$/gm,'').replace(/^export (?=(?:async )?function )/gm,'')+'\nreturn createBlackTrainTransition;';
class Loader{async loadAsync(){const s=new Three.Group();for(const train of [false,true]){const m=new Three.Mesh(new Three.BoxGeometry(train?12:3,1,2),new Three.MeshBasicMaterial({color:0xffffff}));m.material.name=train?'Train':'Civic tile modules';if(train)m.userData.asset_kind='train';s.add(m);}return{scene:s};}}
class TextureLoader{async loadAsync(){return new Three.Texture();}}
const create=new Function('T','GLTFLoader','mergeGeometries','performance',source)({...Three,TextureLoader},Loader,mergeGeometries,{now:()=>now});
const scene=new Three.Scene();scene.background=new Three.Color('#c7e5f2');const normal=new Three.Mesh(new Three.BoxGeometry(),new Three.MeshBasicMaterial());scene.add(normal);
const c=await create(scene,[normal],{baseUrl:'mock'},()=>invalidations++);
assert.equal(c.busy,false);assert.equal(c.active,false);assert.equal(normal.visible,true);
c.set(true);assert.equal(c.busy,true);c.update(500);assert.equal(normal.visible,true);c.update(6900);assert.equal(c.busy,false);assert.equal(c.active,true);assert.equal(c.stats().trainAxis,'x');
const train=scene.children.find(m=>m.material?.name==='Train');assert.equal(train.position.x,0);assert.equal(train.visible,true);
now=7000;c.set(false);c.update(9500);assert.equal(normal.visible,true);c.update(13900);assert.equal(train.visible,false);assert.equal(train.position.x,0);assert.equal(normal.material.color.getHex(),0xffffff);assert.equal(scene.background.getHexString(),'c7e5f2');
for(let i=0;i<3;i++){now+=15000;c.set(true);c.update(now+6900);now+=2400;c.set(false);c.update(now+6900);}assert.equal(train.position.x,0);
now+=15000;c.set(true);c.update(now+600);const before=train.position.x;now+=600;c.set(false);c.update(now);assert.equal(train.position.x,before,'mid-flight reversal must not jump');c.update(now+6900);
c.set(true,true);assert.equal(c.busy,false);assert.equal(train.visible,true);c.set(false,true);assert.equal(train.visible,false);
const old=invalidations;c.update(now+10000);assert.equal(invalidations,old,'idle update must not schedule frames');
c.dispose();assert.equal(scene.children.length,1);assert.equal(normal.material.color.getHex(),0xffffff);
console.log('PASS black-train transition: cached theme, ingress/egress, restoration, reversal, idle, disposal');
