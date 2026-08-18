import { describe, expect, it } from 'vitest';
import { getReportTimeWindowLabel } from './report-item-utils';

describe('getReportTimeWindowLabel', () => {
  it('uses the report time window when the dashboard is on Default', () => {
    expect(
      getReportTimeWindowLabel({
        savedRange: '30d',
        overrideRange: null,
        overrideStartDate: null,
        overrideEndDate: null,
      }),
    ).toBe('Last 30 days');
  });

  it('shows only the effective dashboard override', () => {
    expect(
      getReportTimeWindowLabel({
        savedRange: '30d',
        overrideRange: '6m',
        overrideStartDate: null,
        overrideEndDate: null,
      }),
    ).toBe('Last 6 months');
  });

  it('labels a custom dashboard range without showing the saved range', () => {
    expect(
      getReportTimeWindowLabel({
        savedRange: '30d',
        overrideRange: 'custom',
        overrideStartDate: '2026-01-01 00:00:00',
        overrideEndDate: '2026-02-01 23:59:59',
      }),
    ).toBe('Custom dates');
  });
});
