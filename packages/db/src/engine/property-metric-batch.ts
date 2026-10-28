import type { ISerieDataItem } from '@openpanel/common';
import type { IChartEvent } from '@openpanel/validation';
import { chQuery } from '../clickhouse/client';
import {
  getAggregateChartSql,
  isKnownEventField,
  PROPERTY_METRIC_FUNCTIONS,
} from '../services/chart.service';
import type { NormalizedInput } from './normalize';

/** Fuse compatible stock/property metrics without changing other report paths. */
export async function fetchPropertyMetricBatch(
  input: NormalizedInput,
  limit: number | undefined
): Promise<ISerieDataItem[][] | null> {
  if (input.breakdowns.length || input.series.length < 2) return null;
  const first = input.series[0];
  if (!first || first.type !== 'event') return null;
  const filters = JSON.stringify(first.filters);
  const groupScope = first.property?.startsWith('group.') ?? false;
  const compatible = input.series.every(
    (item) =>
      item.type === 'event' &&
      !!PROPERTY_METRIC_FUNCTIONS[item.segment] &&
      !!item.property &&
      isKnownEventField(item.property) &&
      !item.property.startsWith('profile.') &&
      !item.property.includes('*') &&
      item.property.startsWith('group.') === groupScope &&
      item.name === first.name &&
      !item.eventNames?.length &&
      !item.customEventId &&
      JSON.stringify(item.filters) === filters
  );
  if (!compatible) return null;

  const metrics = input.series as IChartEvent[];
  const sql = await getAggregateChartSql(
    {
      event: metrics[0]!,
      projectId: input.projectId,
      startDate: input.startDate,
      endDate: input.endDate,
      breakdowns: [],
      limit,
      metric: input.metric,
      previous: input.previous,
      timezone: input.timezone,
    },
    metrics
  );
  const rows = await chQuery<ISerieDataItem & Record<string, unknown>>(sql, {
    session_timezone: input.timezone,
  });
  return metrics.map((_, index) =>
    rows.flatMap((row) =>
      Number(row[`present_${index}`]) > 0
        ? [
            {
              date: row.date,
              label_0: row.label_0,
              count: row[`metric_${index}`] as number,
            },
          ]
        : []
    )
  );
}
