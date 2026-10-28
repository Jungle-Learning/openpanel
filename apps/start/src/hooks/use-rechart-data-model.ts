import type { IChartData } from '@/trpc/client';
import { getChartColor } from '@/utils/theme';
import { useMemo } from 'react';
import { parseChartDate } from './use-format-date-interval';

export type IRechartPayloadItem = {
  id: string;
  names: string[];
  color: string;
  event: { id?: string; name: string };
  count: number;
  date: string;
  previous?: {
    value: number;
    diff: number | null;
    state: 'positive' | 'negative' | 'neutral';
  };
};

export function useRechartDataModel(series: IChartData['series']) {
  return useMemo(() => buildRechartDataModel(series), [series]);
}

export function buildRechartDataModel(series: IChartData['series']) {
  const rows = new Map<
    string,
    { date: string; timestamp: number } & Record<string, any>
  >();
  for (const [index, serie] of series.entries()) {
    for (const item of serie.data) {
      let row = rows.get(item.date);
      if (!row) {
        row = {
          date: item.date,
          timestamp: parseChartDate(item.date).getTime(),
        };
        rows.set(item.date, row);
      }
      if (item.previous) {
        row[`${serie.id}:prev:count`] = item.previous.value;
      }
      row[`${serie.id}:count`] = item.count;
      row[`${serie.id}:payload`] = {
        ...item,
        id: serie.id,
        event: serie.event,
        names: serie.names,
        color: getChartColor(index),
      } satisfies IRechartPayloadItem;
    }
  }
  return [...rows.values()].sort((a, b) => a.timestamp - b.timestamp);
}
