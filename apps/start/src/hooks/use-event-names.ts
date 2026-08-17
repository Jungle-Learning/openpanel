import { useQuery } from '@tanstack/react-query';
import { useTRPC } from '@/integrations/trpc/react';

export function useEventNames(params: {
  projectId: string;
  anyEvents?: boolean;
  includeCustomEvents?: boolean;
  enabled?: boolean;
}) {
  const trpc = useTRPC();
  const {
    enabled = true,
    anyEvents = true,
    includeCustomEvents = false,
    projectId,
  } = params;
  const query = useQuery(
    trpc.chart.events.queryOptions(
      { projectId, includeCustomEvents },
      {
        enabled: enabled !== false && !!params.projectId,
        staleTime: 1000 * 60 * 10,
      },
    ),
  );
  return (query.data ?? []).filter((event) =>
    anyEvents ? true : event.name !== '*',
  );
}
