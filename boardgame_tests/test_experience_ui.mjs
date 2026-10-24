import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const html=await readFile(new URL('../sakura_boardgame/board.html',import.meta.url),'utf8');
const classes=()=>{const items=new Set();return {add:x=>items.add(x),remove:x=>items.delete(x),contains:x=>items.has(x),toggle(x,on){on?items.add(x):items.delete(x);}}};
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,innerHTML:'',textContent:'',classList:classes(),setAttribute(k,v){this[k]=v;}});return elements.get(id);};
let now=1000,id=0,timers=new Map(),flow={allows:()=>true,reason:''},calls=[],resolveAction,polls=0,notes=[];
const c=vm.createContext({console,Date:{now:()=>now},document:{getElementById:el,querySelectorAll:()=>[],body:el('body')},$:s=>el(s.replace(/^#/,'')),state:{gameSeq:7,game:{kind:'monopoly',turn:'user'},autoMode:'hers'},uiEpoch:2,autoTimer:null,
  presentationSnapshot:()=>flow,BlackTrainView:{phase:()=> 'returning'},AgentUI:{busy:ui=>!!ui?.busy,retry(ui){ui.paused=false;},sync(){}},esc:String,
  connection:{action:async(action,args)=>{calls.push([action,args]);return new Promise(r=>resolveAction=r);}},
  clearTimeout:key=>timers.delete(key),setTimeout:(fn,ms)=>{const key=++id;timers.set(key,{fn,ms});return key;},toast:(...args)=>notes.push(args),poll:async()=>{polls++;return true;},
  naviLogPrefix:()=> '事件',syncBlackTrainUI(){},renderActionBar(){},cardQueue:[],cardTimer:null});
vm.runInContext("let playControlBusy=false; const playWaitClock={key:'',since:0};",c);
for(const name of ['api','syncAutoModeSnapshot','setPlayPaused','syncPlaySessionUI','showCard','showNextCard','closeCard','resetCardQueue']){
  const match=html.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^}`,'m'));assert.ok(match,name);vm.runInContext(match[0],c);
}
// Important receipts remain until explicit acknowledgement, including a long read.
c.showCard('获得道具','获得护盾，现金 ¥80',false,'item-draw');assert.equal(timers.size,0);assert.equal(el('card-continue').hidden,false);assert.equal(el('card-modal').requiresConfirm,true);
now+=60000;assert.equal(el('card-modal').classList.contains('show'),true);
c.showCard('普通提示','路边风景',false);assert.equal(c.cardQueue.length,1);
const oldConfirm=el('card-continue').onclick;oldConfirm({stopPropagation(){}});assert.equal(el('e-card-title').textContent,'普通提示');oldConfirm({stopPropagation(){}});assert.equal(el('e-card-title').textContent,'普通提示');assert.equal(el('card-modal').classList.contains('show'),true,'stale double confirmation cannot dismiss the successor');assert.equal(timers.size,1);assert.equal(el('card-continue').hidden,true);
const lightTimer=[...timers.values()][0];assert.equal(lightTimer.ms,4200);lightTimer.fn();assert.equal(el('card-modal').classList.contains('show'),false);
c.showCard('对局结束','获胜',true);assert.equal(el('card-modal').requiresConfirm,true);assert.equal(timers.size,0);c.resetCardQueue();
// Pause/resume are explicit, single-flight and reachable despite presentation locks.
flow={allows:()=>false,reason:'正在返程',blackLocked:true};c.syncPlaySessionUI();assert.match(el('play-wait').textContent,/正在返程/);now+=16000;c.syncPlaySessionUI();assert.match(el('play-wait').textContent,/16 秒/);assert.equal(el('play-check').hidden,false);
const first=c.setPlayPaused(true);assert.equal(calls.length,1);assert.equal(calls[0][0],'pause_play');await c.setPlayPaused(true);assert.equal(calls.length,1,'double-click pause stays single-flight');
resolveAction({ok:true,playPaused:true});await first;assert.equal(c.state.playPaused,true);assert.equal(polls,1);assert.equal(el('play-resume').hidden,false);assert.equal(el('play-hide').hidden,false);assert.match(notes.at(-1)[0],/暂停并保存/);
const blocked=await c.api('roll');assert.equal(blocked.playPaused,true);assert.equal(calls.length,1,'paused move never reaches transport');
const resume=c.setPlayPaused(false);assert.equal(calls[1][0],'resume_play');resolveAction({ok:true,playPaused:false});await resume;assert.equal(c.state.playPaused,false);assert.equal(c.state.autoMode,'hers');assert.equal(calls.length,2,'resume itself never dispatches a game action');
const failed=c.setPlayPaused(true);resolveAction({ok:false,error:'保存失败'});await failed;assert.equal(c.state.playPaused,false);assert.equal(notes.filter(x=>/暂停并保存/.test(x[0])).length,1,'failed save never claims persisted pause');
c.syncAutoModeSnapshot({autoMode:'hers',playPaused:true});assert.equal(c.state.playPaused,true);assert.equal(c.state.autoMode,'hers');
console.log('PASS important receipt confirmation, light notice timeout, wait phase/elapsed, pause/resume single-flight and non-advancing controls');
