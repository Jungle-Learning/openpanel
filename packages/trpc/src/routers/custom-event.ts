import { ch, clix, db, TABLE_NAMES } from '@openpanel/db';
import {
  type ICreateCustomEvent,
  zCreateCustomEvent,
  zUpdateCustomEvent,
} from '@openpanel/validation';
import { TRPCError } from '@trpc/server';
import { z } from 'zod';
import { getProjectAccess } from '../access';
import { protectedProcedure } from '../trpc';

async function assertProjectAccess(projectId: string, userId: string) {
  if (!(await getProjectAccess({ projectId, userId }))) {
    throw new TRPCError({
      code: 'UNAUTHORIZED',
      message: 'You do not have access to this project',
    });
  }
}

async function validateDefinition(input: ICreateCustomEvent, id?: string) {
  const [existingCustomEvent, sourceEventNames, trackedEventWithSameName] =
    await Promise.all([
      db.customEvent.findUnique({
        where: {
          name_projectId: { name: input.name, projectId: input.projectId },
        },
      }),
      clix(ch)
        .select<{ name: string }>(['DISTINCT name'])
        .from(TABLE_NAMES.event_names_mv)
        .where('project_id', '=', input.projectId)
        .where('name', 'IN', input.eventNames)
        .execute(),
      clix(ch)
        .select<{ name: string }>(['name'])
        .from(TABLE_NAMES.event_names_mv)
        .where('project_id', '=', input.projectId)
        .where('name', '=', input.name)
        .limit(1)
        .execute(),
    ]);

  if (existingCustomEvent && existingCustomEvent.id !== id) {
    throw new TRPCError({
      code: 'BAD_REQUEST',
      message: `A custom event named “${input.name}” already exists`,
    });
  }
  const existingSourceNames = new Set(
    sourceEventNames.map((event) => event.name)
  );
  const missingSourceNames = input.eventNames.filter(
    (name) => !existingSourceNames.has(name)
  );
  if (missingSourceNames.length > 0) {
    throw new TRPCError({
      code: 'BAD_REQUEST',
      message: `Source event${missingSourceNames.length === 1 ? '' : 's'} not found: ${missingSourceNames.join(', ')}`,
    });
  }
  if (trackedEventWithSameName.length > 0 || input.name === '*') {
    throw new TRPCError({
      code: 'BAD_REQUEST',
      message: `“${input.name}” is already used by a tracked event`,
    });
  }
}

function handleWriteError(error: unknown, name: string): never {
  if (error && typeof error === 'object' && 'code' in error) {
    if (error.code === 'P2002') {
      throw new TRPCError({
        code: 'BAD_REQUEST',
        message: `A custom event named “${name}” already exists`,
      });
    }
    if (error.code === 'P2025') {
      throw new TRPCError({
        code: 'CONFLICT',
        message:
          'This custom event changed or was removed. Reload its definition before saving again.',
      });
    }
  }
  throw error;
}

const eventIdentity = z.object({ projectId: z.string().min(1), id: z.uuid() });

export const customEventProcedures = {
  customEvents: protectedProcedure
    .input(z.object({ projectId: z.string().min(1) }))
    .query(async ({ input, ctx }) => {
      await assertProjectAccess(input.projectId, ctx.session.userId);
      return db.customEvent.findMany({
        where: { projectId: input.projectId },
        orderBy: { name: 'asc' },
      });
    }),

  customEvent: protectedProcedure
    .input(eventIdentity)
    .query(async ({ input, ctx }) => {
      await assertProjectAccess(input.projectId, ctx.session.userId);
      const event = await db.customEvent.findFirst({ where: input });
      if (!event) {
        throw new TRPCError({
          code: 'NOT_FOUND',
          message: 'Custom event not found',
        });
      }
      return event;
    }),

  createCustomEvent: protectedProcedure
    .input(zCreateCustomEvent)
    .mutation(async ({ input, ctx }) => {
      await assertProjectAccess(input.projectId, ctx.session.userId);
      await validateDefinition(input);
      try {
        return await db.customEvent.create({
          data: { ...input, description: input.description || null },
        });
      } catch (error) {
        handleWriteError(error, input.name);
      }
    }),

  updateCustomEvent: protectedProcedure
    .input(zUpdateCustomEvent)
    .mutation(async ({ input, ctx }) => {
      await assertProjectAccess(input.projectId, ctx.session.userId);
      const event = await db.customEvent.findFirst({
        where: { id: input.id, projectId: input.projectId },
      });
      if (!event) {
        throw new TRPCError({
          code: 'NOT_FOUND',
          message: 'Custom event not found',
        });
      }
      await validateDefinition(input, input.id);
      try {
        return await db.customEvent.update({
          where: {
            id: input.id,
            projectId: input.projectId,
            updatedAt: input.updatedAt,
          },
          data: {
            name: input.name,
            description: input.description || null,
            operation: input.operation,
            eventNames: input.eventNames,
          },
        });
      } catch (error) {
        handleWriteError(error, input.name);
      }
    }),
};
