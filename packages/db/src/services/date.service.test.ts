import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getDatesFromRange } from './date.service';

describe('getDatesFromRange', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-18T12:00:00.000Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('resolves the last 16 months to exactly 16 calendar buckets', () => {
    expect(getDatesFromRange('16m', 'UTC')).toEqual({
      startDate: '2025-05-01 00:00:00',
      endDate: '2026-09-01 00:00:00',
    });
  });
});
