#!/usr/bin/env node
// Compare the actual baseline and candidate chart functions, with equal outputs.
// Run from the repo root: node scripts/benchmark-chart-cpu.mjs [base-sha] [output]
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { randomUUID } from 'node:crypto';
import { writeFileSync, unlinkSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { isDeepStrictEqual } from 'node:util';

const requireDb = createRequire(new URL('../packages/db/package.json', import.meta.url));
const { createJiti } = requireDb('jiti');
const jiti = createJiti(import.meta.url);
const base = process.argv[2] || '1eea86324ce65298b3fd7c39731b0bcbed9db646';
const output = resolve(process.argv[3] || '.context/chart-cpu-experiments.json');
const temporary = [];
function baseline(path) {
  const target = resolve(dirname(path), `.benchmark-${randomUUID()}.ts`);
  writeFileSync(target, execFileSync('git', ['show', `${base}:${path}`]));
  temporary.push(target);
  return jiti.import(target);
}
const median = values => [...values].sort((a,b) => a-b)[Math.floor(values.length/2)];
function measure(name, control, candidate) {
  if (!isDeepStrictEqual(control(), candidate())) throw new Error(`${name}: output changed`);
  const controlTimes=[], candidateTimes=[];
  for(let i=0;i<5;i++) {
    const runs = i%2 ? [[candidate,candidateTimes],[control,controlTimes]] : [[control,controlTimes],[candidate,candidateTimes]];
    for(const [fn,times] of runs) { const start=performance.now(); fn(); times.push(performance.now()-start); }
  }
  return {experiment:name, result_equal:true, repeats:5, control_ms:median(controlTimes),candidate_ms:median(candidateTimes), speedup:median(controlTimes)/median(candidateTimes)};
}
try {
  const { compute: oldCompute } = await baseline('packages/db/src/engine/compute.ts');
  const { compute } = await jiti.import(resolve('packages/db/src/engine/compute.ts'));
  const { groupByLabels: oldGroup } = await baseline('packages/common/src/group-by-labels.ts');
  const { groupByLabels } = await jiti.import(resolve('packages/common/src/group-by-labels.ts'));
  const dates=Array.from({length:365},(_,i)=>new Date(Date.UTC(2025,0,1+i)).toISOString());
  const definitions=[{id:'A',type:'event',name:'answer',segment:'event',filters:[]},{id:'B',type:'event',name:'generation',segment:'event',filters:[]},{id:'C',type:'formula',formula:'A/B*100'},{id:'D',type:'formula',formula:'C-A'}];
  const series=Array.from({length:12},(_,g)=>definitions.slice(0,2).map((definition,index)=>({
    id:`${index}-${g}`,definitionId:definition.id,definitionIndex:index,name:[definition.name,`group-${g}`],context:{filters:[]},definition,
    data:dates.filter((_,i)=>index===0 || i%17!==0).map((date,i)=>({date,count:index===0?i%7+3:i%11,total_count:index===0?120:30})),
  }))).flat();
  const rows=Array.from({length:200},(_,g)=>dates.filter((_,i)=>i%13!==g%13).map((date,i)=>({date,count:i,label_0:'answer',label_1:`group-${g}`}))).flat();
  const results=[
    measure('26 Compile formula once across dates and breakdowns',()=>oldCompute(series,definitions),()=>compute(series,definitions)),
    measure('27 Index dates when aligning large chart series',()=>oldGroup(rows),()=>groupByLabels(rows)),
  ];
  mkdirSync(dirname(output),{recursive:true});writeFileSync(output,JSON.stringify({base,results},null,2));
  console.log(JSON.stringify(results,null,2));
} finally {for(const path of temporary) unlinkSync(path);}
