import type { ISerieDataItem } from '@openpanel/common';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import {
  setupFixtures,
  setupPostgresFixtures,
  teardownFixtures,
  teardownPostgresFixtures,
} from '../../../../test/fixtures';
import { chQuery } from '../clickhouse/client';
import { executeAggregateChart } from '../engine';
import { db } from '../prisma-client';
import { getAggregateChartSql } from './chart.service';

const PROJECT_ID = 'chart-profile-set-test';
const ORGANIZATION_ID = 'chart-profile-set-org';

function dateDaysAgo(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().replace('T', ' ').slice(0, 19);
}

async function queryProfileSet(options: {
  operation: 'union' | 'intersection';
  eventNames: string[];
}) {
  const sql = await getAggregateChartSql({
    event: {
      id: 'profile-set',
      name: options.operation.toUpperCase(),
      segment: 'user',
      filters: [],
      eventNames: options.eventNames,
      setOperation: options.operation,
    },
    projectId: PROJECT_ID,
    startDate: dateDaysAgo(15),
    endDate: dateDaysAgo(-1),
    breakdowns: [],
    timezone: 'UTC',
    metric: 'sum',
    previous: false,
  });
  return chQuery<ISerieDataItem>(sql, { session_timezone: 'UTC' });
}

beforeAll(async () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  await setupPostgresFixtures(PROJECT_ID, ORGANIZATION_ID);
  await setupFixtures(PROJECT_ID);
});

afterAll(async () => {
  await teardownFixtures(PROJECT_ID);
  await teardownPostgresFixtures(PROJECT_ID, ORGANIZATION_ID);
  vi.restoreAllMocks();
});

describe('profile set chart queries', () => {
  it('counts each profile once when it matches any union source', async () => {
    const [result] = await queryProfileSet({
      operation: 'union',
      eventNames: ['session_start', 'purchase'],
    });

    expect(result?.count).toBe(2);
  });

  it('counts only profiles that match every intersection source', async () => {
    const [result] = await queryProfileSet({
      operation: 'intersection',
      eventNames: ['session_start', 'purchase'],
    });

    expect(result?.count).toBe(1);
  });

  it('migrates legacy A | B formulas to a real profile union', async () => {
    const result = await executeAggregateChart({
      projectId: PROJECT_ID,
      chartType: 'histogram',
      interval: 'day',
      series: [
        {
          id: 'source-a',
          type: 'event',
          name: 'session_start',
          segment: 'event',
          filters: [],
        },
        {
          id: 'source-b',
          type: 'event',
          name: 'purchase',
          segment: 'event',
          filters: [],
        },
        {
          id: 'profile-union',
          type: 'formula',
          formula: 'A | B',
          displayName: 'Active Action',
        },
      ],
      breakdowns: [],
      range: 'custom',
      startDate: dateDaysAgo(15),
      endDate: dateDaysAgo(-1),
      previous: false,
      metric: 'sum',
    });

    const unionSeries = result.series.find(
      (series) => series.event.id === 'profile-union',
    );
    expect(unionSeries?.metrics.sum).toBe(2);
  });

  it('resolves saved custom events at query time', async () => {
    const customEvent = await db.customEvent.create({
      data: {
        projectId: PROJECT_ID,
        name: 'Active Action',
        operation: 'union',
        eventNames: ['session_start', 'purchase'],
      },
    });

    const result = await executeAggregateChart({
      projectId: PROJECT_ID,
      chartType: 'histogram',
      interval: 'day',
      series: [
        {
          id: 'active-action-series',
          type: 'event',
          name: customEvent.name,
          customEventId: customEvent.id,
          segment: 'user',
          filters: [],
        },
      ],
      breakdowns: [],
      range: 'custom',
      startDate: dateDaysAgo(15),
      endDate: dateDaysAgo(-1),
      previous: false,
      metric: 'sum',
    });

    expect(result.series[0]?.metrics.sum).toBe(2);
    expect(result.series[0]?.names[0]).toBe('Active Action');
  });
});
