import type { IInterval } from '@openpanel/validation';
import { getISOWeek } from 'date-fns';

const OPENPANEL_DATE_LOCALE = 'en-US';

export function formatDateInterval(options: {
  interval: IInterval;
  date: Date | string;
  short: boolean;
}): string {
  const { interval, short } = options;
  const date = parseChartDate(options.date);
  try {
    if (interval === 'hour' || interval === 'minute') {
      if (short) {
        return new Intl.DateTimeFormat(OPENPANEL_DATE_LOCALE, {
          hour: '2-digit',
          minute: '2-digit',
          hour12: false,
        }).format(date);
      }
      return new Intl.DateTimeFormat(OPENPANEL_DATE_LOCALE, {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(date);
    }

    if (interval === 'month') {
      return new Intl.DateTimeFormat(OPENPANEL_DATE_LOCALE, {
        month: 'short',
      }).format(date);
    }

    if (interval === 'week') {
      if (short) {
        return `W${getISOWeek(date)}`;
      }
      return new Intl.DateTimeFormat(OPENPANEL_DATE_LOCALE, {
        weekday: 'short',
        day: '2-digit',
        month: '2-digit',
      }).format(date);
    }

    if (interval === 'day') {
      if (short) {
        return new Intl.DateTimeFormat(OPENPANEL_DATE_LOCALE, {
          day: 'numeric',
          month: 'short',
        }).format(date);
      }
      return new Intl.DateTimeFormat(OPENPANEL_DATE_LOCALE, {
        weekday: 'short',
        day: '2-digit',
        month: '2-digit',
      }).format(date);
    }

    return date.toISOString();
  } catch {
    return '';
  }
}

export function parseChartDate(date: Date | string): Date {
  if (date instanceof Date) {
    return date;
  }

  const dateOnlyMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (dateOnlyMatch) {
    const [, year, month, day] = dateOnlyMatch;
    // ClickHouse date buckets are calendar labels in the project's timezone,
    // not UTC instants. Parsing YYYY-MM-DD with `new Date(string)` treats it as
    // UTC and can display the previous month/day in western timezones.
    return new Date(Number(year), Number(month) - 1, Number(day));
  }

  return new Date(date);
}

export function useFormatDateInterval(options: {
  interval: IInterval;
  short: boolean;
}) {
  return (date: Date | string) =>
    formatDateInterval({
      ...options,
      date,
    });
}
