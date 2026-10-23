/* Exercise production board UI functions, without browser, engine, or service. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const html = await readFile(new URL('../sakura_boardgame/board.html', import.meta.url), 'utf8');
const presentationSource = await readFile(new URL('../sakura_boardgame/art/lib/presentation_flow.js', import.meta.url), 'utf8');
const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].filter(m => !/type=["']importmap["']/.test(m[1])).map(m => m[2]).filter(Boolean);
for (const script of scripts) new vm.Script(`(async () => {\n${script}\n})`); // Parse classic and top-level-await module scripts.
function fn(name) {
  const found = html.match(new RegExp(`(?:async )?function ${name}\\([^]*?\\n}`));
  assert.ok(found, `production function ${name} exists`);
  return found[0];
}
class Node {
  constructor() {
    const values = new Set();
    this.classList = {contains:x=>values.has(x), add:x=>values.add(x), remove:x=>values.delete(x), toggle:(x,on)=>on?values.add(x):values.delete(x)};
    this.textContent=''; this.innerHTML=''; this.style={}; this.offsetWidth=350;
  }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}
const nodes = new Map();
const $ = selector => { if (!nodes.has(selector)) nodes.set(selector,new Node()); return nodes.get(selector); };
let held=false, nextTimer=0, calls=[], timerCallbacks=new Map(), shopRows=[];
$('#shop-modal').querySelectorAll = () => {
  shopRows=[...$('#shop-rows').innerHTML.matchAll(/class="shop-row ([^"]*)" data-action="([^"]+)"/g)].map(m=>{
    const row=new Node();row.dataset={action:m[2]};if(m[1].includes('disabled'))row.classList.add('disabled');
    row.addEventListener=(type,cb)=>{row[type]=cb;};return row;
  });
  return shopRows;
};
const state={pollCount:0,itemDrawSeq:0,heldCards:null,stats:null};
const context=vm.createContext({state,$,cardQueue:[],cardTimer:null,NAMES:{user:'玩家',sakura:'对手'},
  esc:s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'),
  document:{querySelector:selector=>selector==='.modal.show'?[...nodes.values()].find(n=>n.classList.contains('show')):null},
  BlackTrainView:{active:g=>g?.black_train?.active===true,presentationActive:()=>false,inputLocked:()=>false}, boardMoveHeld:()=>held,
  mapState:{},towerBusy:false,
  naviLogPrefix:()=> 'NAVI', syncBlackTrainUI:()=>{},
  setTimeout:(cb)=>{const id=++nextTimer;timerCallbacks.set(id,cb);return id;}, clearTimeout:id=>timerCallbacks.delete(id),
  setInterval:cb=>{context.interval=cb;return 1;}, clearInterval:()=>{}, performance:{now:()=>10000},
  api:async(...args)=>calls.push(args),poll:()=>{},
  opponent:()=>({portrait:'portrait'}),avatarCropCache:{},DEFAULT_OPPONENT:{portrait:'portrait'},autoCropAvatar:()=>{},
});
vm.runInContext(presentationSource,context);
vm.runInContext(html.slice(html.indexOf('const ORDINARY_ITEMS ='),html.indexOf('/* ---------- 商店货架')),context);
for(const name of ['presentationSnapshot','towerLocked','consumeItemDraw','showCard','showNextCard','closeCard','resetCardQueue','showShop','renderPlayers','stopDieAnimation','showGameLanding','pendingUpgradeCost','cellHTML','syncOrdinaryRollButtons','renderActionBar']) vm.runInContext(fn(name),context);
const game={kind:'monopoly',cash:{user:300,sakura:300},turn:'user',cells:[{name:'商店'}],flags:{user:{},sakura:{}}};
state.game=game;
const pool=vm.runInContext('Object.entries(ORDINARY_ITEMS).map(([key,v])=>({key,...v}))',context);
const pending={cell:0,options:['buy_dice','buy_gain','buy_draw','skip'],items:{dice:{price:90},gain:{price:120},draw:{price:100,pool}}};
context.showShop(pending);
assert.match($('#shop-rows').innerHTML,/data-action="buy_draw"/);
assert.match($('#shop-rows').innerHTML,/¥100/);
assert.match($('#shop-rows').innerHTML,/可抽 4\/4/);
for(const label of ['租金护盾','装修券','冒险契约','判定幸运签']) assert.ok($('#shop-rows').innerHTML.includes(label));
assert.match($('#shop-rows').innerHTML,/调整骰不在抽卡池/);
shopRows.find(row=>row.dataset.action==='buy_draw').click();
assert.equal(calls[0][0],'decide');assert.equal(calls[0][1].action,'buy_draw');
assert.equal($('#shop-modal').classList.contains('show'),false);
game.flags.user={rent_shield:true,adventure_left:2};context.showShop(pending);
assert.match($('#shop-rows').innerHTML,/可抽 2\/4/);
assert.match($('#shop-rows').innerHTML,/已持有，不入池/);
game.cash.user=99;context.showShop(pending);
assert.match($('#shop-rows').innerHTML,/shop-row disabled" data-action="buy_draw"/);
const callCount=calls.length;shopRows.find(row=>row.dataset.action==='buy_draw').click();assert.equal(calls.length,callCount);
assert.equal(context.ordinaryBuffsHTML({kind:'gomoku'},'user'),'');
game.flags.user={pick_dice:true,buff:'gain',buff_left:3,rent_shield:true,upgrade_coupon:true,adventure_left:2,lucky_charm:true};
let buffs=context.ordinaryBuffsHTML(game,'user');
for(const text of ['自定义骰','收入×2','剩余3回合','租金护盾','装修券','冒险契约','剩余2个本人行动','判定幸运签','最多减免¥60'])assert.ok(buffs.includes(text),text);
assert.doesNotMatch(buffs,/特殊骰子（调整骰）/);
assert.match(buffs,/title="[^"]*不跳过等级限制/);
assert.equal(context.ordinaryBuffsHTML({...game,black_train:{active:true}},'user'),'');
assert.match(context.ordinaryBuffsHTML(game,'sakura'),/暂无普通道具/);
context.renderPlayers(game);
assert.match($('#players').innerHTML,/玩家的普通地图/);
assert.match($('#players').innerHTML,/对手的普通地图/);
game.flags.user.adventure_left=1;context.renderPlayers(game);
assert.match($('#players').innerHTML,/剩余1个本人行动/,'flag-only changes invalidate player cache');
context.renderPlayers({...game,black_train:{active:true}});
assert.doesNotMatch($('#players').innerHTML,/ordinary-buffs/);
// Initial loading does not replay the authoritative last draw, including the opponent's draw.
$('#shop-modal').classList.remove('show');
const event={seq:4,player:'sakura',key:'rent_shield',label:'租金护盾',price:100};
context.consumeItemDraw(event);assert.equal(context.cardQueue.length,0);assert.equal(state.itemDrawSeq,4);
state.pollCount=1;context.consumeItemDraw(event);assert.equal(context.cardQueue.length,0);
// Existing modal owns the screen; both ordinary and draw notices retain FIFO order.
$('#dice-modal').classList.add('show');context.showCard('原结果','先展示',false);
context.consumeItemDraw({...event,seq:5});assert.equal(context.cardQueue.length,2);
assert.equal($('#card-modal').classList.contains('show'),false);
$('#dice-modal').classList.remove('show');held=true;context.showNextCard();assert.equal(context.cardQueue.length,2);
held=false;context.showNextCard();assert.equal($('#e-card-title').textContent,'原结果');
assert.equal($('#e-card').classList.contains('item-draw'),false);
context.closeCard();assert.match($('#e-card-title').textContent,/对手 抽到「租金护盾」/);
assert.equal($('#e-card').classList.contains('item-draw'),true);
assert.equal($('#e-card-from').textContent,'商店 · 道具抽卡');
assert.match($('#e-card-flavor').textContent,/减半后消耗/);
context.consumeItemDraw({...event,seq:5});assert.equal(context.cardQueue.length,0);
context.closeCard();held=true;context.consumeItemDraw({...event,seq:6});assert.equal(state.heldCards.length,1);
assert.equal(state.heldCards[0][3],'item-draw');
held=false;context.showCard(...state.heldCards.shift());assert.equal($('#e-card').classList.contains('item-draw'),true);
context.showCard('后续结果','不能被抽卡吞掉',false);context.closeCard();assert.equal($('#e-card-title').textContent,'后续结果');
assert.equal($('#e-card').classList.contains('item-draw'),false);
context.resetCardQueue();assert.equal(context.cardQueue.length,0);assert.equal($('#card-modal').classList.contains('show'),false);
// The production new-game reset and hold release retain the draw cursor/style contract.
assert.match(html,/state\.fxSeq = state\.landingSeq = state\.checkSeq = state\.towerSeq = state\.itemDrawSeq = 0/);
assert.match(fn('endMoveHold'),/for \(const card of cards\) showCard\(\.\.\.card\)/);
assert.match(html,/consumeItemDraw\(data\.itemDraw, moveIncoming\)/);
assert.match(html,/body\.black-train-active \.ordinary-buffs/);
assert.match(html,/@keyframes item-reveal/);
assert.match(html,/prefers-reduced-motion:reduce/);
// New game accepts a lower sequence, and full-pool ownership stays explicit.
state.itemDrawSeq=0;context.consumeItemDraw({...event,seq:1});assert.equal(state.itemDrawSeq,1);
context.resetCardQueue();game.flags.user.adventure_left=2;
assert.match(context.shopDrawPoolHTML(pending.items.draw,game.flags.user),/可抽 0\/4/);
assert.match(context.shopDrawPoolHTML(pending.items.draw,game.flags.user),/全部持有时不可购买/);
// Execute real landing verdict code: lucky rejudge clearly shows the failed first roll.
context.drawDieAt=()=>{};let clock=0;context.performance.now=()=>clock;
context.showGameLanding({player:'user',name:'游戏',roll:6,rolls:[1,6],won:true,reward:'奖励'});
clock=1000;context.interval();
for(const cb of [...timerCallbacks.values()]) cb();
assert.match($('#game-verdict').textContent,/首次 1 点失败 → 重判 6 点/);
assert.match($('#game-verdict').textContent,/过关/);
// Both property tooltip and actionable upgrade button use the same authoritative quote.
Object.assign(context,{currentKind:()=> 'monopoly',scheduleAutoStep:()=>{},GROUPS:{},useStickers:false,ICONS:{property:''}});
Object.assign(context.BlackTrainView,{presentationActive:()=>false,inputLocked:()=>false,bind:()=>{}});
const property={id:0,type:'property',price:300,name:'地产',owner:'user',level:0};
game.cells=[property];game.pending={type:'property',player:'user',cell:0,options:['upgrade','skip'],upgradeCost:90};
assert.equal(context.pendingUpgradeCost(game.pending,property),90);
context.renderActionBar(game,false);assert.match($('#action-bar').innerHTML,/升级 ¥90/);
assert.match(context.cellHTML(property),/升级 ¥90/);
game.pending.upgradeCost=0;context.renderActionBar(game,false);assert.match($('#action-bar').innerHTML,/升级 ¥0/);
assert.match(context.cellHTML(property),/升级 ¥0/);
delete game.pending.upgradeCost;assert.equal(context.pendingUpgradeCost(game.pending,property),150);
assert.match(context.cellHTML(property),/升级 ¥150/);
for(const flag of ['rent_shield','upgrade_coupon','adventure_left','lucky_charm']) assert.match(html,new RegExp(`${flag}:'[^']*[\\u4e00-\\u9fff]`));
console.log('PASS shop items UI: syntax, shop pool/price/actions, buffs/cache, authoritative dedup, FIFO/holds, new game, lucky rejudge and upgrade quote');
