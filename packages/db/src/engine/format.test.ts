import { describe, expect, it } from 'vitest';
import { format } from './format';
import type { ConcreteSeries } from './types';

function series(values: [string, number][]): ConcreteSeries {
  return {
    id: 'snapshot',
    definitionId: 'snapshot',
    definitionIndex: 0,
    name: ['snapshot'],
    context: { filters: [] },
    definition: {
      type: 'event',
      id: 'snapshot',
      name: 'snapshot',
      segment: 'event',
      filters: [],
    },
    data: values.map(([date, count]) => ({ date, count })),
  };
}

describe('observed previous-period alignment', () => {
  it('matches period bucket offsets and leaves missing previous observations unset', () => {
    const current = series([
      ['2026-10-22', 90],
      ['2026-10-23', 70],
      ['2026-10-24', 60],
    ]);
    const previous = series([
      ['2026-10-20', 100],
      ['2026-10-21', 80],
    ]);
    const result = format(
      [current],
      [current.definition],
      false,
      [previous],
      undefined,
      {
        interval: 'day',
        currentStartDate: '2026-10-22 00:00:00',
        previousStartDate: '2026-10-19 00:00:00',
      }
    );
    expect(
      result.series[0]?.data.map((point) => point.previous?.value)
    ).toEqual([undefined, 100, 80]);
  });

  it('aligns calendar months rather than elapsed milliseconds', () => {
    const current = series([
      ['2026-03-01', 9],
      ['2026-04-01', 0],
    ]);
    const previous = series([['2026-01-01', 0]]);
    const result = format(
      [current],
      [current.definition],
      false,
      [previous],
      undefined,
      {
        interval: 'month',
        currentStartDate: '2026-03-01 00:00:00',
        previousStartDate: '2026-01-01 00:00:00',
      }
    );
    expect(
      result.series[0]?.data.map((point) => point.previous?.value)
    ).toEqual([0, undefined]);
  });
});
