import { describe, expect, it } from 'vitest';
import { getPropertyEventNames } from './chart-property-events';

describe('getPropertyEventNames', () => {
  it('uses the existing single-event scope', () => {
    expect(getPropertyEventNames({ event: 'Payment' })).toEqual(['Payment']);
  });

  it('uses and deduplicates a multi-event breakdown scope', () => {
    expect(
      getPropertyEventNames({ eventNames: ['Payment', 'Checkout', 'Payment'] })
    ).toEqual(['Payment', 'Checkout']);
  });

  it('prefers the single-event scope when both inputs are present', () => {
    expect(
      getPropertyEventNames({ event: 'Payment', eventNames: ['Checkout'] })
    ).toEqual(['Payment']);
  });

  it('keeps an explicit empty scope empty so callers can skip the global scan', () => {
    expect(getPropertyEventNames({ eventNames: [] })).toEqual([]);
  });

  it('removes wildcard event names from a bounded scope', () => {
    expect(getPropertyEventNames({ eventNames: ['*', 'Payment'] })).toEqual([
      'Payment',
    ]);
    expect(getPropertyEventNames({ event: '*', eventNames: [] })).toEqual([]);
  });

  it('preserves the legacy unscoped behavior when no scope is supplied', () => {
    expect(getPropertyEventNames({})).toBeUndefined();
  });
});
