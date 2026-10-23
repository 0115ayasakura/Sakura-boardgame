/* Shared policy and actual board adapters. No browser/service/deployment. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const moduleSource = await readFile(new URL('../sakura_boardgame/art/lib/presentation_flow.js',import.meta.url),'utf8');
const html = await readFile(new URL('../sakura_boardgame/board.html',import.meta.url),'utf8');
const agentSource = await readFile(new URL('../sakura_boardgame/art/lib/agent_ui.js',import.meta.url),'utf8');
const context = vm.createContext({});
vm.runInContext(moduleSource,context);
vm.runInContext(agentSource,context);
const P = context.PresentationFlow;
const idle=P.snapshot();
assert.equal(idle.phase,'idle');
for (const intent of ['ordinary','auto','tab','entry','card']) assert.equal(idle.allows(intent),true);
assert.equal(idle.allows('api','roll'),true);
assert.equal(idle.allows('typo'),false);
assert.equal(Object.isFrozen(idle),true);
for (const [flag,phase] of [['moving','movement'],['modal','modal'],['queued','queued'],['tower','tower'],
  ['towerBusy','tower'],['transitioning','transition'],['mounting','mounting'],['blackActive','black-active'],
  ['blackVisible','black-active'],['blackLocked','black-resolution']]) {
  const s=P.snapshot({[flag]:true});
  assert.equal(s.phase,phase);
  for (const intent of ['ordinary','auto','tab']) assert.equal(s.allows(intent),false,`${flag}/${intent}`);
  assert.notEqual(s.reason,'');
  if (flag!=='blackActive') assert.equal(s.allows('api','roll'),false,`${flag} blocks ordinary roll`);
}
assert.equal(P.snapshot({blackActive:true,blackVisible:true}).allows('auto','black-roll'),true);
assert.equal(P.snapshot({blackVisible:true}).allows('auto','black-roll'),false,'presentation alone cannot authorize a role roll');
for (const flag of ['moving','modal','queued','tower','towerBusy','blackLocked','transitioning','mounting'])
  assert.equal(P.snapshot({blackActive:true,blackVisible:true,[flag]:true}).allows('auto','black-roll'),false,`black role roll blocks ${flag}`);
for (const flag of ['moving','modal','queued','tower']) assert.equal(P.snapshot({[flag]:true}).allows('entry'),false);
for (const flag of ['blackLocked','blackActive','blackVisible','mounting','transitioning'])
  assert.equal(P.snapshot({[flag]:true}).allows('entry'),true,`entry must not wait on its own ${flag}`);
for (const action of ['roll','decide','act_sakura']) assert.equal(P.snapshot({blackActive:true}).allows('api',action),true);
for (const action of ['place','undo','start']) assert.equal(P.snapshot({blackActive:true}).allows('api',action),false);
for (const action of ['set_piece','set_auto_mode','set_style']) assert.equal(P.snapshot({modal:true}).allows('api',action),true);
assert.equal(P.snapshot({modal:true,blackLocked:true}).allows('api','set_piece'),false,'return lock remains absolute');
assert.equal(P.snapshot({tower:true,towerBusy:true}).allows('api','tower_step'),true,'tower may send its own in-flight request');
assert.equal(P.snapshot({tower:true,towerBusy:true}).allows('tower'),false,'but cannot start a second request');
assert.equal(P.snapshot({tower:true,modal:true}).allows('api','tower_finish'),false);
for (const key of ['visualMovePending','moveAnim','pendingMove','arrivalHold']) {
  assert.equal(P.movementHeld({[key]:key==='arrivalHold'?0:{}}),true);
}
assert.equal(P.movementHeld({arrivalHold:null}),false);
const canceled=[],die={_spin:3,_resultTimer:4};
assert.equal(P.cancelDie(die,{clearInterval:id=>canceled.push(id),clearTimeout:id=>canceled.push(id)}),1);
assert.deepEqual(canceled,[3,4]); assert.equal(die._spin,null);assert.equal(die._resultTimer,null);
assert.equal(P.cancelDie(die,{clearInterval(){assert.fail();},clearTimeout(){assert.fail();}}),2);

function production(name) {
  const match=html.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^}`,'m'));
  assert.ok(match,`production ${name} exists`);return match[0];
}
class Element {
  constructor(){this.style={};this.textContent='';this.innerHTML='';this.title='';this.disabled=false;
    const classes=new Set();this.classList={add:(...xs)=>xs.forEach(x=>classes.add(x)),remove:(...xs)=>xs.forEach(x=>classes.delete(x)),
      contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)};}
}
const nodes=new Map();const $=s=>{if(!nodes.has(s))nodes.set(s,new Element());return nodes.get(s);};
let modal=false, modalIds=['game-modal'], blackActive=false, blackVisible=false, blackLocked=false;
let calls=[],polls=0,now=5000;
const normal=new Element(),custom=new Element();
Object.assign(context,{
  state:{gameSeq:1,game:{kind:'monopoly',turn:'user',flags:{user:{pick_dice:true}}},autoMode:'hers'},
  cardQueue:[],cardTimer:null,towerBusy:false,mapState:{glMounting:false,board3d:{transitioning:false}},
  BlackTrainView:{active:()=>blackActive,presentationActive:()=>blackVisible,inputLocked:()=>blackLocked},
  document:{querySelector:s=>s==='.modal.show'?(modal?{id:modalIds[0]}:null):s==='[data-ordinary-roll]'?normal:s==='[data-custom-die]'?custom:null,
    querySelectorAll:()=>modal?modalIds.map(id=>({id})):[]},
  $,NAMES:{user:'玩家'},performance:{now:()=>now},naviLogPrefix:()=>'',
  setTimeout:()=>1,clearTimeout(){},connection:{action:async(action,args)=>{calls.push([action,args]);return {ok:true};}},
  uiEpoch:0, autoTimer:null, renderActionBar(){},
  toast(){},poll:async()=>{polls++;},syncBlackTrainUI(){},closeCard(){}
});
for(const name of ['boardMoveHeld','presentationSnapshot','towerLocked','towerCanAct','tabSwitchLocked',
  'api','syncOrdinaryRollButtons','useCustomDie','autoCanRun','showNextCard','ordinaryPresentationPending'])
  vm.runInContext(production(name),context);
const state=context.state;
for(const flag of ['visualMovePending','moveAnim','pendingMove','arrivalHold']){
  state[flag]=flag==='arrivalHold'?0:{};
  context.syncOrdinaryRollButtons();assert.equal(normal.disabled,true);assert.equal(custom.disabled,true);
  assert.equal(context.tabSwitchLocked(),true);
  assert.equal(context.ordinaryPresentationPending(),true);
  assert.equal((await context.api('roll')).presentationLocked,true);
  assert.equal((await context.api('decide')).presentationLocked,true);
  await context.useCustomDie();assert.equal(calls.length,0);
  state[flag]=null;
}
for(const flag of ['modal','transition','mount','queued','blackLock','blackVisible']){
  modal=flag==='modal';context.mapState.board3d.transitioning=flag==='transition';context.mapState.glMounting=flag==='mount';
  if(flag==='queued')state.heldLanding={seq:4};blackLocked=flag==='blackLock';blackVisible=flag==='blackVisible';
  context.syncOrdinaryRollButtons();assert.equal(normal.disabled,true,flag);assert.equal(custom.disabled,true,flag);
  assert.equal(context.tabSwitchLocked(),true,flag);
  assert.equal((await context.api('roll')).presentationLocked,true,flag);
  state.heldLanding=null;
}
modal=false;blackLocked=blackVisible=false;context.mapState.glMounting=false;context.mapState.board3d.transitioning=false;
context.syncOrdinaryRollButtons();assert.equal(normal.disabled,false);assert.equal(custom.disabled,false);
await context.api('roll');assert.equal(calls.at(-1)[0],'roll');
await context.useCustomDie();assert.equal(calls.at(-1)[1].use_custom_die,true);assert.equal(polls,1);
state.game.turn='sakura';assert.equal(context.autoCanRun(false),true);
modal=true;assert.equal(context.autoCanRun(false),false);modal=false;
state.heldCards=[['公告','正文',false]];assert.equal(context.autoCanRun(false),false);state.heldCards=null;
blackActive=true;assert.equal(context.autoCanRun(false),true,'unlocked role roll uses scoped black auto policy');
modal=true;modalIds=['black-train-event-modal'];
assert.equal((await context.api('decide',{action:'take_ticket'})).ok,true,'black choice modal cannot reject its own decision');
assert.equal((await context.api('act_sakura')).ok,true,'opponent black event modal can send its own automatic decision');
assert.equal(context.autoCanRun(false),false,'ordinary auto stays locked even with only a black modal');
modalIds.push('help-modal');
assert.equal((await context.api('decide',{action:'take_ticket'})).presentationLocked,true,'ordinary overlay above black event still blocks stale choice');
modalIds=['black-train-event-modal'];
blackLocked=true;assert.equal((await context.api('decide',{action:'take_ticket'})).presentationLocked,true);
assert.equal(context.ordinaryPresentationPending(),false,'waiting-entry cannot block itself');
blackActive=blackLocked=false;modal=false;modalIds=['game-modal'];
state.game.pending={type:'tower'};assert.equal(context.towerCanAct(),true);
context.towerBusy=true;assert.equal(context.towerCanAct(),false);
assert.equal((await context.api('tower_step')).ok,true);
context.towerBusy=false;state.game.pending=null;
// FIFO is still owned by the existing queue; the policy only supplies a gate.
modal=true;context.cardQueue.push(['第一张','A',false],['第二张','B',false]);
context.showNextCard();assert.equal(context.cardQueue.length,2);
modal=false;context.showNextCard();assert.equal($('#e-card-title').textContent,'第一张');assert.equal(context.cardQueue.length,1);
modal=true;context.showNextCard();assert.equal($('#e-card-title').textContent,'第一张');
context.cardQueue.length=0;modal=false;

// Resource configuration is an explicit server-provided script, not preview text rewriting.
assert.ok(html.indexOf('<script src="/board-config.js"></script>')<html.indexOf('<script src="/art/lib/presentation_flow.js"></script>'));
assert.ok(html.indexOf('<script src="/art/lib/presentation_flow.js"></script>')<html.indexOf('async function api('));
vm.runInContext(production('mountBoard3D'),context);
const stage={isConnected:true};nodes.set('#gl-stage',stage);context.mapState.use3D=true;
context.render=()=>{};
let options;
const B3={createBoard3D:async(_stage,opts)=>{options=opts;return {dispose(){}};}};
context.BoardRuntimeConfig={blackTrainTheme:{baseUrl:'/provided-by-server'}};
await context.mountBoard3D(stage,B3,{});assert.equal(options.blackTrainTheme.baseUrl,'/provided-by-server');
delete context.BoardRuntimeConfig;
await context.mountBoard3D(stage,B3,{});assert.equal(options.blackTrainTheme,null,'missing resource configuration uses static fallback');
for (const script of [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)]) {
  if(!script[2] || /importmap/.test(script[1]))continue;
  new vm.Script(`(async()=>{${script[2]}\n})`);
}
console.log('PASS presentation policy matrix, production API/buttons/auto/tab/tower/entry/FIFO adapters, modal cancellation and runtime theme wiring');
