// Emit measured SELECTs through the repository's ClickHouse query builder.
// Fixture DDL/INSERTs remain in the Python runner; no production client exists here.
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
const requireDb = createRequire(new URL('../packages/db/package.json', import.meta.url));
const { createJiti } = requireDb('jiti');
const { clix } = await createJiti(import.meta.url).import(resolve('packages/db/src/clickhouse/query-builder.ts'));
const query = () => clix(undefined, 'UTC'); // toSQL only; never executes a client
const base = (builder) => builder.where('e.project_id', '=', 'bench')
  .where('e.created_at', '>=', '2026-10-01').where('e.created_at', '<', '2026-10-28');
const snapshot = (builder) => base(builder).where('e.name', '=', 'Active Paid Subscribers Snapshot');
const answer = (builder) => base(builder).where('e.name', '=', 'answer');
function groups({ scoped = true, restrictIds = false } = {}) {
  const builder = query().select(['id', 'properties']).from('groups', true);
  if (scoped) builder.where('project_id', '=', 'bench');
  if (restrictIds) builder.where('id', 'IN', clix.exp(snapshot(query().select(['arrayJoin(e.groups)']).from('events e'))));
  return builder;
}
function stock({ scoped = true, restrictIds = false, providers = ['stripe'], named = false } = {}) {
  return snapshot(query().select(providers.map(provider => `max(toFloat64OrNull(g.properties['${provider}'])) ${named ? provider : 'v'}`))
    .from('events e ARRAY JOIN groups AS gid').leftAnyJoin(groups({ scoped, restrictIds }), 'g.id=gid', 'g')).toSQL();
}
const breakdown = (property) => answer(query().select([`${property} k`, 'count() v']).from('events e')).groupBy(['k']).orderBy('k').toSQL();
const platform = breakdown("e.properties['platform']");
const materializedPlatform = breakdown('e._property_platform');
const materializedAcquisition = breakdown('e._property_exact_source_name');
const series = answer(query().select(['toDate(e.created_at) k', 'count() v']).from('events e')).groupBy(['k']).orderBy('k').toSQL();
const stockSql = stock();
const schemaCount = (numeric) => snapshot(query().select(['count() v']).from('events e'))
  .where(numeric ? "toFloat64OrNull(e.properties['snapshotSchemaVersion'])" : "e.properties['snapshotSchemaVersion']", '=', numeric ? 2 : '2').toSQL();
// Query has no PREWHERE method. Rewrite only the explicitly qualified outer
// WHERE emitted by the builder, preserving the nested group scope.
const prewhere = (sql) => sql.replace(" WHERE e.project_id = 'bench'", " PREWHERE e.project_id = 'bench'");
const experiments = [];
function add(name, control, candidate, controlSettings = {}, candidateSettings = {}, qualification = 'Measured only; review before rollout') {
  experiments.push({ name, control, candidate, controlSettings, candidateSettings, qualification });
}
add('01 Scope group CTE to project', stock({ scoped: false }), stockSql, {}, {}, 'Already implemented; retain tenant scope');
add('02 Restrict group CTE to matched group IDs', stockSql, stock({ restrictIds: true }));
add('03 Hash join versus parallel hash', stockSql, stockSql, { join_algorithm: 'hash' }, { join_algorithm: 'parallel_hash' });
add('04 Hash join versus full sorting merge', stockSql, stockSql, { join_algorithm: 'hash' }, { join_algorithm: 'full_sorting_merge' });
add('05 Explicit PREWHERE on sparse snapshot event', stockSql, prewhere(stockSql));
add('06 Automatic PREWHERE for property filters', schemaCount(true), schemaCount(true), { optimize_move_to_prewhere: '0' }, { optimize_move_to_prewhere: '1' });
for (const threads of [1, 2, 4]) add(`0${7 + [1, 2, 4].indexOf(threads)} Sparse snapshot max threads ${threads}`, stockSql, stockSql, {}, { max_threads: String(threads) });
for (const threads of [1, 2, 4]) add(`${10 + [1, 2, 4].indexOf(threads)} Broad timeseries max threads ${threads}`, series, series, {}, { max_threads: String(threads) });
add('13 Materialized platform versus map lookup', platform, materializedPlatform);
add('14 Name projection for platform breakdown', materializedPlatform, materializedPlatform, { optimize_use_projections: '0' }, { optimize_use_projections: '1' });
add('15 Name projection for acquisition breakdown', materializedAcquisition, materializedAcquisition, { optimize_use_projections: '0' }, { optimize_use_projections: '1' });
add('16 Reuse server result cache', series, series, { use_query_cache: '0' }, { use_query_cache: '1', query_cache_ttl: '60', query_cache_min_query_runs: '0', query_cache_min_query_duration: '0' });
add('17 Explicit cheap PREWHERE before wide properties', platform, prewhere(platform), { optimize_move_to_prewhere: '0' });
const totals = answer(query().select(['uniq(e.profile_id) total_count']).from('events e')).toSQL();
add('18 Omit unused all-period unique subquery', query().select(['k', 'v']).from(`(${series}) AS bucketed`).crossJoin(clix.exp(totals), 'totals').toSQL(), series);
add('19 Reuse narrow materialized acquisition value', breakdown("e.properties['exactSourceName']"), materializedAcquisition);
const latest = snapshot(query().select(['e.created_at k']).from('events e')).orderBy('e.created_at', 'DESC').limit(1).toSQL();
add('20 Read-order optimization on latest events', latest, latest, { optimize_read_in_order: '0' }, { optimize_read_in_order: '1' });
add('21 Query-condition cache on repeated property scan', platform, platform, { use_query_condition_cache: '0' }, { use_query_condition_cache: '1' });
add('22 Sorting in-order aggregation', series, series, { optimize_aggregation_in_order: '0' }, { optimize_aggregation_in_order: '1' });
add('23 Numeric versus string snapshot schema filter', schemaCount(true), schemaCount(false), {}, {}, 'Reject general rewrite: numeric strings can differ');
const directGroup = query().select(["max(toFloat64OrNull(properties['stripe'])) v"]).from('groups', true).where('project_id', '=', 'bench').where('id', '=', 'census').toSQL();
add('24 Inlining one group current count', stockSql, directGroup, {}, {}, 'Reject generic rewrite: drops event/date/filter semantics');
add('25 Three provider aggregates in one scan', ['stripe', 'apple', 'google'].map(provider => stock({ providers: [provider] })), stock({ providers: ['stripe', 'apple', 'google'], named: true }), {}, {}, 'Requires fused-series planner with preserved per-series filters');
const counts = Object.fromEntries(['events', 'groups'].map(table => [table, query().select(['count()']).from(table).toSQL()]));
counts.snapshots = query().select(['count()']).from('events').where('name', '=', 'Active Paid Subscribers Snapshot').toSQL();
process.stdout.write(JSON.stringify({ experiments, counts }));
