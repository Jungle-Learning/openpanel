import type { IChartEventItem } from './types.validation';

export function getConcreteReportEventNames(series: IChartEventItem[]) {
  return Array.from(
    new Set(
      series
        .filter(
          (item): item is IChartEventItem & { type: 'event' } =>
            item.type === 'event' && item.name !== '*'
        )
        .map((item) => item.name)
    )
  );
}
