import { describe, expect, it } from 'vitest';
import { getConcreteReportEventNames } from './report-events';
import type { IChartEventItem } from './types.validation';

function event(name: string): IChartEventItem {
  return {
    id: name,
    type: 'event',
    name,
    segment: 'event',
    filters: [],
  };
}

describe('getConcreteReportEventNames', () => {
  it('scopes property discovery to the selected event', () => {
    expect(
      getConcreteReportEventNames([event('Subscription Payment Received')])
    ).toEqual(['Subscription Payment Received']);
  });

  it('includes every distinct event in a multi-series report', () => {
    expect(
      getConcreteReportEventNames([
        event('Subscription Payment Received'),
        event('Checkout Started'),
        event('Subscription Payment Received'),
      ])
    ).toEqual(['Subscription Payment Received', 'Checkout Started']);
  });

  it('ignores formulas because they do not own event properties', () => {
    const formula: IChartEventItem = {
      id: 'formula',
      type: 'formula',
      formula: 'A / B',
    };

    expect(getConcreteReportEventNames([event('Payment'), formula])).toEqual([
      'Payment',
    ]);
  });

  it('returns no dynamic-property scope before an event is selected', () => {
    expect(getConcreteReportEventNames([])).toEqual([]);
  });

  it('does not trigger an unbounded property scan for the wildcard event', () => {
    expect(getConcreteReportEventNames([event('*')])).toEqual([]);
  });
});
