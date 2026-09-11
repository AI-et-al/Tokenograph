import test from 'node:test';
import assert from 'node:assert/strict';
import {plain,connection,EventDecoder,TerminalView} from '../lib/terminal-view.mjs';

test('SSE preserves split Unicode and split frame boundaries',()=>{
  const events=[],decoder=new EventDecoder(e=>events.push(e));
  const bytes=Buffer.from(': connected\r\n\r\ndata: {"type":"text","data":{"delta":"hello 語"}}\r\n\r\ndata: {"type":"activity"}\n\n');
  for(const byte of bytes)decoder.push(Uint8Array.of(byte));
  assert.equal(events.length,2);assert.equal(events[0].data.delta,'hello 語');
});
test('terminal text cannot carry control sequences or recognizable keys',()=>{
  assert.equal(plain('\x1b[2Jhello\x1b]52;c;secret\x07\x07'),'hello');
  assert.equal(plain('sk-'+'a'.repeat(30)),'[REDACTED KEY]');
});
test('viewer only authenticates to the local companion',async()=>{
  const hash='#'+'a'.repeat(64);
  let calls=0;
  const request=async(url,options)=>{
    calls++;assert.equal(url,'http://127.0.0.1:4321/api/unlock');
    assert.equal(options.method,'POST');assert.equal(options.redirect,'error');
    assert.equal(options.headers.Origin,'http://127.0.0.1:4321');
    assert.equal(options.headers['Content-Type'],'application/json');
    assert.deepEqual(JSON.parse(options.body),{token:hash.slice(1)});
    return new Response('{}',{headers:{'set-cookie':'local_session='+hash.slice(1)+'; HttpOnly; SameSite=Strict; Path=/'}});
  };
  const result=await connection({url:'http://127.0.0.1:4321/'+hash},request);
  assert.equal(result.origin,'http://127.0.0.1:4321');
  assert.equal(result.headers.Cookie,'local_session='+hash.slice(1));
  for(const url of ['https://example.com/'+hash,'http://127.0.0.1.evil/'+hash,'http://user@127.0.0.1/'+hash])await assert.rejects(connection({url},request),/Invalid/);
  assert.equal(calls,1);
});
test('viewer rejects missing, malformed, or mismatched authentication cookies',async()=>{
  const token='a'.repeat(64),launch={url:'http://127.0.0.1:4321/#'+token};
  for(const cookie of [undefined,'session=wrong','session='+'b'.repeat(64),'bad name='+token]){
    await assert.rejects(connection(launch,async()=>new Response('{}',{headers:cookie?{'set-cookie':cookie}:{}})),/Cannot authenticate/);
  }
  await assert.rejects(connection(launch,async()=>new Response('{}',{status:403,headers:{'set-cookie':'session='+token}})),/Cannot authenticate/);
});
test('other thread output is excluded and voice paraphrases do not duplicate Codex output',()=>{
  let output='';const view=new TerminalView('ours',{write:t=>output+=t,isTTY:false});
  view.event({type:'text',data:{threadId:'other',itemId:'a',delta:'hidden'}});
  view.event({type:'transcript',data:{role:'assistant',delta:'spoken paraphrase'}});
  view.event({type:'text',data:{threadId:'ours',itemId:'a',delta:'Actual '}});
  view.event({type:'text',data:{threadId:'ours',itemId:'a',delta:'answer'}});
  assert.equal(output,'\nCODEX\nActual answer');
});
test('ending or switching voice cannot leak another conversation into the view',()=>{
  let output='';const view=new TerminalView('ours',{write:t=>output+=t,isTTY:false});
  view.event({type:'state',data:null});
  view.event({type:'transcript',data:{role:'user',delta:'stale'}});
  view.event({type:'state',data:{taskId:'other'}});
  view.event({type:'transcript',data:{role:'user',delta:'other task'}});
  assert.equal(output,'');
});
