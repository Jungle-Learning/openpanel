import { GanttChartIcon, InfoIcon, LayersIcon, UsersIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import type { RouterOutputs } from '@/trpc/client';

export type CustomEvent = RouterOutputs['event']['customEvents'][number];

export function CustomEventBadge() {
  return (
    <Badge
      className="gap-1 border-highlight/20 bg-highlight/5 text-highlight"
      variant="outline"
    >
      <LayersIcon size={12} />
      Custom event
    </Badge>
  );
}

export function getCustomEventCountingDescription(
  operation: CustomEvent['operation']
) {
  return operation === 'union'
    ? 'Count each unique user once per time bucket if they performed any source event.'
    : 'Count each unique user once per time bucket only if they performed every source event.';
}

export function CustomEventDefinition({ event }: { event: CustomEvent }) {
  const any = event.operation === 'union';
  return (
    <div className="space-y-5">
      <section aria-label="Event definition">
        <h3 className="mb-3 font-medium">Definition</h3>
        <div className="overflow-hidden rounded-lg border bg-card">
          <div className="flex items-center gap-2 border-b bg-def-100 px-4 py-3 font-medium text-sm">
            <span className="rounded bg-highlight/10 px-2 py-1 font-semibold text-highlight text-xs">
              {any ? 'OR' : 'AND'}
            </span>
            {any ? 'Any of these events' : 'All of these events'}
          </div>
          <ul className="divide-y">
            {event.eventNames.map((name) => (
              <li
                className="flex min-w-0 items-center gap-3 px-4 py-3"
                key={name}
              >
                <GanttChartIcon
                  className="shrink-0 text-muted-foreground"
                  size={16}
                />
                <code className="min-w-0 break-words text-sm">{name}</code>
              </li>
            ))}
          </ul>
        </div>
      </section>
      <div className="flex gap-3 rounded-lg bg-def-100 p-4 text-muted-foreground text-sm leading-relaxed">
        <UsersIcon className="mt-0.5 shrink-0" size={16} />
        <div>
          <p className="mb-1 font-medium text-foreground">
            Counts unique users
          </p>
          <p>{getCustomEventCountingDescription(event.operation)}</p>
        </div>
      </div>
      <div className="space-y-3 border-t pt-4 text-sm">
        <dl className="flex justify-between gap-4">
          <dt className="text-muted-foreground">Available in</dt>
          <dd>This project</dd>
        </dl>
        <p className="flex gap-2 text-muted-foreground leading-relaxed">
          <InfoIcon className="mt-0.5 shrink-0" size={14} />
          Combines existing tracked events when a report runs. No additional
          events are recorded.
        </p>
      </div>
    </div>
  );
}
