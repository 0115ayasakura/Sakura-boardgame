// Run: node --test sakura_boardgame/tests/board3d_scheduler.test.mjs
// Execute the shipped scheduler/frame/lifecycle bodies, not a rewritten scheduler.
// Only read board3d.js; no packages, configs, DOM, WebGL, server or real timers.
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {test} from 'node:test';
import vm from 'node:vm';

const source=(await readFile(new URL('../art/lib/board3d.js',import.meta.url),'utf8')).replace(/\r\n/g,'\n');
function between(start,end){
 const a=source.indexOf(start),b=source.indexOf(end,a+start.length);
 assert.ok(a>=0&&b>a,`production source boundaries exist: ${start}`);
 return source.slice(a,b);
}
const scheduler=between('function cancelFrame(){','function setSpritePosition(');
const frame=between('let previous=0,lastTick=0;','function resize(){');
const dispose=between('dispose(){if(disposed)return;','canvas.remove();}')+'canvas.remove();}';
const lifecycle=between("listen('webglcontextlost'", "document.addEventListener('visibilitychange'");

function fixture({hidden=false,visible=true,transition=false,moving=false,eligible=false}={}){
 let now=1000,serial=0;
 const jobs=new Map(),history=[],renders=[],updates=[],moveUpdates=[],handlers=new Map();
 const setJob=(kind,fn,delay)=>{const id=++serial;const due=now+delay;jobs.set(id,{id,kind,fn,due});history.push({kind,due});return id;};
 const life={visible,eligible,interval:250,update(){return false;},observeCost(){},dispose(){}};
 const document={hidden,removeEventListener(){}};
 const transitionState={busy:transition,update(time){
  if(!this.busy)return;
  updates.push(time);
  if(time>=7800)this.busy=false;
  // The real transition invalidates both intermediate updates and finish().
  context.schedule();
 },dispose(){this.busy=false;}};
 const moves=new Map(moving?[['piece',{}]]:[]);
 const context=vm.createContext({
  performance:{now:()=>now},document,life,trainTransition:transitionState,moves,
  pendingFrame:null,frameSerial:0,disposed:false,dead:false,dirty:true,
  dragging:false,cinematic:null,interactionUntil:0,hoverRequest:null,
  camState:{followPt:null},opts:{},tickers:new Set(),
  updateMoves(time){moveUpdates.push(time);if(time>=1430)moves.clear();},
  cameraUpdate(){},overlayUpdate(){},scene:{},camera:{},
  renderer:{render(){renders.push(now);},info:{render:{calls:1,triangles:2}},dispose(){}},
  stats:{frames:0,frameIntervals:[]},
  setTimeout:(fn,delay)=>setJob('timeout',fn,delay),clearTimeout:id=>jobs.delete(id),
  requestAnimationFrame:fn=>setJob('raf',fn,16),cancelAnimationFrame:id=>jobs.delete(id),
  listen:(type,fn)=>handlers.set(type,fn),console:{warn(){}},
  window:{dispatchEvent(){}},Event:class {},
  ro:{disconnect(){}},listeners:[],canvas:{removeEventListener(){},remove(){}},
  overlays:new Map(),geometries:new Set(),materials:new Set(),textures:new Map(),
  matCache:new Map(),blank:{dispose(){}},sprites:new Map()
 });
 vm.runInContext(`${scheduler}\n${frame}\nconst api={${dispose}};\n${lifecycle}\nglobalThis.disposeBoard=()=>api.dispose();`,context);
 function advance(ms){
  const target=now+ms;let count=0;
  for(;;){
   const next=[...jobs.values()].filter(j=>j.due<=target).sort((a,b)=>a.due-b.due||a.id-b.id)[0];
   if(!next)break;
   assert.ok(++count<2000,'scheduler must not spin');jobs.delete(next.id);now=next.due;next.fn();
  }
  now=target;
 }
 return {context,document,life,transitionState,moves,jobs,history,renders,updates,moveUpdates,handlers,advance,now:()=>now};
}

for(const mode of [{name:'document hidden',hidden:true},{name:'life.visible false',visible:false}]){
 test(`${mode.name}: transition alone advances every 120ms, finishes and stops without GL`,()=>{
  const f=fixture({...mode,transition:true,eligible:true});
  f.context.schedule();
  assert.equal(f.jobs.size,1,'busy transition must enqueue a hidden logical tick');
  assert.equal([...f.jobs.values()][0].due,1120);
  f.advance(119);assert.equal(f.updates.length,0);
  f.advance(1);assert.deepEqual(f.updates,[1120]);assert.equal(f.jobs.size,1);
  f.advance(6720);
  assert.equal(f.transitionState.busy,false,'finish reached even without visible rendering');
  assert.equal(f.updates.length,57);
  assert.ok(f.updates.every((t,i)=>t===1120+i*120));
  assert.equal(f.jobs.size,0,'completion must not leave ambient polling');
  const count=f.moveUpdates.length;f.advance(10000);assert.equal(f.moveUpdates.length,count);
  assert.equal(f.renders.length,0);assert.ok(f.history.every(j=>j.kind==='timeout'),'no hidden RAF');
 });
 test(`${mode.name}: idle does not enqueue; moves retain low-frequency completion`,()=>{
  const idle=fixture({...mode,eligible:true});idle.context.schedule();idle.context.schedule(false);
  assert.equal(idle.jobs.size,0);assert.equal(idle.history.length,0);
  const f=fixture({...mode,moving:true});f.context.schedule();f.advance(480);
  assert.equal(f.moves.size,0);assert.equal(f.jobs.size,0);
  assert.deepEqual(f.moveUpdates,[1120,1240,1360,1480]);assert.equal(f.renders.length,0);
 });
 test(`${mode.name}: visibility notification cancels visible work and restarts hidden transition`,()=>{
  const f=fixture({transition:true});f.context.schedule();
  assert.equal(f.jobs.size,2);
  if(mode.hidden)f.document.hidden=true;else f.life.visible=false;
  f.context.visibilityChanged();
  assert.equal(f.jobs.size,1);assert.equal([...f.jobs.values()][0].due,1120);
  f.advance(120);assert.deepEqual(f.updates,[1120]);assert.equal(f.renders.length,0);
 });
}

test('visible transition uses RAF, renders and cancels the redundant timeout fallback',()=>{
 const f=fixture({transition:true});f.context.schedule();
 assert.equal(f.jobs.size,2);assert.equal([...f.jobs.values()].find(j=>j.kind==='raf').due,1016);
 f.advance(16);assert.deepEqual(f.updates,[1016]);assert.deepEqual(f.renders,[1016]);
 assert.equal(f.jobs.size,2);f.advance(16);assert.equal(f.updates.length,2);assert.equal(f.renders.length,2);
 f.advance(7000);assert.equal(f.transitionState.busy,false);assert.equal(f.jobs.size,0);
 const count=f.renders.length;f.advance(10000);assert.equal(f.renders.length,count);
});

test('visible fallback works when RAF is throttled, and stale RAF cannot double-fire',()=>{
 const f=fixture();f.context.schedule();
 const raf=[...f.jobs.values()].find(j=>j.kind==='raf');f.jobs.delete(raf.id);
 f.advance(32);assert.deepEqual(f.renders,[1032]);assert.equal(f.jobs.size,0);
 raf.fn();assert.equal(f.renders.length,1);
});

test('hidden requests coalesce, preserve later requested delays, and reject stale callbacks',()=>{
 const f=fixture({hidden:true,transition:true});f.context.schedule(true,500);
 const stale=[...f.jobs.values()][0];f.context.schedule(false,700);assert.equal(f.jobs.size,1);
 assert.equal([...f.jobs.values()][0].due,1500);
 f.context.schedule();assert.equal(f.jobs.size,1);assert.equal([...f.jobs.values()][0].due,1120);
 stale.fn();assert.equal(f.updates.length,0);
 for(let i=0;i<20;i++)f.context.schedule();assert.equal(f.jobs.size,1);
 f.advance(120);assert.deepEqual(f.updates,[1120]);assert.equal(f.jobs.size,1);
});

for(const hidden of [false,true]){
 for(const stop of ['dispose','dead']){
  test(`${hidden?'hidden':'visible'} ${stop} cancels all handles and rejects late callbacks/restarts`,()=>{
   const f=fixture({hidden,transition:true});f.context.schedule();
   assert.equal(f.jobs.size,hidden?1:2);
   const stale=[...f.jobs.values()].map(j=>j.fn);
   if(stop==='dispose'){f.context.disposeBoard();f.context.disposeBoard();}
   else f.handlers.get('webglcontextlost')({preventDefault(){}});
   assert.equal(f.jobs.size,0);
   for(const fn of stale)fn();
   f.context.schedule();f.context.frame();f.context.visibilityChanged();f.advance(10000);
   assert.equal(f.jobs.size,0);assert.equal(f.updates.length,0);assert.equal(f.renders.length,0);
  });
 }
}
