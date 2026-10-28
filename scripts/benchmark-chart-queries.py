#!/usr/bin/env python3
"""Run 25 paired query experiments on a dedicated LOCAL synthetic dataset.

Requires local OpenPanel ClickHouse migrations. Creates openpanel_perf; never
connects to production or changes application data. Five interleaved paired
samples follow warmups. Results include SQL, settings and equality evidence.
"""
import urllib.request, urllib.parse, json, time, statistics, pathlib, argparse, subprocess
URL='http://localhost:8123/'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',default='.context/chart-query-experiments.json')
args=parser.parse_args()
plan=json.loads(subprocess.check_output(['node',str(pathlib.Path(__file__).with_name('benchmark-chart-query-plan.mjs'))],text=True))
out=pathlib.Path(args.output)
out.parent.mkdir(parents=True,exist_ok=True)
def call(sql,settings=None):
 params={'database':'openpanel_perf','max_execution_time':'15','max_memory_usage':'1500000000',**(settings or {})}
 req=urllib.request.Request(URL+'?'+urllib.parse.urlencode(params),data=sql.encode())
 with urllib.request.urlopen(req,timeout=25) as r: return r.read()
def command(sql):
 req=urllib.request.Request(URL,data=sql.encode())
 with urllib.request.urlopen(req,timeout=90) as r:return r.read()
command('CREATE DATABASE IF NOT EXISTS openpanel_perf')
command('CREATE TABLE IF NOT EXISTS openpanel_perf.events AS openpanel.events')
command('CREATE TABLE IF NOT EXISTS openpanel_perf.groups AS openpanel.groups')
if int(call(plan['counts']['events']))==0:
 command("""INSERT INTO openpanel_perf.events (name,project_id,profile_id,device_id,session_id,created_at,country,groups,properties,revenue)
 SELECT ['answer','generation','payment','screen_view'][1+number%4],'bench',concat('u',toString(number%25000)),concat('d',toString(number%50000)),concat('s',toString(number%100000)),toDateTime('2026-10-01')+number%2300000,['US','GB','IN'][1+number%3],[],map('platform',['web','ios','android'][1+number%3],'exactSourceName',concat('source',toString(number%100)),'amount',toString(number%70),'payload',repeat(hex(cityHash64(number)),16)),number%4000 FROM numbers(1200000)""")
if int(call(plan['counts']['groups']))==0:
 command("""INSERT INTO openpanel_perf.groups (id,project_id,type,name,properties,created_at,version) SELECT concat('g',toString(number)),if(number%3=0,'other','bench'),'organization','test',map('stripe','1'),now(),toUnixTimestamp(now()) FROM numbers(40000)""")
 command("""INSERT INTO openpanel_perf.groups (id,project_id,type,name,properties,created_at,version) VALUES ('census','bench','analytics_census','Paid subscribers',map('stripe','5000','apple','940','google','230'),now()-100,toUnixTimestamp(now())-100),('census','bench','analytics_census','Paid subscribers',map('stripe','4885','apple','931','google','224'),now(),toUnixTimestamp(now()))""")
if int(call(plan['counts']['snapshots']))==0:
 command("""INSERT INTO openpanel_perf.events (name,project_id,profile_id,device_id,session_id,created_at,groups,properties) SELECT 'Active Paid Subscribers Snapshot','bench','','','',toDateTime('2026-10-01')+number*86400,['census'],map('snapshotSchemaVersion','2','stripe',toString(5000-number),'apple','931','google','224') FROM numbers(27)""")
print('Dataset ready',flush=True)
experiments=plan['experiments']
results=[]
def run(sql,settings):
 qs=sql if isinstance(sql,list) else [sql]; data=[];elapsed=0;rows=0;bytes_=0;t=time.perf_counter()
 for q in qs:
  d=json.loads(call(q+' FORMAT JSON',settings));data.extend(d['data']);s=d['statistics'];elapsed+=s['elapsed'];rows+=s['rows_read'];bytes_+=s['bytes_read']
 return {'wall_ms':(time.perf_counter()-t)*1000,'server_ms':elapsed*1000,'rows_read':rows,'bytes_read':bytes_,'data':data}
for experiment in experiments:
 name,control,candidate,control_settings,candidate_settings,qualification=(experiment[key] for key in ('name','control','candidate','controlSettings','candidateSettings','qualification'))
 try:
  run(control,control_settings);run(candidate,candidate_settings)
  control_samples=[];candidate_samples=[]
  for i in range(5):
   pairs=[(control_samples,control,control_settings),(candidate_samples,candidate,candidate_settings)] if i%2==0 else [(candidate_samples,candidate,candidate_settings),(control_samples,control,control_settings)]
   for target,q,s in pairs:target.append(run(q,s))
  canonical=lambda data:sorted(json.dumps(row,sort_keys=True) for row in data)
  match=all(canonical(control_sample['data'])==canonical(candidate_sample['data']) for control_sample,candidate_sample in zip(control_samples,candidate_samples))
  if name.startswith('25 '): match=all([row['v'] for row in control_sample['data']]==[candidate_sample['data'][0][provider] for provider in ('stripe','apple','google')] for control_sample,candidate_sample in zip(control_samples,candidate_samples))
  result={'experiment':name,'result_equal':match,'qualification':qualification,'control':{k:statistics.median(x[k] for x in control_samples) for k in ('wall_ms','server_ms','rows_read','bytes_read')},'candidate':{k:statistics.median(x[k] for x in candidate_samples) for k in ('wall_ms','server_ms','rows_read','bytes_read')},'control_data':control_samples[-1]['data'],'candidate_data':candidate_samples[-1]['data'],'control_sql':control,'candidate_sql':candidate,'control_settings':control_settings,'candidate_settings':candidate_settings}
  result['speedup']=result['control']['server_ms']/max(result['candidate']['server_ms'],.001)
 except Exception as e:
  body=e.read().decode()[:500] if hasattr(e,'read') else str(e)
  result={'experiment':name,'error':body,'qualification':'Failed: query did not execute'}
 results.append(result);out.write_text(json.dumps({'dataset':'isolated synthetic, 1,200,027 events; 40,002 group versions','repeats':5,'results':results},indent=2,default=str))
 print(json.dumps({k:v for k,v in result.items() if k in ('experiment','speedup','result_equal','error','control','candidate')}),flush=True)

if any(result.get("error") or not result.get("result_equal",False) for result in results):
 raise SystemExit("Benchmark failed: query errors or unequal outputs; inspect the saved artifact")
