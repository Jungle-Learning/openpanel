import {
  getDefaultIntervalByDates,
  getDefaultIntervalByRange,
  intervals,
  timeWindows,
} from '@openpanel/constants';
import type { IChartRange, IInterval } from '@openpanel/validation';
import { mapKeys } from '@openpanel/validation';
import { parseAsString, parseAsStringEnum, useQueryState } from 'nuqs';

const nuqsOptions = { history: 'push' } as const;

export function getDashboardInterval({
  overrideInterval,
  startDate,
  endDate,
  range,
}: {
  overrideInterval: IInterval | null;
  startDate: string | null;
  endDate: string | null;
  range: IChartRange | null;
}) {
  if (!range && !(startDate && endDate)) {
    return null;
  }

  return (
    overrideInterval ||
    getDefaultIntervalByDates(startDate, endDate) ||
    (range ? getDefaultIntervalByRange(range) : null)
  );
}

/**
 * Dashboard filters are overrides, not defaults. With no query-string range,
 * every report must keep the time window and interval it was saved with.
 */
export function useDashboardOptions() {
  const [startDate, setStartDate] = useQueryState(
    'start',
    parseAsString.withOptions(nuqsOptions),
  );
  const [endDate, setEndDate] = useQueryState(
    'end',
    parseAsString.withOptions(nuqsOptions),
  );
  const [range, setRangeState] = useQueryState(
    'range',
    parseAsStringEnum(mapKeys(timeWindows)).withOptions(nuqsOptions),
  );
  const [overrideInterval, setInterval] = useQueryState(
    'overrideInterval',
    parseAsStringEnum(mapKeys(intervals)).withOptions(nuqsOptions),
  );

  const interval = getDashboardInterval({
    overrideInterval,
    startDate,
    endDate,
    range,
  });

  return {
    range,
    setRange: (value: IChartRange | null) => {
      if (value !== 'custom') {
        setStartDate(null);
        setEndDate(null);
        setInterval(null);
      }
      setRangeState(value);
    },
    startDate,
    setStartDate,
    endDate,
    setEndDate,
    interval,
    setInterval,
  };
}
