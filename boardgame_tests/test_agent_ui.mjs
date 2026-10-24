import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const html=await readFile(new URL('../sakura_boardgame/board.html',import.meta.url),'utf8');
const source=await readFile(new URL('../sakura_boardgame/art/lib/agent_ui.js',import.meta.url),'utf8');
function production(name){const m=html.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^}`,'m'));assert.ok(m,name);return m[0];}
let allowed=true, resolveAction, actions=[], timers=[], renders=0, polls=0;
const bar={innerHTML:''};
const c=vm.createContext({state:{gameSeq:4,game:{kind:'monopoly',turn:'sakura'},autoMode:'hers'},uiEpoch:1,
  autoTimer:null, presentationSnapshot:()=>({blackLocked:false,allows:()=>allowed}),
  connection:{action:async(action,args)=>{actions.push([action,args]);return new Promise(r=>resolveAction=r);}},
  $:()=>bar,currentKind:()=> 'monopoly',NAMES:{sakura:'樱'},esc:s=>String(s??'').replace(/</g,'&lt;'),
  BlackTrainView:{active:()=>false,bind(){}}, clearTimeout(){},setTimeout:f=>{timers.push(f);return timers.length;},
  syncOrdinaryRollButtons(){},toast(){},poll:async()=>{polls++;}});
vm.runInContext(source,c);
for(const n of ['api','syncAgentSnapshot','requestSakuraAction','cancelAgentDecision','renderActionBar','autoCanRun','scheduleAutoStep'])vm.runInContext(production(n),c);
const A=c.AgentUI, s=c.state;
const snap=(status,id='a',more={})=>({gameSeq:4,decisionDriver:'agent',agentDecision:status?{id,status,gameSeq:4,version:8,expiresAt:Date.now()+10000,...more}:null});
c.syncAgentSnapshot(snap(null));
assert.equal(s.agentUI.driver,'agent');assert.equal(c.autoCanRun(false),true);
// The very first submission locks even before a queued snapshot exists.
const first=c.api('act_sakura');assert.equal(A.busy(s.agentUI),true);assert.match(bar.innerHTML,/正在思考/);
assert.equal((await c.api('act_sakura')).agentLocked,true);assert.equal((await c.api('place')).agentLocked,true);
assert.equal(c.autoCanRun(false),false);assert.equal(actions.length,1);
resolveAction({ok:true,agentDecision:snap('queued').agentDecision});await first;
assert.equal(A.busy(s.agentUI),true);assert.match(bar.innerHTML,/取消本次决策/);
c.syncAgentSnapshot(snap('thinking'));assert.equal(c.autoCanRun(false),false);
// Cancel is id-bound and reachable while all ordinary presentation is locked.
allowed=false;assert.equal((await c.api('cancel_agent',{id:'wrong'})).agentLocked,true);
const cancelling=c.cancelAgentDecision('a');assert.equal(actions.at(-1)[0],'cancel_agent');assert.equal(actions.at(-1)[1].id,'a');
assert.equal(s.agentUI.cancelling,true);resolveAction({ok:true,agentDecision:snap('cancelled').agentDecision});await cancelling;
assert.equal(s.agentUI.paused,true);assert.equal(c.autoCanRun(false),false);assert.match(bar.innerHTML,/手动重试/);
assert.equal((await c.api('act_sakura')).agentLocked,true);
allowed=true;
s.game.turn='user';c.renderActionBar(s.game,false);assert.match(bar.innerHTML,/data-ordinary-roll/,'a cancellation/commit race cannot hide the next user turn');
const beforeUserRetry=actions.length;await c.requestSakuraAction();assert.equal(actions.length,beforeUserRetry);
s.game.turn='sakura';
// Failure state changes must sync even with the same game version.
for(const status of ['failed','expired']){
  c.syncAgentSnapshot({...snap(status,status,{error:'宿主忙 <失败>'}),version:8});
  c.renderActionBar(s.game,false);assert.match(bar.innerHTML,/宿主忙 &lt;失败>/);assert.equal(c.autoCanRun(false),false);
  c.scheduleAutoStep(false);assert.equal(timers.length,0);
}
const retry=c.requestSakuraAction();assert.equal(A.busy(s.agentUI),true);assert.equal(s.agentUI.paused,false);
// Same failed snapshot arriving during explicit retry must not relatch the failure.
c.syncAgentSnapshot(snap('expired','expired',{error:'宿主忙'}));assert.equal(s.agentUI.paused,false);
resolveAction({ok:true,agentDecision:snap('queued','b').agentDecision});await retry;
const countBeforeStaleCancel=actions.length;await c.cancelAgentDecision('a');assert.equal(actions.length,countBeforeStaleCancel);
// Commit permits next auto action only after existing visual gate has cleared.
c.syncAgentSnapshot(snap('committed','b'));allowed=false;assert.equal(c.autoCanRun(false),false);
allowed=true;assert.equal(c.autoCanRun(false),true);
c.scheduleAutoStep(false);assert.equal(timers.length,1);
// Timer callback rechecks authoritative busy state, including black's independent sends.
c.syncAgentSnapshot(snap('thinking','c'));await timers.pop()();assert.equal(actions.length,3);
// Explicit dispatch failure/host busy pauses rather than automatic fallback/retry.
c.syncAgentSnapshot(snap(null));const bad=c.api('act_sakura');resolveAction({ok:false,error:'宿主 busy'});await bad;
assert.equal(s.agentUI.paused,true);assert.equal(c.autoCanRun(false),false);
c.syncAgentSnapshot(snap(null));assert.equal(s.agentUI.paused,true,'null state cannot clear user-retry requirement');
// An old in-flight response cannot mutate the next game's UI.
A.retry(s.agentUI);const old=c.api('act_sakura');s.gameSeq=5;c.uiEpoch++;
c.syncAgentSnapshot({gameSeq:5,decisionDriver:'algorithm',agentDecision:null});
resolveAction({ok:false,error:'old failure'});await old;
assert.equal(s.agentUI.paused,false);assert.equal(s.agentUI.driver,'algorithm');
c.renderActionBar(s.game,false);assert.match(bar.innerHTML,/算法模式（非 Agent）/);
assert.doesNotMatch(html,/不经过模型/);
assert.ok(html.indexOf('syncAgentSnapshot(data)')<html.indexOf('if (data.version !== state.version)'));
assert.match(html,/src="\/art\/lib\/agent_ui.js"/);
// Execute the full production poll twice at the same game version. Agent-only
// updates must refresh controls rather than getting swallowed by version gating.
let polledDecision='queued';
Object.assign(s,{version:8,gameSeq:5,pollCount:1});
Object.assign(c,{document:{querySelectorAll:()=>[]},pollBusy:false,pollAgain:false,setRetryBusy(){},location:{reload(){assert.fail('unexpected reload');}},
  towerLocked:()=>false,consumeItemDraw(){},showNextCard(){},syncTowerUI(){},syncPlaySessionUI(){},
  render(){renders++;c.renderActionBar(s.game,false);},
  BoardConnection:{ConnectionError:class extends Error{}},console:{error(...x){assert.fail(String(x));}}});
c.BlackTrainView.scheduleNotice=()=>null;
c.connection.cancel=()=>{};c.connection.schedule=()=>{};
c.connection.state=async()=>({changed:false,data:{version:8,gameSeq:5,game:s.game,autoMode:'hers',decisionDriver:'agent',
  agentDecision:{id:'poll',gameSeq:5,status:polledDecision,error:polledDecision==='failed'?'host busy':undefined}}});
for(const name of ['syncAutoModeSnapshot','tabSwitchLocked','syncTabLocks','poll'])vm.runInContext(production(name),c);
await c.poll();assert.equal(A.busy(s.agentUI),true);assert.match(bar.innerHTML,/取消本次决策/);
polledDecision='failed';await c.poll();assert.equal(s.version,8);assert.equal(s.agentUI.paused,true);
assert.match(bar.innerHTML,/host busy/);assert.equal(renders,2);
// Backend statuses are not inferred from wall time: only backend may expire/commit.
const ui=A.create(5);A.sync(ui,{gameSeq:5,agentDecision:{id:'z',gameSeq:5,status:'thinking',expiresAt:0}});
assert.equal(A.busy(ui),true);
for (const script of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
  if(script[2] && !/importmap/.test(script[1])) new vm.Script(`(async()=>{${script[2]}\n})`);
}
console.log('agent UI lifecycle + actual board API/actions/auto tests PASS');
