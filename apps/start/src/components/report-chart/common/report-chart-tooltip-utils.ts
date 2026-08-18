import type { IRechartPayloadItem } from '@/hooks/use-rechart-data-model';

export function getBreakdownTooltipTotal(
  payloadItems: IRechartPayloadItem[],
  breakdownCount: number,
) {
  if (breakdownCount === 0 || payloadItems.length === 0) {
    return null;
  }

  return payloadItems.reduce((total, item) => total + item.count, 0);
}
