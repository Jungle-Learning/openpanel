import { ReportInterval } from '@/components/report/ReportInterval';
import { TimeWindowPicker } from '@/components/time-window-picker';
import { useDashboardOptions } from './use-dashboard-options';

export function DashboardTimeControls() {
  const {
    range,
    setRange,
    startDate,
    setStartDate,
    endDate,
    setEndDate,
    interval,
    setInterval,
  } = useDashboardOptions();

  return (
    <>
      <TimeWindowPicker
        endDate={endDate}
        onChange={setRange}
        onEndDateChange={setEndDate}
        onIntervalChange={setInterval}
        onStartDateChange={setStartDate}
        showDefault
        startDate={startDate}
        value={range}
      />
      {range && interval && (
        <ReportInterval
          chartType="linear"
          endDate={endDate}
          interval={interval}
          onChange={setInterval}
          range={range}
          startDate={startDate}
        />
      )}
    </>
  );
}
