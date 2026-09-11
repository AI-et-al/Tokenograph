import test from 'node:test';
import assert from 'node:assert/strict';
import {TerminalScreen,stripRows,fit} from '../lib/terminal-screen.mjs';
const data={models:['test-model'],generated_at:100,base:0,usage_at:90,running:[],recent:[],observed_tools:[],window_rows:[],rebuilds:0,limits:{primary:{used_percent:67,window_minutes:10080}},stats:{tokens:{total:1000,prompt_computed:100,prompt_cached:800,completion:100,reasoning:50,cache_hit_ratio:8/9},context:{used:900,window:10000,pct:.09},counts:{tools:3,tool_errors:0,compactions:1},time:{prefill:1,reasoning:2,generation:3,tools:4,idle:5},cost:null,cost_reported:null}};
test('strip keeps clock animation separate from authoritative usage and missing prices',()=>{
  const render=now=>stripRows(data,{now}).flat().map(s=>s.text).join('\n');
  assert.match(render(100),/usage 10.0s old/);assert.match(render(110),/usage 20.0s old/);
  assert.match(render(100),/SESSION 1.0k tok/);assert.match(render(110),/SESSION 1.0k tok/);
  assert.match(render(100),/cost — no model price/);assert.match(render(100),/week 67% used/);
  assert.equal(JSON.stringify(data).includes('~$'),false);
});
test('Ctrl+O changes details; paging and End preserve bounded live following',()=>{
  let output='';const terminal={write:t=>output+=t,columns:50,rows:20};const screen=new TerminalScreen(terminal);
  screen.update(data);screen.append(Array.from({length:100},(_,i)=>'line '+i).join('\n'));
  screen.key('\x0f');assert.equal(screen.expanded,false);screen.key('\x1b[5~');assert.equal(screen.offset,10);
  screen.render();assert.match(output,/\x1b\[38;2;121;218;200m/);
  screen.key('\x1b[F');assert.equal(screen.offset,0);
  screen.close();screen.close();assert.equal(output.split('\x1b[?1049l').length,2);
});
test('snapshot replay does not flood history and nested completion is shown once',()=>{
  const screen=new TerminalScreen({write(){}});
  const old={id:'old',name:'read',label:'file',status:'completed',duration_s:.4};
  screen.update({...data,observed_tools:[old]});assert.equal(screen.lines.join(''),'');
  const next={id:'new',name:'exec_command',label:'rg query',status:'failed',duration_s:.2};
  screen.update({...data,observed_tools:[next,old]});screen.update({...data,observed_tools:[next,old]});
  assert.equal(screen.lines.join('\n').match(/exec_command/g).length,1);
});
test('terminal content cannot escape its width or inject terminal commands',()=>{
  assert.equal(fit('\x1b[2Jhello',3),'hel');assert.equal(fit('語語abc',5),'語語a');
  assert.equal(fit('🎵abc',3),'🎵a');assert.equal(fit('x\n\ty',4),'x  y');
});
