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
export function useEventPropertyOptions(projectId: string, events?: string[]) {
  const trpc = useTRPC();
  const queries = useQueries({
    queries: (events?.length ? events : [undefined]).map((event) =>
      trpc.chart.properties.queryOptions(
        { projectId, event },
        { enabled: !!projectId }
      )
    ),
  });
  return {
    properties: [...new Set(queries.flatMap((query) => query.data ?? []))],
    isPending: queries.some((query) => query.isPending),
    isError: queries.some((query) => query.isError),
    retry: () => {
      for (const query of queries) void query.refetch();
    },
  };
}
