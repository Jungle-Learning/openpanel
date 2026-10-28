# OpenPanel chart performance qualification — October 28, 2026

## Changes

Saved bar and pie report data now use the same aggregate engine as the browser.
Previously their API used the time-series engine, which also failed to resolve
group property metrics because the project context was omitted. Both paths now
resolve those properties through the project-scoped group join.

Compatible aggregate property metrics share one ClickHouse scan instead of
running one scan per metric. Eligibility requires the same event, filters and
join scope, with no breakdowns, formulas, custom events or wildcard properties.
Other reports keep the general path. Conditional aggregates and per-metric
presence counts preserve missing values, zero, invalid numeric values, every
supported property aggregation and previous-period comparisons. Group reads
continue to use `FINAL`, so a lower latest subscriber count replaces the old one.

The chart engine carries the timezone already read during normalization through
planning and aggregation, avoiding repeated project/organization lookups in the
same request. Formula expressions compile once per formula. Date alignment uses
an index, retaining the first duplicate and the original global date order.
No retention, tenant access, ingestion, metric definition, or global ClickHouse
settings change is needed. There is no data migration.

## Method

These are **isolated lab measurements, not production latency claims**. The query
workload uses ClickHouse 26.1.3.52, 1,200,027 synthetic events, 40,002 synthetic
group versions, a sparse subscriber census, and broad event/acquisition/platform
breakdowns. Each experiment has a control and candidate, warmups, and five
interleaved paired samples. SQL, settings, counts, and median rows/bytes read
are retained in [the query artifact](performance-2026-10-28.queries.json).
Outputs are compared as records for every paired sample; provider fusion compares the three named values. Measured SELECTs are generated through the repository ClickHouse query builder. The command saves results and exits unsuccessfully on any query error or unequal output.

The CPU experiments run the actual baseline and candidate functions on the same
inputs and require deep output equality. Formula inputs include missing dates,
zero divisors, chained formulas and 12 breakdowns over 365 dates. Date alignment
uses 200 breakdowns over 365 dates. See [the CPU artifact](performance-2026-10-28.cpu.json).
Baseline source: `1eea86324ce65298b3fd7c39731b0bcbed9db646`.

## All 27 experiments

Times below are median ClickHouse execution milliseconds for 1–25 and process
milliseconds for 26–27. Small single-digit millisecond differences can be noise;
thread, join, and cache defaults were not changed on that basis. Existing
optimizations are explicitly distinguished from changes introduced here.

| # | Experiment | Control ms | Candidate ms | Control / candidate | Decision |
|---|---|---:|---:|---:|---|
| 1 | Scope group CTE to project | 23.13 | 16.51 | 1.40× | Existing project scope retained |
| 2 | Restrict group CTE to matched group IDs | 14.91 | 13.45 | 1.11× | Deferred: extra scan; workload dependent |
| 3 | Hash join versus parallel hash | 14.53 | 19.06 | 0.76× | No global setting change; workload dependent |
| 4 | Hash join versus full sorting merge | 17.66 | 16.49 | 1.07× | No global setting change; workload dependent |
| 5 | Explicit PREWHERE on sparse snapshot event | 14.82 | 13.58 | 1.09× | Deferred: no global PREWHERE rewrite |
| 6 | Automatic PREWHERE for property filters | 6.52 | 7.27 | 0.90× | Existing default retained |
| 7 | Sparse snapshot max threads 1 | 12.81 | 13.68 | 0.94× | No global thread override |
| 8 | Sparse snapshot max threads 2 | 13.35 | 13.94 | 0.96× | No global thread override |
| 9 | Sparse snapshot max threads 4 | 15.89 | 15.03 | 1.06× | No global thread override |
| 10 | Broad timeseries max threads 1 | 14.43 | 20.09 | 0.72× | No global setting change; workload dependent |
| 11 | Broad timeseries max threads 2 | 14.77 | 16.08 | 0.92× | No global setting change; workload dependent |
| 12 | Broad timeseries max threads 4 | 16.37 | 13.61 | 1.20× | No global thread override |
| 13 | Materialized platform versus map lookup | 99.07 | 15.04 | 6.59× | Existing narrow column retained |
| 14 | Name projection for platform breakdown | 13.15 | 11.98 | 1.10× | Existing projection retained |
| 15 | Name projection for acquisition breakdown | 14.05 | 14.76 | 0.95× | Existing projection retained |
| 16 | Reuse server result cache | 14.98 | 0.99 | 15.10× | Existing Redis cache retained; no new server cache |
| 17 | Explicit cheap PREWHERE before wide properties | 102.20 | 95.28 | 1.07× | Deferred: map scans remain expensive |
| 18 | Omit unused all-period unique subquery | 34.22 | 15.40 | 2.22× | Existing optimization retained |
| 19 | Reuse narrow materialized acquisition value | 104.99 | 15.23 | 6.89× | Existing narrow column retained |
| 20 | Read-order optimization on latest events | 5.44 | 5.51 | 0.99× | Existing default retained |
| 21 | Query-condition cache on repeated property scan | 92.40 | 92.97 | 0.99× | No global cache setting change |
| 22 | Sorting in-order aggregation | 12.83 | 15.21 | 0.84× | No global setting change; workload dependent |
| 23 | Numeric versus string snapshot schema filter | 7.87 | 6.64 | 1.19× | Rejected: changes numeric filter semantics |
| 24 | Inlining one group current count | 14.73 | 3.07 | 4.80× | Rejected: loses event/filter/date semantics |
| 25 | Three provider aggregates in one scan | 43.29 | 14.30 | 3.03× | Adopted for compatible property metrics |
| 26 | Compile formula once across dates and breakdowns | 79.79 | 31.99 | 2.49× | Adopted |
| 27 | Index dates when aligning large chart series | 205.83 | 124.22 | 1.66× | Adopted |

All 25 query candidates executed and matched their controls. Review corrected experiment 1 to remove only the nested group project filter while retaining the outer event/date filters; all 25 experiments were rerun. Negative controls verify that unequal output and query errors make the runner fail. A match on this
fixture does not establish general semantic equivalence: experiments 23 and 24
were rejected despite matching. Numeric strings can have different spellings,
and direct group reads would discard event/date/filter semantics.

## Reproduce and operate

After installing dependencies, generating clients and migrating **local**
OpenPanel services:

```sh
python3 scripts/benchmark-chart-queries.py
node scripts/benchmark-chart-cpu.mjs
pnpm test
pnpm --filter @openpanel/db --filter @openpanel/common typecheck
```

The query benchmark is intentionally restricted to localhost, creates only the
`openpanel_perf` database, and leaves that fixture available for reruns. Its
aggregates contain no real customer data. CPU baseline files are temporary and
removed in a `finally` block.

Regression coverage compares fused queries with separate real ClickHouse
queries, checks a decreasing latest group value and identical group IDs in a
different tenant, and exercises the saved bar/pie/metric report API dispatch.
The full suite passes 776 tests across 63 files. Local API build and affected
package type checks also pass. The frontend production build and browser receipt
are recorded in the release evidence, separately from the query benchmarks.

Existing ClickHouse query logs expose elapsed time, rows and bytes read for the
fused statement. Check the saved subscriber report data endpoint returns HTTP
200 and 4,885 / 931 / 224 for the qualified October 27 snapshot (6,040 total),
and that the published snapshot timestamp is current. Daily collection remains
at 09:15 UTC. Do not sum subscriber stocks across dates, or use mutable group
properties to claim historical subscriber counts.

Rollback uses the previous OpenPanel application images; no aggregate data or
provider collector must be rolled back. Browser layout/styles and analytics
collection are unchanged. Production browser verification requires an
authenticated operator session; do not treat API-only evidence as visual QA.
