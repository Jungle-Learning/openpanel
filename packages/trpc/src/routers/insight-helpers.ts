import type { IChartEventFilter, InsightPayload } from '@openpanel/validation';

export function buildInsightSegmentFilters(
  payload: InsightPayload | null
): IChartEventFilter[] {
  return (payload?.dimensions ?? []).flatMap((dimension) => {
    if (!dimension.value) {
      return [];
    }

    // Referrer insights normalize an empty referrer to "direct" for display,
    // while the sessions table stores the original empty value.
    const value =
      dimension.key === 'referrer_name' &&
      dimension.value.toLowerCase() === 'direct'
        ? ''
        : dimension.value;

    return [
      {
        name: dimension.key,
        operator: 'is' as const,
        value: [value],
      },
    ];
  });
}
