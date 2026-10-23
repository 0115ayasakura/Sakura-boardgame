/* Production modal/tab/auto functions executed without a browser or service. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const html = await readFile(new URL('../sakura_boardgame/board.html', import.meta.url), 'utf8');
const presentationSource = await readFile(new URL('../sakura_boardgame/art/lib/presentation_flow.js', import.meta.url), 'utf8');
function production(name) {
  const match = html.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^}`, 'm'));
  assert.ok(match, `production ${name} exists`);
  return match[0];
}
class Element {
  constructor() {
    this.style = {}; this.dataset = {}; this.textContent = ''; this.innerHTML = ''; this.children = []; this.attrs = {};
    const classes = new Set();
    this.classList = {add:(...xs)=>xs.forEach(x=>classes.add(x)), remove:(...xs)=>xs.forEach(x=>classes.delete(x)), contains:x=>classes.has(x)};
  }
  setAttribute(key,value) { this.attrs[key] = value; }
  appendChild(el) { this.children.push(el); }
  remove() {}
}
const nodes = new Map();
const $ = selector => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
let now = 0, timerId = 0, renders = 0, moves = 0, polls = 0, draws = 0;
const timers = new Map();
const addTimer = (fn,delay) => { const id = ++timerId; timers.set(id,{fn,delay}); return id; };
const state = {game:{kind:'monopoly'}, gameSeq:1, autoMode:'off', tab:'monopoly'};
const black = {active:()=>false, presentationActive:()=>false, inputLocked:()=>false};
const context = vm.createContext({
  $, state, cardQueue:[], uiEpoch:0, moveTimers:[], towerTimers:[], towerGeneration:0, towerBusy:false,
  towerDismissed:null, towerClimb:null, autoTimer:null,
  mapState:{board3d:{cancelMoves(){}, transitioning:false}},
  BlackTrainView:black, GAMES:{monopoly:'大富翁',gomoku:'五子棋'}, NAMES:{user:'玩家'},
  performance:{now:()=>now}, setTimeout:addTimer, setInterval:addTimer,
  clearTimeout:id=>timers.delete(id), clearInterval:id=>timers.delete(id),
  drawDieAt:()=>draws++, animateMove:()=>moves++, render:()=>renders++, openHelp(){}, esc:String,
  document:{createElement:()=>new Element(),
    querySelector:()=>[...nodes].find(([key,el])=>key.endsWith('-modal') && el.classList.contains('show'))?.[1] || null,
    querySelectorAll:selector=>selector === '#tabs [data-game-kind]' ? $('#tabs').children.filter(el=>el.dataset.gameKind)
      : [...nodes].filter(([key,el])=>key.endsWith('-modal') && el.classList.contains('show')).map(([,el])=>el)},
  api:async()=>({ok:true}), poll:async()=>{polls++;}, toast(){}
});
vm.runInContext(presentationSource,context);
for (const name of ['presentationSnapshot','stopDieAnimation','closeResultModal','resetOrdinaryModals','showCheck','showGameLanding','showDice',
  'boardMoveHeld','scheduleMove','towerLocked','resetTowerFlowUI','tabSwitchLocked','buildTabs','currentKind',
  'syncAutoModeSnapshot','toggleAutoMode','syncTabLocks','autoCanRun','scheduleAutoStep']) vm.runInContext(production(name),context);

// Both result callbacks formerly overwrote a new event after its predecessor settled.
for (const [show,modal,die,verdict,note] of [
  ['showGameLanding','#game-modal','#game-die','#game-verdict','#game-note'],
  ['showCheck','#check-modal','#check-die','#check-verdict','#check-note']
]) {
  const old = {player:'user',name:'旧事件',label:'旧道具',roll:1,won:false,success:false,threshold:4,sides:6,price:10,reward:'旧奖励',note:'旧效果'};
  const fresh = {...old,name:'新事件',label:'新道具',roll:6,won:true,success:true,reward:'新奖励',note:'新效果'};
  context[show](old);
  now += 900; timers.get($(die)._spin).fn();
  const oldTimer = $(die)._resultTimer, staleResult = timers.get(oldTimer).fn;
  context[show](fresh);
  assert.equal(timers.has(oldTimer),false,'reopen cancels predecessor result timer');
  staleResult();
  assert.equal($(note).textContent,'','even an already queued stale callback cannot write new modal');
  assert.equal($(verdict).textContent,show === 'showGameLanding' ? '掷骰中…' : '');
  now += 900; timers.get($(die)._spin).fn();
  const result = timers.get($(die)._resultTimer).fn;
  result();
  assert.match($(verdict).textContent,/6/);
  assert.match($(note).textContent,/新/);
  // Closing before settlement cancels the interval, and stale interval cannot enqueue results.
  context[show](old);
  const spinId = $(die)._spin, staleSpin = timers.get(spinId).fn;
  context.closeResultModal(modal,die);
  assert.equal(timers.has(spinId),false);
  const before = draws;
  now += 2000; staleSpin();
  assert.equal(draws,before);
  assert.equal($(die)._resultTimer,null);
  assert.equal($(modal).classList.contains('show'),false);
}
// New game/reset invalidates all three modal lifecycles plus automatic dice dismissal.
context.showGameLanding({player:'user',name:'旧',roll:2});
now += 900; timers.get($('#game-die')._spin).fn();
const staleGameResult = timers.get($('#game-die')._resultTimer).fn;
context.showCheck({player:'user',label:'旧',roll:2,threshold:3,sides:6});
context.showDice({player:'sakura',dice:3});
const staleAuto = timers.get(state.diceAutoTimer).fn;
context.scheduleMove({player:'user'});
const delayedMoveId = context.moveTimers.at(-1), staleMove = timers.get(delayedMoveId).fn;
context.resetTowerFlowUI();
state.gameSeq++;
assert.equal(timers.has(delayedMoveId),false,'60ms move start is registered for reset cancellation');
staleMove(); assert.equal(moves,0,'old game move never starts after reset');
assert.equal(state.diceAutoTimer,null);
assert.equal($('#big-die')._spin,null);
assert.equal($('#game-die')._resultTimer,null);
assert.equal($('#check-die')._spin,null);
context.showGameLanding({player:'user',name:'换局新事件',roll:5});
staleGameResult(); staleAuto();
assert.equal($('#game-verdict').textContent,'掷骰中…');
assert.equal($('#game-modal').classList.contains('show'),true);
context.resetOrdinaryModals();
context.scheduleMove({player:'user'});
const normalMove = timers.get(context.moveTimers.at(-1)).fn;
normalMove(); assert.equal(moves,1,'current game delayed movement still starts');

// Actual buildTabs handlers: prevent disposing the 3D renderer during movement.
const buttons = () => { $('#tabs').children=[]; context.buildTabs(); return $('#tabs').children; };
const staleButton = buttons()[1]; // Captured while switching was still allowed.
for (const key of ['visualMovePending','moveAnim','pendingMove','arrivalHold']) {
  state[key] = key === 'arrivalHold' ? 0 : {};
  const other = buttons()[1];
  assert.equal(other.attrs['aria-disabled'],'true');
  other.onclick(); staleButton.onclick();
  assert.equal(state.tab,'monopoly'); assert.equal(renders,0);
  state[key] = null;
}
for (const mode of ['transition','modal','tower','black','presentation','return']) {
  if (mode === 'transition') context.mapState.board3d.transitioning=true;
  if (mode === 'modal') $('#game-modal').classList.add('show');
  if (mode === 'tower') state.game.pending={type:'tower'};
  if (mode === 'black') black.active=()=>true;
  if (mode === 'presentation') black.presentationActive=()=>true;
  if (mode === 'return') black.inputLocked=()=>true;
  buttons()[1].onclick(); staleButton.onclick();
  assert.equal(state.tab,'monopoly',`${mode} blocks tab switching`);
  assert.equal(renders,0);
  context.mapState.board3d.transitioning=false; $('#game-modal').classList.remove('show');
  state.game.pending=null; black.active=black.presentationActive=black.inputLocked=()=>false;
}
staleButton.onclick(); assert.equal(state.tab,'gomoku'); assert.equal(renders,1);
buttons()[0].onclick(); assert.equal(state.tab,'monopoly'); assert.equal(renders,2);

// Auto mode must never become optimistic truth after rejection or a replaced game.
let resolveApi;
context.api=()=>new Promise(resolve=>{resolveApi=resolve;});
let pending=context.toggleAutoMode(); resolveApi({ok:false,status:'拒绝'}); await pending;
assert.equal(state.autoMode,'off');
pending=context.toggleAutoMode(); resolveApi({ok:true}); await pending;
assert.equal(state.autoMode,'hers');
state.autoMode='off'; pending=context.toggleAutoMode(); state.gameSeq++; resolveApi({ok:true}); await pending;
assert.equal(state.autoMode,'off','previous game success does not change replacement game');
pending=context.toggleAutoMode(); context.uiEpoch++; resolveApi({ok:true}); await pending;
assert.equal(state.autoMode,'off','reset epoch invalidates response even before new gameSeq arrives');
assert.equal(context.syncAutoModeSnapshot({version:7,autoMode:'hers'}),true);
assert.equal(state.autoMode,'hers');
assert.equal(context.syncAutoModeSnapshot({version:7,autoMode:'off'}),true);
assert.equal(state.autoMode,'off','same-version authoritative snapshot corrects auto state');
assert.equal(context.syncAutoModeSnapshot({version:7,autoMode:'off'}),false);
const pollStart=html.indexOf('async function poll()');
const versionBlock=html.indexOf('if (data.version !== state.version)',pollStart);
assert.ok(html.indexOf('syncAutoModeSnapshot(data)',pollStart)<versionBlock,'auto snapshot reconciliation is outside version gate');
assert.match(html,/scheduleMove\(data\.lastEvent\)/);
assert.match(html,/\$\("#game-modal"\)\.addEventListener\("click", \(\) => closeResultModal/);
assert.match(html,/\$\("#check-modal"\)\.addEventListener\("click", \(\) => closeResultModal/);
// Execute the real poll -> dice placeholder -> close -> movement pipeline, not a
// hand-built approximation: blocked Agent rolls legitimately return path:[37].
let snapshotData;
const played = [], settlements = [];
Object.assign(context, {
  pollBusy:false, pollAgain:false, moveRaf:0, cancelAnimationFrame(){},
  connection:{cancel(){}, state:async()=>({data:snapshotData}), schedule(){}},
  setRetryBusy(){}, syncAgentSnapshot:()=>false, syncTowerUI(){},
  consumeItemDraw(){}, showNextCard(){}, syncOrdinaryRollButtons(){},
  showCashFx:items=>settlements.push(items),
  BoardConnection:{ConnectionError:class extends Error {}},
  console:{warn(){}, error(...args){throw args.at(-1);}},
  render:()=>renders++
});
black.scheduleNotice=()=>null;
Object.assign(context.mapState, {use3D:true, iso:true,
  isoPos:new Map([[37,[10,20]],[38,[30,40]],[39,[50,60]]])});
context.mapState.board3d.playMove=(...args)=>played.push(args);
for (const name of ['poll','pieceAnchor','tokenPos','_anchor','isGateHop','startPendingMove','animateMove','endMoveHold']) {
  vm.runInContext(production(name),context);
}
context.resetOrdinaryModals();
state.game={kind:'monopoly', cells:Array.from({length:40},(_,id)=>({id,x:id,y:0})), positions:{user:37,sakura:37}};
state.version=20; state.pollCount=1; state.autoMode='off'; state.tab='monopoly';
state.pieceCell={sakura:{cell:37,ver:20}};
state.pendingMove=state.moveAnim=state.visualMovePending=state.arrivalHold=null;
snapshotData={version:21, gameSeq:state.gameSeq, game:state.game, autoMode:'off',
  lastEvent:{kind:'move',player:'sakura',dice:5,path:[37]}, cashFx:{seq:1,items:[{text:'+1'}]}};
await context.poll();
assert.equal($('#dice-modal').classList.contains('show'),true);
assert.equal(state.pendingMove,snapshotData.lastEvent);
assert.equal(state.visualMovePending,snapshotData.lastEvent);
assert.equal(state.moveAnim.player,'sakura');
assert.deepEqual(Array.from(state.moveAnim.current),[10,20],'production poll pins the single starting cell');
assert.equal(context.boardMoveHeld(),true);
assert.equal(settlements.length,0,'cash effect waits for arrival');
context.closeResultModal('#dice-modal','#big-die');
context.startPendingMove();
for (const key of ['pendingMove','moveAnim','visualMovePending','arrivalHold','heldFx']) {
  assert.equal(state[key],null,`single-cell settlement releases ${key}`);
}
assert.equal(state.pieceCell.sakura.cell,37);
assert.equal(state.pieceCell.sakura.ver,21);
assert.equal(settlements.length,1,'arrival effects released exactly once');
assert.equal(context.boardMoveHeld(),false);
for (const intent of ['entry','ordinary','tab']) {
  assert.equal(context.presentationSnapshot().allows(intent),true,`later ${intent} actions are unlocked`);
}
assert.equal(context.presentationSnapshot().allows('api','roll'),true,'later valid roll transport is unlocked');
assert.equal(played.length,0,'single-cell path does not start a fake 3D hop');
context.startPendingMove(); assert.equal(settlements.length,1,'repeated close is harmless');

// A subsequent real move still follows the old multi-node animation contract.
snapshotData={...snapshotData,version:22,lastEvent:{kind:'move',player:'user',path:[37,38,39]}};
await context.poll();
const nextMove=timers.get(context.moveTimers.at(-1)).fn;
nextMove();
assert.equal(played.length,1);
assert.equal(played[0][0],'user');
assert.deepEqual(Array.from(played[0][1],p=>p.u),[37,38,39]);
assert.equal(context.boardMoveHeld(),true,'multi-node movement stays held until arrival');
played[0][4](1); assert.equal(state.pieceCell.user.cell,38,'per-hop cell tracking is retained');
played[0][3]();
assert.equal(state.pieceCell.user.cell,39);
assert.equal(state.moveAnim,null);
assert.equal(state.visualMovePending,null);
assert.equal(context.boardMoveHeld(),false);

// Degenerate continuation after branch truncation also settles at the endpoint.
state.pieceCell.user={cell:39,ver:22};
state.visualMovePending={}; state.moveAnim={player:'user'};
context.animateMove({player:'user',path:[37,38,39]});
assert.equal(state.pieceCell.user.cell,39);
assert.equal(state.moveAnim,null); assert.equal(state.visualMovePending,null);
assert.equal(played.length,1);

// The DOM hop branch likewise remains asynchronous and tracks each landing.
context.mapState.use3D=false;
Object.assign(context.mapState,{viewport:{width:800,height:600},zoom:1,ox:0,oy:0});
Object.assign(context,{boardWrap:{querySelector:()=>null},applyTransform(){},
  HOP_MS:430,HOP_HEIGHT:34,PORTAL_OUT_MS:430,PORTAL_IN_MS:460});
delete state.pieceCell.user;
state.visualMovePending={};
context.animateMove({player:'user',path:[37,38,39]});
assert.equal(state.moveAnim.player,'user');
assert.equal(state.pieceCell.user.cell,37);
for (const cell of [38,39]) {
  now+=430;
  timers.get(context.moveTimers.at(-1)).fn();
  assert.equal(state.pieceCell.user.cell,cell);
}
assert.equal(state.moveAnim,null); assert.equal(state.visualMovePending,null);
assert.equal(context.boardMoveHeld(),false);

// Bad input is a no-op, never a blanket cleanup of a newer animation/hold.
const active={player:'sakura'}, visual={player:'sakura'}, pendingMove={player:'sakura'};
state.moveAnim=active; state.visualMovePending=visual; state.pendingMove=pendingMove; state.arrivalHold=38;
const liveTimer=addTimer(()=>{},16); context.moveTimers.push(liveTimer);
const heldFx=[{text:'new game'}]; state.heldFx=heldFx;
for (const ev of [null,{}, {path:[37]}, {player:'user'}, {player:'user',path:[]},
  {player:'user',path:'37'}, {player:'user',path:[999]}, {player:'user',path:[37,999]}]) {
  context.animateMove(ev);
  assert.equal(state.moveAnim,active); assert.equal(state.visualMovePending,visual);
  assert.equal(state.pendingMove,pendingMove); assert.equal(state.arrivalHold,38);
  assert.equal(state.heldFx,heldFx); assert.equal(timers.has(liveTimer),true);
}
const liveGame=state.game;
for (const game of [null,{}]) {
  state.game=game; context.animateMove({player:'user',path:[37]});
  assert.equal(state.moveAnim,active); assert.equal(state.visualMovePending,visual);
  assert.equal(state.heldFx,heldFx); assert.equal(timers.has(liveTimer),true);
}
state.game=liveGame;

// The original scheduleMove epoch/gameSeq guards must precede even single-cell
// cleanup: an already queued old callback cannot unlock a replacement game.
for (const invalidate of [()=>context.uiEpoch++,()=>state.gameSeq++]) {
  context.scheduleMove({player:'user',path:[37]});
  const stale=timers.get(context.moveTimers.at(-1)).fn;
  invalidate(); stale();
  assert.equal(state.moveAnim,active); assert.equal(state.visualMovePending,visual);
  assert.equal(state.pendingMove,pendingMove); assert.equal(state.arrivalHold,38);
  assert.equal(state.heldFx,heldFx); assert.equal(timers.has(liveTimer),true);
}
console.log('PASS production modal generations/cancellation, reset/delayed movement, guarded tab interaction and authoritative auto mode');
console.log('PASS production poll dice:[37] close/settle/unlock, subsequent multi-node 3D/DOM movement, truncated continuation, invalid input and stale epoch/gameSeq callbacks');
