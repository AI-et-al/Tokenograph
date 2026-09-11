import {scrub} from './scrub.mjs';

// Transcript content is text, never terminal control sequences.
export function plain(text) {
  return scrub(text).replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g,'')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'')
    .replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g,'');
}

export function connection(launch) {
  const url=new URL(launch.url);
  if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.username||url.password||!/^#[a-f0-9]{64}$/.test(url.hash))
    throw new Error('Invalid local Vesper launcher. Open Vesper first.');
  return {origin:url.origin,headers:{Cookie:'vesper='+url.hash.slice(1)}};
}

export class EventDecoder {
  constructor(onEvent){this.onEvent=onEvent;this.pending='';this.decoder=new TextDecoder();}
  push(chunk) {
    this.pending+=typeof chunk==='string'?chunk:this.decoder.decode(chunk,{stream:true});
    let match;
    while((match=/\r?\n\r?\n/.exec(this.pending))) {
      const block=this.pending.slice(0,match.index);this.pending=this.pending.slice(match.index+match[0].length);
      const data=block.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).replace(/^ /,'')).join('\n');
      if(data){try{this.onEvent(JSON.parse(data));}catch(error){if(!(error instanceof SyntaxError))throw error;}}
    }
    if(this.pending.length>2_000_000)throw new Error('Vesper sent an oversized event.');
  }
}

export class TerminalView {
  constructor(threadId,output=process.stdout) {
    this.threadId=threadId;this.output=output;this.speaker=null;this.lineOpen=false;this.statusVisible=false;
    this.activity='Connected';this.connected=true;this.started=Date.now();this.lastActivity=null;this.voiceTaskId=threadId;
  }
  colour(code,text){return this.output.isTTY?`\x1b[${code}m${text}\x1b[0m`:text;}
  clearStatus(){if(this.statusVisible){this.output.write('\r\x1b[2K');this.statusVisible=false;}}
  line(text,code='90') {
    this.clearStatus();if(this.lineOpen)this.output.write('\n');this.lineOpen=false;this.speaker=null;
    this.output.write(this.colour(code,plain(text))+'\n');
  }
  text(speaker,id,delta) {
    this.clearStatus();const key=speaker+':'+id;
    if(this.speaker!==key){if(this.lineOpen)this.output.write('\n');this.output.write('\n'+this.colour(speaker==='YOU'?'36':'35',speaker)+'\n');this.speaker=key;}
    const value=plain(delta);this.output.write(value);this.lineOpen=!value.endsWith('\n');
  }
  event({type,data}) {
    if(type==='state'){this.voiceTaskId=data?.taskId||null;return;}
    data=data||{};
    if(data.threadId&&data.threadId!==this.threadId)return;
    if(type==='text')this.text('CODEX',data.itemId||'response',data.delta||'');
    else if(type==='transcript'&&data.role==='user'&&this.voiceTaskId===this.threadId)this.text('YOU','speech',data.delta||'');
    else if(type==='activity') {
      this.activity=data.text||'Working';
      if(this.activity!==this.lastActivity&&!['Preparing a response','Codex is working.'].includes(this.activity))this.line('  '+this.activity,data.busy?'33':'32');
      this.lastActivity=this.activity;
    } else if(type==='request')this.line('  Decision needed — use the controls in Vesper.','33');
    else if(type==='notice')this.line('  '+(data.text||''),data.level==='error'?'31':'33');
    else if(type==='answer'&&data.status!=='completed')this.line('  Codex '+data.status+': '+(data.error||''),'31');
  }
  tick() {
    if(!this.output.isTTY||this.lineOpen)return;
    const seconds=Math.floor((Date.now()-this.started)/1000),time=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
    const label=`${this.connected?'●':'○'} ${time}  ${this.activity}  ·  watching only · Ctrl-C closes this view`;
    this.clearStatus();this.output.write(this.colour(this.connected?'32':'33',label.slice(0,Math.max(10,(this.output.columns||100)-1))));this.statusVisible=true;
  }
}
