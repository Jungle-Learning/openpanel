import { useTRPC } from '@/integrations/trpc/react';
import { useQuery } from '@tanstack/react-query';

export function useProfileProperties(
  projectId: string,
  options?: { enabled: boolean },
) {
  const trpc = useTRPC();
  const query = useQuery(
    trpc.profile.properties.queryOptions(
      { projectId },
      {
        enabled: !!projectId && (options?.enabled ?? true),
      },
    ),
  );
  return query.data ?? [];
}
