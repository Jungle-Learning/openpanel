import { describe, expect, it } from 'vitest';
import {
  getRetentionEventNames,
  getRetentionSelectedEventNames,
} from './retention';

describe('getRetentionEventNames', () => {
  it('reads the event from an older saved report series', () => {
    expect(
      getRetentionEventNames({
        name: 'New User Sign Up',
        filters: [{ name: 'country', value: ['IN'] }],
      }),
    ).toEqual(['New User Sign Up']);
  });

  it('prefers the multi-event name filter used by current reports', () => {
    expect(
      getRetentionEventNames({
        name: '*',
        filters: [
          { name: 'name', value: ['app_open', 'page_view', 'app_open'] },
          { name: 'country', value: ['US'] },
        ],
      }),
    ).toEqual(['app_open', 'page_view']);
  });

  it('represents the any-event wildcard as an empty name list', () => {
    expect(getRetentionEventNames({ name: '*', filters: [] })).toEqual([]);
  });

  it('preserves the wildcard for the event-selection UI', () => {
    expect(
      getRetentionSelectedEventNames({ name: '*', filters: [] }),
    ).toEqual(['*']);
  });
});
