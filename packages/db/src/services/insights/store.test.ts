import { beforeEach, describe, expect, it, vi } from 'vitest';

const findFirst = vi.fn();
const update = vi.fn();

vi.mock('../../prisma-client', () => ({
  Prisma: { DbNull: null },
  db: {
    projectInsight: {
      findFirst: (...args: unknown[]) => findFirst(...args),
      update: (...args: unknown[]) => update(...args),
    },
  },
}));

const { insightStore } = await import('./store');

const previousUpdatedAt = new Date('2026-08-16T02:00:00.000Z');
const now = new Date('2026-08-17T02:00:00.000Z');

const existingInsight = {
  id: 'insight-1',
  projectId: 'project-1',
  moduleKey: 'geo',
  dimensionKey: 'country:US',
  windowKind: 'rolling_7d' as const,
  state: 'active' as const,
  version: 1,
  title: 'United States traffic increased',
  summary: 'Sessions 10 vs 5.',
  displayName: 'United States',
  payload: {
    kind: 'insight_v1' as const,
    dimensions: [{ key: 'country', value: 'US', displayName: 'United States' }],
    primaryMetric: 'sessions' as const,
    metrics: {
      sessions: {
        current: 10,
        compare: 5,
        delta: 5,
        changePct: 1,
        direction: 'up' as const,
        unit: 'count' as const,
      },
    },
  },
  direction: 'up' as const,
  impactScore: 10,
  severityBand: 'moderate',
  lastSeenAt: previousUpdatedAt,
  lastUpdatedAt: previousUpdatedAt,
  threadId: null,
  enrichedAt: previousUpdatedAt,
};

function makeUpsertInput() {
  return {
    projectId: existingInsight.projectId,
    moduleKey: existingInsight.moduleKey,
    dimensionKey: existingInsight.dimensionKey,
    window: {
      kind: 'rolling_7d' as const,
      start: new Date('2026-08-10T00:00:00.000Z'),
      end: new Date('2026-08-17T00:00:00.000Z'),
      baselineStart: new Date('2026-08-03T00:00:00.000Z'),
      baselineEnd: new Date('2026-08-10T00:00:00.000Z'),
      label: 'Last 7 days',
    },
    card: {
      title: existingInsight.title,
      summary: existingInsight.summary,
      displayName: existingInsight.displayName,
      payload: existingInsight.payload,
    },
    metrics: {
      direction: 'up' as const,
      impactScore: existingInsight.impactScore,
      severityBand: existingInsight.severityBand,
    },
    now,
    decision: { material: false as const, reason: 'none' as const },
    prev: existingInsight,
  };
}

beforeEach(() => {
  findFirst.mockReset();
  update.mockReset();
  findFirst.mockResolvedValue(existingInsight);
  update.mockImplementation(async ({ data }) => ({
    ...existingInsight,
    ...data,
  }));
});

describe('insightStore.upsertInsight', () => {
  it('keeps enrichment current when none of its inputs changed', async () => {
    await insightStore.upsertInsight(makeUpsertInput());

    const updateData = update.mock.calls[0]![0].data;
    expect(updateData).not.toHaveProperty('enrichedAt');
  });

  it('re-queues enrichment when the payload changes below the materiality gate', async () => {
    const input = makeUpsertInput();
    input.card.payload = {
      ...input.card.payload,
      metrics: {
        ...input.card.payload.metrics,
        sessions: {
          ...input.card.payload.metrics.sessions,
          current: 11,
          delta: 6,
        },
      },
    };

    await insightStore.upsertInsight(input);

    expect(update.mock.calls[0]![0].data).toHaveProperty('enrichedAt', null);
  });
});
