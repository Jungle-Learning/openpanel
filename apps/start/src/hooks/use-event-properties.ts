import { useTRPC } from '@/integrations/trpc/react';
import type { RouterInputs } from '@/trpc/client';
import { useQueries, useQuery } from '@tanstack/react-query';

export function useEventProperties(
  params: RouterInputs['chart']['properties'],
  options?: {
    enabled: boolean;
  }
) {
  const trpc = useTRPC();
  const query = useQuery(
    trpc.chart.properties.queryOptions(params, {
      enabled: !!params.projectId && (options?.enabled ?? true),
    })
  );
  return query.data ?? [];
}

/** Reuse the same event-scoped cache entries as individual event filters. */
export function useEventPropertyOptions(
  projectId: string,
  events?: string[],
  customEventIds: string[] = []
) {
  const trpc = useTRPC();
  const needsCustomEvents = customEventIds.length > 0;
  const customEventsQuery = useQuery(
    trpc.event.customEvents.queryOptions(
      { projectId },
      { enabled: !!projectId && needsCustomEvents }
    )
  );
  const customEvents = customEventsQuery.data ?? [];
  const unresolvedCustomEvents = customEventIds.some(
    (id) => !customEvents.some((event) => event.id === id)
  );
  const customEventSourceNames = customEvents
    .filter((event) => customEventIds.includes(event.id))
    .flatMap((event) => event.eventNames);
  const needsProjectWideDiscovery = events === undefined;
  // Explicit event scopes combine tracked names with resolved custom sources.
  // An undefined scope preserves All events / empty-report discovery.
  const sources = needsProjectWideDiscovery
    ? [undefined]
    : [...new Set([...events, ...customEventSourceNames])];
  const queries = useQueries({
    queries: sources.map((event) =>
      trpc.chart.properties.queryOptions(
        { projectId, event },
        { enabled: !!projectId }
      )
    ),
  });
  const isResolvingCustomEvents =
    needsCustomEvents && customEventsQuery.isPending;
  const hasCustomEventError =
    needsCustomEvents &&
    (customEventsQuery.isError ||
      (customEventsQuery.isSuccess && unresolvedCustomEvents));
  return {
    properties: [...new Set(queries.flatMap((query) => query.data ?? []))],
    isPending:
      isResolvingCustomEvents || queries.some((query) => query.isPending),
    isError: hasCustomEventError || queries.some((query) => query.isError),
    retry: () => {
      if (needsCustomEvents) void customEventsQuery.refetch();
      for (const query of queries) void query.refetch();
    },
  };
}
