import { describe, expect, it, vi } from 'vitest';
import { migrateSubscriberHistoryReport } from './subscriber-history-report-migration';

vi.mock('@openpanel/db', () => ({ db: {} }));

function client(
  version = '2',
  name = 'Active paid subscribers — daily trend (history from Oct 27, 2026)'
) {
  return {
    report: {
      findFirst: vi.fn().mockResolvedValue({
        id: 'fa8f2769-009b-4db5-a681-6cb5628f3fa3',
        name,
        range: 'lastYear',
        updatedAt: new Date('2026-10-28T16:34:15.858Z'),
        globalFilters: [
          {
            id: 'snapshot-schema',
            name: 'properties.snapshotSchemaVersion',
            operator: 'is',
            type: 'number',
            value: [version],
          },
        ],
      }),
      updateMany: vi.fn().mockResolvedValue({ count: 1 }),
    },
  };
}

describe('subscriber history report configuration migration', () => {
  it('updates only the scoped report with a timestamp and filter precondition', async () => {
    const database = client();
    const result = await migrateSubscriberHistoryReport(database as any);
    expect(result.status).toBe('upgraded');
    expect(database.report.findFirst.mock.calls[0]?.[0].where).toEqual({
      id: 'fa8f2769-009b-4db5-a681-6cb5628f3fa3',
      projectId: 'jungle',
      dashboardId: 'core-metrics',
    });
    const update = database.report.updateMany.mock.calls[0]?.[0];
    expect(update.where.updatedAt).toEqual(result.previous?.updatedAt);
    expect(update.where.globalFilters.equals).toEqual(
      result.previous?.globalFilters
    );
    expect(update.data).toMatchObject({
      name: 'Active paid subscribers — daily trend',
      range: '12m',
      globalFilters: [{ value: ['3'] }],
    });
    expect(Object.keys(update.data).sort()).toEqual([
      'globalFilters',
      'name',
      'range',
    ]);
  });

  it('leaves other installations and already upgraded reports untouched', async () => {
    for (const exists of [false, true]) {
      const database = client('3');
      if (!exists) database.report.findFirst.mockResolvedValue(null);
      const result = await migrateSubscriberHistoryReport(database as any);
      expect(result.status).toBe(exists ? 'current' : 'absent');
      expect(database.report.updateMany).not.toHaveBeenCalled();
    }
  });

  it('preserves custom filters and custom titles', async () => {
    const customized = client('4');
    expect(
      (await migrateSubscriberHistoryReport(customized as any)).status
    ).toBe('customized');
    expect(customized.report.updateMany).not.toHaveBeenCalled();
    const titled = client('2', 'My subscriber trend');
    await migrateSubscriberHistoryReport(titled as any);
    expect(titled.report.updateMany.mock.calls[0]?.[0].data.name).toBe(
      'My subscriber trend'
    );
  });

  it('does not overwrite a concurrent edit or hide a database error', async () => {
    const database = client();
    database.report.updateMany.mockResolvedValue({ count: 0 });
    expect((await migrateSubscriberHistoryReport(database as any)).status).toBe(
      'changed-concurrently'
    );
    database.report.updateMany.mockRejectedValue(
      new Error('database unavailable')
    );
    await expect(
      migrateSubscriberHistoryReport(database as any)
    ).rejects.toThrow('database unavailable');
  });
});
