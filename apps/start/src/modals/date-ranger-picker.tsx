import { getDefaultIntervalByDates } from '@openpanel/constants';
import type { IInterval } from '@openpanel/validation';
import { endOfDay, format, isAfter, subMonths } from 'date-fns';
import { CheckIcon, XIcon } from 'lucide-react';
import { useState } from 'react';
import { popModal } from '.';
import { ModalContent } from './Modal/Container';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';

interface Props {
  onChange: (payload: {
    startDate: Date;
    endDate: Date;
    interval: IInterval;
  }) => void;
  startDate?: Date;
  endDate?: Date;
}
export default function DateRangerPicker({
  onChange,
  startDate: initialStartDate,
  endDate: initialEndDate,
}: Props) {
  const maximumSelectableDate = endOfDay(new Date());
  const selectableInitialEndDate =
    initialEndDate && isAfter(initialEndDate, maximumSelectableDate)
      ? new Date()
      : initialEndDate;
  const [startDate, setStartDate] = useState(initialStartDate);
  const [endDate, setEndDate] = useState(selectableInitialEndDate);
  const [startCalendarMonth, setStartCalendarMonth] = useState(
    initialStartDate ?? subMonths(new Date(), 1)
  );
  const [endCalendarMonth, setEndCalendarMonth] = useState(
    selectableInitialEndDate ?? new Date()
  );
  const hasValidRange =
    startDate !== undefined &&
    endDate !== undefined &&
    !isAfter(startDate, endDate);

  return (
    <ModalContent className="min-w-fit p-4 md:p-8">
      <div className="grid gap-6 md:grid-cols-2">
        <div className="col gap-2">
          <div className="text-center font-medium text-sm">Start date</div>
          <Calendar
            aria-label="Start date"
            captionLayout="dropdown"
            className="mx-auto min-h-[310px] p-0 [&_table]:mx-auto [&_table]:w-auto"
            endMonth={maximumSelectableDate}
            hidden={{ after: maximumSelectableDate }}
            initialFocus
            mode="single"
            month={startCalendarMonth}
            onMonthChange={setStartCalendarMonth}
            onSelect={setStartDate}
            selected={startDate}
          />
        </div>
        <div className="col gap-2">
          <div className="text-center font-medium text-sm">End date</div>
          <Calendar
            aria-label="End date"
            captionLayout="dropdown"
            className="mx-auto min-h-[310px] p-0 [&_table]:mx-auto [&_table]:w-auto"
            endMonth={maximumSelectableDate}
            hidden={{ after: maximumSelectableDate }}
            mode="single"
            month={endCalendarMonth}
            onMonthChange={setEndCalendarMonth}
            onSelect={setEndDate}
            selected={endDate}
          />
        </div>
      </div>
      {startDate && endDate && !hasValidRange && (
        <div className="text-center text-destructive text-sm">
          Start date must be before end date.
        </div>
      )}
      <div className="col md:row flex-col-reverse gap-2">
        <Button
          icon={XIcon}
          onClick={() => popModal()}
          type="button"
          variant="outline"
        >
          Cancel
        </Button>

        {hasValidRange && (
          <Button
            className="md:ml-auto"
            icon={CheckIcon}
            onClick={() => {
              popModal();
              onChange({
                startDate,
                endDate,
                interval: getDefaultIntervalByDates(
                  startDate.toISOString(),
                  endDate.toISOString()
                )!,
              });
            }}
            type="button"
          >
            {`Select ${format(startDate, 'MM/dd/yyyy')} - ${format(endDate, 'MM/dd/yyyy')}`}
          </Button>
        )}
      </div>
    </ModalContent>
  );
}
