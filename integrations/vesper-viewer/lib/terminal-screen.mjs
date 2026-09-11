import {plain} from './terminal-view.mjs';

// Moonfly Default: https://terminalcolors.com/themes/moonfly/default/
const palette={bg:'080808',ink:'bdbdbd',muted:'949494',cyan:'79dac8',green:'8cc85f',amber:'e3c78a',purple:'cf87e8',rose:'ff5189',red:'ff5454',blue:'80a0ff'};
const rgb=hex=>hex.match(/../g).map(n=>parseInt(n,16)).join(';');
const fg=key=>`\x1b[38;2;${rgb(palette[key]||palette.ink)}m`;
const bg=`\x1b[48;2;${rgb(palette.bg)}m`;
const number=n=>n==null?'—':n>=1e6?(n/1e6).toFixed(2)+'M':n>=1e3?(n/1e3).toFixed(1)+'k':Math.round(n).toString();
const seconds=n=>n==null?'—':n<60?Math.max(0,n).toFixed(1)+'s':n<3600?Math.floor(n/60)+'m '+Math.floor(n%60)+'s':Math.floor(n/3600)+'h '+Math.floor(n/60%60)+'m';
const pct=n=>n==null?'—':(n*100).toFixed(1)+'%';
const segmenter=new Intl.Segmenter('en',{granularity:'grapheme'});
function width(char){return /[\p{Extended_Pictographic}\u1100-\u115f\u2329\u232a\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe10-\ufe19\ufe30-\ufe6f\uff00-\uff60\uffe0-\uffe6]/u.test(char)?2:1;}
export function fit(text,columns){let result='',used=0;for(const {segment} of segmenter.segment(plain(String(text)).replace(/[\r\n\t]/g,' '))){const size=width(segment);if(used+size>columns)break;result+=segment;used+=size;}return result;}
function wrap(text,columns){const lines=[];let line='',used=0;for(const {segment} of segmenter.segment(text)){const size=width(segment);if(used+size>columns){lines.push(line);line='';used=0;}line+=segment;used+=size;}lines.push(line);return lines;}
const span=(colour,text)=>({colour,text});

export function stripRows(data,{now=Date.now()/1000,expanded=true,activity='Connected',connected=true}={}){
  const pulse=Math.floor(now)%2?'◌':'●';
  if(!data)return [[span('cyan',`${pulse} TOKENOGRAPH`),span('amber','  connecting to the selected transcript…')]];
  const {stats:s}=data,t=s.tokens,c=s.context,count=s.counts;
  const cost=s.cost_reported!=null?'$'+s.cost_reported.toFixed(2):s.cost?'~$'+s.cost.total.toFixed(2):'cost — no model price';
  const age=data.usage_at==null?'not reported':seconds(now-data.usage_at)+' old';
  const rows=[
    [span('cyan',`${pulse} TOKENOGRAPH`),span('purple','  '+data.models.join(' / ')),span(connected?'green':'amber','  '+activity)],
    [span('cyan',`CTX ${number(c.used)}/${number(c.window)} ${pct(c.pct)}`),span('green',`  free ${number(Math.max(0,c.window-c.used))}`),span('muted',`  usage ${age}`)],
    [span('muted',`SESSION ${number(t.total)} tok`),span('cyan',`  in ${number(t.prompt_computed)}`),span('green',`  cached ${number(t.prompt_cached)}`),span('purple',`  out ${number(t.completion)} · reasoning ${number(t.reasoning)}`)],
    [span('green',`CACHE ${pct(t.cache_hit_ratio)} session`),span('rose',`  ${cost}`),span('amber',`  tools ${count.tools} · errors ${count.tool_errors}`),span('muted',`  compact ${count.compactions} · rebuilds ${data.rebuilds}`)],
  ];
  if(expanded){
    rows.push([span('muted','~WINDOW '),...data.window_rows.filter(r=>r.tokens>0).slice(0,6).map((r,i)=>span(['cyan','purple','amber','green','muted','blue'][i],`${r.key==='residual'?'unmeasured':r.key} ${number(r.tokens)}  `))]);
    rows.push([span('muted',`TIME ${seconds(now-data.base)}`),span('purple',`  ~model ${seconds(s.time.prefill+s.time.reasoning+s.time.generation)}`),span('amber',`  tools ${seconds(s.time.tools)}`),span('muted',`  idle ${seconds(s.time.idle)} · split at last snapshot`)]);
  }
  if(data.running.length){
    for(const call of data.running.slice(expanded?-3:-1))rows.push([span('amber',`RUN ${call.n} · ${seconds(now-call.started_at)}`),span('ink','  '+call.l)]);
  }else rows.push([span('muted','RUN — no unfinished tool calls observed')]);
  const observed=data.observed_tools||[];
  for(const item of observed.slice(0,expanded?3:1))rows.push([span(item.status==='failed'?'red':'green',`${item.status==='failed'?'×':'✓'} ${item.name} · ${seconds(item.duration_s)}`),span('ink','  '+item.label)]);
  if(!observed.length)for(const item of data.recent.slice(0,expanded?2:1))rows.push([span(item.err?'red':'green',`${item.err?'×':'✓'} ${item.n} · ${seconds(item.ended_at-item.started_at)}`),span('ink','  '+item.l)]);
  const limits=Object.values(data.limits||{}).filter(Boolean).map(l=>`${l.window_minutes===10080?'week':l.window_minutes%60===0?l.window_minutes/60+'h':l.window_minutes+'m'} ${l.used_percent}% used`).join(' · ');
  rows.push([span('blue',limits?`LIMITS ${limits}  `:''),span('muted',`snapshot ${seconds(now-data.generated_at)} old · local monitor $0`)]);
  return rows;
}

export class TerminalScreen {
  constructor(output=process.stdout){this.output=output;this.lines=[''];this.data=null;this.expanded=true;this.offset=0;this.activity='Connected';this.connected=true;this.closed=false;this.seen=new Set();this.primed=false;}
  append(text){const chunks=plain(text).replace(/\r/g,'').replace(/\t/g,'    ').split('\n');this.lines[this.lines.length-1]+=chunks.shift();this.lines.push(...chunks);if(this.lines.length>1500)this.lines.splice(0,this.lines.length-1500);}
  update(data){
    this.data=data;
    for(const item of [...data.observed_tools].reverse()){
      if(!this.seen.has(item.id)&&this.primed)this.append(`\n${item.status==='failed'?'×':'✓'} ${item.name} · ${seconds(item.duration_s)} · ${item.label}\n`);
      this.seen.add(item.id);
    }
    if(this.seen.size>1000)this.seen=new Set(data.observed_tools.map(i=>i.id));
    this.primed=true;
  }
  start(){this.output.write('\x1b[?1049h\x1b[?25l'+bg+fg('ink')+'\x1b[2J\x1b[H');}
  close(){if(!this.closed){this.closed=true;this.output.write('\x1b[0m\x1b[?25h\x1b[?1049l');}}
  key(key){if(key==='\x0f'){this.expanded=!this.expanded;}else if(key==='\x1b[5~'){this.offset+=10;}else if(key==='\x1b[6~'){this.offset=Math.max(0,this.offset-10);}else if(['\x1b[F','\x1b[4~'].includes(key)){this.offset=0;}}
  render(){
    if(this.closed)return;
    const cols=Math.max(10,(this.output.columns||100)-1),height=Math.max(4,this.output.rows||30);
    let rows=stripRows(this.data,{expanded:this.expanded,activity:this.activity,connected:this.connected});
    // Keep a readable transcript at small sizes; expanded detail remains opt-in.
    if(rows.length>height-6)rows=stripRows(this.data,{expanded:false,activity:this.activity,connected:this.connected});
    if(rows.length>height-4)rows=rows.slice(0,Math.max(1,height-4));
    const contentHeight=Math.max(1,height-rows.length-2);
    const history=this.lines.flatMap(line=>wrap(line,cols));
    this.offset=Math.min(this.offset,Math.max(0,history.length-contentHeight));
    const end=Math.max(0,history.length-this.offset),visible=history.slice(Math.max(0,end-contentHeight),end);
    let buffer='\x1b[H'+bg;
    for(let i=0;i<contentHeight;i++){
      const line=visible[i]||'',colour=line==='YOU'?'cyan':line==='CODEX'?'purple':line.startsWith('✓')?'green':line.startsWith('×')?'red':'ink';
      buffer+=fg(colour)+fit(line,cols)+'\x1b[K\r\n';
    }
    buffer+=fg('muted')+'─'.repeat(cols)+'\x1b[K\r\n';
    for(const row of rows){let used=0;for(const s of row){const value=fit(s.text,cols-used);buffer+=fg(s.colour)+value;for(const {segment} of segmenter.segment(value))used+=width(segment);}buffer+='\x1b[K\r\n';}
    buffer+=fg('muted')+fit(`Ctrl+O ${this.expanded?'less':'more'} detail · PgUp/PgDn scroll · End live · Ctrl+C closes view${this.offset?' · SCROLLED':''}`,cols)+'\x1b[K';
    this.output.write(buffer);
  }
}
