import type { IChartData } from '@/trpc/client';
import { describe, expect, it } from 'vitest';
import { getChartTotal } from './chart-total';

describe('getChartTotal', () => {
  it('uses the aggregate returned for the complete chart', () => {
    const data = {
      series: [],
      metrics: {
        sum: 101_878,
        average: 0,
        min: 0,
        max: 0,
        count: undefined,
      },
    } as IChartData;

    expect(getChartTotal(data)).toBe(101_878);
  });
});
