/* Run the production info renderer; no browser or service required. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const html = await readFile(new URL('../sakura_boardgame/board.html', import.meta.url), 'utf8');
const start = html.indexOf('function renderMonopolyInfo(game) {');
const end = html.indexOf('\nfunction renderActionBar(', start);
assert.ok(start >= 0 && end > start, 'production info renderer is present');
const nodes = {'#info-card': {style: {display:'none'}}, '#info': {innerHTML:''}};
const context = vm.createContext({
  $: selector => nodes[selector],
  GROUPS: {pink: {label:'樱花街'}},
  NAMES: {user:'玩家', sakura:'樱'}
});
vm.runInContext(html.slice(start, end), context);
const render = game => {
  context.renderMonopolyInfo(game);
  assert.equal(nodes['#info-card'].style.display, '');
  return nodes['#info'].innerHTML;
};

const base = {move_count:80, dice_turn:90, max_rounds:86, cells:[]};
const returned = render(base);
assert.match(returned, /^经营回合 80 \/ 86<br>/);
assert.match(returned, /总掷骰 90 次（含黑列车探险）/);
assert.doesNotMatch(returned, /回合 90 \/ 86/);
assert.match(returned, /尚无地产主/);
assert.equal(base.move_count, 80, 'rendering must not change settlement progress');
assert.equal(base.dice_turn, 90);

const active = render({...base, black_train:{active:true}});
assert.match(active, /^经营回合 80 \/ 86<br>/, 'expedition does not advance ordinary round display');
const legacy = render({move_count:12, max_rounds:86, cells:[]});
assert.match(legacy, /^经营回合 12 \/ 86<br>/);
assert.match(legacy, /总掷骰 12 次（含黑列车探险）/);
assert.doesNotMatch(legacy, /undefined|NaN/);
const beginning = render({move_count:0, dice_turn:0, max_rounds:86, cells:[]});
assert.match(beginning, /^经营回合 0 \/ 86<br>/);
assert.match(beginning, /总掷骰 0 次/);
const finished = render({...base, move_count:86, dice_turn:96, winner:'user'});
assert.match(finished, /^经营回合 86 \/ 86<br>/);
assert.match(finished, /总掷骰 96 次/);
const propertyInfo = render({...base, cells:[
  {type:'property', group:'pink', name:'一号店', owner:'user', level:1},
  {type:'property', group:'pink', name:'二号店', owner:'user', level:0}
]});
assert.match(propertyInfo, /垄断：/);
assert.match(propertyInfo, /樱花街 → 玩家（租金×2）/);
assert.match(propertyInfo, /一号店（玩家·升级）/);
console.log('PASS production round display: ordinary progress, expedition totals, legacy saves, zero, endgame and property info');
