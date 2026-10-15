import { zCreateCustomEvent } from '@openpanel/validation';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  access: vi.fn(),
  findMany: vi.fn(),
  findFirst: vi.fn(),
  findUnique: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  execute: vi.fn(),
}));
vi.mock('@openpanel/db', () => {
  const builder = {
    select: () => builder,
    from: () => builder,
    where: () => builder,
    limit: () => builder,
    execute: mocks.execute,
  };
  return {
    ch: {},
    clix: () => builder,
    TABLE_NAMES: { event_names_mv: 'distinct_event_names_mv' },
    db: {
      customEvent: {
        findMany: mocks.findMany,
        findFirst: mocks.findFirst,
        findUnique: mocks.findUnique,
        create: mocks.create,
        update: mocks.update,
      },
    },
  };
});
vi.mock('../access', () => ({ getProjectAccess: mocks.access }));
vi.mock('../trpc', async () => {
  const { initTRPC } = await import('@trpc/server');
  const t = initTRPC.context<{ session: { userId: string } }>().create();
  return { protectedProcedure: t.procedure };
});

import { initTRPC } from '@trpc/server';
import { customEventProcedures } from './custom-event';
import { getCustomEventCacheKey } from './custom-event-cache';

const id = '57a989ff-38b8-46d2-8130-321da33ba3ba';
const input = {
  id,
  projectId: 'jungle',
  name: 'Active Action',
  description: 'Learning activity or a new signup.',
  operation: 'union' as const,
  eventNames: ['answer', 'generation', 'voice', 'New User Sign Up'],
  updatedAt: new Date('2026-08-17T21:37:15.691Z'),
};
const t = initTRPC.context<{ session: { userId: string } }>().create();
const caller = t
  .router(customEventProcedures)
  .createCaller({ session: { userId: 'user' } });

beforeEach(() => {
  vi.resetAllMocks();
  mocks.access.mockResolvedValue(true);
  mocks.findFirst.mockResolvedValue(input);
  mocks.findUnique.mockResolvedValue({ id });
  mocks.execute
    .mockResolvedValueOnce(input.eventNames.map((name) => ({ name })))
    .mockResolvedValueOnce([]);
  mocks.update.mockResolvedValue(input);
});

describe('custom event definitions', () => {
  it('adds signup while retaining identity and scoping the write to the project and revision', async () => {
    await expect(caller.updateCustomEvent(input)).resolves.toEqual(input);
    expect(mocks.update).toHaveBeenCalledWith({
      where: { id, projectId: 'jungle', updatedAt: input.updatedAt },
      data: {
        name: input.name,
        description: input.description,
        operation: 'union',
        eventNames: input.eventNames,
      },
    });
  });

  it('rejects access before looking up or updating a definition', async () => {
    mocks.access.mockResolvedValue(false);
    await expect(caller.updateCustomEvent(input)).rejects.toMatchObject({
      code: 'UNAUTHORIZED',
    });
    expect(mocks.findFirst).not.toHaveBeenCalled();
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it('cannot read or update an event from another project by ID', async () => {
    mocks.findFirst.mockResolvedValue(null);
    const identity = { id, projectId: 'another-project' };
    await expect(caller.customEvent(identity)).rejects.toMatchObject({
      code: 'NOT_FOUND',
    });
    await expect(
      caller.updateCustomEvent({ ...input, ...identity })
    ).rejects.toMatchObject({ code: 'NOT_FOUND' });
    expect(mocks.findFirst).toHaveBeenCalledWith({ where: identity });
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it('rejects a name belonging to a different saved event', async () => {
    mocks.findUnique.mockResolvedValue({ id: 'another-event' });
    await expect(caller.updateCustomEvent(input)).rejects.toThrow(
      'already exists'
    );
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it('rejects tracked event names and missing sources', async () => {
    mocks.execute
      .mockReset()
      .mockResolvedValueOnce(input.eventNames.map((name) => ({ name })))
      .mockResolvedValueOnce([{ name: 'signup' }]);
    await expect(
      caller.updateCustomEvent({ ...input, name: 'signup' })
    ).rejects.toThrow('already used by a tracked event');
    mocks.execute
      .mockReset()
      .mockResolvedValueOnce([{ name: 'answer' }])
      .mockResolvedValueOnce([]);
    await expect(caller.updateCustomEvent(input)).rejects.toThrow(
      'Source events not found'
    );
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it('reports concurrent edits instead of overwriting them', async () => {
    mocks.update.mockRejectedValue({ code: 'P2025' });
    await expect(caller.updateCustomEvent(input)).rejects.toMatchObject({
      code: 'CONFLICT',
    });
  });

  it('returns a useful duplicate-name error if another save wins the race', async () => {
    mocks.update.mockRejectedValue({ code: 'P2002' });
    await expect(caller.updateCustomEvent(input)).rejects.toMatchObject({
      code: 'BAD_REQUEST',
    });
  });

  it('preserves creation and normalizes empty descriptions', async () => {
    mocks.findUnique.mockResolvedValue(null);
    mocks.create.mockResolvedValue(input);
    const { id: _id, updatedAt: _updatedAt, ...definition } = input;
    await caller.createCustomEvent({ ...definition, description: '' });
    expect(mocks.create).toHaveBeenCalledWith({
      data: { ...definition, description: null },
    });
  });

  it('rejects duplicate, undersized and oversized source selections', () => {
    for (const eventNames of [
      ['answer'],
      ['answer', 'answer'],
      Array.from({ length: 11 }, (_, i) => `source-${i}`),
    ]) {
      expect(
        zCreateCustomEvent.safeParse({ ...input, eventNames }).success
      ).toBe(false);
    }
  });
});

describe('custom event chart cache', () => {
  it('changes cache identity when the saved definition changes', async () => {
    const report = {
      projectId: 'jungle',
      series: [{ type: 'event', customEventId: id }],
    };
    mocks.findMany.mockResolvedValueOnce([{ id, updatedAt: input.updatedAt }]);
    const before = await getCustomEventCacheKey(report);
    mocks.findMany.mockResolvedValueOnce([
      { id, updatedAt: new Date('2026-10-15T16:50:28.978Z') },
    ]);
    expect(await getCustomEventCacheKey(report)).not.toBe(before);
    expect(mocks.findMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { projectId: 'jungle', id: { in: [id] } },
      })
    );
  });

  it('leaves ordinary tracked-event queries alone', async () => {
    expect(
      await getCustomEventCacheKey({
        projectId: 'jungle',
        series: [{ type: 'event', name: 'answer' }],
      })
    ).toBe('');
    expect(mocks.findMany).not.toHaveBeenCalled();
  });

  it('versions legacy event arrays and deduplicates references', async () => {
    mocks.findMany.mockResolvedValue([{ id, updatedAt: input.updatedAt }]);
    await getCustomEventCacheKey({
      projectId: 'jungle',
      series: undefined,
      events: [{ customEventId: id }, { type: 'event', customEventId: id }],
    });
    expect(mocks.findMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { projectId: 'jungle', id: { in: [id] } },
      })
    );
  });
});
