import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {connection,EventDecoder,TerminalView} from './lib/terminal-view.mjs';
import {TerminalScreen} from './lib/terminal-screen.mjs';
import {tokenographFeed} from './lib/tokenograph-feed.mjs';

const check=process.argv.includes('--check'),abort=new AbortController();
let view,screen,tick,limit,stopFeed,telemetry,closed=false;const counts={};
const onKey=key=>{if(key.includes('\x03'))stop();else screen?.key(key);};
function cleanup(){clearInterval(tick);clearTimeout(limit);stopFeed?.();view?.clearStatus();screen?.close();if(process.stdin.isTTY&&screen){process.stdin.setRawMode(false);process.stdin.off('data',onKey);process.stdin.pause();}}
function stop(){if(closed)return;closed=true;abort.abort();cleanup();if(!check)process.stdout.write('\nTokenograph terminal viewer closed. The companion and Codex keep running.\n');}
process.once('SIGINT',stop);process.once('SIGTERM',stop);
try {
  if(!process.env.TOKENOGRAPH_COMPANION_DATA_DIR)throw new Error('Set TOKENOGRAPH_COMPANION_DATA_DIR to the running companion data directory.');
  const launch=JSON.parse(await readFile(join(process.env.TOKENOGRAPH_COMPANION_DATA_DIR,'launch.json'),'utf8'));
  const {origin,headers}=await connection(launch);
  const readState=async()=>{
    const response=await fetch(origin+'/api/state',{headers,signal:AbortSignal.timeout(5000),redirect:'error'});
    if(!response.ok)throw new Error('Cannot read companion state. Open the companion with its launcher.');
    return response.json();
  };
  const state=await readState();
  if(!state.selected)throw new Error('Select a task in the companion first.');
  const task=state.selected,expected=process.argv.find(a=>a.startsWith('--thread='))?.slice(9);
  if(expected&&expected!==task.id)throw new Error('The companion has a different task selected. Select the requested thread first.');
  const fullScreen=!check&&!process.argv.includes('--plain')&&process.stdout.isTTY&&process.stdin.isTTY;
  if(fullScreen){screen=new TerminalScreen();screen.start();process.stdin.setEncoding('utf8');process.stdin.setRawMode(true);process.stdin.on('data',onKey);process.stdin.resume();}
  const output=check?{write(){},isTTY:false}:screen?{write:t=>screen.append(t),isTTY:false}:process.stdout;
  view=new TerminalView(task.id,output);
  if(!check){
    view.line('TOKENOGRAPH  /  LIVE TERMINAL VIEW','36');
    view.line(task.name,'37');view.line(`${task.model}${task.effort?' · '+task.effort:''}  ·  ${task.cwd}`);
    view.line('Thread '+task.id);
    view.line('Your speech, Codex responses and activity appear here as they arrive.');
    view.line('Read-only: speak or type in the companion. Closing this window does not stop work.');
    const history=(task.history||[]).slice(-2);
    if(history.length){view.line('\nContext captured when the companion attached:');for(const m of history){const text=(m.content||[]).map(c=>c.text||'').join('\n');view.text(m.role==='user'?'YOU':'CODEX','history',text+'\n');}}
    view.line('\n── Live updates from now ──','36');
  }
  stopFeed=tokenographFeed(task.id,data=>{telemetry=data;screen?.update(data);},message=>view.line(message,'33'));
  tick=setInterval(()=>{if(screen){screen.activity=view.activity;screen.connected=view.connected;screen.render();}else view.tick();},screen?250:1000);
  if(check)limit=setTimeout(()=>abort.abort(),6000);
  while(!abort.signal.aborted){
    try {
      // Check selection on each reconnect; never silently switch the displayed thread.
      const current=await readState();
      if(current.selected?.id!==task.id){view.line('The companion changed tasks. Reopen this view to follow the new selection.','33');break;}
      const response=await fetch(origin+'/api/events',{headers,signal:abort.signal,redirect:'error'});
      if(!response.ok)throw new Error('Live event connection returned '+response.status);
      view.connected=true;
      const decoder=new EventDecoder(event=>{counts[event.type]=(counts[event.type]||0)+1;view.event(event);});
      if(check)counts.connected=(counts.connected||0)+1;
      for await(const chunk of response.body)decoder.push(chunk);
      if(!abort.signal.aborted)throw new Error('Live event connection ended.');
    }catch(error){
      if(abort.signal.aborted)break;
      view.connected=false;view.activity='Reconnecting';view.line('Connection interrupted; reconnecting in two seconds.','33');
      await new Promise(resolve=>{const timer=setTimeout(resolve,2000);abort.signal.addEventListener('abort',()=>{clearTimeout(timer);resolve();},{once:true});});
    }
  }
  if(check)console.log(JSON.stringify({test:'read-only live terminal connection',threadId:task.id,events:counts,telemetry:telemetry?{sessionId:telemetry.session_id,observedTools:telemetry.observed_tools.length,confirmedTokens:telemetry.stats.tokens.total}:null,modelCalls:0,voiceInterrupted:false}));
}catch(error){console.error(error.message);process.exitCode=1;}
finally{cleanup();}
