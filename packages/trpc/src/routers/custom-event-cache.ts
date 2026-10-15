import { db } from '@openpanel/db';

// Saved definitions resolve at query time. Include their revision in cache keys
// so a completed edit cannot reuse an earlier result or an in-flight query.
export async function getCustomEventCacheKey(input: unknown): Promise<string> {
  if (
    !input ||
    typeof input !== 'object' ||
    !('projectId' in input) ||
    typeof input.projectId !== 'string'
  ) {
    return '';
  }
  const series =
    ('series' in input ? input.series : undefined) ??
    ('events' in input ? input.events : undefined);
  if (!Array.isArray(series)) {
    return '';
  }
  const ids = [
    ...new Set(
      series.flatMap((item) =>
        item &&
        typeof item === 'object' &&
        item.type !== 'formula' &&
        typeof item.customEventId === 'string'
          ? [item.customEventId]
          : []
      )
    ),
  ].sort();
  if (ids.length === 0) {
    return '';
  }
  const definitions = await db.customEvent.findMany({
    where: { projectId: input.projectId, id: { in: ids } },
    select: { id: true, updatedAt: true },
    orderBy: { id: 'asc' },
  });
  return `:custom-events:${JSON.stringify(definitions)}`;
}
