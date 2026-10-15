import { useQuery } from '@tanstack/react-query';
import {
  ChevronRightIcon,
  InfoIcon,
  LayersIcon,
  PencilIcon,
  SearchIcon,
} from 'lucide-react';
import { useState } from 'react';
import {
  CustomEventBadge,
  CustomEventDefinition,
} from './custom-event-definition';
import { CustomEventDialog } from './custom-event-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useTRPC } from '@/integrations/trpc/react';
import { cn } from '@/utils/cn';

export function CustomEventsLibrary({ projectId }: { projectId: string }) {
  const trpc = useTRPC();
  const query = useQuery(trpc.event.customEvents.queryOptions({ projectId }));
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string>();
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const events = (query.data ?? []).filter((event) =>
    [event.name, event.description ?? '', ...event.eventNames].some((value) =>
      value.toLocaleLowerCase().includes(normalizedSearch)
    )
  );
  const selected = events.find((event) => event.id === selectedId) ?? events[0];

  if (query.isPending) {
    return (
      <p className="py-12 text-center text-muted-foreground" role="status">
        Loading custom events…
      </p>
    );
  }
  if (query.isError) {
    return (
      <div className="space-y-4 rounded-lg border p-8 text-center" role="alert">
        <h2 className="font-semibold">Could not load custom events</h2>
        <p className="text-muted-foreground text-sm">{query.error.message}</p>
        <Button onClick={() => query.refetch()} variant="outline">
          Try again
        </Button>
      </div>
    );
  }
  if (query.data.length === 0) {
    return (
      <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed px-6 py-16 text-center">
        <LayersIcon className="text-muted-foreground" size={28} />
        <h2 className="font-semibold text-xl">No custom events yet</h2>
        <p className="max-w-md text-muted-foreground text-sm leading-relaxed">
          In the report builder, combine source events with a union or
          intersection formula, then choose “Save as custom event.” Your saved
          definitions will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
      <div className="min-w-0">
        <section
          aria-label="Custom event library"
          className="overflow-hidden rounded-lg border bg-card"
        >
          <div className="relative border-b p-4">
            <SearchIcon
              className="absolute top-1/2 left-7 -translate-y-1/2 text-muted-foreground"
              size={14}
            />
            <Input
              aria-label="Search custom events"
              className="pl-9"
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search custom events…"
              value={search}
            />
          </div>
          <p className="border-b bg-def-100 px-4 py-3 font-medium text-muted-foreground text-xs uppercase tracking-wide">
            Saved definitions · {events.length}
          </p>
          <div className="divide-y">
            {events.map((event) => (
              <button
                aria-pressed={selected?.id === event.id}
                className={cn(
                  'flex w-full min-w-0 items-start gap-3 p-4 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                  selected?.id === event.id &&
                    'bg-highlight/5 shadow-[inset_3px_0_0_var(--highlight)]'
                )}
                key={event.id}
                onClick={() => setSelectedId(event.id)}
                type="button"
              >
                <span className="rounded-md border bg-card p-2 text-highlight">
                  <LayersIcon size={17} />
                </span>
                <span className="min-w-0 flex-1 space-y-2">
                  <span className="block break-words font-medium">
                    {event.name}
                  </span>
                  {event.description && (
                    <span className="block break-words text-muted-foreground text-sm leading-relaxed">
                      {event.description}
                    </span>
                  )}
                  <span className="block text-muted-foreground text-sm">
                    {event.operation === 'union' ? 'Any of' : 'All of'}{' '}
                    {event.eventNames.length} events · Unique users
                  </span>
                </span>
                <ChevronRightIcon
                  className="mt-2 shrink-0 text-muted-foreground"
                  size={14}
                />
              </button>
            ))}
            {!selected && (
              <p
                className="p-8 text-center text-muted-foreground text-sm"
                role="status"
              >
                No custom events match your search.
              </p>
            )}
          </div>
        </section>
        <p className="mt-4 flex gap-2 text-muted-foreground text-sm leading-relaxed">
          <InfoIcon className="mt-0.5 shrink-0" size={14} />
          Create custom events in the report builder using “Save as custom
          event.” Manage their shared definitions here.
        </p>
      </div>
      {selected && (
        <section
          aria-labelledby="selected-custom-event"
          className="min-w-0 overflow-hidden rounded-lg border bg-card"
        >
          <header className="space-y-4 border-b p-6">
            <CustomEventBadge />
            <div className="flex flex-wrap items-start justify-between gap-3">
              <h2
                className="min-w-0 break-words font-semibold text-2xl"
                id="selected-custom-event"
              >
                {selected.name}
              </h2>
              <CustomEventDialog
                customEventId={selected.id}
                defaultEditing
                key={selected.id}
                projectId={projectId}
              >
                <Button icon={PencilIcon} variant="outline">
                  Edit definition
                </Button>
              </CustomEventDialog>
            </div>
            <p className="break-words text-muted-foreground text-sm leading-relaxed">
              {selected.description || 'No description added.'}
            </p>
          </header>
          <div className="p-6">
            <CustomEventDefinition event={selected} />
          </div>
        </section>
      )}
    </div>
  );
}
