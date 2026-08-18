export function getSerieDisplayNames(
  names: string[],
  breakdownCount: number,
): string[] {
  if (breakdownCount > 0 && names.length > 1) {
    return names.slice(1);
  }

  return names;
}

export function hasBreakdownValue(
  names: string[],
  breakdownCount: number,
): boolean {
  return breakdownCount === 0 || names.length > 1;
}
