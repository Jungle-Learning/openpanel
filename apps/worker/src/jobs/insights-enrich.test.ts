import { beforeEach, describe, expect, it, vi } from 'vitest';

const enrichInsights = vi.fn();
const findMany = vi.fn();
const update = vi.fn();
const loggerInfo = vi.fn();
const loggerError = vi.fn();
const loggerWarn = vi.fn();

vi.mock('@openpanel/ai', () => ({
  ENRICH_VERSION: 2,
  enrichInsights: (...args: unknown[]) => enrichInsights(...args),
}));
vi.mock('@openpanel/db', () => ({
  db: {
    projectInsight: {
      findMany: (...args: unknown[]) => findMany(...args),
      update: (...args: unknown[]) => update(...args),
    },
  },
}));
vi.mock('@/utils/logger', () => ({
  logger: {
    child: () => ({
      info: (...args: unknown[]) => loggerInfo(...args),
      error: (...args: unknown[]) => loggerError(...args),
      warn: (...args: unknown[]) => loggerWarn(...args),
    }),
  },
}));

const { enrichProjectInsights } = await import('./insights-enrich');

function makeInsight(id: string) {
  return {
    id,
    moduleKey: 'geo',
    dimensionKey: `country:${id}`,
    windowKind: 'rolling_7d',
    title: `Traffic changed for ${id}`,
    summary: null,
    displayName: id,
    direction: 'up',
    impactScore: 120,
    severityBand: 'moderate',
    payload: { metrics: {} },
  };
}

beforeEach(() => {
  process.env.OPENAI_API_KEY = 'test-key';
  enrichInsights.mockReset();
  findMany.mockReset();
  update.mockReset();
  loggerInfo.mockReset();
  loggerError.mockReset();
  loggerWarn.mockReset();
  update.mockResolvedValue({});
});

describe('enrichProjectInsights', () => {
  it('skips model calls when no active insights need enrichment', async () => {
    findMany.mockResolvedValue([]);

    await enrichProjectInsights('project-1');

    expect(enrichInsights).not.toHaveBeenCalled();
    expect(update).not.toHaveBeenCalled();
  });

  it('persists matched model results and clamps relevance scores', async () => {
    findMany.mockResolvedValue([makeInsight('one'), makeInsight('two')]);
    enrichInsights.mockResolvedValue([
      {
        id: 'one',
        relevanceScore: 1.4,
        summary: 'Country one drove the increase.',
        category: 'spike',
        emailWorthy: false,
        referenceWorthy: true,
      },
      {
        id: 'two',
        relevanceScore: Number.NaN,
        summary: 'Country two changed gradually.',
        category: 'trend',
        emailWorthy: false,
        referenceWorthy: false,
      },
    ]);

    await enrichProjectInsights('project-1');

    expect(update).toHaveBeenNthCalledWith(1, {
      where: { id: 'one' },
      data: expect.objectContaining({
        relevanceScore: 1,
        aiSummary: 'Country one drove the increase.',
        enrichVersion: 2,
      }),
    });
    expect(update).toHaveBeenNthCalledWith(2, {
      where: { id: 'two' },
      data: expect.objectContaining({
        relevanceScore: 0,
        aiSummary: 'Country two changed gradually.',
        enrichVersion: 2,
      }),
    });
  });

  it('isolates provider failures from the daily insight job', async () => {
    findMany.mockResolvedValue([makeInsight('one')]);
    enrichInsights.mockRejectedValue(new Error('provider unavailable'));

    await expect(enrichProjectInsights('project-1')).resolves.toBeUndefined();
    expect(update).not.toHaveBeenCalled();
    expect(loggerError).toHaveBeenCalled();
  });
});
