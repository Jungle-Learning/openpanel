#!/usr/bin/env python3
"""Run 25 paired query experiments on a dedicated LOCAL synthetic dataset.

Requires local OpenPanel ClickHouse migrations. Creates openpanel_perf; never
connects to production or changes application data. Five interleaved paired
samples follow warmups. Results include SQL, settings and equality evidence.
"""
import urllib.request, urllib.parse, json, time, statistics, pathlib, argparse
URL='http://localhost:8123/'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',default='.context/chart-query-experiments.json')
args=parser.parse_args()
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
if int(call('SELECT count() FROM events'))==0:
 command("""INSERT INTO openpanel_perf.events (name,project_id,profile_id,device_id,session_id,created_at,country,groups,properties,revenue)
 SELECT ['answer','generation','payment','screen_view'][1+number%4],'bench',concat('u',toString(number%25000)),concat('d',toString(number%50000)),concat('s',toString(number%100000)),toDateTime('2026-10-01')+number%2300000,['US','GB','IN'][1+number%3],[],map('platform',['web','ios','android'][1+number%3],'exactSourceName',concat('source',toString(number%100)),'amount',toString(number%70),'payload',repeat(hex(cityHash64(number)),16)),number%4000 FROM numbers(1200000)""")
if int(call('SELECT count() FROM groups'))==0:
 command("""INSERT INTO openpanel_perf.groups (id,project_id,type,name,properties,created_at,version) SELECT concat('g',toString(number)),if(number%3=0,'other','bench'),'organization','test',map('stripe','1'),now(),toUnixTimestamp(now()) FROM numbers(40000)""")
 command("""INSERT INTO openpanel_perf.groups (id,project_id,type,name,properties,created_at,version) VALUES ('census','bench','analytics_census','Paid subscribers',map('stripe','5000','apple','940','google','230'),now()-100,toUnixTimestamp(now())-100),('census','bench','analytics_census','Paid subscribers',map('stripe','4885','apple','931','google','224'),now(),toUnixTimestamp(now()))""")
if int(call("SELECT count() FROM events WHERE name='Active Paid Subscribers Snapshot'"))==0:
 command("""INSERT INTO openpanel_perf.events (name,project_id,profile_id,device_id,session_id,created_at,groups,properties) SELECT 'Active Paid Subscribers Snapshot','bench','','','',toDateTime('2026-10-01')+number*86400,['census'],map('snapshotSchemaVersion','2','stripe',toString(5000-number),'apple','931','google','224') FROM numbers(27)""")
print('Dataset ready',flush=True)
BASE="project_id='bench' AND created_at>='2026-10-01' AND created_at<'2026-10-28'"
SNAP=BASE+" AND name='Active Paid Subscribers Snapshot'"
GROUP="SELECT id,properties FROM groups FINAL WHERE project_id='bench'"
JOIN=f"FROM events e ARRAY JOIN groups AS gid LEFT ANY JOIN ({GROUP}) g ON g.id=gid WHERE {SNAP}"
stock=f"SELECT max(toFloat64OrNull(g.properties['stripe'])) v {JOIN}"
props="properties['snapshotSchemaVersion']"
platform=f"SELECT properties['platform'] k,count() v FROM events WHERE {BASE} AND name='answer' GROUP BY k ORDER BY k"
material=f"SELECT _property_platform k,count() v FROM events WHERE {BASE} AND name='answer' GROUP BY k ORDER BY k"
series=f"SELECT toDate(created_at) k,count() v FROM events WHERE {BASE} AND name='answer' GROUP BY k ORDER BY k"
uniques=f"SELECT toDate(created_at) k,uniq(profile_id) v FROM events WHERE {BASE} AND name='answer' GROUP BY k ORDER BY k"
experiments=[]
def add(name,control,candidate,control_settings=None,candidate_settings=None,qualification='Measured only; review before rollout'):
 experiments.append((name,control,candidate,control_settings or {},candidate_settings or {},qualification))
add('01 Scope group CTE to project',stock.replace(" WHERE project_id='bench'",''),stock,qualification='Already implemented; retain tenant scope')
add('02 Restrict group CTE to matched group IDs',stock,stock.replace(GROUP,GROUP+" AND id IN (SELECT arrayJoin(groups) FROM events WHERE "+SNAP+")"))
add('03 Hash join versus parallel hash',stock,stock,{'join_algorithm':'hash'},{'join_algorithm':'parallel_hash'})
add('04 Hash join versus full sorting merge',stock,stock,{'join_algorithm':'hash'},{'join_algorithm':'full_sorting_merge'})
add('05 Explicit PREWHERE on sparse snapshot event',stock,stock.replace(' WHERE '+SNAP,' PREWHERE '+SNAP))
add('06 Automatic PREWHERE for property filters',f"SELECT count() v FROM events WHERE {SNAP} AND toFloat64OrNull({props})=2",f"SELECT count() v FROM events WHERE {SNAP} AND toFloat64OrNull({props})=2",{'optimize_move_to_prewhere':'0'},{'optimize_move_to_prewhere':'1'})
add('07 Sparse snapshot max threads 1',stock,stock,{}, {'max_threads':'1'})
add('08 Sparse snapshot max threads 2',stock,stock,{}, {'max_threads':'2'})
add('09 Sparse snapshot max threads 4',stock,stock,{}, {'max_threads':'4'})
add('10 Broad timeseries max threads 1',series,series,{}, {'max_threads':'1'})
add('11 Broad timeseries max threads 2',series,series,{}, {'max_threads':'2'})
add('12 Broad timeseries max threads 4',series,series,{}, {'max_threads':'4'})
add('13 Materialized platform versus map lookup',platform,material)
add('14 Name projection for platform breakdown',material,material,{'optimize_use_projections':'0'},{'optimize_use_projections':'1'})
exact=f"SELECT _property_exact_source_name k,count() v FROM events WHERE {BASE} AND name='answer' GROUP BY k ORDER BY k"
add('15 Name projection for acquisition breakdown',exact,exact,{'optimize_use_projections':'0'},{'optimize_use_projections':'1'})
add('16 Reuse server result cache',series,series,{'use_query_cache':'0'},{'use_query_cache':'1','query_cache_ttl':'60','query_cache_min_query_runs':'0','query_cache_min_query_duration':'0'})
add('17 Explicit cheap PREWHERE before wide properties',platform,platform.replace(' WHERE '+BASE+" AND name='answer'",' PREWHERE '+BASE+" AND name='answer'"),{'optimize_move_to_prewhere':'0'})
add('18 Omit unused all-period unique subquery',f"SELECT k,v FROM ({series}) AS bucketed CROSS JOIN (SELECT uniq(profile_id) total_count FROM events WHERE {BASE} AND name='answer') AS totals",series)
add('19 Reuse narrow materialized acquisition value',exact.replace('_property_exact_source_name',"properties['exactSourceName']"),exact)
add('20 Read-order optimization on latest events',f"SELECT created_at k FROM events WHERE {SNAP} ORDER BY created_at DESC LIMIT 1",f"SELECT created_at k FROM events WHERE {SNAP} ORDER BY created_at DESC LIMIT 1",{'optimize_read_in_order':'0'},{'optimize_read_in_order':'1'})
add('21 Query-condition cache on repeated property scan',platform,platform,{'use_query_condition_cache':'0'},{'use_query_condition_cache':'1'})
add('22 Sorting in-order aggregation',series,series,{'optimize_aggregation_in_order':'0'},{'optimize_aggregation_in_order':'1'})
add('23 Numeric versus string snapshot schema filter',f"SELECT count() v FROM events WHERE {SNAP} AND toFloat64OrNull({props})=2",f"SELECT count() v FROM events WHERE {SNAP} AND {props}='2'",qualification='Reject general rewrite: numeric strings can differ')
add('24 Inlining one group current count',stock,"SELECT max(toFloat64OrNull(properties['stripe'])) v FROM groups FINAL WHERE project_id='bench' AND id='census'",qualification='Reject generic rewrite: drops event/date/filter semantics')
add('25 Three provider aggregates in one scan',[stock.replace("['stripe']",f"['{p}']") for p in ('stripe','apple','google')],f"SELECT max(toFloat64OrNull(g.properties['stripe'])) stripe,max(toFloat64OrNull(g.properties['apple'])) apple,max(toFloat64OrNull(g.properties['google'])) google {JOIN}",qualification='Requires fused-series planner with preserved per-series filters')
results=[]
def run(sql,settings):
 qs=sql if isinstance(sql,list) else [sql]; data=[];elapsed=0;rows=0;bytes_=0;t=time.perf_counter()
 for q in qs:
  d=json.loads(call(q+' FORMAT JSON',settings));data.extend(d['data']);s=d['statistics'];elapsed+=s['elapsed'];rows+=s['rows_read'];bytes_+=s['bytes_read']
 return {'wall_ms':(time.perf_counter()-t)*1000,'server_ms':elapsed*1000,'rows_read':rows,'bytes_read':bytes_,'data':data}
for name,control,candidate,cs,vs,qualification in experiments:
 try:
  run(control,cs);run(candidate,vs)
  a=[];b=[]
  for i in range(5):
   pairs=[(a,control,cs),(b,candidate,vs)] if i%2==0 else [(b,candidate,vs),(a,control,cs)]
   for target,q,s in pairs:target.append(run(q,s))
  canonical=lambda data:sorted(json.dumps(row,sort_keys=True) for row in data)
  match=canonical(a[-1]['data'])==canonical(b[-1]['data'])
  if name.startswith('25 '): match=[row['v'] for row in a[-1]['data']]==[b[-1]['data'][0][p] for p in ('stripe','apple','google')]
  result={'experiment':name,'result_equal':match,'qualification':qualification,'control':{k:statistics.median(x[k] for x in a) for k in ('wall_ms','server_ms','rows_read','bytes_read')},'candidate':{k:statistics.median(x[k] for x in b) for k in ('wall_ms','server_ms','rows_read','bytes_read')},'control_data':a[-1]['data'],'candidate_data':b[-1]['data'],'control_sql':control,'candidate_sql':candidate,'control_settings':cs,'candidate_settings':vs}
  result['speedup']=result['control']['server_ms']/max(result['candidate']['server_ms'],.001)
 except Exception as e:
  body=e.read().decode()[:500] if hasattr(e,'read') else str(e)
  result={'experiment':name,'error':body,'qualification':'Rejected: candidate did not execute'}
 results.append(result);out.write_text(json.dumps({'dataset':'isolated synthetic, 1,200,027 events; 40,002 group versions','repeats':5,'results':results},indent=2,default=str))
 print(json.dumps({k:v for k,v in result.items() if k in ('experiment','speedup','result_equal','error','control','candidate')}),flush=True)
