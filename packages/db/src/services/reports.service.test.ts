import { describe, expect, it } from 'vitest';

import { transformReportEventItem } from './reports.service';

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
});
