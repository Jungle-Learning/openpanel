import { describe, expect, it } from 'vitest';
import { formatDate } from './date';

describe('formatDate', () => {
  it('formats dates with the month before the day', () => {
    expect(formatDate(new Date(2026, 6, 4))).toBe('jul 4');
  });
});
