import { changeVisibleSeries } from '@/components/report/reportSlice';
import { useVisibleSeries } from '@/hooks/use-visible-series';
import { useDispatch } from '@/redux';
import type { IChartData } from '@/trpc/client';
import { cn } from '@/utils/cn';
import { round } from '@/utils/math';
import { getChartColor } from '@/utils/theme';
import { Fragment, useMemo } from 'react';
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';

import {
  ChartTooltipContainer,
  ChartTooltipHeader,
  ChartTooltipItem,
} from '@/components/charts/chart-tooltip';
import { useNumber } from '@/hooks/use-numer-formatter';
import { parseChartDate } from '@/hooks/use-format-date-interval';
import { formatDate } from '@/utils/date';
import { AXIS_FONT_PROPS } from '../common/axis';
import { PreviousDiffIndicator } from '../common/previous-diff-indicator';
import { ReportTable } from '../common/report-table';
import { SerieIcon } from '../common/serie-icon';
import { SerieName } from '../common/serie-name';
import {
  getSerieDisplayNames,
  hasBreakdownValue,
} from '../common/serie-name-utils';
import { useReportChartContext } from '../context';

interface Props {
  data: IChartData;
}

const PieTooltip = (props: { payload?: any[] }) => {
  const number = useNumber();
  return (
    <ChartTooltipContainer>
      {props.payload?.map((serie, index) => {
        const item = serie.payload;
        return (
          <Fragment key={item.id}>
            {index === 0 && item.date && (
              <ChartTooltipHeader>
                <div>{formatDate(parseChartDate(item.date))}</div>
              </ChartTooltipHeader>
            )}
            <ChartTooltipItem color={item.color}>
              <div className="flex items-center gap-1">
                <SerieIcon name={item.name} />
                <SerieName name={item.names} className="font-medium" />
              </div>
              <div className="flex justify-between gap-8 font-mono font-medium">
                <div className="row gap-1">
                  {number.formatWithUnit(item.count)}
                  {!!item.previous && (
                    <span className="text-muted-foreground">
                      ({number.formatWithUnit(item.previous.sum.value)})
                    </span>
                  )}
                </div>
                <PreviousDiffIndicator {...item.previous?.sum} />
              </div>
            </ChartTooltipItem>
          </Fragment>
        );
      })}
    </ChartTooltipContainer>
  );
};

export function Chart({ data }: Props) {
  const {
    isEditMode,
    report: { breakdowns, visibleSeries: savedVisibleSeries },
  } = useReportChartContext();
  const dispatch = useDispatch();
  const dataWithBreakdownValues = useMemo(
    () => ({
      ...data,
      series: data.series.filter((serie) =>
        hasBreakdownValue(serie.names, breakdowns.length),
      ),
    }),
    [data, breakdowns.length],
  );
  const { series, setVisibleSeries } = useVisibleSeries(dataWithBreakdownValues, {
    savedVisibleSeries,
    onVisibleSeriesChange: isEditMode
      ? (ids) => dispatch(changeVisibleSeries(ids))
      : undefined,
  });

  const sum =
    series.reduce((acc, serie) => acc + serie.metrics.sum, 0) || 1;
  const pieData = series.map((serie) => ({
    id: serie.id,
    color: getChartColor(serie.index),
    index: serie.index,
    name: getSerieDisplayNames(serie.names, breakdowns.length).join(' > '),
    names: serie.names,
    count: serie.metrics.sum,
    percent: serie.metrics.sum / sum,
    previous: serie.metrics.previous ? serie.metrics.previous : undefined,
  }));

  return (
    <>
      <div
        className={cn(
          'flex h-full w-full flex-col max-sm:-mx-3',
          isEditMode && 'card p-4',
        )}
      >
        <div className="min-h-0 flex-1">
          <ResponsiveContainer>
            <PieChart>
              <Tooltip content={<PieTooltip />} />
              <Pie
                dataKey={'count'}
                data={pieData}
                innerRadius={'30%'}
                outerRadius={'82%'}
                isAnimationActive={false}
                label={renderLabel}
                labelLine={false}
              >
                {pieData.map((item) => {
                  return (
                    <Cell
                      key={item.id}
                      strokeWidth={4}
                      className="stroke-background"
                      fill={item.color}
                    />
                  );
                })}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 px-2 text-xs">
          {series.map((serie) => (
            <div className="flex min-w-0 items-center gap-1.5" key={serie.id}>
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ backgroundColor: getChartColor(serie.index) }}
              />
              <SerieName
                className="max-w-40 truncate font-medium"
                name={serie.names}
              />
            </div>
          ))}
        </div>
      </div>
      {isEditMode && (
        <ReportTable
          data={dataWithBreakdownValues}
          visibleSeries={series}
          setVisibleSeries={setVisibleSeries}
        />
      )}
    </>
  );
}

const renderLabel = ({
  cx,
  cy,
  midAngle,
  innerRadius,
  outerRadius,
  payload,
}: {
  cx: number;
  cy: number;
  midAngle: number;
  innerRadius: number;
  outerRadius: number;
  payload: { name: string; percent: number };
}) => {
  const RADIAN = Math.PI / 180;
  const radiusProcent = innerRadius + (outerRadius - innerRadius) * 0.5;
  const xProcent = cx + radiusProcent * Math.cos(-midAngle * RADIAN);
  const yProcent = cy + radiusProcent * Math.sin(-midAngle * RADIAN);
  const percent = round(payload.percent * 100, 1);

  if (percent < 4) {
    return null;
  }

  return (
    <text
      x={xProcent}
      y={yProcent}
      fill="white"
      stroke="rgb(0 0 0 / 65%)"
      strokeWidth={3}
      paintOrder="stroke"
      textAnchor="middle"
      dominantBaseline="central"
      pointerEvents={'none'}
      {...AXIS_FONT_PROPS}
      fontSize={12}
      fontWeight={700}
    >
      {percent}%
    </text>
  );
};
