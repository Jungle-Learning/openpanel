import { useNumber } from '@/hooks/use-numer-formatter';
import type { IChartData } from '@/trpc/client';

import { useReportChartContext } from '../context';

interface ChartTotalProps {
  data: IChartData;
}

export function getChartTotal(data: IChartData) {
  return data.metrics.sum;
}

export function ChartTotal({ data }: ChartTotalProps) {
  const {
    report: { unit },
  } = useReportChartContext();
  const number = useNumber();

  return (
    <div className="mb-3 shrink-0">
      <div className="text-sm text-muted-foreground">Total</div>
      <div className="font-mono text-2xl font-semibold tabular-nums">
        {number.formatWithUnit(getChartTotal(data), unit)}
      </div>
    </div>
  );
}
