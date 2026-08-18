import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { formatDateInterval, parseChartDate } from './use-format-date-interval';

const originalTimezone = process.env.TZ;

beforeAll(() => {
  process.env.TZ = 'America/Los_Angeles';
});

afterAll(() => {
  if (originalTimezone) {
    process.env.TZ = originalTimezone;
  } else {
    delete process.env.TZ;
  }
});

describe('formatDateInterval', () => {
  it('keeps a date-only monthly bucket in its stated calendar month', () => {
    expect(
      formatDateInterval({
        interval: 'month',
        date: '2026-08-01',
        short: false,
      })
    ).toBe('Aug');
  });

  it('parses date-only buckets as local calendar dates instead of UTC instants', () => {
    const parsedDate = parseChartDate('2026-08-01');

    expect(parsedDate.getFullYear()).toBe(2026);
    expect(parsedDate.getMonth()).toBe(7);
    expect(parsedDate.getDate()).toBe(1);
  });

  it('preserves timestamp strings as instants', () => {
    expect(parseChartDate('2026-08-01T12:30:00.000Z').toISOString()).toBe(
      '2026-08-01T12:30:00.000Z'
    );
  });

  it('formats daily chart dates in month/day order', () => {
    expect(
      formatDateInterval({
        interval: 'day',
        date: '2026-07-04',
        short: false,
      })
    ).toBe('Sat, 07/04');
  });

  it('formats compact daily chart dates with the month first', () => {
    expect(
      formatDateInterval({
        interval: 'day',
        date: '2026-07-04',
        short: true,
      })
    ).toBe('Jul 4');
  });
});
