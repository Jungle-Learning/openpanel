import { format } from 'date-fns';
import { describe, expect, it } from 'vitest';
import {
  getCustomTimeWindowDates,
  MAX_CUSTOM_TIME_WINDOW_BUCKETS,
} from './time-window-picker-helpers';

const now = new Date(2026, 7, 18, 12);
const formatRangeDate = (date: Date) => format(date, 'yyyy-MM-dd HH:mm:ss');

describe('getCustomTimeWindowDates', () => {
  it('returns the requested number of calendar days including today', () => {
    const range = getCustomTimeWindowDates('7', 'days', now);

    expect(range && formatRangeDate(range.start)).toBe('2026-08-12 00:00:00');
    expect(range && formatRangeDate(range.end)).toBe('2026-08-19 00:00:00');
  });

  it('returns calendar weeks beginning on Monday including the current week', () => {
    const range = getCustomTimeWindowDates('3', 'weeks', now);

    expect(range && formatRangeDate(range.start)).toBe('2026-08-03 00:00:00');
    expect(range && formatRangeDate(range.end)).toBe('2026-08-19 00:00:00');
  });

  it('returns calendar months including the current month', () => {
    const range = getCustomTimeWindowDates('16', 'months', now);

    expect(range && formatRangeDate(range.start)).toBe('2025-05-01 00:00:00');
    expect(range && formatRangeDate(range.end)).toBe('2026-08-19 00:00:00');
  });

  it.each([
    '',
    '0',
    '1.5',
    `${MAX_CUSTOM_TIME_WINDOW_BUCKETS + 1}`,
  ])('rejects an invalid bucket count of %j', (rawAmount) => {
    expect(getCustomTimeWindowDates(rawAmount, 'days', now)).toBeNull();
  });
});
