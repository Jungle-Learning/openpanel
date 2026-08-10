import { describe, expect, it } from 'vitest';
import { getChartSql } from './chart.service';

type ChartSqlInput = Parameters<typeof getChartSql>[0];

function chartInput(overrides: Partial<ChartSqlInput> = {}): ChartSqlInput {
  return {
    event: {
      id: 'series-0',
      name: 'New User Sign Up',
      segment: 'event',
      filters: [
        {
          id: 'exclude-india',
          name: 'country',
          operator: 'isNot',
          value: ['IN'],
        },
      ],
    },
    projectId: 'jungle',
    startDate: '2026-02-10T00:00:00.000Z',
    endDate: '2026-08-11T00:00:00.000Z',
    chartType: 'histogram',
    interval: 'month',
    breakdowns: [{ id: 'platform', name: 'properties.platform' }],
    previous: false,
    metric: 'sum',
    limit: undefined,
    offset: undefined,
    options: { type: 'histogram', stacked: true },
    formula: undefined,
    name: 'Sign ups per month',
    lineType: 'monotone',
    unit: undefined,
    timezone: 'America/Los_Angeles',
    ...overrides,
  };
}

describe('getChartSql total unique count', () => {
  it('does not scan unique users for a sum metric', () => {
    const sql = getChartSql(chartInput());

    expect(sql).not.toContain('uniq(profile_id)');
    expect(sql).not.toContain('total_count');
    expect(sql).toContain('count(*) as count');
  });

  it('keeps per-bucket unique users without an all-period rescan', () => {
    const sql = getChartSql(
      chartInput({
        event: {
          ...chartInput().event,
          segment: 'user',
        },
      })
    );

    expect(sql).toContain('countDistinct(profile_id) as count');
    expect(sql).not.toContain('uniq(profile_id)');
    expect(sql).not.toContain('total_count');
  });

  it('pre-aggregates count metrics by breakdown instead of correlating', () => {
    const sql = getChartSql(chartInput({ metric: 'count' }));

    expect(sql).toContain('_unique_counts AS');
    expect(sql).toContain('uniq(profile_id) as total_count');
    expect(sql).toContain('LEFT ANY JOIN _unique_counts');
    expect(sql).toContain(
      '_unique_counts._unique_label_1 = _property_platform'
    );
    expect(sql).not.toContain('_property_platform = label_1');
  });

  it('uses one uncorrelated total for count metrics without breakdowns', () => {
    const sql = getChartSql(chartInput({ metric: 'count', breakdowns: [] }));

    expect(sql).toContain('_unique_count AS');
    expect(sql).toContain('(SELECT total_count FROM _unique_count)');
    expect(sql).not.toContain('_unique_counts._unique_label_1');
  });
});
