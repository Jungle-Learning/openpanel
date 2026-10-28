import { describe, expect, it } from 'vitest';
import { groupByLabels } from './group-by-labels';

describe('groupByLabels', () => {
  it('fills missing dates and preserves the first duplicate and global date order', () => {
    const data = groupByLabels([
      { label_0: 'B', date: '2026-10-02', count: 4 },
      { label_0: 'A', date: '2026-10-01', count: 2 },
      { label_0: 'B', date: '2026-10-02', count: 99 },
      { label_0: 'B', date: '2026-10-01', count: 3 },
    ]);
    expect(data).toEqual([
      {
        name: ['B'],
        data: [
          { date: '2026-10-02', count: 4, total_count: undefined },
          { date: '2026-10-01', count: 3, total_count: undefined },
        ],
      },
      {
        name: ['A'],
        data: [
          { date: '2026-10-02', count: 0, total_count: 0 },
          { date: '2026-10-01', count: 2, total_count: undefined },
        ],
      },
    ]);
  });
  it('omits rows with no labels', () => {
    expect(
      groupByLabels([{ label_0: null, date: '2026-10-01', count: 2 }])
    ).toEqual([]);
  });
});
