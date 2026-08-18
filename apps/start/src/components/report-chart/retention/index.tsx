import { getRetentionEventNames } from '@openpanel/common';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useTRPC } from '@/integrations/trpc/react';

import { AspectContainer } from '../aspect-container';
import { ReportChartEmpty } from '../common/empty';
import { ReportChartError } from '../common/error';
import { ReportChartLoading } from '../common/loading';
import { useReportChartContext } from '../context';
import { Chart } from './chart';
import CohortTable from './table';

export function ReportRetentionChart() {
  const { isLazyLoading, report, shareId } = useReportChartContext();
  const eventSeries = report.series.filter((item) => item.type === 'event');
  const firstEvent = getRetentionEventNames(eventSeries[0]);
  const secondEvent = getRetentionEventNames(eventSeries[1]);
  // A `name` filter is an event selector in newer reports. All other filters
  // scope the retention audience, including filters on older saved reports.
  const filters = [
    ...(report.globalFilters ?? []),
    ...eventSeries.flatMap((item) =>
      (item.filters ?? []).filter((filter) => filter.name !== 'name'),
    ),
  ];
  const isEnabled = eventSeries.length >= 2 && !isLazyLoading;

  const retentionOptions =
    report.options?.type === 'retention' ? report.options : undefined;
  const criteria = retentionOptions?.criteria ?? 'on_or_after';

  const trpc = useTRPC();
  const res = useQuery(
    trpc.chart.cohort.queryOptions(
      {
        firstEvent,
        secondEvent,
        filters,
        projectId: report.projectId,
        range: report.range,
        startDate: report.startDate,
        endDate: report.endDate,
        criteria,
        interval: report.interval,
        shareId,
        id: 'id' in report ? report.id : undefined,
      },
      {
        placeholderData: keepPreviousData,
        enabled: isEnabled,
      },
    ),
  );

  if (!isEnabled) {
    return <Disabled />;
  }

  if (isLazyLoading || res.isLoading) {
    return <Loading />;
  }

  if (res.isError) {
    return <Error />;
  }

  if (!res.data || res.data?.length === 0) {
    return <Empty />;
  }

  return (
    <div className="col gap-4">
      <AspectContainer>
        <Chart data={res.data} />
      </AspectContainer>
      <CohortTable data={res.data} />
    </div>
  );
}

function Loading() {
  return (
    <AspectContainer>
      <ReportChartLoading />
    </AspectContainer>
  );
}

function Error() {
  return (
    <AspectContainer>
      <ReportChartError />
    </AspectContainer>
  );
}

function Empty() {
  return (
    <AspectContainer>
      <ReportChartEmpty />
    </AspectContainer>
  );
}

function Disabled() {
  return (
    <AspectContainer>
      <ReportChartEmpty title="Select 2 events">
        We need two events to determine the retention rate.
      </ReportChartEmpty>
    </AspectContainer>
  );
}
