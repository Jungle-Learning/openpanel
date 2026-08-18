import { getDefaultIntervalByRange } from '@openpanel/constants';
import { describe, expect, it } from 'vitest';
import { getDashboardInterval } from './use-dashboard-options';

describe('getDashboardInterval', () => {
  it('leaves the interval unset when the dashboard uses report defaults', () => {
    expect(
      getDashboardInterval({
        overrideInterval: null,
        startDate: null,
        endDate: null,
        range: null,
      }),
    ).toBeNull();
  });

  it('ignores an orphaned interval when no dashboard time window is selected', () => {
    expect(
      getDashboardInterval({
        overrideInterval: 'day',
        startDate: null,
        endDate: null,
        range: null,
      }),
    ).toBeNull();
  });

  it('derives an interval when the dashboard overrides the time window', () => {
    expect(
      getDashboardInterval({
        overrideInterval: null,
        startDate: null,
        endDate: null,
        range: '6m',
      }),
    ).toBe(getDefaultIntervalByRange('6m'));
  });

  it('keeps an explicit interval override', () => {
    expect(
      getDashboardInterval({
        overrideInterval: 'day',
        startDate: null,
        endDate: null,
        range: '6m',
      }),
    ).toBe('day');
  });
});
