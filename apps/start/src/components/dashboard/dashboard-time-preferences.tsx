import { intervals, timeWindows } from '@openpanel/constants';
import { mapKeys } from '@openpanel/validation';
import { parseAsString, parseAsStringEnum, useQueryStates } from 'nuqs';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import FullPageLoadingState from '@/components/full-page-loading-state';

const timeParsers = {
  start: parseAsString,
  end: parseAsString,
  range: parseAsStringEnum(mapKeys(timeWindows)),
  overrideInterval: parseAsStringEnum(mapKeys(intervals)),
};

type TimeSelection = {
  start: string | null;
  end: string | null;
  range: keyof typeof timeWindows | null;
  overrideInterval: keyof typeof intervals | null;
};

export function dashboardTimeStorageKey(
  userId: string,
  organizationId: string,
  projectId: string,
  dashboardId: string
) {
  return `openpanel:dashboard-time:v1:${JSON.stringify([userId, organizationId, projectId, dashboardId])}`;
}

export function parseSavedDashboardTime(
  raw: string | null
): TimeSelection | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value))
      return null;
    const parsed: TimeSelection = {
      start: null,
      end: null,
      range: null,
      overrideInterval: null,
    };
    for (const key of Object.keys(timeParsers) as Array<keyof TimeSelection>) {
      const item = value[key];
      if (item === null) continue;
      if (typeof item !== 'string' || timeParsers[key].parse(item) === null)
        return null;
      Object.assign(parsed, { [key]: item });
    }
    const hasDates = parsed.start !== null || parsed.end !== null;
    if (hasDates || parsed.range === 'custom') {
      if (!parsed.start || !parsed.end) return null;
      const start = new Date(parsed.start).getTime();
      const end = new Date(parsed.end).getTime();
      if (!Number.isFinite(start) || !Number.isFinite(end) || start > end)
        return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

/** Mounted once per authenticated dashboard, keyed by user/project/dashboard.
 * Explicit links win on entry. Later edits (including Default) replace the
 * remembered view. Shared/public dashboards deliberately do not mount this.
 */
export function DashboardTimePreferences({
  storageKey,
  children,
}: {
  storageKey: string;
  children: ReactNode;
}) {
  const [selection, setSelection] = useQueryStates(timeParsers);
  const serialized = JSON.stringify(selection);
  const initialized = useRef(false);
  const restoring = useRef<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      const params = new URLSearchParams(window.location.search);
      const hasExplicitTime = Object.keys(timeParsers).some((key) =>
        params.has(key)
      );
      if (!hasExplicitTime) {
        let saved: TimeSelection | null = null;
        try {
          saved = parseSavedDashboardTime(
            window.localStorage.getItem(storageKey)
          );
        } catch {
          // Storage may be blocked; the dashboard remains fully usable.
        }
        if (saved && JSON.stringify(saved) !== serialized) {
          restoring.current = JSON.stringify(saved);
          void setSelection(saved, { history: 'replace' }).catch(() => {
            restoring.current = null;
            setReady(true);
          });
          return;
        }
      }
    }
    // Wait for nuqs' restored state before saving, so defaults cannot overwrite
    // a remembered view during hydration or StrictMode's repeated effects.
    if (restoring.current !== null && restoring.current !== serialized) return;
    restoring.current = null;
    setReady(true);
    try {
      // Do not remember a half-filled custom date picker.
      if (parseSavedDashboardTime(serialized))
        window.localStorage.setItem(storageKey, serialized);
    } catch {
      // A full/disabled browser store must not prevent changing the chart.
    }
  }, [serialized, setSelection, storageKey]);

  return ready ? children : <FullPageLoadingState />;
}
