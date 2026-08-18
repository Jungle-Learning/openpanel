type RetentionEventSeries = {
  name?: string | null;
  filters?: Array<{
    name: string;
    value?: unknown[];
  }> | null;
};

/**
 * Retention reports have used two event-selection shapes over time. Newer
 * reports keep a multi-event selection in a `name` filter, while older saved
 * reports keep the selected event directly on the series.
 */
export function getRetentionSelectedEventNames(
  series: RetentionEventSeries | null | undefined,
): string[] {
  const eventNameFilter = series?.filters?.find(
    (filter) => filter.name === 'name',
  );
  const rawEventNames = eventNameFilter
    ? (eventNameFilter.value ?? [])
    : series?.name
      ? [series.name]
      : [];

  return [
    ...new Set(
      rawEventNames
        .map(String)
        .map((eventName) => eventName.trim())
        .filter((eventName) => eventName.length > 0),
    ),
  ];
}

export function getRetentionEventNames(
  series: RetentionEventSeries | null | undefined,
): string[] {
  // The retention service represents the any-event wildcard with no name
  // predicate, while the selector still needs the literal `*` to render it.
  return getRetentionSelectedEventNames(series).filter(
    (eventName) => eventName !== '*',
  );
}
