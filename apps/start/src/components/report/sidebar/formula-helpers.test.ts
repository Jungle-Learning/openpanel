import type { IChartEventItem } from '@openpanel/validation';
import { describe, expect, it } from 'vitest';
import {
  buildFormulaFromTemplate,
  resolveProfileSetFormula,
} from './formula-helpers';

const event = (name: string): Extract<IChartEventItem, { type: 'event' }> => ({
  id: name,
  type: 'event',
  name,
  segment: 'event',
  filters: [],
});

describe('formula helpers', () => {
  it('builds profile unions from every eligible preceding event', () => {
    const series = [event('answer'), event('generation'), event('voice')];
    expect(
      buildFormulaFromTemplate({
        templateId: 'union',
        series,
        formulaIndex: 3,
      }),
    ).toBe('UNION(A, B, C)');
  });

  it('excludes filtered events from profile set templates', () => {
    const filteredEvent: IChartEventItem = {
      ...event('generation'),
      filters: [{ name: 'country', operator: 'is', value: ['US'] }],
    };
    expect(
      buildFormulaFromTemplate({
        templateId: 'intersection',
        series: [event('answer'), filteredEvent, event('voice')],
        formulaIndex: 3,
      }),
    ).toBe('INTERSECTION(A, C)');
  });

  it('resolves the legacy pipe syntax as a union of source event names', () => {
    expect(
      resolveProfileSetFormula({
        formula: 'A | B',
        series: [event('answer'), event('generation')],
        formulaIndex: 2,
      }),
    ).toEqual({
      operation: 'union',
      referenceIds: ['A', 'B'],
      eventNames: ['answer', 'generation'],
    });
  });
});
