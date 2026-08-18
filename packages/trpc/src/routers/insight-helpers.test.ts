import { describe, expect, it } from 'vitest';
import { buildInsightSegmentFilters } from './insight-helpers';

describe('buildInsightSegmentFilters', () => {
  it('scopes an explanation to every populated insight dimension', () => {
    expect(
      buildInsightSegmentFilters({
        kind: 'insight_v1',
        dimensions: [
          { key: 'origin', value: 'https://example.com', displayName: 'Site' },
          { key: 'path', value: '/pricing', displayName: '/pricing' },
        ],
        primaryMetric: 'sessions',
        metrics: {},
      })
    ).toEqual([
      {
        name: 'origin',
        operator: 'is',
        value: ['https://example.com'],
      },
      { name: 'path', operator: 'is', value: ['/pricing'] },
    ]);
  });

  it('maps the displayed direct referrer back to its stored empty value', () => {
    expect(
      buildInsightSegmentFilters({
        kind: 'insight_v1',
        dimensions: [
          { key: 'referrer_name', value: 'direct', displayName: 'direct' },
        ],
        primaryMetric: 'sessions',
        metrics: {},
      })
    ).toEqual([{ name: 'referrer_name', operator: 'is', value: [''] }]);
  });

  it('returns no filters when the insight has no usable dimensions', () => {
    expect(buildInsightSegmentFilters(null)).toEqual([]);
  });
});
