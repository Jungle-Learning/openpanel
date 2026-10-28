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
Outputs are compared as records; provider fusion compares the three named values.

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
| 1 | Scope group CTE to project | 17.71 | 14.36 | 1.23× | Existing project scope retained |
| 2 | Restrict group CTE to matched group IDs | 15.80 | 14.35 | 1.10× | Deferred: extra scan; workload dependent |
| 3 | Hash join versus parallel hash | 12.86 | 15.67 | 0.82× | Rejected: slower |
| 4 | Hash join versus full sorting merge | 14.20 | 17.66 | 0.80× | Rejected: slower |
| 5 | Explicit PREWHERE on sparse snapshot event | 14.76 | 13.63 | 1.08× | Deferred: no global PREWHERE rewrite |
| 6 | Automatic PREWHERE for property filters | 6.87 | 8.57 | 0.80× | Existing default retained |
| 7 | Sparse snapshot max threads 1 | 15.20 | 17.87 | 0.85× | No global thread override |
| 8 | Sparse snapshot max threads 2 | 16.28 | 16.82 | 0.97× | No global thread override |
| 9 | Sparse snapshot max threads 4 | 15.37 | 16.47 | 0.93× | No global thread override |
| 10 | Broad timeseries max threads 1 | 16.94 | 23.89 | 0.71× | Rejected: slower |
| 11 | Broad timeseries max threads 2 | 21.40 | 20.03 | 1.07× | Rejected: slower |
| 12 | Broad timeseries max threads 4 | 21.29 | 22.01 | 0.97× | No global thread override |
| 13 | Materialized platform versus map lookup | 118.54 | 19.84 | 5.97× | Existing narrow column retained |
| 14 | Name projection for platform breakdown | 14.52 | 13.92 | 1.04× | Existing projection retained |
| 15 | Name projection for acquisition breakdown | 21.12 | 16.60 | 1.27× | Existing projection retained |
| 16 | Reuse server result cache | 16.30 | 1.09 | 14.97× | Existing Redis cache retained; no new server cache |
| 17 | Explicit cheap PREWHERE before wide properties | 108.17 | 110.00 | 0.98× | Deferred: map scans remain expensive |
| 18 | Omit unused all-period unique subquery | 37.91 | 19.23 | 1.97× | Existing optimization retained |
| 19 | Reuse narrow materialized acquisition value | 95.43 | 18.02 | 5.29× | Existing narrow column retained |
| 20 | Read-order optimization on latest events | 4.42 | 4.13 | 1.07× | Existing default retained |
| 21 | Query-condition cache on repeated property scan | 90.45 | 96.18 | 0.94× | No global cache setting change |
| 22 | Sorting in-order aggregation | 15.26 | 15.81 | 0.96× | Rejected: workload dependent/slower |
| 23 | Numeric versus string snapshot schema filter | 8.02 | 7.49 | 1.07× | Rejected: changes numeric filter semantics |
| 24 | Inlining one group current count | 15.57 | 3.71 | 4.20× | Rejected: loses event/filter/date semantics |
| 25 | Three provider aggregates in one scan | 46.25 | 17.49 | 2.64× | Adopted for compatible property metrics |
| 26 | Compile formula once across dates and breakdowns | 117.90 | 48.21 | 2.45× | Adopted |
| 27 | Index dates when aligning large chart series | 258.49 | 164.45 | 1.57× | Adopted |

All 25 query candidates executed and matched their controls. A match on this
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
