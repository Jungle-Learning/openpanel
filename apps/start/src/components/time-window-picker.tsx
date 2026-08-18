import { getDefaultIntervalByDates, timeWindows } from '@openpanel/constants';
import type { IChartRange, IInterval } from '@openpanel/validation';
import { bind } from 'bind-event-listener';
import { endOfDay, format, startOfDay } from 'date-fns';
import { CalendarIcon } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  type CustomTimeWindowUnit,
  getCustomTimeWindowDates,
  MAX_CUSTOM_TIME_WINDOW_BUCKETS,
} from './time-window-picker-helpers';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { InputEnter } from '@/components/ui/input-enter';
import { pushModal, useOnPushModal } from '@/modals';
import { cn } from '@/utils/cn';
import { shouldIgnoreKeypress } from '@/utils/should-ignore-keypress';

interface Props {
  value: IChartRange;
  onChange: (value: IChartRange) => void;
  onStartDateChange: (date: string) => void;
  onEndDateChange: (date: string) => void;
  onIntervalChange: (interval: IInterval) => void;
  endDate: string | null;
  startDate: string | null;
  className?: string;
}

const customTimeWindowOptions: Array<{
  unit: CustomTimeWindowUnit;
  label: string;
  placeholder: string;
}> = [
  { unit: 'days', label: 'Last days', placeholder: 'X days' },
  { unit: 'weeks', label: 'Last weeks', placeholder: 'X weeks' },
  { unit: 'months', label: 'Last months', placeholder: 'X months' },
];

export function TimeWindowPicker({
  value,
  onChange,
  startDate,
  onStartDateChange,
  endDate,
  onEndDateChange,
  onIntervalChange,
  className,
}: Props) {
  const isDateRangerPickerOpen = useRef(false);
  useOnPushModal('DateRangerPicker', (open) => {
    isDateRangerPickerOpen.current = open;
  });
  const timeWindow = timeWindows[value ?? '30d'];
  const [open, setOpen] = useState(false);
  const [customTimeWindowKey, setCustomTimeWindowKey] = useState(0);

  const handleCustomTimeWindow = useCallback(
    (rawAmount: string, unit: CustomTimeWindowUnit) => {
      const range = getCustomTimeWindowDates(rawAmount, unit);
      if (!range) {
        return;
      }

      const { start, end } = range;
      onStartDateChange(format(start, 'yyyy-MM-dd HH:mm:ss'));
      onEndDateChange(format(end, 'yyyy-MM-dd HH:mm:ss'));
      onChange('custom');
      const interval = getDefaultIntervalByDates(
        start.toISOString(),
        end.toISOString()
      );
      if (interval) {
        onIntervalChange(interval);
      }
      setCustomTimeWindowKey((currentKey) => currentKey + 1);
      setOpen(false);
    },
    [onChange, onStartDateChange, onEndDateChange, onIntervalChange]
  );

  const handleCustom = useCallback(() => {
    pushModal('DateRangerPicker', {
      onChange: ({ startDate, endDate, interval }) => {
        onStartDateChange(format(startOfDay(startDate), 'yyyy-MM-dd HH:mm:ss'));
        onEndDateChange(format(endOfDay(endDate), 'yyyy-MM-dd HH:mm:ss'));
        onChange('custom');
        onIntervalChange(interval);
      },
      startDate: startDate ? new Date(startDate) : undefined,
      endDate: endDate ? new Date(endDate) : undefined,
    });
  }, [startDate, endDate]);

  useEffect(() => {
    return bind(document, {
      type: 'keydown',
      listener(event) {
        if (shouldIgnoreKeypress(event)) {
          return;
        }

        if (isDateRangerPickerOpen.current) {
          return;
        }

        const match = Object.values(timeWindows).find(
          (tw) => event.key === tw.shortcut.toLowerCase()
        );
        if (match?.key === 'custom') {
          handleCustom();
        } else if (match) {
          onChange(match.key);
        }
      },
    });
  }, [handleCustom]);

  return (
    <DropdownMenu onOpenChange={setOpen} open={open}>
      <DropdownMenuTrigger asChild>
        <Button
          className={cn('justify-start', className)}
          icon={CalendarIcon}
          variant="outline"
        >
          {timeWindow?.label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-56">
        <DropdownMenuLabel>Time window</DropdownMenuLabel>
        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <DropdownMenuItem onClick={() => onChange(timeWindows['30min'].key)}>
            {timeWindows['30min'].label}
            <DropdownMenuShortcut>
              {timeWindows['30min'].shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows.lastHour.key)}>
            {timeWindows.lastHour.label}
            <DropdownMenuShortcut>
              {timeWindows.lastHour.shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows.last24h.key)}>
            {timeWindows.last24h.label}
            <DropdownMenuShortcut>
              {timeWindows.last24h.shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows.today.key)}>
            {timeWindows.today.label}
            <DropdownMenuShortcut>
              {timeWindows.today.shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows.yesterday.key)}>
            {timeWindows.yesterday.label}
            <DropdownMenuShortcut>
              {timeWindows.yesterday.shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuGroup>

        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <DropdownMenuItem onClick={() => onChange(timeWindows['7d'].key)}>
            {timeWindows['7d'].label}
            <DropdownMenuShortcut>
              {timeWindows['7d'].shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows['30d'].key)}>
            {timeWindows['30d'].label}
            <DropdownMenuShortcut>
              {timeWindows['30d'].shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows['3m'].key)}>
            {timeWindows['3m'].label}
            <DropdownMenuShortcut>
              {timeWindows['3m'].shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows['6m'].key)}>
            {timeWindows['6m'].label}
            <DropdownMenuShortcut>
              {timeWindows['6m'].shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows['12m'].key)}>
            {timeWindows['12m'].label}
            <DropdownMenuShortcut>
              {timeWindows['12m'].shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows['16m'].key)}>
            {timeWindows['16m'].label}
            <DropdownMenuShortcut>
              {timeWindows['16m'].shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuGroup>

        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <DropdownMenuItem
            onClick={() => onChange(timeWindows.monthToDate.key)}
          >
            {timeWindows.monthToDate.label}
            <DropdownMenuShortcut>
              {timeWindows.monthToDate.shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows.lastMonth.key)}>
            {timeWindows.lastMonth.label}
            <DropdownMenuShortcut>
              {timeWindows.lastMonth.shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuGroup>

        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <DropdownMenuItem
            onClick={() => onChange(timeWindows.yearToDate.key)}
          >
            {timeWindows.yearToDate.label}
            <DropdownMenuShortcut>
              {timeWindows.yearToDate.shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange(timeWindows.lastYear.key)}>
            {timeWindows.lastYear.label}
            <DropdownMenuShortcut>
              {timeWindows.lastYear.shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuGroup>

        <DropdownMenuSeparator />

        {/** Keep typing in these fields from selecting their surrounding menu. */}
        <DropdownMenuGroup
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          {customTimeWindowOptions.map(({ unit, label, placeholder }) => (
            <div
              className="flex items-center gap-4 rounded-sm px-2 py-1.5"
              key={unit}
            >
              <span className="w-20 whitespace-nowrap">{label}</span>
              <InputEnter
                aria-label={`Number of ${unit} for custom date filter`}
                className="h-7 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                inputMode="numeric"
                key={`${unit}-${customTimeWindowKey}`}
                max={MAX_CUSTOM_TIME_WINDOW_BUCKETS}
                min={1}
                onChangeValue={(rawAmount) =>
                  handleCustomTimeWindow(rawAmount, unit)
                }
                placeholder={placeholder}
                step={1}
                type="number"
                value=""
              />
            </div>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={() => handleCustom()}>
            {timeWindows.custom.label}
            <DropdownMenuShortcut>
              {timeWindows.custom.shortcut}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
