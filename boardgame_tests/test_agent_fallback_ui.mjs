import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const html=await readFile(new URL('../sakura_boardgame/board.html',import.meta.url),'utf8');
const c=vm.createContext({});
for(const file of ['agent_ui','presentation_flow']) vm.runInContext(await readFile(new URL(`../sakura_boardgame/art/lib/${file}.js`,import.meta.url),'utf8'),c);
const A=c.AgentUI, P=c.PresentationFlow;
const ui=A.create(7), game={kind:'monopoly',turn:'sakura'};
let allowed=true, resolve, calls=[], polls=0;
const bar={innerHTML:''};
Object.assign(c,{state:{gameSeq:7,game,agentUI:ui},uiEpoch:1,autoTimer:null,
  presentationSnapshot:()=>({allows:()=>allowed}),clearTimeout(){},
  connection:{action:async(action,args)=>{calls.push([action,args]);return new Promise(r=>resolve=r);}},
  $:()=>bar,currentKind:()=> 'monopoly',NAMES:{sakura:'樱'},esc:String,
  toast(){},poll:async()=>{polls++;}});
for(const name of ['api','fallbackAgentDecision','renderActionBar']) {
  const m=html.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^}`,'m'));
  assert.ok(m,name);vm.runInContext(m[0],c);
}
function sync(status,id='timeout') {A.sync(ui,{gameSeq:7,decisionDriver:'agent',agentDecision:{id,gameSeq:7,status}});}
for(const status of ['thinking','queued','cancelled','committed']) {sync(status);assert.equal(A.fallbackId(ui),null);}
for(const status of ['expired','failed']) {sync(status);assert.equal(A.fallbackId(ui),'timeout');c.renderActionBar(game,false);assert.match(bar.innerHTML,/由算法代操作/);}
sync('expired');allowed=false;await c.fallbackAgentDecision('timeout');assert.equal(calls.length,0);
allowed=true;await c.fallbackAgentDecision('stale');assert.equal(calls.length,0);
game.turn='user';await c.fallbackAgentDecision('timeout');assert.equal(calls.length,0);game.turn='sakura';
const first=c.fallbackAgentDecision('timeout');assert.equal(calls.length,1);assert.equal(calls[0][0],'agent_fallback');assert.equal(ui.submitting,true);
await c.fallbackAgentDecision('timeout');assert.equal(calls.length,1,'double click cannot issue a second action');
resolve({ok:true,agentDecision:{id:'timeout',gameSeq:7,status:'cancelled'}});await first;
assert.equal(ui.paused,false);assert.equal(ui.driver,'agent');assert.equal(polls,1);assert.equal(A.fallbackId(ui),null);
sync('expired','second');const failed=c.fallbackAgentDecision('second');resolve({ok:false,error:'state changed'});await failed;
assert.equal(ui.paused,true);assert.equal(ui.error,'state changed');
for(const flags of [{moving:true},{modal:true},{queued:true},{transitioning:true},{mounting:true},{blackLocked:true},{tower:true}]) {
 assert.equal(P.snapshot(flags).allows('api','agent_fallback'),false,JSON.stringify(flags));
}
assert.equal(P.snapshot({blackActive:true}).allows('api','agent_fallback'),true);
assert.equal(P.snapshot({blackActive:true,modal:true,ordinaryModal:false}).allows('api','agent_fallback'),true,'current black choice modal does not block its own action');
assert.equal(P.snapshot({blackActive:true,blackLocked:true}).allows('api','agent_fallback'),false);
console.log('manual Agent fallback UI + presentation gates PASS');
