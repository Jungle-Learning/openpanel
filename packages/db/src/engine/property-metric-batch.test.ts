import type { ISerieDataItem } from '@openpanel/common';
import { zReportInput } from '@openpanel/validation';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import {
  setupPostgresFixtures,
  teardownPostgresFixtures,
} from '../../../../test/fixtures';
import { ch, chQuery } from '../clickhouse/client';
import { db } from '../prisma-client';
import { getAggregateChartSql } from '../services/chart.service';
import { getReportDataCore } from '../services/reports.service';
import { AggregateChartEngine, ChartEngine } from './index';
import { normalize } from './normalize';
import { fetchPropertyMetricBatch } from './property-metric-batch';

const projectId = 'property-metric-batch-test';
const organizationId = `${projectId}-org`;
const eventName = 'Test Paid Subscriber Snapshot';
const providers = ['stripe', 'apple', 'google'];
const startDate = '2026-10-24 00:00:00';
const endDate = '2026-10-27 23:59:59';
function report(group = false) {
  return zReportInput.parse({
    projectId,
    chartType: 'bar',
    interval: 'day',
    range: 'custom',
    startDate,
    endDate,
    metric: 'max',
    previous: false,
    breakdowns: [],
    globalFilters: [
      {
        id: 'schema',
        name: 'properties.schema',
        operator: 'is',
        type: 'number',
        value: ['2'],
      },
    ],
    series: providers.map((provider, index) => ({
      id: `metric-${index}`,
      type: 'event',
      name: eventName,
      displayName: provider,
      segment: 'property_max',
      property: `${group ? 'group.properties' : 'properties'}.${provider}`,
      filters: [],
    })),
  });
}
async function clearRows() {
  for (const table of ['events', 'groups']) {
    await ch.command({
      query: `ALTER TABLE ${table} DELETE WHERE project_id IN ('${projectId}', '${projectId}-other') SETTINGS mutations_sync=1`,
    });
  }
}
beforeAll(async () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  await setupPostgresFixtures(projectId, organizationId);
  await clearRows();
  const properties = [
    { stripe: '99999', apple: '99999', google: '99999', schema: '1' },
    { stripe: '10', apple: '', google: '0', broken: 'invalid', schema: '2' },
    { stripe: '9', apple: '7', google: '3', broken: 'invalid', schema: '2' },
  ];
  await ch.insert({
    table: 'events',
    format: 'JSONEachRow',
    values: properties.map((props, index) => ({
      name: eventName,
      project_id: projectId,
      created_at: `2026-10-${24 + index} 12:00:00`,
      properties: props,
      groups: ['test-census'],
      revenue: index,
    })),
  });
  await ch.insert({
    table: 'groups',
    format: 'JSONEachRow',
    values: [
      {
        id: 'test-census',
        project_id: projectId,
        type: 'analytics_census',
        name: 'Census',
        created_at: '2026-10-24 12:00:00',
        version: 1,
        properties: { stripe: '5000', apple: '940', google: '230' },
      },
      {
        id: 'test-census',
        project_id: projectId,
        type: 'analytics_census',
        name: 'Census',
        created_at: '2026-10-24 12:00:00',
        version: 2,
        properties: { stripe: '4885', apple: '931', google: '224' },
      },
      {
        id: 'test-census',
        project_id: `${projectId}-other`,
        type: 'analytics_census',
        name: 'Other tenant',
        created_at: '2026-10-24 12:00:00',
        version: 3,
        properties: { stripe: '999999' },
      },
    ],
  });
});
afterAll(async () => {
  await clearRows();
  await teardownPostgresFixtures(projectId, organizationId);
  vi.restoreAllMocks();
});

async function expectParity(input = report()) {
  const normalized = await normalize(input);
  const batch = await fetchPropertyMetricBatch(normalized, 100);
  const separate = [];
  for (const event of normalized.series) {
    if (event.type !== 'event') throw new Error('Expected event fixture');
    const rows = await chQuery<ISerieDataItem>(
      await getAggregateChartSql({
        ...normalized,
        event,
        timezone: normalized.timezone,
        limit: 100,
      })
    );
    separate.push(
      rows.map(({ date, label_0, count }) => ({ date, label_0, count }))
    );
  }
  expect(batch).toEqual(separate);
  return batch;
}

describe('batched property metrics against ClickHouse', () => {
  it('matches separate queries and preserves independent missing values and zero', async () => {
    const rows = await expectParity();
    expect(rows?.map((series) => series[0]?.count)).toEqual([10, 7, 3]);
  });
  it('uses the latest group after a decrease and isolates identical IDs in another tenant', async () => {
    const rows = await expectParity(report(true));
    expect(rows?.map((series) => series[0]?.count)).toEqual([4885, 931, 224]);
  });
  it.each([
    'property_sum',
    'property_average',
    'property_min',
    'property_max',
  ] as const)('preserves %s aggregation', async (segment) => {
    const input = report();
    input.series = input.series.map((series) => ({ ...series, segment }));
    await expectParity(input);
  });
  it('preserves empty series, malformed numbers, and numeric columns', async () => {
    const input = report();
    input.series = input.series.map((series, index) => ({
      ...series,
      property: ['properties.missing', 'properties.broken', 'revenue'][index],
    }));
    const rows = await expectParity(input);
    expect(rows?.[0]).toEqual([]);
    expect(rows?.[1]?.[0]?.count).toBeNull();
    expect(rows?.[2]?.[0]?.count).toBe(2);
  });
  it('preserves an empty date window', async () => {
    await expectParity({
      ...report(),
      startDate: '2026-09-01 00:00:00',
      endDate: '2026-09-02 23:59:59',
    });
  });
  it('leaves formulas, different filters, mixed joins, and breakdowns on the existing path', async () => {
    const input = report();
    const normalized = await normalize(input);
    for (const changed of [
      { ...normalized, breakdowns: [{ id: 'country', name: 'country' }] },
      {
        ...normalized,
        series: [
          ...normalized.series,
          { id: 'D', type: 'formula' as const, formula: 'A+B' },
        ],
      },
      {
        ...normalized,
        series: normalized.series.map((series, i) =>
          i === 1 ? { ...series, property: 'group.properties.apple' } : series
        ),
      },
      {
        ...normalized,
        series: normalized.series.map((series, i) =>
          i === 1 ? { ...series, filters: [] } : series
        ),
      },
    ])
      expect(await fetchPropertyMetricBatch(changed, 100)).toBeNull();
  });
  it('executes group property metrics on the time-series path too', async () => {
    const chart = await ChartEngine.execute({
      ...report(true),
      chartType: 'linear',
    });
    expect(chart.series.map((series) => series.metrics.max)).toEqual([
      4885, 931, 224,
    ]);
  });
  it('matches the existing path when comparing previous periods', async () => {
    const input = {
      ...report(),
      previous: true,
      startDate: '2026-10-26 00:00:00',
    };
    const batched = await AggregateChartEngine.execute(input);
    // A formula requires the general execution path; it does not change its
    // three input metrics, their IDs, or their previous-period comparisons.
    const separate = await AggregateChartEngine.execute({
      ...input,
      series: [
        ...input.series,
        { id: 'comparison-formula', type: 'formula', formula: '0' },
      ],
    });
    expect(batched.series).toEqual(
      separate.series.filter(
        (series) => series.event.id !== 'comparison-formula'
      )
    );
  });
  it.each([
    'bar',
    'pie',
    'metric',
  ] as const)('routes saved %s reports through the aggregate engine', async (chartType) => {
    const dashboard = await db.dashboard.create({
      data: { name: `QA ${chartType}`, projectId, organizationId },
    });
    const input = report(true);
    const saved = await db.report.create({
      data: {
        name: 'Paid subscribers',
        projectId,
        dashboardId: dashboard.id,
        chartType,
        interval: 'day',
        range: 'custom',
        startDate,
        endDate,
        metric: 'max',
        events: input.series,
        globalFilters: input.globalFilters,
        breakdowns: [],
        visibleSeries: [],
      },
    });
    const response = await getReportDataCore({
      projectId,
      organizationId,
      reportId: saved.id,
    });
    expect(
      'series' in response.data &&
        response.data.series.map((series) => series.metrics.sum)
    ).toEqual([4885, 931, 224]);
  });
});
