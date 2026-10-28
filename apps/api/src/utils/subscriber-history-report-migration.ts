import { db } from '@openpanel/db';

const REPORT_ID = 'fa8f2769-009b-4db5-a681-6cb5628f3fa3';
const PROJECT_ID = 'jungle';
const DASHBOARD_ID = 'core-metrics';
const PREVIOUS_NAME =
  'Active paid subscribers — daily trend (history from Oct 27, 2026)';

function hasSnapshotSchema(
  filters: unknown,
  version: string
): filters is {
  id: string;
  name: string;
  operator: string;
  type: string;
  value: string[];
}[] {
  if (!Array.isArray(filters) || filters.length !== 1) {
    return false;
  }
  const filter = filters[0];
  // Match every canonical field so customized filters are never replaced.
  return (
    filter?.id === 'snapshot-schema' &&
    filter.name === 'properties.snapshotSchemaVersion' &&
    filter.operator === 'is' &&
    filter.type === 'number' &&
    Array.isArray(filter.value) &&
    filter.value.length === 1 &&
    filter.value[0] === version
  );
}

/**
 * Idempotent configuration migration for Jungle's reviewed subscriber report.
 * No customer records, other reports or analytics observations are changed.
 * Run after the schema-3 historical observations have been published.
 */
export async function migrateSubscriberHistoryReport(client = db) {
  const report = await client.report.findFirst({
    where: { id: REPORT_ID, projectId: PROJECT_ID, dashboardId: DASHBOARD_ID },
    select: {
      id: true,
      name: true,
      range: true,
      globalFilters: true,
      updatedAt: true,
    },
  });
  if (!report) {
    return { status: 'absent' };
  }
  if (hasSnapshotSchema(report.globalFilters, '3')) {
    return { status: 'current' };
  }
  if (!hasSnapshotSchema(report.globalFilters, '2')) {
    return { status: 'customized' };
  }

  const updated = await client.report.updateMany({
    where: {
      id: REPORT_ID,
      projectId: PROJECT_ID,
      dashboardId: DASHBOARD_ID,
      updatedAt: report.updatedAt,
      globalFilters: { equals: report.globalFilters },
    },
    data: {
      name:
        report.name === PREVIOUS_NAME
          ? 'Active paid subscribers — daily trend'
          : report.name,
      range: '12m',
      globalFilters: [
        {
          id: 'snapshot-schema',
          name: 'properties.snapshotSchemaVersion',
          operator: 'is',
          type: 'number',
          value: ['3'],
        },
      ],
    },
  });
  return {
    status: updated.count === 1 ? 'upgraded' : 'changed-concurrently',
    // Aggregate report configuration only; preserve the previous values in
    // operator logs so a rollback does not require a laptop SSH session.
    previous: report,
  };
}
