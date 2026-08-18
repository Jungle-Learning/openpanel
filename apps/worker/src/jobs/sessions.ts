import type { Job } from 'bullmq';

import type { SessionsQueuePayload } from '@openpanel/queue';

import { logger } from '@/utils/logger';
import {
  db,
  getOrganizationBillingEventsCount,
  getProjectEventsCount,
} from '@openpanel/db';
import { cacheable } from '@openpanel/redis';
import { createSessionEnd } from './events.create-session-end';

const INT4_MAX = 2_147_483_647;

export async function sessionsJob(job: Job<SessionsQueuePayload>) {
  const res = await createSessionEnd(job);
  try {
    await updateEventsCount(job.data.payload.projectId);
  } catch (e) {
    logger.error({ err: e }, 'Failed to update events count');
  }
  return res;
}

const updateEventsCount = cacheable(async function updateEventsCount(
  projectId: string,
) {
  const organization = await db.organization.findFirst({
    where: {
      projects: {
        some: {
          id: projectId,
        },
      },
    },
    include: {
      projects: true,
    },
  });

  if (!organization) {
    return;
  }

  const organizationEventsCount =
    await getOrganizationBillingEventsCount(organization);
  const projectEventsCount = await getProjectEventsCount(projectId);

  if (projectEventsCount) {
    await db.project.update({
      where: {
        id: projectId,
      },
      data: {
        // Saturating counter: the column is INT4 and lifetime counts can
        // exceed it. It's only used as a sort key and activity threshold,
        // never for billing, so clamping is safe.
        eventsCount: Math.min(projectEventsCount, INT4_MAX),
      },
    });
  }

  if (organizationEventsCount) {
    // Self-hosting has no billing/event limits. Never flag the org as
    // exceeded, and clear any stale flag that was set before this guard
    // existed (default limit is 0, which otherwise trips on the first event).
    const isSelfHosted = process.env.SELF_HOSTED === 'true';

    await db.organization.update({
      where: {
        id: organization.id,
      },
      data: {
        subscriptionPeriodEventsCount: Math.min(
          organizationEventsCount,
          INT4_MAX,
        ),
        subscriptionPeriodEventsCountExceededAt: isSelfHosted
          ? null
          : organizationEventsCount >
                organization.subscriptionPeriodEventsLimit &&
              !organization.subscriptionPeriodEventsCountExceededAt
            ? new Date()
            : organizationEventsCount <=
                organization.subscriptionPeriodEventsLimit
              ? null
              : organization.subscriptionPeriodEventsCountExceededAt,
      },
    });
  }

  return true;
}, 60 * 60);
