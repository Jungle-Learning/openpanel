import { describe, expect, it } from 'vitest';
import {
  getSerieDisplayNames,
  hasBreakdownValue,
} from './serie-name-utils';

describe('getSerieDisplayNames', () => {
  it('shows only breakdown values when the report has a breakdown', () => {
    expect(
      getSerieDisplayNames(
        ['Answer Submitted for Feedback', 'United States'],
        1,
      ),
    ).toEqual(['United States']);
  });

  it('keeps the event name when there is no breakdown', () => {
    expect(getSerieDisplayNames(['New User Sign Up'], 0)).toEqual([
      'New User Sign Up',
    ]);
  });
});

describe('hasBreakdownValue', () => {
  it('rejects an aggregate row whose breakdown value is missing', () => {
    expect(hasBreakdownValue(['Answer Submitted for Feedback'], 1)).toBe(
      false,
    );
  });

  it('keeps ordinary series and populated breakdown rows', () => {
    expect(hasBreakdownValue(['Answer Submitted for Feedback'], 0)).toBe(true);
    expect(
      hasBreakdownValue(['Answer Submitted for Feedback', 'Harvard'], 1),
    ).toBe(true);
  });
});
