import { describe, expect, it } from 'vitest';
import { getAggregateQuerySeriesLimit } from '.';

describe('getAggregateQuerySeriesLimit', () => {
  const eventSeries = [{ type: 'event' as const }];

  it('limits simple aggregate queries before transferring their rows', () => {
    expect(getAggregateQuerySeriesLimit(eventSeries, false, 100)).toBe(100);
  });

  it('does not truncate formula dependencies before computation', () => {
    expect(
      getAggregateQuerySeriesLimit(
        [...eventSeries, { type: 'formula' as const }],
        false,
        100,
      ),
    ).toBeUndefined();
  });

  it('does not truncate breakdowns before matching previous-period data', () => {
    expect(
      getAggregateQuerySeriesLimit(eventSeries, true, 100),
    ).toBeUndefined();
  });
});
