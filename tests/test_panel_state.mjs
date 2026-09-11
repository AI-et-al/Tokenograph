// Optional browser-state regression check: node --test tests/test_panel_state.mjs
// Uses Node's standard library and synthetic data; no runtime dependency is added.
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source=readFileSync(new URL('../tokenograph/panel.html',import.meta.url),'utf8');
const rendering=source.slice(source.indexOf('/* ---------- context ledger ---------- */'),source.indexOf('function renderHistory()'));
const wiring=source.slice(source.indexOf('function wireCtx()'),source.indexOf('function renderAll('));

// Only the table DOM operations used by the actual renderer and delegated click
// handler are needed here. Browser verification separately covers real DOM/layout.
class Table {
  constructor(){this.rows=[];this.listeners={};}
  set innerHTML(value){
    this.markup=value;
    this.rows=[...value.matchAll(/<tr\b([^>]*)>/g)].map(([,attrs])=>{
      const attr=name=>attrs.match(new RegExp('\\b'+name+'="([^"]*)"'))?.[1];
      const classes=new Set((attr('class')||'').split(' '));
      return {dataset:{i:attr('data-i'),p:attr('data-p'),key:attr('data-key')},hidden:/\bhidden\b/.test(attrs),
        classList:{contains:c=>classes.has(c),toggle(c){if(classes.has(c))classes.delete(c);else classes.add(c);return classes.has(c);}}};
    });
  }
  addEventListener(type,listener){this.listeners[type]=listener;}
  querySelectorAll(selector){const i=selector.match(/data-p="([^"]*)"/)[1];return this.rows.filter(r=>r.classList.contains('sub')&&r.dataset.p===i);}
  group(key){return this.rows.find(r=>r.dataset.key===key);}
  click(key){const row=this.group(key);this.listeners.click({target:{closest:selector=>selector==='tr.grp'?row:null}});}
  expanded(key){const row=this.group(key);return row.classList.contains('open')&&this.querySelectorAll(`tr.sub[data-p="${row.dataset.i}"]`).every(r=>!r.hidden);}
}

function fixture(){
  const row=key=>({key,label:key,tokens:100,pct:.1,computed:80,cached:20,cost:.2,
    subs:[{label:'example tool',tokens:100,computed:80,cached:20,cost:.2}]});
  const ledger={window:1000,read_mult:.1,series:[{t:1,m:200,g:{tool_result:100,tool_input:100}}],
    now:{rows:[row('tool_result'),row('tool_input')]},
    cum:{rows:[row('tool_result'),row('tool_input')],requests:1,computed:160,cached:40}};
  const elements={'ctx-now':new Table(),'ctx-cum':new Table(),cv2:{getContext:()=>({}),addEventListener(){}}};
  const context=vm.createContext({D:{ledger},$:id=>elements[id]||(elements[id]={}),
    esc:s=>String(s),fmtTok:String,fmtClockS:String});
  vm.runInContext(rendering+'\n'+wiring+'\nwireCtx();',context);
  const refresh=()=>vm.runInContext('renderNow(null); renderCum();',context);
  refresh();return {ledger,elements,refresh};
}

test('expanded categories survive repeated live refreshes and row reordering',()=>{
  const {ledger,elements,refresh}=fixture(),now=elements['ctx-now'],cum=elements['ctx-cum'];
  now.click('tool_result');cum.click('tool_input');
  for(let i=0;i<4;i++){
    ledger.now.rows.reverse();ledger.cum.rows.reverse();
    ledger.series.push({...ledger.series[0],t:i+2});
    refresh();
    assert.equal(now.expanded('tool_result'),true);
    assert.equal(cum.expanded('tool_input'),true);
    assert.equal(now.expanded('tool_input'),false);
    assert.equal(cum.expanded('tool_result'),false);
  }
  now.click('tool_result');refresh();
  assert.equal(now.expanded('tool_result'),false);
  assert.equal(cum.expanded('tool_input'),true);
});

test('temporarily missing category details do not erase the expansion choice',()=>{
  const {ledger,elements,refresh}=fixture(),now=elements['ctx-now'];
  now.click('tool_result');const row=ledger.now.rows.shift();refresh();
  ledger.now.rows.push({...row,subs:[]});refresh();
  assert.equal(now.group('tool_result').classList.contains('leaf'),true);
  ledger.now.rows[1]=row;refresh();
  assert.equal(now.expanded('tool_result'),true);
});

test('inner tools appear as completion ticks without adding to the wall-time lanes',()=>{
  const direct={k:'t',n:'exec',t0:1,t1:2};
  const D={base:1000,calls:[direct],laps:[],stats:{time:{prefill:0,reasoning:0,generation:0,idle:0}},
    tool_details:[{id:'inner',name:'docs.read',label:'guide',completed_at:1001.5,duration_s:3,status:'completed'}]};
  const context=vm.createContext({D,C:{},fmtInt:String,fmtH:String});
  vm.runInContext(source.slice(source.indexOf('function buildRows()'),source.indexOf('function renderRowLabels()'))+'\nvar result=buildRows();',context);
  const all=context.result.find(r=>r.id==='all'),detail=context.result.find(r=>r.id==='detail:docs.read');
  assert.equal(all.spans.length,1);assert.equal(all.spans[0].t1-all.spans[0].t0,1);
  assert.equal(detail.spans[0].t0,1.5);assert.equal(detail.spans[0].t1,1.5);
  assert.equal(detail.spans[0].c.duration_s,3);
});

test('tool detail expansion survives updates and escapes command text',()=>{
  const table=new Table(),elements={'tool-detail-rows':table};
  const D={base:1000,tool_details:[{id:'one',name:'exec_command',status:'completed',completed_at:1001,
    duration_s:.4,label:'example',detail:'<script>not executable</script>'}]};
  const context=vm.createContext({D,$:id=>elements[id]||(elements[id]={}),fmtInt:String,fmtDur:String,fmtClockS:String,fmtTok:String});
  const render=source.slice(source.indexOf('function renderToolDetails()'),source.indexOf('/* ---------- context ledger ---------- */'));
  const click=source.slice(source.indexOf("  $('tool-detail-rows').addEventListener"),source.indexOf("  document.querySelectorAll('[data-toggle]')"));
  vm.runInContext('const toolDetailExpanded=new Set(); const toolDetailLimits=new Map();\n'+source.match(/^const esc = .*$/m)[0]+'\n'+render+click+'\nrenderToolDetails();',context);
  table.click('exec_command');D.tool_details.push({...D.tool_details[0],id:'two',completed_at:1002});
  vm.runInContext('renderToolDetails();',context);
  assert.equal(table.expanded('exec_command'),true);
  assert.match(table.markup,/&lt;script&gt;not executable&lt;\/script&gt;/);
  assert.equal(table.markup.includes('<script>'),false);
  D.tool_details.push(...Array.from({length:49},(_,i)=>({...D.tool_details[0],id:'extra'+i,completed_at:1003+i})));
  vm.runInContext('renderToolDetails();',context);
  assert.equal(table.rows.filter(r=>r.classList.contains('sub')).length,21);
  table.listeners.click({target:{closest:selector=>selector==='[data-tool-more]'?{dataset:{toolMore:'exec_command'}}:null}});
  vm.runInContext('renderToolDetails();',context);
  assert.equal(table.rows.filter(r=>r.classList.contains('sub')).length,41);
  assert.equal(table.expanded('exec_command'),true);
});
