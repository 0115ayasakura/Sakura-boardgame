/* Exercise the real classic-script transport with deterministic fetch/timer doubles. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const sandbox={AbortController};
vm.runInNewContext(await readFile(new URL('../sakura_boardgame/art/lib/board_connection.js',import.meta.url),'utf8'),sandbox);
const {createClient,ConnectionError}=sandbox.BoardConnection;
function fixture(fetch) {
  let next=0;const timers=new Map(),seen=[];
  const client=createClient({fetch,onStatus:s=>seen.push(s),setTimeout:(fn,ms)=>{const id=++next;timers.set(id,{fn:()=>{timers.delete(id);fn();},ms});return id;},clearTimeout:id=>timers.delete(id)});
  return {client,timers,seen};
}
const response=(data,{status=200,type='application/json',instance='one'}={})=>({ok:status===200,status,headers:{get:k=>k==='content-type'?type:k==='x-board-instance'?instance:null},json:async()=>data});
let passed=0;
async function test(name,fn){await fn();passed++;console.log('PASS',name);}
await test('state_success_single_timer_and_no_store',async()=>{
  const calls=[],f=fixture(async(url,init)=>{calls.push({url,init});return response({version:1,game:null});});
  const data=await f.client.state(true);assert.equal(data.changed,false);assert.equal(calls[0].url,'/state?busy=1&visible=0');assert.equal(calls[0].init.cache,'no-store');assert.equal(f.timers.size,0);
  let polls=0;f.client.schedule(()=>polls++);f.client.schedule(()=>polls++);assert.equal(f.timers.size,1);const item=[...f.timers.values()][0];assert.equal(item.ms,1000);item.fn();assert.equal(polls,1);f.client.dispose();assert.equal(f.timers.size,0);
});
await test('network_backoff_is_bounded_and_success_recovers',async()=>{
  let online=false;const f=fixture(async()=>{if(!online)throw Error('offline');return response({version:2,game:null});});
  for(let i=0;i<8;i++)await assert.rejects(f.client.state(false),ConnectionError);
  assert.equal(f.client.getStatus().failures,8);assert.equal(f.client.getStatus().retryMs,15000);
  online=true;await f.client.state(false);assert.equal(f.client.getStatus().failures,0);assert.equal(f.client.getStatus().retryMs,1000);
});
await test('http_html_bad_json_and_invalid_state_are_not_success',async()=>{
  for(const value of [response({}, {status:503}),response({}, {type:'text/html'}),response([]),response({version:1}),response({version:'1',game:null})]){
    const f=fixture(async()=>value);await assert.rejects(f.client.state(false),ConnectionError);assert.equal(f.client.getStatus().connected,false);assert.equal(f.timers.size,0);
  }
  const f=fixture(async()=>({...response({}),json:async()=>{throw SyntaxError('broken');}}));await assert.rejects(f.client.state(false),ConnectionError);
});
await test('request_timeout_aborts_and_releases_deadline',async()=>{
  let signal;const f=fixture(async(url,init)=>new Promise((resolve,reject)=>{signal=init.signal;signal.addEventListener('abort',()=>reject(Object.assign(Error('aborted'),{name:'AbortError'})));}));
  const pending=f.client.state(false);assert.equal(f.timers.size,1);[...f.timers.values()][0].fn();await assert.rejects(pending,/连接超时/);assert.equal(signal.aborted,true);assert.equal(f.timers.size,0);
});
await test('action_failure_returns_uncertain_without_retry',async()=>{
  let calls=0;const f=fixture(async()=>{calls++;throw Error('response lost');});const value=await f.client.action('roll');assert.equal(calls,1);assert.equal(value.ok,false);assert.equal(value.uncertain,true);assert.match(value.error,/勿重复点击/);assert.equal(f.timers.size,0);
});
await test('game_rejection_is_not_connection_failure',async()=>{
  const calls=[];const f=fixture(async(url,init)=>{calls.push({url,init});return response({ok:false,error:'没轮到你'});});
  const value=await f.client.action('place',{row:1,col:2});assert.equal(value.error,'没轮到你');assert.equal(value.uncertain,undefined);assert.equal(f.client.getStatus().failures,0);assert.equal(calls[0].url,'/api/action');assert.equal(calls[0].init.headers['Content-Type'],'application/json');assert.equal(JSON.parse(calls[0].init.body).arguments.col,2);
});
await test('new_server_instance_detected_even_with_lower_version',async()=>{
  let instance='old',version=100;const f=fixture(async()=>response({version,game:null},{instance}));
  assert.equal((await f.client.state(false)).changed,false);instance='new';version=1;assert.equal((await f.client.state(false)).changed,true);assert.equal((await f.client.state(false)).changed,false);
});
await test('missing_instance_header_transition_detected_without_fresh_page_loop',async()=>{
  let instance=null;const f=fixture(async()=>response({version:1,game:null},{instance}));
  assert.equal((await f.client.state(false)).changed,false);
  assert.equal((await f.client.state(false)).changed,false);
  instance='new';assert.equal((await f.client.state(false)).changed,true);
  instance=null;assert.equal((await f.client.state(false)).changed,true);
  assert.equal((await f.client.state(false)).changed,false);
});
await test('only_visible_document_sends_presence',async()=>{
  const calls=[],f=fixture(async url=>{calls.push(url);return response({version:1,game:null});});
  sandbox.document={visibilityState:'visible'};await f.client.state(false);
  assert.equal(calls.at(-1),'/state?busy=0&visible=1');
  sandbox.document.visibilityState='hidden';await f.client.state(false);
  assert.equal(calls.at(-1),'/state?busy=0&visible=0');
  delete sandbox.document;
});
console.log(`${passed} transport tests passed.`);
