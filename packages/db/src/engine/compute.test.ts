import { describe, expect, it } from 'vitest';
import { compute } from './compute';
import type { ConcreteSeries } from './types';

function event(index: number, values: number[]): ConcreteSeries {
  const id = index === 0 ? 'A' : 'B';
  const definition = {
    type: 'event' as const,
    id,
    name: id,
    segment: 'event' as const,
    filters: [],
  };
  return {
    id,
    definitionId: id,
    definitionIndex: index,
    definition,
    name: [id],
    context: { filters: [] },
    data: values.map((count, day) => ({
      date: `2026-10-0${day + 1}`,
      count,
      total_count: values[0],
    })),
  };
}

describe('formula computation', () => {
  it('keeps date scopes separate, resolves chained formulas, and handles division by zero', () => {
    const a = event(0, [8, 9, 10]),
      b = event(1, [2, 3, 0]);
    const result = compute(
      [a, b],
      [
        a.definition,
        b.definition,
        { type: 'formula', id: 'C', formula: 'A/B' },
        { type: 'formula', id: 'D', formula: 'C*2' },
      ]
    );
    expect(result[2]?.data.map((point) => point.count)).toEqual([4, 3, 0]);
    expect(result[3]?.data.map((point) => point.count)).toEqual([8, 6, 0]);
    expect(result[2]?.data.map((point) => point.total_count)).toEqual([
      4, 4, 4,
    ]);
  });
  it('retains zeros and unknown totals for an invalid formula', () => {
    const a = event(0, [8, 9]);
    const result = compute(
      [a],
      [a.definition, { type: 'formula', id: 'B', formula: 'A / (' }]
    );
    expect(result[1]?.data.map((point) => point.count)).toEqual([0, 0]);
    expect(result[1]?.data.map((point) => point.total_count)).toEqual([
      undefined,
      undefined,
    ]);
  });
});
