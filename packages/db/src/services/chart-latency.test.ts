import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { getChartProjectionCommands } from '../../code-migrations/18-add-chart-projection';
import { getExactSourceProjectionCommands } from '../../code-migrations/19-add-exact-source-chart-projection';
import { getChartSql as getChartSqlImpl } from './chart.service';

const getChartSql: (input: any) => Promise<string> = getChartSqlImpl as any;

function chartInput(overrides: Record<string, unknown> = {}) {
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
    timezone: 'America/Los_Angeles',
    ...overrides,
  };
}

beforeAll(() => {
  vi.spyOn(console, 'log').mockImplementation(() => {
    // Keep generated SQL out of the test output.
  });
});

afterAll(() => {
  vi.restoreAllMocks();
});

describe('chart latency SQL', () => {
  it('skips the unused all-period unique scan for sum metrics', async () => {
    const sql = await getChartSql(chartInput());

    expect(sql).not.toContain('uniq(profile_id)');
    expect(sql).not.toContain('total_count');
    expect(sql).toContain('count(*) as count');
  });

  it('keeps the all-period unique total for count metrics', async () => {
    const sql = await getChartSql(chartInput({ metric: 'count' }));

    expect(sql).toContain('_uc AS');
    expect(sql).toContain('uniq(profile_id) as total_count');
    expect(sql).toContain('LEFT ANY JOIN _uc');
  });

  it('preserves the unique total when older callers omit metric', async () => {
    const sql = await getChartSql(chartInput({ metric: undefined }));

    expect(sql).toContain('uniq(profile_id) as total_count');
  });

  it('uses the projection-backed column for mapped properties', async () => {
    const sql = await getChartSql(chartInput());

    expect(sql).toContain('e._property_platform as label_1');
    expect(sql).not.toContain("properties['platform']");
  });

  it('uses the dedicated projection column for attribution breakdowns', async () => {
    const sql = await getChartSql(
      chartInput({
        breakdowns: [
          { id: 'exact-source', name: 'properties.exactSourceName' },
        ],
      }),
    );

    expect(sql).toContain('e._property_exact_source_name as label_1');
    expect(sql).not.toContain("properties['exactSourceName']");
  });
});

describe('chart projection migration', () => {
  it('updates both distributed schemas and the replicated projection in clustered mode', () => {
    const sql = getChartProjectionCommands(true).join('\n');

    expect(sql).toContain(
      "ALTER TABLE events_replicated ON CLUSTER '{cluster}' ADD COLUMN"
    );
    expect(sql).toContain(
      "ALTER TABLE events ON CLUSTER '{cluster}' ADD COLUMN"
    );
    expect(sql).toContain(
      "ALTER TABLE events_replicated ON CLUSTER '{cluster}' ADD PROJECTION"
    );
  });

  it('targets the local events table in self-hosted mode', () => {
    const sql = getChartProjectionCommands(false).join('\n');

    expect(sql).toContain('ALTER TABLE events ADD COLUMN');
    expect(sql).toContain('ALTER TABLE events ADD PROJECTION');
    expect(sql).not.toContain('events_replicated');
  });

  it('adds a dedicated exact-source projection in self-hosted mode', () => {
    const sql = getExactSourceProjectionCommands(false).join('\n');

    expect(sql).toContain(
      "MATERIALIZED properties['exactSourceName']",
    );
    expect(sql).toContain(
      'ALTER TABLE events ADD PROJECTION IF NOT EXISTS chart_events_by_exact_source',
    );
    expect(sql).toContain('_property_exact_source_name');
  });

  it('updates the distributed schema and replicated projection in clustered mode', () => {
    const sql = getExactSourceProjectionCommands(true).join('\n');

    expect(sql).toContain(
      "ALTER TABLE events_replicated ON CLUSTER '{cluster}' ADD COLUMN",
    );
    expect(sql).toContain(
      "ALTER TABLE events ON CLUSTER '{cluster}' ADD COLUMN",
    );
    expect(sql).toContain(
      "ALTER TABLE events_replicated ON CLUSTER '{cluster}' ADD PROJECTION",
    );
  });
});
