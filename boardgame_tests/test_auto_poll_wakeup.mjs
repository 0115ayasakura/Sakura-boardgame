import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const html = fs.readFileSync('sakura_boardgame/board.html', 'utf8');
const agentSource = fs.readFileSync('sakura_boardgame/art/lib/agent_ui.js', 'utf8');
const flowSource = fs.readFileSync('sakura_boardgame/art/lib/presentation_flow.js', 'utf8');
const blackSource = fs.readFileSync('sakura_boardgame/art/lib/black_train_view.js', 'utf8');
function production(name) {
  const match = html.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^}`, 'm'));
  assert.ok(match, `production function ${name}`);
  return match[0];
}
function harness() {
  let id = 0, modalShown = false, renders = 0, actionResult;
  const timers = new Map(), actions = [], busySent = [];
  const modal = {id:'card-modal'}, bar = {innerHTML:'',querySelectorAll:()=>[],querySelector:()=>null};
  const tabs = {children:[], set innerHTML(value){this.children=[];}, appendChild(btn){this.children.push(btn);}};
  const s = {version:8, gameSeq:4, pollCount:1, autoMode:'hers', previewBlackTrain:false,
    game:{kind:'monopoly', turn:'sakura'}};
  const c = vm.createContext({state:s, uiEpoch:1, autoTimer:null, pollBusy:false, pollAgain:false,
    towerBusy:false, mapState:{}, cardQueue:[], document:{
      querySelector:selector => selector === '.modal.show' && modalShown ? modal : null,
      querySelectorAll:selector => selector === '#tabs [data-game-kind]' ? tabs.children.filter(btn=>btn.dataset.gameKind)
        : selector === '.modal.show' && modalShown ? [modal] : [],
      createElement:()=>({dataset:{},classList:{add(){}},setAttribute(key,value){this[key]=value;}})},
    $:selector=>selector==='#tabs'?tabs:bar, GAMES:{monopoly:'地产',gomoku:'五子棋'}, openHelp(){}, NAMES:{sakura:'樱'}, esc:String,
    BlackTrainView:{active:()=>false, presentationActive:()=>false, inputLocked:()=>false,
      bind(){}, scheduleNotice:()=>null},
    setTimeout(fn, ms){const key=++id;timers.set(key,{fn,ms});return key;},
    clearTimeout(key){timers.delete(key);},
    setRetryBusy(){}, consumeItemDraw(){}, showNextCard(){}, syncTowerUI(){}, toast(){},
    render(){renders++;c.renderActionBar(s.game, false);},
    location:{reload(){assert.fail('unexpected reload');}},
    BoardConnection:{ConnectionError:class extends Error{}},
    console:{error(...args){assert.fail(args.join(' '));}},
    connection:{cancel(){}, schedule(){},
      state:async busy => {busySent.push(busy);return {changed:false,data:{version:s.version,gameSeq:s.gameSeq,
        game:s.game,autoMode:s.autoMode,agentDecision:s.agentUI.decision}};},
      action:async (action,args) => {
        actions.push([action,args]);
        return await actionResult;
      }}
  });
  vm.runInContext(agentSource,c);vm.runInContext(flowSource,c);
  vm.runInContext(blackSource,c);
  // Real production active/actions/bind and lifecycle lock queries; mutable facade
  // only so individual tests can inject a locked lifecycle without a browser.
  c.BlackTrainView={...c.BlackTrainView};
  s.agentUI=c.AgentUI.create(s.gameSeq);
  for(const name of ['boardMoveHeld','towerLocked','presentationSnapshot','api','syncAgentSnapshot',
    'syncAutoModeSnapshot','syncOrdinaryRollButtons','renderActionBar','autoCanRun','scheduleAutoStep','poll',
    'tabSwitchLocked','buildTabs','syncTabLocks','currentKind']) {
    vm.runInContext(production(name),c);
  }
  return {c,s,timers,actions,busySent,tabs,
    get renders(){return renders;},
    showModal(value){modalShown=value;},
    result(value){actionResult=value;},
    async fire(){
      assert.equal(timers.size,1,'exactly one pending auto timer');
      const [key,{fn,ms}]=[...timers][0];assert.equal(ms,1800);
      timers.delete(key);fn();
      // Timer callbacks intentionally do not return their API/poll promise.
      for(let i=0;i<12;i++)await Promise.resolve();
    }};
}

// RED on the original: timer sees a local modal, then same-version poll never
// calls renderActionBar again. Execute the actual production poll, not a model.
{
  const h=harness();h.c.renderActionBar(h.s.game,false);
  assert.equal(h.timers.size,1);
  h.showModal(true);await h.fire();assert.equal(h.actions.length,0);assert.equal(h.timers.size,0);
  h.showModal(false);await h.c.poll();
  assert.equal(h.s.version,8);assert.equal(h.renders,0,'unchanged snapshot must not rebuild board');
  assert.deepEqual(h.busySent,[false]);
  assert.equal(h.timers.size,1,'unlocked same-version poll must recover lost auto wakeup');
  await h.c.poll();await h.c.poll();assert.equal(h.timers.size,1,'polls must coalesce');
  // A pending network request is protected by the real AgentUI submitting gate.
  let resolve;h.result(new Promise(r=>resolve=r));await h.fire();
  assert.equal(h.actions.length,1);assert.equal(h.actions[0][0],'act_sakura');
  await h.c.poll();await h.c.poll();assert.equal(h.timers.size,0);
  assert.equal(h.actions.length,1,'no duplicate act while transport is unresolved');
  resolve({ok:true,agentDecision:{id:'a',status:'queued',gameSeq:4}});
  for(let i=0;i<12;i++)await Promise.resolve();
  await h.c.poll();assert.equal(h.timers.size,0);assert.equal(h.actions.length,1);
}
console.log('PASS production timer-busy -> unlock -> same-version poll wakeup; single timer/action');

// Reconciliation must obey manual recovery and every current presentation gate.
for(const status of ['failed','expired','cancelled','queued','thinking']) {
  const h=harness();h.c.syncAgentSnapshot({gameSeq:4,agentDecision:{id:status,status,gameSeq:4}});
  await h.c.poll();assert.equal(h.timers.size,0,status);assert.equal(h.actions.length,0);
}
for(const gate of ['paused','modal','moving','queued-card','transition','mounting','tower','black','demo','off']) {
  const h=harness();
  if(gate==='paused')h.s.agentUI.paused=true;
  if(gate==='modal')h.showModal(true);
  if(gate==='moving')h.s.pendingMove={};
  if(gate==='queued-card')h.c.cardQueue.push(['unread']);
  if(gate==='transition')h.c.mapState.board3d={transitioning:true};
  if(gate==='mounting')h.c.mapState.glMounting=true;
  if(gate==='tower')h.s.game.pending={type:'tower'};
  if(gate==='black')h.c.BlackTrainView.inputLocked=()=>true;
  if(gate==='demo')h.s.tab='gomoku';
  if(gate==='off')h.s.autoMode='off';
  await h.c.poll();assert.equal(h.timers.size,0,gate);assert.equal(h.actions.length,0);
}
{
  const h=harness();h.result({ok:false,uncertain:true,error:'unknown mutation outcome'});
  await h.c.poll();await h.fire();assert.equal(h.s.agentUI.paused,true);
  await h.c.poll();await h.c.poll();assert.equal(h.timers.size,0);assert.equal(h.actions.length,1);
}
{
  const h=harness();
  h.c.connection.action=async()=>{h.actions.push(['act_sakura']);throw new Error('unknown transport outcome');};
  await assert.rejects(h.c.api('act_sakura'),/unknown transport outcome/);
  assert.equal(h.s.agentUI.paused,true);
  await h.c.poll();await h.c.poll();assert.equal(h.timers.size,0);assert.equal(h.actions.length,1);
}
console.log('PASS failed/expired/cancelled/paused, in-flight Agent, presentation/demo/off and uncertain mutation guards');

// Old scheduled callbacks cannot act in another epoch/game, even if its turn
// happens to be auto-eligible. Lifecycle cleanup is not our only defense.
for(const changed of ['uiEpoch','gameSeq']) {
  const h=harness();h.c.scheduleAutoStep(false);
  if(changed==='uiEpoch')h.c.uiEpoch++;else h.s.gameSeq++;
  await h.fire();assert.equal(h.actions.length,0,`stale ${changed} timer must not dispatch`);
}
console.log('PASS epoch/gameSeq isolation');

{
  const h=harness();h.showModal(true);h.c.buildTabs();
  const [live,other,help]=h.tabs.children;
  assert.equal(live['aria-disabled'],'false');assert.equal(other['aria-disabled'],'true');
  assert.match(other.title,/等待/);
  h.showModal(false);await h.c.poll();
  assert.equal(h.renders,0);assert.equal(other['aria-disabled'],'false');assert.equal(other.title,'');
  assert.equal(h.tabs.children[1],other,'poll updates tab properties without replacing focused DOM');
  h.showModal(true);await h.c.poll();assert.equal(other['aria-disabled'],'true');
  other.onclick();assert.equal(h.s.tab,undefined,'fresh click guard still rejects locked switch');
  assert.equal(help['aria-disabled'],undefined,'help is not a game switch');
}
console.log('PASS same-version poll tab lock metadata refresh with live click guard and stable DOM');

function blackHarness() {
  const h=harness();
  h.s.gameSeq=7;h.s.version=136;h.s.game.pending=null;h.s.game.black_train={active:true};
  h.s.agentUI=h.c.AgentUI.create(7);
  h.s.agentUI.decision={id:'old-committed',gameSeq:7,status:'committed',version:133};
  return h;
}
function continuationHarness(type = 'route') {
  const h=blackHarness();h.s.version=137;
  h.s.game.pending=type==='route'
    ? {player:'sakura',type:'route',options:[56,18],steps_left:1}
    : {player:'sakura',type:'dice',options:[1,2,3,4,5,6]};
  if(type==='route')h.s.game.walk={player:'sakura',steps_left:1,path:[55]};
  return h;
}
// v136's successful roll produced v137's route pause. Both route and custom-die
// choices belong to the board continuation, not the black-event-only FSM.
for(const type of ['route','dice']) {
  const h=continuationHarness(type);
  assert.equal(h.c.BlackTrainView.pending(h.s.game),null,`${type} has no black-event FSM owner`);
  assert.equal(h.c.autoCanRun(false),true,`v137 ${type} continuation must be auto eligible`);
  h.c.renderActionBar(h.s.game,false);await h.c.poll();await h.c.poll();
  assert.equal(h.timers.size,1,`${type} render/polls coalesce into one timer`);
  let resolve;h.result(new Promise(r=>resolve=r));await h.fire();
  assert.equal(h.actions.length,1);assert.equal(h.actions[0][0],'act_sakura');
  await h.c.poll();assert.equal(h.timers.size,0,`${type} submitting blocks duplicates`);
  resolve({ok:true,agentDecision:{id:`black-${type}`,status:'queued',gameSeq:7}});
  for(let i=0;i<12;i++)await Promise.resolve();
  await h.c.poll();assert.equal(h.actions.length,1);assert.equal(h.timers.size,0);
}
console.log('PASS gameSeq7/v137 route [56,18]/remaining1 and dice: real render/poll/timer/API -> one request');
for(const type of ['route','dice']) {
  for(const gate of ['user-pending','user-turn','unknown-pending','unknown-kind','kind-black-event','type-black-event',
    'empty-options','missing-options','walk-owner','walk-steps','walk-missing','walk-orphan',
    'modal','black-lock','moving','queued-card','transition','mounting','paused','submitting',
    'queued','thinking','off','demo']) {
    const h=continuationHarness(type);
    if(gate==='user-pending')h.s.game.pending.player='user';
    if(gate==='user-turn')h.s.game.turn='user';
    if(gate==='unknown-pending')h.s.game.pending.type='future-choice';
    if(gate==='unknown-kind')h.s.game.pending.kind='future-choice';
    if(gate==='kind-black-event')h.s.game.pending.kind='black_train';
    if(gate==='type-black-event')h.s.game.pending.type='black_train';
    if(gate==='empty-options')h.s.game.pending.options=[];
    if(gate==='missing-options')delete h.s.game.pending.options;
    if(gate==='walk-owner')h.s.game.walk={player:'user',steps_left:1};
    if(gate==='walk-steps')h.s.game.walk={player:'sakura',steps_left:2};
    if(gate==='walk-missing') {
      if(type==='dice')h.s.game.walk={player:'sakura',steps_left:1};
      else delete h.s.game.walk;
    }
    if(gate==='walk-orphan')delete h.s.game.pending;
    if(gate==='modal')h.showModal(true);
    if(gate==='black-lock')h.c.BlackTrainView.inputLocked=()=>true;
    if(gate==='moving')h.s.visualMovePending={};
    if(gate==='queued-card')h.c.cardQueue.push(['unread']);
    if(gate==='transition')h.c.mapState.board3d={transitioning:true};
    if(gate==='mounting')h.c.mapState.glMounting=true;
    if(gate==='paused')h.s.agentUI.paused=true;
    if(gate==='submitting')h.s.agentUI.submitting=true;
    if(['queued','thinking'].includes(gate))h.s.agentUI.decision={status:gate};
    if(gate==='off')h.s.autoMode='off';
    // A dice with no pending and no walk is intentionally a legal fresh roll.
    if(gate==='walk-orphan'&&type==='dice')h.s.game.walk={player:'sakura',steps_left:1};
    if(gate==='demo')h.s.tab='gomoku';
    if(gate==='demo')h.c.scheduleAutoStep(true);else await h.c.poll();
    assert.equal(h.timers.size,0,`${type}/${gate}`);assert.equal(h.actions.length,0,`${type}/${gate}`);
  }
  for(const changed of ['uiEpoch','gameSeq','pending-user','pending-event','walk','paused','locked']) {
    const h=continuationHarness(type);h.c.scheduleAutoStep(false);
    if(changed==='uiEpoch')h.c.uiEpoch++;
    if(changed==='gameSeq')h.s.gameSeq++;
    if(changed==='pending-user')h.s.game.pending.player='user';
    if(changed==='pending-event')h.s.game.pending={type:'black_train',player:'sakura'};
    if(changed==='walk')h.s.game.walk={player:'user',steps_left:1};
    if(changed==='paused')h.s.agentUI.paused=true;
    if(changed==='locked')h.c.BlackTrainView.inputLocked=()=>true;
    await h.fire();assert.equal(h.actions.length,0,`${type} stale timer rechecks ${changed}`);
  }
}
for(const steps of [0,-1,1.5,'1',null,undefined]) {
  const h=continuationHarness();h.s.game.walk.steps_left=steps;h.s.game.pending.steps_left=steps;
  await h.c.poll();assert.equal(h.timers.size,0,`invalid route remaining steps ${steps}`);
}
for(const type of ['route','dice']) {
  const h=continuationHarness(type);
  h.result({ok:false,uncertain:true,error:'unknown continuation outcome'});
  await h.c.poll();await h.fire();assert.equal(h.s.agentUI.paused,true);
  await h.c.poll();await h.c.poll();assert.equal(h.actions.length,1);assert.equal(h.timers.size,0);
}
console.log('PASS route/dice ownership, malformed/unknown pending, walk matching, visual/Agent gates, uncertain outcomes and stale timer matrix');

// Exercise the real black lifecycle too: it must create zero event timers for
// route/dice, and retain exclusive dispatch for both black event discriminators.
for(const type of ['route','dice','black_train','kind-black_train']) {
  const h=continuationHarness(type==='dice'?'dice':'route');
  if(type==='black_train')h.s.game.pending={type:'black_train',player:'sakura',choices:['wait']};
  if(type==='kind-black_train')h.s.game.pending={kind:'black_train',player:'sakura',choices:['wait']};
  let eventShown=false;
  const modal={id:'black-train-event-modal',classList:{toggle(_name,on){eventShown=on;},remove(){eventShown=false;}}};
  const eventCard={innerHTML:'',querySelectorAll:()=>[],querySelector:()=>null};
  const query=h.c.document.querySelector, queryAll=h.c.document.querySelectorAll;
  h.c.document.body={classList:{toggle(){}}};
  h.c.document.querySelector=selector=>selector==='#black-train-event-modal'?modal
    : selector==='#black-train-event-card'?eventCard
    : selector==='.modal.show'?(eventShown?modal:null):query(selector);
  h.c.document.querySelectorAll=selector=>selector==='.modal.show'?(eventShown?[modal]:[]):queryAll(selector);
  h.result({ok:true,agentDecision:{id:'black-fsm',status:'queued',gameSeq:7}});
  const sync=()=>h.c.BlackTrainView.sync({game:h.s.game,document:h.c.document,gameKey:7,
    names:{sakura:'樱'},send:action=>h.c.api(action)});
  sync();h.c.renderActionBar(h.s.game,false);sync();await h.c.poll();
  assert.equal(h.timers.size,1,`${type}: exactly one owner timer`);
  const event=type==='black_train'||type==='kind-black_train';
  const [key,timer]=[...h.timers][0];
  assert.equal(timer.ms,event?1400:1800,`${type}: ${event?'FSM':'board'} owns dispatch`);
  h.timers.delete(key);await timer.fn();
  for(let i=0;i<12;i++)await Promise.resolve();
  assert.equal(h.actions.length,1);assert.equal(h.actions[0][0],'act_sakura');
  sync();await h.c.poll();assert.equal(h.timers.size,0,`${type}: no competing retry`);
}
console.log('PASS real BlackTrainView.sync: route/dice board-only 1800ms; kind/type black_train FSM-only 1400ms');
// Exact reported state: role roll has no owner inside BlackTrainView (only its
// pending-event FSM auto-selects). Exercise production render, policy, timer/API.
{
  const h=blackHarness();
  assert.equal(h.c.presentationSnapshot().phase,'black-active');
  assert.equal(h.c.autoCanRun(false),true,'unlocked black role roll must be auto eligible');
  h.c.renderActionBar(h.s.game,false);
  assert.equal(h.timers.size,1,'black action-bar branch must schedule the role roll');
  h.c.renderActionBar(h.s.game,false);await h.c.poll();await h.c.poll();
  assert.equal(h.timers.size,1,'render and unchanged polls share one timer');
  let resolve;h.result(new Promise(r=>resolve=r));await h.fire();
  assert.equal(h.actions.length,1);assert.equal(h.actions[0][0],'act_sakura');
  await h.c.poll();assert.equal(h.timers.size,0,'in-flight role request blocks duplicates');
  resolve({ok:true,agentDecision:{id:'black-role',status:'queued',gameSeq:7}});
  for(let i=0;i<12;i++)await Promise.resolve();
  await h.c.poll();assert.equal(h.actions.length,1);assert.equal(h.timers.size,0);
}
console.log('PASS gameSeq7/v136 black-active role roll: production render/poll -> one Agent request');
for(const gate of ['user','pending-role','pending-user','dice','route','walk','modal','unread-result',
  'transition','mounting','movement','queued-card','tower','paused','submitting','queued','thinking','off','demo','finished']) {
  const h=blackHarness();
  if(gate==='user')h.s.game.turn='user';
  if(['pending-role','pending-user','dice','route'].includes(gate))h.s.game.pending={
    type:gate==='dice'||gate==='route'?gate:'black_train',player:gate==='pending-user'?'user':'sakura'};
  if(gate==='walk')h.s.game.walk={};
  if(gate==='modal')h.showModal(true);
  if(gate==='unread-result')h.c.BlackTrainView.inputLocked=()=>true;
  if(gate==='transition')h.c.mapState.board3d={transitioning:true};
  if(gate==='mounting')h.c.mapState.glMounting=true;
  if(gate==='movement')h.s.visualMovePending={};
  if(gate==='queued-card')h.c.cardQueue.push(['unread']);
  if(gate==='tower')h.s.game.pending={type:'tower'};
  if(gate==='paused')h.s.agentUI.paused=true;
  if(gate==='submitting')h.s.agentUI.submitting=true;
  if(gate==='queued'||gate==='thinking')h.s.agentUI.decision={status:gate};
  if(gate==='off')h.s.autoMode='off';
  if(gate==='demo')h.s.tab='gomoku';
  if(gate==='finished')h.s.game.winner='sakura';
  if(gate==='demo')h.c.scheduleAutoStep(true);else await h.c.poll();
  assert.equal(h.timers.size,0,gate);assert.equal(h.actions.length,0,gate);
}
for(const changed of ['uiEpoch','gameSeq','pending','paused','locked','transition']) {
  const h=blackHarness();h.c.scheduleAutoStep(false);
  if(changed==='uiEpoch')h.c.uiEpoch++;
  if(changed==='gameSeq')h.s.gameSeq++;
  if(changed==='pending')h.s.game.pending={type:'black_train',player:'sakura'};
  if(changed==='paused')h.s.agentUI.paused=true;
  if(changed==='locked')h.c.BlackTrainView.inputLocked=()=>true;
  if(changed==='transition')h.c.mapState.board3d={transitioning:true};
  await h.fire();assert.equal(h.actions.length,0,`black timer rechecks ${changed}`);
}
{
  const h=blackHarness();h.result({ok:false,uncertain:true,error:'unknown black mutation outcome'});
  await h.c.poll();await h.fire();assert.equal(h.s.agentUI.paused,true);
  await h.c.poll();assert.equal(h.actions.length,1);assert.equal(h.timers.size,0);
}
console.log('PASS black auto permissions: black-event pending stays FSM-owned; locks/epoch/gameSeq/paused/unknown remain closed');
