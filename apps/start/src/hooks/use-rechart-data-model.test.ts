import type { IChartData } from '@/trpc/client';
import { describe, expect, it } from 'vitest';
import { buildRechartDataModel } from './use-rechart-data-model';

function series(
  id: string,
  values: [string, number][]
): IChartData['series'][number] {
  return {
    id,
    names: [id],
    event: { name: id },
    metrics: { sum: 0, average: 0, min: 0, max: 0, count: undefined },
    data: values.map(([date, count]) => ({ date, count, previous: undefined })),
  };
}

describe('observed chart dates', () => {
  it('retains the sorted union, true zeroes and missing values for different series', () => {
    const rows = buildRechartDataModel([
      series('stripe', [
        ['2026-10-27', 10],
        ['2026-10-28', 9],
      ]),
      series('google', [
        ['2026-10-28', 3],
        ['2026-10-26', 0],
      ]),
    ]);
    expect(rows.map((row) => row.date)).toEqual([
      '2026-10-26',
      '2026-10-27',
      '2026-10-28',
    ]);
    expect(rows[0]?.['stripe:count']).toBeUndefined();
    expect(rows[0]?.['google:count']).toBe(0);
    expect(rows[1]?.['google:count']).toBeUndefined();
    expect(rows[2]?.['stripe:payload'].count).toBe(9);
    expect(rows[2]?.['google:payload'].count).toBe(3);
  });

  it('preserves a later series when the first one is empty', () => {
    expect(
      buildRechartDataModel([
        series('stripe', []),
        series('google', [['2026-10-28', 3]]),
      ])
    ).toMatchObject([{ date: '2026-10-28', 'google:count': 3 }]);
    expect(buildRechartDataModel([])).toEqual([]);
  });
});
