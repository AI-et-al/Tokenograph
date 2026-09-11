import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createInterface} from 'node:readline';

export function tokenographFeed(threadId,onData,onError){
  if(!/^[a-f0-9-]{36}$/.test(threadId))throw new Error('Invalid thread ID for Tokenograph.');
  const cwd=process.env.TOKENOGRAPH_DIR||fileURLToPath(new URL('../../../',import.meta.url));
  const child=spawn(process.env.TOKENOGRAPH_PYTHON||'python3',['-m','tokenograph.live',threadId],{cwd,stdio:['ignore','pipe','pipe']});
  let stopped=false,reported=false;
  const fail=message=>{if(!stopped&&!reported){reported=true;onError(message);}};
  child.on('error',()=>fail('Tokenograph collector could not start. Check TOKENOGRAPH_DIR and Python.'));
  // No transcript content or arbitrary Python exception text enters the viewer.
  child.stderr.on('data',()=>{});
  child.on('exit',code=>{if(code!==0)fail('Tokenograph collector stopped. Transcript view remains connected.');});
  createInterface({input:child.stdout}).on('line',line=>{
    try{const data=JSON.parse(line);if(data.session_id===threadId)onData(data);else fail('Tokenograph returned another session; telemetry was rejected.');}
    catch{fail('Tokenograph emitted an invalid snapshot.');}
  });
  return ()=>{stopped=true;child.kill('SIGTERM');};
}
