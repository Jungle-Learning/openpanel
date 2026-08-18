import {
  addDays,
  startOfDay,
  startOfMonth,
  startOfWeek,
  subMonths,
  subWeeks,
} from 'date-fns';

export const MAX_CUSTOM_TIME_WINDOW_BUCKETS = 365;

export type CustomTimeWindowUnit = 'days' | 'weeks' | 'months';

export function getCustomTimeWindowDates(
  rawAmount: string,
  unit: CustomTimeWindowUnit,
  now = new Date()
): { start: Date; end: Date } | null {
  const amount = Number(rawAmount);
  if (
    !Number.isInteger(amount) ||
    amount < 1 ||
    amount > MAX_CUSTOM_TIME_WINDOW_BUCKETS
  ) {
    return null;
  }

  const end = startOfDay(addDays(now, 1));

  if (unit === 'days') {
    return {
      start: startOfDay(addDays(now, -(amount - 1))),
      end,
    };
  }

  if (unit === 'weeks') {
    const currentWeekStart = startOfWeek(now, { weekStartsOn: 1 });
    return {
      start: subWeeks(currentWeekStart, amount - 1),
      end,
    };
  }

  const currentMonthStart = startOfMonth(now);
  return {
    start: subMonths(currentMonthStart, amount - 1),
    end,
  };
}
