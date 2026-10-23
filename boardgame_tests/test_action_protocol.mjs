/* Production transport protocol with deterministic HTTP, concurrent requests and recovery. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const sandbox={AbortController};
vm.runInNewContext(await readFile(new URL('../sakura_boardgame/art/lib/board_connection.js',import.meta.url),'utf8'),sandbox);
const {createClient}=sandbox.BoardConnection;
const response=(data,status=200,instance='server-a',type='application/json')=>({ok:status===200,status,headers:{get:key=>key==='content-type'?type:key==='x-board-instance'?instance:null},json:async()=>data});
function fixture(handler) {
  const calls=[],timers=new Map();let timer=0;
  const client=createClient({fetch:async(url,init)=>{const call={url,init,body:init.body?JSON.parse(init.body):undefined};calls.push(call);return handler(call);},setTimeout:(cb,ms)=>{timers.set(++timer,{cb,ms});return timer;},clearTimeout:id=>timers.delete(id)});
  return {client,calls,timers};
}
const snapshot=(version=10,gameSeq=3)=>({version,gameSeq,game:{kind:'monopoly'}});
const ack=(call,extra={})=>({ok:true,requestId:call.body.metadata.requestId,gameSeq:call.body.metadata.gameSeq,version:call.body.metadata.expectedVersion+1,duplicate:false,...extra});
let passed=0;
async function test(name,fn){await fn();passed++;console.log('PASS',name);}
await test('legacy_state_and_actions_without_gameSeq_remain_compatible',async()=>{
  const f=fixture(call=>response(call.body?{ok:false,error:'业务失败'}:{version:1,game:null}));
  await f.client.state(false);const result=await f.client.action('roll');
  assert.equal(result.uncertain,undefined);assert.equal(f.calls[1].body.metadata,undefined);
});
await test('valid_snapshot_enables_complete_metadata_and_ack_advances_revision',async()=>{
  const f=fixture(call=>response(call.body?ack(call):snapshot()));
  await f.client.state(true);await f.client.action('decide',{action:'buy_draw'});await f.client.action('roll');
  const first=f.calls[1].body,second=f.calls[2].body;
  assert.equal(first.action,'decide');assert.equal(first.arguments.action,'buy_draw');
  assert.equal(first.metadata.gameSeq,3);assert.equal(first.metadata.expectedVersion,10);
  assert.equal(second.metadata.expectedVersion,11);assert.notEqual(first.metadata.requestId,second.metadata.requestId);
  assert.equal(typeof first.metadata.requestId,'string');assert.ok(first.metadata.requestId.length<=128);
  assert.equal(f.timers.size,0);
});
await test('concurrent_mutations_are_explicitly_blocked_not_replayed',async()=>{
  let resolve;const f=fixture(call=>call.body?new Promise(r=>{resolve=()=>r(response(ack(call)));}):response(snapshot()));
  await f.client.state(false);const first=f.client.action('roll');
  const second=await f.client.action('roll');assert.equal(second.code,'ACTION_IN_FLIGHT');assert.equal(second.uncertain,undefined);
  assert.equal(f.calls.filter(c=>c.body).length,1);resolve();assert.equal((await first).ok,true);
});
await test('unknown_action_failure_requires_successful_state_before_new_mutation',async()=>{
  let offline=true;const f=fixture(call=>{if(call.body&&offline)throw Error('lost response');return response(call.body?ack(call):snapshot(11));});
  await f.client.state(false);const first=await f.client.action('roll');assert.equal(first.uncertain,true);
  const id=f.calls[1].body.metadata.requestId;
  assert.equal((await f.client.action('roll')).code,'STATE_REQUIRED');assert.equal(f.calls.length,2);
  offline=false;await f.client.state(false);assert.equal((await f.client.action('roll')).ok,true);
  assert.notEqual(f.calls[3].body.metadata.requestId,id,'recovered user action is new, never silent replay');
  assert.equal(f.calls.filter(c=>c.body).length,2);
});
await test('failed_state_cannot_release_uncertain_mutation_barrier',async()=>{
  let stateFails=false;const f=fixture(call=>{if(call.body||stateFails)throw Error('offline');return response(snapshot());});
  await f.client.state(false);await f.client.action('roll');stateFails=true;
  await assert.rejects(f.client.state(false));assert.equal((await f.client.action('roll')).code,'STATE_REQUIRED');
  assert.equal(f.calls.filter(c=>c.body).length,1);
});
await test('state_started_before_uncertain_action_is_not_recovery',async()=>{
  let resolveState,fetches=0;const f=fixture(call=>{
    if(call.body)throw Error('response lost');
    if(fetches++===0)return response(snapshot());
    return new Promise(r=>{resolveState=()=>r(response(snapshot()));});
  });
  await f.client.state(false);const oldPoll=f.client.state(false);await f.client.action('roll');resolveState();await oldPoll;
  assert.equal((await f.client.action('roll')).code,'STATE_REQUIRED');
});
await test('json_conflicts_are_definite_rejections_and_require_state_not_retry',async()=>{
  for(const [code,http] of [['STALE_GAME',409],['STALE_VERSION',409],['REQUEST_ID_REUSED',409],['INVALID_REQUEST_METADATA',400]]){
    const f=fixture(call=>response(call.body?ack(call,{ok:false,code,error:'冲突',version:12}):snapshot(),call.body?http:200));
    await f.client.state(false);const result=await f.client.action('roll');
    assert.equal(result.code,code);assert.equal(result.uncertain,undefined);assert.equal(f.client.getStatus().failures,0);
    assert.equal((await f.client.action('roll')).code,'STATE_REQUIRED');assert.equal(f.calls.length,2);
  }
});
await test('metadata_validation_rejection_without_ack_is_still_definite',async()=>{
  const f=fixture(call=>call.body?response({ok:false,code:'INVALID_REQUEST_METADATA',error:'元数据无效'},400):response(snapshot()));
  await f.client.state(false);const result=await f.client.action('roll');
  assert.equal(result.code,'INVALID_REQUEST_METADATA');assert.equal(result.uncertain,undefined);
  assert.equal((await f.client.action('roll')).code,'STATE_REQUIRED');assert.equal(f.client.getStatus().failures,0);
});
await test('definite_business_failure_can_advance_revision_without_barrier',async()=>{
  const f=fixture(call=>response(call.body?ack(call,{ok:false,error:'没轮到你'}):snapshot()));
  await f.client.state(false);assert.equal((await f.client.action('roll')).uncertain,undefined);
  await f.client.action('roll');assert.equal(f.calls[2].body.metadata.expectedVersion,11);
});
await test('older_duplicate_ack_and_old_poll_never_regress_authoritative_revision',async()=>{
  let poll=0;const f=fixture(call=>response(call.body?ack(call,{version:5,duplicate:true}):snapshot(poll++?8:10)));
  await f.client.state(false);await f.client.action('roll');await f.client.state(false);await f.client.action('roll');
  assert.equal(f.calls[3].body.metadata.expectedVersion,10);
});
await test('new_game_snapshot_resets_version_and_request_identity_remains_unique',async()=>{
  let game=3;const f=fixture(call=>response(call.body?ack(call):snapshot(game===3?20:0,game)));
  await f.client.state(false);await f.client.action('roll');game=4;await f.client.state(false);await f.client.action('roll');
  assert.equal(f.calls[3].body.metadata.gameSeq,4);assert.equal(f.calls[3].body.metadata.expectedVersion,0);
  assert.notEqual(f.calls[1].body.metadata.requestId,f.calls[3].body.metadata.requestId);
});
await test('server_restart_does_not_get_hidden_by_action_response',async()=>{
  let restarted=false;const f=fixture(call=>response(call.body?ack(call,{gameSeq:0,version:0}):snapshot(restarted?0:10,restarted?0:3),200,restarted?'server-b':'server-a'));
  await f.client.state(false);restarted=true;const result=await f.client.action('roll');assert.equal(result.code,'STATE_REQUIRED');
  assert.equal((await f.client.state(false)).changed,true);
  await f.client.action('roll');assert.equal(f.calls[3].body.metadata.gameSeq,0);assert.equal(f.calls[3].body.metadata.expectedVersion,0);
});
await test('malformed_success_ack_and_unknown_HTTP_remain_uncertain',async()=>{
  for(const make of [call=>response(ack(call,{requestId:'wrong'})),call=>response(ack(call,{duplicate:undefined})),call=>response(ack(call),500),()=>response({ok:false,code:'STALE_VERSION'},409,'server-a','text/html')]){
    const f=fixture(call=>call.body?make(call):response(snapshot()));await f.client.state(false);
    assert.equal((await f.client.action('roll')).uncertain,true);assert.equal((await f.client.action('roll')).code,'STATE_REQUIRED');
    assert.equal(f.calls.filter(c=>c.body).length,1);
  }
});
await test('protocol_cannot_silently_downgrade_on_missing_or_invalid_gameSeq',async()=>{
  let bad=false;const f=fixture(()=>response(bad?{version:11,game:null}:snapshot()));await f.client.state(false);bad=true;
  await assert.rejects(f.client.state(false),/状态格式无效/);
  for(const gameSeq of [-1,1.5,'3']){const f=fixture(()=>response(snapshot(0,gameSeq)));await assert.rejects(f.client.state(false),/状态格式无效/);}
});
console.log(`${passed} browser action protocol tests passed.`);
