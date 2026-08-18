import { timeWindows } from '@openpanel/constants';
import type { IChartRange } from '@openpanel/validation';

export function getReportTimeWindowLabel({
  savedRange,
  overrideRange,
  overrideStartDate,
  overrideEndDate,
}: {
  savedRange: IChartRange | null | undefined;
  overrideRange: IChartRange | null | undefined;
  overrideStartDate: string | null | undefined;
  overrideEndDate: string | null | undefined;
}): string | undefined {
  if (overrideStartDate && overrideEndDate) {
    return 'Custom dates';
  }

  const effectiveRange = overrideRange ?? savedRange;
  return effectiveRange ? timeWindows[effectiveRange]?.label : undefined;
}
