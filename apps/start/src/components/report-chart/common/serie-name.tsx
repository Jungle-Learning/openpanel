import { cn } from '@/utils/cn';
import { ChevronRightIcon } from 'lucide-react';

import { NOT_SET_VALUE } from '@openpanel/constants';

import React, { Fragment } from 'react';
import { useReportChartContext } from '../context';
import { getSerieDisplayNames } from './serie-name-utils';

interface SerieNameProps {
  name: string | string[];
  className?: string;
}

export function SerieName({ name, className }: SerieNameProps) {
  const {
    options: { renderSerieName },
    report: { breakdowns },
  } = useReportChartContext();

  if (Array.isArray(name)) {
    if (renderSerieName) {
      return renderSerieName(name);
    }
    const displayNames = getSerieDisplayNames(name, breakdowns.length);
    return (
      <div className={cn('flex items-center gap-1', className)}>
        {displayNames.map((n, index) => {
          return (
            <Fragment key={n}>
              <span>{n || NOT_SET_VALUE}</span>
              {displayNames.length - 1 > index && (
                <ChevronRightIcon className="text-muted-foreground" size={12} />
              )}
            </Fragment>
          );
        })}
      </div>
    );
  }

  if (renderSerieName) {
    return renderSerieName([name]);
  }

  return <span className={className}>{name}</span>;
}
