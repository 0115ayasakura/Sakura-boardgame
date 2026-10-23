/* No browser/service required: production UI helper + deterministic DOM/clock. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const source = await readFile(new URL('../sakura_boardgame/art/lib/black_train_view.js', import.meta.url), 'utf8');
const html = await readFile(new URL('../sakura_boardgame/board.html', import.meta.url), 'utf8');
const presentationSource = await readFile(new URL('../sakura_boardgame/art/lib/presentation_flow.js', import.meta.url), 'utf8');
function loadPresentation(target) {
  vm.runInContext(presentationSource,target);
  target.state ??= {};
  target.mapState ??= {};
  target.towerBusy ??= false;
  target.BlackTrainView ??= V;
  target.towerLocked ??= ()=>false;
  if (vm.runInContext('typeof cardQueue',target)==='undefined') target.cardQueue=[];
  if (!target.boardMoveHeld) vm.runInContext(html.match(/function boardMoveHeld\(\) \{[^\n]*\}/)[0],target);
  vm.runInContext(html.match(/function presentationSnapshot\(\) \{[^]*?\n\}/)[0],target);
}
let clock = 0, nextTimer = 0;
const timers = new Map();
const context = vm.createContext({
  setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, {fn, at:clock + delay}); return id; },
  clearTimeout(id) { timers.delete(id); }
});
const advance = async ms => { clock += ms; for (const [id, timer] of [...timers]) if (timer.at <= clock) { timers.delete(id); await timer.fn(); } };
vm.runInContext(source, context);
const V = context.BlackTrainView;
const choices = [{action:'take_ticket',label:'留下票根',effect_text:'个人票根 +2；收益 +30'}, {action:'pay_ticket',label:'交出票根',effect_text:'个人票根 -1；收益 +60'}];
const game = {kind:'monopoly', turn:'user', black_train:{active:true, clues:3, energy:5, actions_left:6, actions_taken:{user:2,sakura:2}, players:{user:{tickets:3,reward:90},sakura:{tickets:1,reward:40}}, current_event:{category:'蓝色契约',color:'blue',title:'契约',story:'可以拒绝 <script>',choices},history:[]}, pending:{kind:'black_train', type:'black_train', player:'user',options:['take_ticket','pay_ticket']}};
assert.equal(V.active(game), true);
assert.equal(V.active({...game,kind:'gomoku'}), false);
assert.equal(V.active({kind:'monopoly'}), false);
assert.equal(V.model(game).used.user, 2);
for (const name of ['金色车票','蓝色契约','白色B.E.G.','黑色异象']) assert.ok(V.summary(game).includes(name));
assert.match(V.summary(game), /票根 3 · 收益 ¥90/);
assert.match(V.summary(game), /票根 1 · 收益 ¥40/);
assert.match(V.summary(game), /剩余 6 次行动/);
assert.match(V.summary(game), /2 \/ 5/);
assert.doesNotMatch(V.summary(game), /线索|能量/);
assert.match(V.summary(game), /不含固定报酬与票根折现/);
assert.match(V.summary(game), /100＋个人收益＋剩余票根×20/);
const nested = {...game,black_train:{...game.black_train,players:undefined,state:{players:game.black_train.players}}};
assert.match(V.summary(nested), /收益 ¥90/);
const event = V.eventHTML(game);
assert.match(event, /可以拒绝 &lt;script&gt;/);
assert.match(event, /个人票根 \+2；收益 \+30/);
assert.match(event, /个人票根 -1；收益 \+60/);
assert.equal([...event.matchAll(/data-bt-choice=/g)].length,2);
assert.doesNotMatch(event, /<script>|onclick=/);
const limited={...game,black_train:{...game.black_train,current_event:{...game.black_train.current_event,choices:[{action:'buy',effect_text:'购骰',available:false,reason:'自定义骰持有上限1'}]}}};
assert.match(V.eventHTML(limited),/自定义骰持有上限1/);
assert.doesNotMatch(V.eventHTML(limited),/票根不足/);
assert.match(V.actions(game), /data-bt-open/);
assert.doesNotMatch(V.actions({...game,pending:null}), /hint|车轱辘|共享目标/);
assert.match(V.actions({...game,pending:null}), /data-bt-roll/);
const dieGame = {...game,pending:null,black_train:{...game.black_train,players:{...game.black_train.players,user:{tickets:3,reward:90,custom_die:true,lock:0}}}};
assert.match(V.actions(dieGame), /data-bt-custom/);
assert.doesNotMatch(V.actions(dieGame), /data-bt-custom disabled/);
assert.match(V.actions({...dieGame,black_train:{...dieGame.black_train,players:{user:{custom_die:true,lock:2}}}}), /data-bt-custom disabled/);
const dieButton={disabled:false}, dieCalls=[];
V.bind({querySelectorAll:s=>s==='button'?[dieButton]:[],querySelector:s=>s==='[data-bt-custom]'?dieButton:null},dieGame,async(...args)=>dieCalls.push(args));
await dieButton.onclick();
assert.equal(dieCalls[0][0],'roll');assert.equal(dieCalls[0][1].use_custom_die,true);
const pickingDie={...dieGame,pending:{type:'dice',player:'user',options:['1','2','3','4','5','6']}};
assert.equal([...V.actions(pickingDie).matchAll(/data-bt-dice=/g)].length,6);
const pickButton={disabled:false,dataset:{btDice:'4'}};
V.bind({querySelectorAll:s=>s==='button'||s==='[data-bt-dice]'?[pickButton]:[],querySelector:()=>null},pickingDie,async(...args)=>dieCalls.push(args));
await pickButton.onclick();assert.equal(dieCalls[1][0],'decide');assert.equal(dieCalls[1][1].action,'5');
assert.match(V.actions({...game,pending:null,turn:'sakura'}), /data-bt-partner/);
const forkGame = {...game,black_train:{...game.black_train,tiles:{7:{color:'blue'},11:{color:'black'}}},pending:{type:'route',player:'user',options:[7,11]},cells:[{id:11,name:'汉加米塔'},{id:7,name:'中央商业街'}]};
const forkHTML = V.actions(forkGame);
assert.match(forkHTML, /路线 1 · 蓝色契约/);
assert.match(forkHTML, /路线 2 · 黑色异象/);
assert.doesNotMatch(forkHTML, /汉加米塔|中央商业街|undefined/);
const forkButtons = [0,1].map(i=>({disabled:false,dataset:{btRoute:String(i)}})), forkCalls=[];
V.bind({querySelectorAll:s=>s==='[data-bt-route]'?forkButtons:s==='button'?forkButtons:[],querySelector:()=>null},forkGame,async(...args)=>forkCalls.push(args));
await forkButtons[1].onclick();
assert.equal(forkCalls[0][0],'decide');assert.equal(forkCalls[0][1].action,'11');
assert.equal(V.cellLabel(game,{id:1,type:'property',name:'普通地产',price:500,event_color:'gold'}),'金色车票');
assert.equal(V.cellLabel(game,{id:2,type:'property',name:'普通地产',price:500}),'列车事件');
assert.equal(V.cellLabel({...game,black_train:{...game.black_train,tiles:{1:{color:'blue'}}}},{id:1}),'蓝色契约');
const legacy = {...game,black_train:{...game.black_train,current_event:{}},pending:{...game.pending,options:['<img onerror=x>','拒绝']}};
assert.equal(V.choices(legacy)[1].action,'拒绝');
assert.doesNotMatch(V.eventHTML(legacy), /<img/);
class Element {
  constructor() { this.hidden=true; this._html=''; this.children=[]; this.open=false; this.classes=new Set(); this.classList={add:c=>this.classes.add(c),remove:c=>this.classes.delete(c),contains:c=>this.classes.has(c),toggle:(c,on)=>on?this.classes.add(c):this.classes.delete(c)}; }
  set innerHTML(value) { this._html=value; this.children=[...value.matchAll(/<button\b([^>]*)>/g)].map(([,attrs])=>{const e=new Element(); e.disabled=/\bdisabled\b/.test(attrs);e.dataset={};for(const [,key,val] of attrs.matchAll(/data-([a-z-]+)(?:="([^"]*)")?/g))e.dataset[key.replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=val??'';return e;}); }
  get innerHTML() { return this._html; }
  querySelectorAll(selector) { if(selector==='button')return this.children;const key=selector.match(/^\[data-([a-z-]+)\]$/)?.[1]?.replace(/-([a-z])/g,(_,c)=>c.toUpperCase());return key?this.children.filter(e=>key in e.dataset):[]; }
  querySelector(selector) { if(selector==='.bt-details')return this.detail??=(new Element());return this.querySelectorAll(selector)[0]||null; }
}
const classes = new Set(), visible = [];
const card = new Element(), result = new Element(), modal = new Element(), eventCard = new Element();
const document = {body:{classList:{toggle(c,on){on?classes.add(c):classes.delete(c);}}}, querySelector(selector){return {'#black-train-card':card,'#black-train-result':result,'#black-train-event-modal':modal,'#black-train-event-card':eventCard}[selector]||null;}};
let marker = {remove(){marker=null;}};
const board = {querySelector(){return marker;}};
const layouts=[];
const board3d = {setBlackTrainVisible(on){visible.push(on);},setBlackTrainTiles(tiles,cells){layouts.push({tiles,cells});}};
const tileLayout=Object.fromEntries(['gold','blue','white','black'].map((color,id)=>[String(id),{color}]));
const layoutGame={...game,cells:[0,1,2,3].map(id=>({id,x:id,y:0,h:0})),black_train:{...game.black_train,tiles:tileLayout}};
for(const [id,label] of ['金色车票','蓝色契约','白色B.E.G.','黑色异象'].entries()) assert.equal(V.cellLabel(layoutGame,{id,event_color:'wrong',name:'普通地产'}),label);
V.sync({game:layoutGame,board3d,document:{body:document.body,querySelector(){return null;}}});
assert.equal(layouts.at(-1).tiles,tileLayout);assert.equal(layouts.at(-1).cells,layoutGame.cells);
const calls=[];
const sync = current => V.sync({game:current,document,board,board3d,send:async(...args)=>calls.push(args)});
const settlement={user:{base:100,reward:90,tickets:3,ticket_cash:60,total:250,expired_items:['lamp']},sakura:{base:100,reward:40,tickets:1,ticket_cash:20,total:160,expired_items:[]}};
const finished = {...game,pending:null,black_train:{...game.black_train,active:false,outcome:'safe_return',settlement}};
sync(finished);assert.equal(card.hidden,true);assert.equal(result.hidden,true);assert.equal(timers.size,0);
sync(game); assert.ok(classes.has('black-train-active')); assert.equal(card.hidden,false); assert.equal(marker,null);
assert.ok(modal.classList.contains('show')); assert.match(eventCard.innerHTML,/留下票根/);
assert.match(card.innerHTML,/<details class="bt-details" open>/);assert.equal(card.detail.open,true,'details expand by default');
card.detail.open=false;sync(game);assert.equal(card.detail.open,false,'polling preserves manual collapse');
card.detail.open=true;sync(game);assert.equal(card.detail.open,true,'polling preserves manual expansion');
await eventCard.querySelectorAll('[data-bt-choice]')[1].onclick();
assert.equal(calls[0][0],'decide');assert.equal(calls[0][1].action,'pay_ticket');
const history={player:'user',title:'契约',story:'你看到蓝色合约',action:'交出票根',effect_text:'票根 -1；收益 +60'};
const resolved={...game,pending:null,black_train:{...game.black_train,history:[history]}};
sync(resolved);assert.match(eventCard.innerHTML,/你看到蓝色合约/);assert.match(eventCard.innerHTML,/已选择/);assert.match(eventCard.innerHTML,/收益 \+60/);
await advance(20000);sync(resolved);assert.ok(modal.classList.contains('show'),'choice result never auto-disappears');
// A newer pending event must wait behind the readable result, then use latest game, not a stale closure.
const opponent={...game,turn:'sakura',pending:{...game.pending,player:'sakura'},black_train:{...game.black_train,history:[history]}};
sync(opponent);assert.match(eventCard.innerHTML,/已选择/);assert.equal(timers.size,0);
eventCard.querySelector('[data-bt-continue]').onclick();assert.match(eventCard.innerHTML,/将自动选择/);
assert.ok(eventCard.querySelectorAll('[data-bt-choice]').every(b=>b.disabled));
await advance(1399);assert.equal(calls.length,1);sync(opponent);
await advance(1);assert.equal(calls.length,2);assert.equal(calls[1][0],'act_sakura');
const herHistory={player:'sakura',title:'契约',story:'她看到蓝色合约',choices,action:'留下票根',effect_text:'个人票根 +2；收益 +30',actual:{reward_delta:30,tickets_delta:2,reward:70,tickets:3}};
const herResolved={...resolved,black_train:{...resolved.black_train,history:[history,herHistory]}};
sync(herResolved);assert.match(eventCard.innerHTML,/对手自动选择/);assert.match(eventCard.innerHTML,/留下票根/);assert.match(eventCard.innerHTML,/收益 \+30/);
assert.match(eventCard.innerHTML,/实际结算：收益 \+30 · 票根 \+2/);assert.match(eventCard.innerHTML,/个人余额：收益 ¥70 · 票根 3/);
assert.equal(eventCard.querySelectorAll('[data-bt-choice]').length,2,'history can show both alternatives after atomic opponent turns');
sync(herResolved);assert.equal(timers.size,0);eventCard.querySelector('[data-bt-continue]').onclick();assert.equal(modal.classList.contains('show'),false);
sync(herResolved);assert.equal(modal.classList.contains('show'),false,'acknowledged history never reappears on poll');
// Without terminal history, a live participant still explicitly confirms completion.
sync(finished);assert.equal(card.hidden,false);assert.equal(result.hidden,true);assert.equal(V.inputLocked(document),true);
assert.match(eventCard.innerHTML,/完整事件记录未提供/);assert.equal(visible.at(-1),true);
await advance(20000);sync(finished);assert.equal(result.hidden,true);assert.equal(visible.at(-1),true);
eventCard.querySelector('[data-bt-continue]').onclick();
assert.equal(card.hidden,true);assert.equal(result.hidden,false);assert.equal(modal.classList.contains('show'),false);
assert.match(result.innerHTML,/收益结算/);assert.match(result.innerHTML,/合计 ¥250/);assert.match(result.innerHTML,/票根折现 ¥60/);assert.match(result.innerHTML,/到期道具：lamp/);
assert.equal(classes.has('black-train-active'),false);assert.equal(V.inputLocked(document),true);
await advance(20000);sync(finished);assert.equal(result.hidden,false,'settlement is not timed');
result.onclick();assert.equal(result.hidden,true);assert.equal(V.inputLocked(document),false);sync(finished);assert.equal(result.hidden,true,'settlement never repeats on poll');assert.equal(timers.size,0);
sync(game);sync(finished);sync(null);assert.equal(result.hidden,true);assert.equal(modal.classList.contains('show'),false);assert.equal(timers.size,0);
// Arrival/dice holds queue events without obscuring movement; they appear once released.
V.sync({game,document,board,board3d,deferEvents:true});assert.equal(modal.classList.contains('show'),false);
sync(game);assert.equal(modal.classList.contains('show'),true);
const unavailable={...game,pending:{...game.pending,choices:[{action:'costly',label:'昂贵选项',requires_tickets:5,effect_text:'消耗5票根'},choices[0]]}};
assert.match(V.eventHTML(unavailable),/data-bt-choice="0" disabled/);assert.match(V.eventHTML(unavailable),/票根不足/);
const temp=new Element();temp.innerHTML=V.eventHTML(unavailable);V.bind(temp,unavailable,async()=>{});await temp.querySelectorAll('[data-bt-choice]')[1].onclick();assert.equal(temp.querySelectorAll('[data-bt-choice]')[0].disabled,true,'request completion preserves unavailable choice');
sync(null);assert.equal(timers.size,0);
const before=JSON.stringify(game);V.sync({game,document:{body:document.body,querySelector(){return null;}}});assert.equal(JSON.stringify(game),before);
// Deterministic real-renderer contract: departure may take arbitrarily long;
// only transitioning=false AND theme inactive allows the independent settlement.
function fixture(renderer = null) {
  const elements=Object.fromEntries(['black-train-card','black-train-result','black-train-event-modal','black-train-event-card'].map(id=>[id,new Element()]));
  const doc={body:new Element(),querySelector:s=>elements[s.slice(1)]||null};
  for(const el of Object.values(elements))el.ownerDocument=doc;
  const view={document:doc,board3d:renderer,gameKey:1};
  return {doc,e:elements,sync(g,extra={}){V.sync({...view,game:g,...extra});}};
}
// Production ownership evidence: there is no black role-roll timer. Only a
// pending role event schedules an automatic choice, exactly once per event key.
{
  const role=fixture(), sent=[];
  const rollGame={...game,turn:'sakura',pending:null};
  const send=async(...args)=>sent.push(args);
  role.sync(rollGame,{send});assert.equal(V.inputLocked(role.doc),false);
  await advance(180000);role.sync(rollGame,{send});
  assert.equal(sent.length,0,'BlackTrainView never owns the role roll');
  assert.equal(timers.size,0,'unlocked role roll has no FSM timer');
  const choiceGame={...rollGame,pending:{...game.pending,player:'sakura'}};
  role.sync(choiceGame,{send});role.sync(choiceGame,{send});
  assert.equal(timers.size,1,'only the pending choice FSM schedules here');
  await advance(1400);assert.equal(sent.length,1);assert.equal(sent[0][0],'act_sakura');
  role.sync(choiceGame,{send});await advance(180000);
  assert.equal(sent.length,1,'same pending event cannot duplicate its Agent request');
  role.sync(null);
}
let requested=false,transitioning=false,themeActive=false;
const transitions=[];
const renderer={setBlackTrainVisible(on){if(on!==requested){requested=on;transitions.push(on);if(!on)transitioning=true;}},get transitioning(){return transitioning;},getBlackTrainStats(){return {active:themeActive,transitioning};}};
const f=fixture(renderer), finalRecord={...herHistory,title:'最终行动',story:'最后结果必须确认'};
const entered={...game,positions:{user:19,sakura:23}}, terminal={...finished,positions:{user:0,sakura:1},black_train:{...finished.black_train,history:[history,finalRecord]}};
f.sync(entered);themeActive=true;
f.sync(terminal);assert.equal(requested,true);assert.equal(transitions.includes(false),false);assert.equal(V.presentationActive(f.doc),true);
assert.equal(V.presentationGame(f.doc,terminal).positions,entered.positions,'final result freezes ordinary restored positions/follow camera');
// A delayed poll can miss both players' last moves; terminal history is authoritative.
const delayed=fixture();delayed.sync(entered);
const caughtUp={...terminal,cells:[{id:7},{id:8}],black_train:{...terminal.black_train,history:[
  {...history,player:'user',cell_id:7},{...finalRecord,player:'sakura',cell_id:8}
]}};
delayed.sync(caughtUp);
assert.equal(V.presentationGame(delayed.doc,caughtUp).positions.user,7);
assert.equal(V.presentationGame(delayed.doc,caughtUp).positions.sakura,8);
assert.equal(V.presentationGame(delayed.doc,caughtUp).turn,'sakura');
assert.equal(caughtUp.positions.user,0,'presentation never mutates server state');
const displaced={...caughtUp,black_train:{...caughtUp.black_train,history:[{...history,player:'user',cell_id:7,actual:{position:8,target:8}},{...finalRecord,player:'sakura',cell_id:8,actual:{position:8,target:7}}]}};
assert.equal(V.presentationGame(delayed.doc,displaced).positions.user,8,'special movement freezes its actual destination');
assert.equal(V.presentationGame(delayed.doc,displaced).positions.sakura,8,'recolor target is not the piece position');
delayed.sync(null);
assert.match(f.e['black-train-event-card'].innerHTML,/你看到蓝色合约/);
f.sync(terminal);await advance(60000);assert.equal(requested,true,'neither polls nor long elapsed time acknowledge results');
const firstAck=f.e['black-train-event-card'].querySelector('[data-bt-continue]').onclick;firstAck();
assert.match(f.e['black-train-event-card'].innerHTML,/最后结果必须确认/);firstAck();assert.match(f.e['black-train-event-card'].innerHTML,/最后结果必须确认/,'stale double click cannot skip final record');
assert.equal(requested,true);assert.equal(f.e['black-train-result'].hidden,true);
f.e['black-train-event-card'].querySelector('[data-bt-continue]').onclick();
assert.equal(requested,false);assert.equal(transitioning,true);assert.equal(f.e['black-train-event-modal'].classList.contains('show'),false);
assert.equal(V.inputLocked(f.doc),true);assert.equal(f.e['black-train-result'].hidden,true);
await advance(60000);assert.equal(f.e['black-train-result'].hidden,true,'a delay never substitutes for renderer completion');
transitioning=false;await advance(50);assert.equal(f.e['black-train-result'].hidden,true,'renderer theme must also be restored');
themeActive=false;await advance(50);assert.equal(f.e['black-train-result'].hidden,false);assert.equal(V.presentationActive(f.doc),false);
assert.match(f.e['black-train-result'].innerHTML,/¥250/);f.e['black-train-result'].onclick({stopPropagation(){}});
f.sync(terminal);await advance(10000);assert.equal(f.e['black-train-result'].hidden,true);assert.equal(V.inputLocked(f.doc),false);
// Loading an already-complete save does not replay history, return, or settlement.
const loaded=fixture();loaded.sync(terminal);assert.equal(loaded.e['black-train-event-modal'].classList.contains('show'),false);assert.equal(loaded.e['black-train-result'].hidden,true);assert.equal(V.presentationActive(loaded.doc),false);
// Identical history rows are distinct actions (index + content, not content alone).
const repeated=fixture();repeated.sync(game);const twins={...terminal,black_train:{...terminal.black_train,history:[history,{...history}]}};
repeated.sync(twins);repeated.e['black-train-event-card'].querySelector('[data-bt-continue]').onclick();assert.equal(repeated.e['black-train-event-modal'].classList.contains('show'),true);
repeated.e['black-train-event-card'].querySelector('[data-bt-continue]').onclick();assert.equal(repeated.e['black-train-result'].hidden,false);repeated.e['black-train-result'].onclick();
// Refresh/new game cleanup cancels actual completion watchers and stale auto sends.
const pendingReturn=fixture(renderer);requested=false;transitioning=false;themeActive=false;
pendingReturn.sync(game);themeActive=true;pendingReturn.sync({...terminal,black_train:{...terminal.black_train,history:[finalRecord]}});
pendingReturn.e['black-train-event-card'].querySelector('[data-bt-continue]').onclick();assert.equal(transitioning,true);
pendingReturn.sync({kind:'monopoly'}, {gameKey:2});themeActive=false;transitioning=false;await advance(500);
assert.equal(pendingReturn.e['black-train-result'].hidden,true);assert.equal(V.inputLocked(pendingReturn.doc),false);
let staleSends=0;const changed=fixture();changed.sync(opponent,{send:async()=>staleSends++});
changed.e['black-train-event-card'].querySelector('[data-bt-continue]').onclick();
changed.sync({kind:'gomoku'}, {gameKey:2});await advance(2000);assert.equal(staleSends,0);assert.equal(changed.e['black-train-event-modal'].classList.contains('show'),false);
// GL mounting blocks settlement; 2D/no renderer and reduced-motion completion are immediate after acknowledgement.
const mounting=fixture();mounting.sync(game);mounting.sync({...terminal,black_train:{...terminal.black_train,history:[finalRecord]}},{mapReady:false});
assert.equal(mounting.e['black-train-event-modal'].classList.contains('show'),false);
mounting.sync({...terminal,black_train:{...terminal.black_train,history:[finalRecord]}},{mapReady:true});mounting.e['black-train-event-card'].querySelector('[data-bt-continue]').onclick();assert.equal(mounting.e['black-train-result'].hidden,false);mounting.e['black-train-result'].onclick();
// Missing final history cannot hide behind an older unread action.
const missing=fixture();missing.sync({...game,black_train:{...game.black_train,history:[history]}});missing.sync(finished);
missing.e['black-train-event-card'].querySelector('[data-bt-continue]').onclick();assert.match(missing.e['black-train-event-card'].innerHTML,/完整事件记录未提供/);assert.equal(missing.e['black-train-result'].hidden,true);
missing.e['black-train-event-card'].querySelector('[data-bt-continue]').onclick();assert.equal(missing.e['black-train-result'].hidden,false);missing.e['black-train-result'].onclick();
// Cached controls and API calls cannot bypass a queued final result.
let lockedCalls=0;const locked=fixture();locked.sync(game,{send:async()=>lockedCalls++});const cachedChoice=locked.e['black-train-event-card'].querySelector('[data-bt-choice]');
locked.sync(terminal);await cachedChoice.onclick();assert.equal(lockedCalls,0);
const apiSource=html.match(/async function api\(action, args = \{\}\) \{[\s\S]*?\n\}/)[0];
const apiContext=vm.createContext({BlackTrainView:V,document:locked.doc,connection:{action:async()=>{lockedCalls++;return {ok:true};}},toast(){}});loadPresentation(apiContext);vm.runInContext(apiSource,apiContext);
assert.equal((await apiContext.api('roll')).presentationLocked,true);assert.equal((await apiContext.api('act_sakura')).presentationLocked,true);assert.equal(lockedCalls,0);
locked.sync(null);
// Async auto failure from a discarded game must not alter a new event with the same content.
let rejectOld;const raced=fixture();const sameOpponent={...opponent,black_train:{...opponent.black_train,history:[]}};
raced.sync(sameOpponent,{send:()=>new Promise((_,reject)=>{rejectOld=reject;})});
// Start the scheduled task without awaiting its unresolved request.
const autoEntry=[...timers.entries()].find(([,t])=>t.at===clock+1400);assert.ok(autoEntry);timers.delete(autoEntry[0]);const oldRequest=autoEntry[1].fn();
raced.sync(sameOpponent,{gameKey:2,send:async()=>{}});rejectOld(Error('old failure'));await oldRequest;assert.doesNotMatch(raced.e['black-train-event-card'].innerHTML,/old failure/);raced.sync(null,{gameKey:2});
// Values are displayed as supplied by settlement, not recomputed from account/reward rules.
const mismatched={...finished,black_train:{...finished.black_train,settlement:{user:{base:7,reward:8,tickets:9,ticket_cash:10,total:123,expired_items:['<lamp>']}}}};
assert.match(V.settlementHTML(mismatched,{user:'<玩家>'}),/固定报酬 ¥7/);assert.match(V.settlementHTML(mismatched),/合计 ¥123/);assert.match(V.settlementHTML(mismatched),/&lt;lamp&gt;/);assert.match(V.settlementHTML(mismatched),/结算明细未提供/);
assert.equal(timers.size,0,'all lifecycle timers cleaned up');
assert.match(html,/BlackTrainView\.inputLocked\(document\)/);assert.match(html,/gameKey: state\.gameSeq/);assert.match(html,/BlackTrainView\.presentationGame/);
assert.doesNotMatch(html, /<button[^>]*data-bt-start|测试：黑列车探险/);
assert.match(html, /if \(game\.kind === 'gomoku' && state\.canUndo\)/);
assert.match(html, /仅五子棋提供/);
const actionBar = new Element(), barDoc = {querySelector:()=>null};
const barContext = vm.createContext({BlackTrainView:V,document:barDoc,$:()=>actionBar,state:{canUndo:true,autoMode:'off'},NAMES:{user:'玩家',sakura:'对手'},currentKind:()=> 'monopoly',scheduleAutoStep(){},boardMoveHeld:()=>false,towerLocked:()=>false});
loadPresentation(barContext);
vm.runInContext(html.match(/function syncOrdinaryRollButtons\(\) \{[\s\S]*?\n\}/)[0],barContext);
vm.runInContext(html.match(/function renderActionBar\(game, demo\) \{[\s\S]*?\n\}/)[0],barContext);
barContext.renderActionBar({kind:'monopoly',turn:'sakura'},false);
assert.doesNotMatch(actionBar.innerHTML,/undoMove|data-bt-start/,'monopoly never exposes undo or debug start');
barContext.renderActionBar({kind:'gomoku',turn:'user'},false);
assert.match(actionBar.innerHTML,/undoMove/,'gomoku retains available undo');
barContext.state.canUndo=false;barContext.renderActionBar({kind:'gomoku',turn:'user'},false);
assert.doesNotMatch(actionBar.innerHTML,/undoMove/);
// Server-persisted safe-completion milestones, visible for either actor, once per game.
const noticeDoc = {}, ordinary = {kind:'monopoly',turn:'user',black_train_schedule:{announce_at:22,enter_at:26,announced:false,entered:false,last_completed:21}};
assert.equal(V.scheduleNotice(noticeDoc,ordinary,1),null);
const announced = {...ordinary,turn:'sakura',black_train_schedule:{...ordinary.black_train_schedule,announced:true,last_completed:22}};
assert.match(V.scheduleNotice(noticeDoc,announced,1)[1],/22.*26.*双方/);
assert.equal(V.scheduleNotice(noticeDoc,announced,1),null);
const arriving = {...announced,black_train_schedule:{...announced.black_train_schedule,entered:true,last_completed:26}};
assert.match(V.scheduleNotice(noticeDoc,arriving,1)[0],/到站/);
assert.equal(V.scheduleNotice(noticeDoc,arriving,1),null);
assert.equal(V.scheduleNotice({},arriving,1),null,'loading an entered save never replays old notices');
assert.match(V.scheduleNotice({},announced,1)[0],/预告/,'unentered announced saves retain readable notice');
assert.match(V.scheduleNotice(noticeDoc,announced,2)[0],/预告/,'new game resets notice tracking');
assert.equal(V.scheduleNotice(noticeDoc,{kind:'gomoku'},2),null);
assert.equal(V.scheduleNotice(noticeDoc,{kind:'monopoly'},3),null,'legacy saves without schedule stay quiet');
assert.match(html,/BlackTrainView\.scheduleNotice\(document, data\.game, state\.gameSeq\)/);
assert.ok(html.includes('(state.heldCards = state.heldCards || []).push(trainNotice)'));
const noticeCode = html.slice(html.indexOf('    const trainNotice = BlackTrainView.scheduleNotice'),html.indexOf('    if(Array.isArray(state.actionRecords))renderActionLog();'));
const noticeCalls = [], noticeContext = vm.createContext({BlackTrainView:V,document:{},data:{game:announced},state:{gameSeq:1},moveIncoming:true,showCard:(...args)=>noticeCalls.push(args)});
vm.runInContext('{'+noticeCode+'}',noticeContext);
assert.equal(noticeCalls.length,0,'announcement waits for visual landing');assert.equal(noticeContext.state.heldCards.length,1);
vm.runInContext('{'+noticeCode+'}',noticeContext);assert.equal(noticeContext.state.heldCards.length,1,'held notices do not multiply on polls');
noticeContext.moveIncoming=false;noticeContext.data.game=arriving;
vm.runInContext('{'+noticeCode+'}',noticeContext);assert.equal(noticeCalls.length,1);assert.match(noticeCalls[0][0],/到站/);
assert.match(html,/deferEvents:.*#card-modal/,'ordinary announcement precedes train event card');
assert.match(html,/flow\.allows\('auto', flow\.blackActive \? 'black-roll' : ''\)/);
assert.match(html,/BlackTrainView\.sync/);assert.match(source,/setBlackTrainVisible/);
assert.match(html,/#board-area \{ position:relative/);
assert.match(html,/#black-train-card \{ position:absolute; top:12px; right:12px; z-index:45/);
assert.match(html,/max-width:min\(340px,calc\(100% - 24px\)\)/);
assert.match(html,/#black-train-card \.bt-detail-body \{ overflow-wrap:anywhere; \}/);
assert.doesNotMatch(html,/#black-train-card \.bt-detail-body \{[^}]*max-height|#black-train-card \.bt-detail-body \{[^}]*overflow:auto/);
for(const id of ['black-train-card','black-train-result','black-train-event-modal'])assert.equal([...html.matchAll(new RegExp(`id="${id}"`,'g'))].length,1);
assert.doesNotMatch(html,/body\.black-train-active #(?:map-switch|dice-card|info-card)[^{]*\{[^}]*display:none/);
for(const selector of ['#tabs button','.opt-btn','#zoom-hint','.pin-chip'])assert.ok(html.includes(`body.black-train-active ${selector}`));
assert.match(html,/#dice-modal \{ place-items: end start; pointer-events: auto; \}/);
assert.match(html,/if \(BlackTrainView\.presentationActive\(document\)\) return `.*BlackTrainView\.cellLabel/);
// Execute the real ordinary FIFO and entry predicate together, not a mocked boolean gate.
const ingress = fixture(), ordinaryElements = Object.fromEntries(['card-modal','e-card-title','e-card-flavor','e-card-from','e-card','dice-modal','game-modal','check-modal','shop-modal'].map(id=>[id,new Element()]));
const originalQuery = ingress.doc.querySelector;
ingress.doc.querySelector = selector => selector === '.modal.show'
  ? Object.entries(ordinaryElements).find(([id,e])=>id.endsWith('modal')&&e.classList.contains('show'))?.[1] || null
  : ordinaryElements[selector.slice(1)] || originalQuery(selector);
let ingressVisible = false;
const ingressRenderer = {setBlackTrainVisible:on=>{ingressVisible=on;},setBlackTrainTiles(){}};
const queueContext = vm.createContext({document:ingress.doc,$:s=>ingress.doc.querySelector(s),state:{},towerLocked:()=>false,naviLogPrefix:()=> 'NAVI',setTimeout:context.setTimeout,clearTimeout:context.clearTimeout});
queueContext.syncBlackTrainUI = () => ingress.sync(game,{board3d:ingressRenderer,deferEntry:()=>queueContext.ordinaryPresentationPending()});
vm.runInContext('let cardTimer=null;const cardQueue=[];',queueContext);
loadPresentation(queueContext);
vm.runInContext(html.match(/function boardMoveHeld\(\) \{[^\n]*\}/)[0],queueContext);
for(const name of ['showCard','showNextCard','closeCard','resetCardQueue','ordinaryPresentationPending'])vm.runInContext(html.match(new RegExp('function '+name+'\\([^]*?\\n\\}'))[0],queueContext);
ordinaryElements['dice-modal'].classList.add('show');queueContext.state.visualMovePending={player:'user'};
queueContext.showCard('本回合普通事件','普通事件效果',false);queueContext.showCard('黑列车预告','22完成，26入场',false);
queueContext.syncBlackTrainUI();assert.equal(ingressVisible,false);assert.equal(V.presentationActive(ingress.doc),false);assert.equal(V.inputLocked(ingress.doc),true,'waiting entry blocks cached/API/automatic actions');
assert.equal(ingress.e['black-train-event-modal'].classList.contains('show'),false);
await advance(1400);assert.equal(ingressVisible,false,'elapsed dice time does not bypass visible dice');
ordinaryElements['dice-modal'].classList.remove('show');queueContext.syncBlackTrainUI();assert.equal(ingressVisible,false,'movement must really finish');
for(const flag of ['visualMovePending','moveAnim','pendingMove','arrivalHold']) {
  for(const key of ['visualMovePending','moveAnim','pendingMove','arrivalHold'])queueContext.state[key]=null;
  queueContext.state[flag]=flag==='arrivalHold'?0:{player:'user'};
  queueContext.syncBlackTrainUI();queueContext.syncBlackTrainUI();
  assert.equal(ingressVisible,false,`${flag} cannot be bypassed by a second poll`);
  assert.equal(V.inputLocked(ingress.doc),true);
}
queueContext.state.arrivalHold=null;ordinaryElements['game-modal'].classList.add('show');queueContext.syncBlackTrainUI();
await advance(1320);assert.equal(ingressVisible,false,'game landing 900+420ms settlement still owns screen');
assert.equal(ordinaryElements['card-modal'].classList.contains('show'),false,'announcement cannot obscure the game result');
ordinaryElements['game-modal'].classList.remove('show');
for(const id of ['check-modal','shop-modal']) {
  ordinaryElements[id].classList.add('show');queueContext.syncBlackTrainUI();
  assert.equal(ingressVisible,false,`${id} must close before entry`);
  assert.equal(ordinaryElements['card-modal'].classList.contains('show'),false);
  ordinaryElements[id].classList.remove('show');
}
queueContext.syncBlackTrainUI();
assert.equal(ordinaryElements['e-card-title'].textContent,'本回合普通事件');assert.equal(ingressVisible,false);
await advance(1000);queueContext.closeCard();assert.equal(ordinaryElements['e-card-title'].textContent,'黑列车预告');assert.equal(ingressVisible,false);
await advance(3200);assert.equal(ordinaryElements['card-modal'].classList.contains('show'),true,'old card timer cannot close its successor');
await advance(1000);assert.equal(ordinaryElements['card-modal'].classList.contains('show'),false);assert.equal(ingressVisible,true,'only drained ordinary presentation permits train tiles/camera');
assert.equal(V.presentationActive(ingress.doc),true);assert.equal(V.inputLocked(ingress.doc),false);
ingress.sync(null);queueContext.resetCardQueue();assert.equal(timers.size,0);
// A renderer not yet mounted cannot consume the entry gate or lose the waiting lock.
const lateMount=fixture();lateMount.sync(game,{mapReady:false});assert.equal(V.presentationActive(lateMount.doc),false);assert.equal(V.inputLocked(lateMount.doc),true);
lateMount.sync(game,{mapReady:true});assert.equal(V.presentationActive(lateMount.doc),true);lateMount.sync(null);assert.equal(timers.size,0);
const luminance=hex=>hex.match(/../g).map(n=>parseInt(n,16)/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4).reduce((s,n,i)=>s+n*[.2126,.7152,.0722][i],0);
assert.ok((luminance('f3ce72')+.05)/(luminance('252938')+.05)>4.5);
for(const [,type,body] of html.matchAll(/<script(?: type="([^"]+)")?>([\s\S]*?)<\/script>/g))if(!type)new vm.Script(body);
console.log('PASS black train view: schema/escaping, personal ledgers, queued explicit final confirmation, frozen presentation positions, real transition/theme completion gate, independent untimed settlement, poll/refresh/new-game cleanup, stale callback and API/auto input locks, DOM fallback, ordinary-card FIFO/timer isolation, entry gating on every movement flag and dice/game/check/shop/card completion, delayed renderer mounting, inline-script syntax');
