import type { IRechartPayloadItem } from '@/hooks/use-rechart-data-model';
import { describe, expect, it } from 'vitest';
import { getBreakdownTooltipTotal } from './report-chart-tooltip-utils';

const createPayloadItem = (count: number) =>
  ({ count }) as IRechartPayloadItem;

describe('getBreakdownTooltipTotal', () => {
  it('sums every breakdown value in the hovered time bucket', () => {
    expect(
      getBreakdownTooltipTotal(
        [createPayloadItem(10_827), createPayloadItem(5_834)],
        1,
      ),
    ).toBe(16_661);
  });

  it('does not show a tooltip total without a breakdown', () => {
    expect(getBreakdownTooltipTotal([createPayloadItem(16_661)], 0)).toBeNull();
  });

  it('does not show a tooltip total when the hovered bucket is empty', () => {
    expect(getBreakdownTooltipTotal([], 1)).toBeNull();
  });
});
