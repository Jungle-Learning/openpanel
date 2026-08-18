import type { IInterval } from '@openpanel/validation';
import {
  format,
  isSameDay,
  isSameHour,
  isSameMinute,
  isSameMonth,
  isSameWeek,
} from 'date-fns';
import React from 'react';
import { useReportChartContext } from '../context';
import { PreviousDiffIndicator } from './previous-diff-indicator';
import { getBreakdownTooltipTotal } from './report-chart-tooltip-utils';
import { SerieIcon } from './serie-icon';
import { SerieName } from './serie-name';
import {
  ChartTooltipHeader,
  ChartTooltipItem,
  createChartTooltip,
} from '@/components/charts/chart-tooltip';
import {
  parseChartDate,
  useFormatDateInterval,
} from '@/hooks/use-format-date-interval';
import { useNumber } from '@/hooks/use-numer-formatter';
import type { IRechartPayloadItem } from '@/hooks/use-rechart-data-model';
import type { RouterOutputs } from '@/trpc/client';

const getMatchingReferences = (
  interval: IInterval,
  references: RouterOutputs['reference']['getChartReferences'],
  date: Date,
) => {
  return references.filter((reference) => {
    if (interval === 'minute') {
      return isSameMinute(reference.date, date);
    }
    if (interval === 'hour') {
      return isSameHour(reference.date, date);
    }
    if (interval === 'day') {
      return isSameDay(reference.date, date);
    }
    if (interval === 'week') {
      return isSameWeek(reference.date, date);
    }
    if (interval === 'month') {
      return isSameMonth(reference.date, date);
    }
    return false;
  });
};

type Context = {
  references?: RouterOutputs['reference']['getChartReferences'];
};
type Data = {
  date: string;
  timestamp: number;
  [key: `${string}:count`]: number;
  [key: `${string}:payload`]: IRechartPayloadItem;
};
export const ReportChartTooltip = createChartTooltip<Data, Context>(
  ({ context: { references }, data }) => {
    const {
      report: { breakdowns, interval, unit },
    } = useReportChartContext();
    const formatDate = useFormatDateInterval({
      interval,
      short: false,
    });
    const number = useNumber();

    if (!data || data.length === 0) {
      return null;
    }

    const firstItem = data[0];
    const matchingReferences = getMatchingReferences(
      interval,
      references ?? [],
      parseChartDate(firstItem.date),
    );

    // Get all payload items from the first data point
    const payloadItems = Object.keys(firstItem)
      .filter((key) => key.endsWith(':payload'))
      .map(
        (key) =>
          firstItem[key as keyof typeof firstItem] as IRechartPayloadItem,
      )
      .filter((item) => item && typeof item === 'object' && 'id' in item);

    // Sort by count
    const sorted = payloadItems.sort((a, b) => (b.count || 0) - (a.count || 0));
    const limit = 3;
    const visible = sorted.slice(0, limit);
    const hidden = sorted.slice(limit);
    const breakdownTotal = getBreakdownTooltipTotal(
      payloadItems,
      breakdowns?.length ?? 0,
    );

    return (
      <>
        {visible[0]?.date && (
          <ChartTooltipHeader>
            <div>{formatDate(visible[0].date)}</div>
          </ChartTooltipHeader>
        )}
        {breakdownTotal !== null && (
          <div className="flex items-center justify-between gap-8 border-border border-b pb-2">
            <div className="text-muted-foreground">Total</div>
            <div className="font-medium font-mono">
              {number.formatWithUnit(breakdownTotal, unit)}
            </div>
          </div>
        )}
        {visible.map((item) => (
          <React.Fragment key={item.id}>
            <ChartTooltipItem color={item.color}>
              <div className="flex items-center gap-1">
                <SerieIcon name={item.names} />
                <SerieName name={item.names} />
              </div>
              <div className="flex justify-between gap-8 font-medium font-mono">
                <div className="row gap-1">
                  {number.formatWithUnit(item.count, unit)}
                  {!!item.previous && (
                    <span className="text-muted-foreground">
                      ({number.formatWithUnit(item.previous.value, unit)})
                    </span>
                  )}
                </div>
                <PreviousDiffIndicator {...item.previous} />
              </div>
            </ChartTooltipItem>
          </React.Fragment>
        ))}
        {hidden.length > 0 && (
          <div className="text-muted-foreground">
            and {hidden.length} more...
          </div>
        )}
        {matchingReferences.length > 0 && (
          <>
            <hr className="border-border" />
            {matchingReferences.map((reference) => (
              <div
                className="row items-center justify-between"
                key={reference.id}
              >
                <div className="font-medium text-sm">{reference.title}</div>
                <div className="shrink-0 font-medium text-muted-foreground text-sm">
                  {format(reference.date, 'HH:mm')}
                </div>
              </div>
            ))}
          </>
        )}
      </>
    );
  },
);
