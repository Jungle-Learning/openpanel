import type { IChartEventFilter, IChartEventItem } from '@openpanel/validation';
import { describe, expect, it } from 'vitest';

import {
  mergeGlobalFilters,
  transformFilter,
  transformReportEventItem,
} from './reports.service';

describe('transformReportEventItem', () => {
  it('preserves saved custom event identity when hydrating a report', () => {
    const customEventId = '57a989ff-38b8-46d2-8130-321da33ba3ba';

    expect(
      transformReportEventItem(
        {
          id: 'active-action',
          type: 'event',
          name: 'Active Action',
          customEventId,
          segment: 'user',
          filters: [],
        },
        0,
      ),
    ).toMatchObject({
      id: 'active-action',
      name: 'Active Action',
      customEventId,
      segment: 'user',
    });
  });

  it('preserves inline profile-set definitions when hydrating a report', () => {
    expect(
      transformReportEventItem(
        {
          id: 'answer-or-generation',
          type: 'event',
          name: 'Answer or generation',
          eventNames: ['answer', 'generation'],
          setOperation: 'union',
          segment: 'user',
          filters: [],
        },
        0,
      ),
    ).toMatchObject({
      eventNames: ['answer', 'generation'],
      setOperation: 'union',
    });
  });

  it('preserves typed and cohort filter metadata when hydrating a report', () => {
    expect(
      transformReportEventItem(
        {
          id: 'retained-users',
          type: 'event',
          name: 'screen_view',
          segment: 'user',
          filters: [
            {
              id: 'cohort-filter',
              name: 'signup_date',
              operator: 'inCohort',
              value: [],
              type: 'date',
              cohortId: 'legacy-cohort',
              cohortIds: ['cohort-a', 'cohort-b'],
            },
          ],
        },
        0,
      ),
    ).toMatchObject({
      filters: [
        {
          type: 'date',
          cohortId: 'legacy-cohort',
          cohortIds: ['cohort-a', 'cohort-b'],
        },
      ],
    });
  });
});

describe('transformFilter', () => {
  it('normalizes legacy scalar values without dropping optional metadata', () => {
    expect(
      transformFilter(
        {
          name: 'amount',
          operator: 'is',
          value: '12.50' as never,
          type: 'number',
        },
        0,
      ),
    ).toEqual({
      id: 'A',
      name: 'amount',
      operator: 'is',
      value: ['12.50'],
      type: 'number',
    });
  });
});

const globalFilter: IChartEventFilter = {
  id: 'g1',
  name: 'country',
  operator: 'is',
  value: ['US'],
  type: 'string',
};

const eventFilter: IChartEventFilter = {
  id: 'e1',
  name: 'path',
  operator: 'is',
  value: ['/pricing'],
  type: 'string',
};

const eventSeries: IChartEventItem = {
  type: 'event',
  id: 'A',
  name: 'screen_view',
  segment: 'event',
  filters: [eventFilter],
};

const formulaSeries: IChartEventItem = {
  type: 'formula',
  id: 'B',
  formula: 'A/A',
};

describe('mergeGlobalFilters', () => {
  it('prepends global filters to each event series (AND combine)', () => {
    const [merged] = mergeGlobalFilters([eventSeries], [globalFilter]) as [
      IChartEventItem & { type: 'event' },
    ];
    expect(merged.filters).toEqual([globalFilter, eventFilter]);
  });

  it('leaves formula series untouched', () => {
    const [, formula] = mergeGlobalFilters(
      [eventSeries, formulaSeries],
      [globalFilter],
    );
    expect(formula).toBe(formulaSeries);
  });

  it('returns the original series when there are no global filters', () => {
    const series = [eventSeries];
    expect(mergeGlobalFilters(series, [])).toBe(series);
    expect(mergeGlobalFilters(series, undefined)).toBe(series);
  });

  it('does not mutate the input series filters', () => {
    mergeGlobalFilters([eventSeries], [globalFilter]);
    expect(eventSeries.filters).toEqual([eventFilter]);
  });
});
