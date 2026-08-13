export function getPropertyEventNames({
  event,
  eventNames,
}: {
  event?: string;
  eventNames?: string[];
}) {
  if (event && event !== '*') {
    return [event];
  }

  if (eventNames) {
    return Array.from(new Set(eventNames.filter((name) => name !== '*')));
  }

  return undefined;
}
