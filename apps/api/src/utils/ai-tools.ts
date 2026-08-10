import { chartTypes } from '@openpanel/constants';
import type { IClickhouseSession } from '@openpanel/db';
import {
  type IClickhouseEvent,
  type IClickhouseProfile,
  TABLE_NAMES,
  ch,
  clix,
  formatClickhouseDate,
  getProjectByIdCached,
} from '@openpanel/db';
import { ChartEngine } from '@openpanel/db';
import { getCache } from '@openpanel/redis';
import { type FinalChart, zReportInput } from '@openpanel/validation';
import { tool } from 'ai';
import { z } from 'zod';

const MAX_AI_ANALYSIS_SERIES = 10;
const MAX_AI_ANALYSIS_POINTS_PER_SERIES = 100;
const MAX_AI_EVENT_CHANGE_SERIES = 12;

function addUtcDays(day: string, days: number): string {
  const value = new Date(`${day}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

function normalizeAiReportDateRange<
  T extends { startDate: string; endDate: string },
>(report: T): T {
  const startDay = report.startDate.slice(0, 10);
  const endDay = report.endDate.slice(0, 10);
  const isCalendarDate = /^\d{4}-\d{2}-\d{2}$/;
  if (isCalendarDate.test(startDay) && isCalendarDate.test(endDay)) {
    const endsAtEndOfDay = /T23:59(?::59(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$/.test(
      report.endDate,
    );
    return {
      ...report,
      startDate: startDay,
      endDate:
        startDay === endDay || endsAtEndOfDay
          ? addUtcDays(endDay, 1)
          : endDay,
    };
  }

  return report;
}

type EventChangeRow = {
  name: string;
  current_count: number | string;
  previous_count: number | string;
};

type EventChangeSummary = {
  name: string;
  current: number;
  previous: number;
  absoluteChange: number;
  percentageChange: number | null;
};

function summarizeEventChange(row: EventChangeRow): EventChangeSummary {
  const current = Number(row.current_count);
  const previous = Number(row.previous_count);
  const absoluteChange = current - previous;
  return {
    name: row.name,
    current,
    previous,
    absoluteChange,
    percentageChange:
      previous === 0
        ? null
        : Math.round((absoluteChange / previous) * 1_000) / 10,
  };
}

function normalizeAiReportMetric<
  T extends {
    chartType: string;
    metric: string;
    series: Array<{ type: string; segment?: string }>;
  },
>(report: T): T {
  const isEventTotalMetric =
    report.chartType === 'metric' &&
    report.metric === 'count' &&
    report.series.length > 0 &&
    report.series.every(
      (series) => series.type === 'event' && series.segment === 'event',
    );

  return isEventTotalMetric ? ({ ...report, metric: 'sum' } as T) : report;
}

function createAiChartAnalysisData(data: FinalChart) {
  return {
    metrics: data.metrics,
    series: data.series
      .slice(0, MAX_AI_ANALYSIS_SERIES)
      .map((series) => ({
        names: series.names,
        metrics: series.metrics,
        data: series.data.slice(0, MAX_AI_ANALYSIS_POINTS_PER_SERIES),
      })),
    truncation: {
      totalSeries: data.series.length,
      returnedSeries: Math.min(data.series.length, MAX_AI_ANALYSIS_SERIES),
      maxPointsPerSeries: MAX_AI_ANALYSIS_POINTS_PER_SERIES,
    },
  };
}

export function getReport({
  projectId,
}: {
  projectId: string;
}) {
  return tool({
    description: `Generate a report (a chart) for 
    - ${chartTypes.area}
    - ${chartTypes.linear}
    - ${chartTypes.pie}
    - ${chartTypes.histogram}
    - ${chartTypes.metric}
    - ${chartTypes.bar}
`,
    parameters: zReportInput.extend({
      startDate: z.string().describe('The start date for the report'),
      endDate: z.string().describe('The end date for the report'),
    }),
    execute: async (report) => {
      const reportWithProjectId = {
        ...normalizeAiReportMetric(normalizeAiReportDateRange(report)),
        projectId,
      };

      try {
        const data = await ChartEngine.execute(reportWithProjectId);
        return {
          type: 'report',
          report: reportWithProjectId,
          analysisData: createAiChartAnalysisData(data),
        };
      } catch {
        return {
          type: 'report',
          report: reportWithProjectId,
          analysisDataError:
            'The chart query could not be executed for written analysis.',
        };
      }
    },
  });
}

export function getEventChanges({
  projectId,
}: {
  projectId: string;
}) {
  return tool({
    description:
      'Compare every tracked event name across two equal periods, rank the most meaningful changes, and return a chart of the largest absolute movers. Use this for broad questions about what changed, grew, declined, appeared, disappeared, or looks unusual across the product. Do not call getAllEventNames first.',
    parameters: z.object({
      startDate: z
        .string()
        .describe('Inclusive start of the current comparison period.'),
      endDate: z
        .string()
        .describe('Exclusive end of the current comparison period.'),
      limit: z
        .number()
        .min(3)
        .max(MAX_AI_EVENT_CHANGE_SERIES)
        .default(8)
        .describe('How many of the largest absolute movers to chart.'),
    }),
    execute: async (input) => {
      const normalizedRange = normalizeAiReportDateRange({
        startDate: input.startDate,
        endDate: input.endDate,
      });
      const currentStart = new Date(`${normalizedRange.startDate}T00:00:00Z`);
      const currentEnd = new Date(`${normalizedRange.endDate}T00:00:00Z`);
      const durationMilliseconds = currentEnd.getTime() - currentStart.getTime();
      if (
        !Number.isFinite(durationMilliseconds) ||
        durationMilliseconds <= 0 ||
        durationMilliseconds > 366 * 24 * 60 * 60 * 1_000
      ) {
        throw new Error(
          'Event-change comparisons require a valid period of 1 to 366 days.',
        );
      }

      const previousStart = new Date(
        currentStart.getTime() - durationMilliseconds,
      );
      const project = await getProjectByIdCached(projectId);
      const timezone = project?.timezone || 'UTC';
      const currentStartValue = formatClickhouseDate(currentStart);
      const currentEndValue = formatClickhouseDate(currentEnd);
      const previousStartValue = formatClickhouseDate(previousStart);

      const queryResult = await ch.query({
        query: `
          SELECT
            name,
            countIf(created_at >= toDateTime({currentStart:String}) AND created_at < toDateTime({currentEnd:String})) AS current_count,
            countIf(created_at >= toDateTime({previousStart:String}) AND created_at < toDateTime({currentStart:String})) AS previous_count
          FROM ${TABLE_NAMES.events}
          WHERE project_id = {projectId:String}
            AND created_at >= toDateTime({previousStart:String})
            AND created_at < toDateTime({currentEnd:String})
          GROUP BY name
          HAVING current_count > 0 OR previous_count > 0
        `,
        query_params: {
          projectId,
          currentStart: currentStartValue,
          currentEnd: currentEndValue,
          previousStart: previousStartValue,
        },
        clickhouse_settings: {
          session_timezone: timezone,
        },
        format: 'JSONEachRow',
      });
      const changes = ((await queryResult.json()) as EventChangeRow[]).map(
        summarizeEventChange,
      );
      const minimumMeaningfulVolume = 20;
      const meaningfulChanges = changes.filter(
        (change) => change.current + change.previous >= minimumMeaningfulVolume,
      );
      const largestMovers = [...meaningfulChanges]
        .sort(
          (left, right) =>
            Math.abs(right.absoluteChange) - Math.abs(left.absoluteChange),
        )
        .slice(0, input.limit);
      const topIncreases = [...meaningfulChanges]
        .filter((change) => change.absoluteChange > 0)
        .sort((left, right) => right.absoluteChange - left.absoluteChange)
        .slice(0, 10);
      const topDecreases = [...meaningfulChanges]
        .filter((change) => change.absoluteChange < 0)
        .sort((left, right) => left.absoluteChange - right.absoluteChange)
        .slice(0, 10);
      const largestRelativeChanges = [...meaningfulChanges]
        .filter(
          (change) =>
            change.current > 0 &&
            change.previous > 0 &&
            Math.max(change.current, change.previous) >= 100,
        )
        .sort(
          (left, right) =>
            Math.abs(right.percentageChange ?? 0) -
            Math.abs(left.percentageChange ?? 0),
        )
        .slice(0, 10);
      const newEvents = [...changes]
        .filter(
          (change) =>
            change.previous === 0 &&
            change.current >= minimumMeaningfulVolume,
        )
        .sort((left, right) => right.current - left.current)
        .slice(0, 10);
      const disappearedEvents = [...changes]
        .filter(
          (change) =>
            change.current === 0 &&
            change.previous >= minimumMeaningfulVolume,
        )
        .sort((left, right) => right.previous - left.previous)
        .slice(0, 10);

      const report = {
        projectId,
        name: 'Largest event changes across the current and previous periods',
        unit: 'events',
        range: 'custom' as const,
        metric: 'sum' as const,
        series: largestMovers.map((change, index) => ({
          id: `event-change-${index}`,
          name: change.name,
          type: 'event' as const,
          segment: 'event' as const,
          filters: [],
          displayName: change.name,
        })),
        startDate: previousStart.toISOString().slice(0, 10),
        endDate: normalizedRange.endDate,
        previous: false,
        chartType: 'linear' as const,
        interval: 'day' as const,
        lineType: 'linear' as const,
        breakdowns: [],
        limit: input.limit,
        offset: 0,
      };
      const chartData = await ChartEngine.execute(report);
      const currentTotal = changes.reduce(
        (total, change) => total + change.current,
        0,
      );
      const previousTotal = changes.reduce(
        (total, change) => total + change.previous,
        0,
      );

      return {
        type: 'report',
        report,
        changeAnalysis: {
          timezone,
          currentPeriod: {
            startDate: normalizedRange.startDate,
            endDateExclusive: normalizedRange.endDate,
          },
          previousPeriod: {
            startDate: previousStart.toISOString().slice(0, 10),
            endDateExclusive: normalizedRange.startDate,
          },
          eventsCompared: changes.length,
          totals: {
            current: currentTotal,
            previous: previousTotal,
            absoluteChange: currentTotal - previousTotal,
            percentageChange:
              previousTotal === 0
                ? null
                : Math.round(
                    ((currentTotal - previousTotal) / previousTotal) * 1_000,
                  ) / 10,
          },
          topIncreases,
          topDecreases,
          largestRelativeChanges,
          newEvents,
          disappearedEvents,
          selectionNotes: {
            queriedAllEventNames: true,
            minimumVolumeForRankedLists: minimumMeaningfulVolume,
            chartedSeries: largestMovers.map((change) => change.name),
          },
        },
        analysisData: createAiChartAnalysisData(chartData),
      };
    },
  });
}
export function getConversionReport({
  projectId,
}: {
  projectId: string;
}) {
  return tool({
    description:
      'Generate a report (a chart) for conversions between two actions a unique user took.',
    parameters: zReportInput.extend({
      startDate: z.string().describe('The start date for the report'),
      endDate: z.string().describe('The end date for the report'),
    }),
    execute: async (report) => {
      return {
        type: 'report',
        // data: await conversionService.getConversion(report),
        report: {
          ...normalizeAiReportDateRange(report),
          projectId,
          chartType: 'conversion',
        },
      };
    },
  });
}
export function getFunnelReport({
  projectId,
}: {
  projectId: string;
}) {
  return tool({
    description:
      'Generate a report (a chart) for funnel between two or more actions a unique user (session_id or profile_id) took.',
    parameters: zReportInput.extend({
      startDate: z.string().describe('The start date for the report'),
      endDate: z.string().describe('The end date for the report'),
    }),
    execute: async (report) => {
      return {
        type: 'report',
        // data: await funnelService.getFunnel(report),
        report: {
          ...normalizeAiReportDateRange(report),
          projectId,
          chartType: 'funnel',
        },
      };
    },
  });
}

export function getProfiles({
  projectId,
}: {
  projectId: string;
}) {
  return tool({
    description: 'Get profiles',
    parameters: z.object({
      projectId: z.string(),
      limit: z.number().optional(),
      email: z.string().optional(),
      firstName: z.string().optional(),
      lastName: z.string().optional(),
      country: z.string().describe('ISO 3166-1 alpha-2').optional(),
      city: z.string().optional(),
      region: z.string().optional(),
      device: z.string().optional(),
      browser: z.string().optional(),
    }),
    execute: async (input) => {
      const builder = clix(ch)
        .select<IClickhouseProfile>([
          'id',
          'email',
          'first_name',
          'last_name',
          'properties',
        ])
        .from(TABLE_NAMES.profiles)
        .where('project_id', '=', projectId);

      if (input.email) {
        builder.where('email', 'LIKE', `%${input.email}%`);
      }

      if (input.firstName) {
        builder.where('first_name', 'LIKE', `%${input.firstName}%`);
      }

      if (input.lastName) {
        builder.where('last_name', 'LIKE', `%${input.lastName}%`);
      }

      if (input.country) {
        builder.where(`properties['country']`, '=', input.country);
      }

      if (input.city) {
        builder.where(`properties['city']`, '=', input.city);
      }

      if (input.region) {
        builder.where(`properties['region']`, '=', input.region);
      }

      if (input.device) {
        builder.where(`properties['device']`, '=', input.device);
      }

      if (input.browser) {
        builder.where(`properties['browser']`, '=', input.browser);
      }

      const profiles = await builder.limit(input.limit ?? 5).execute();

      return profiles;
    },
  });
}

export function getProfile({
  projectId,
}: {
  projectId: string;
}) {
  return tool({
    description: 'Get a specific profile',
    parameters: z.object({
      projectId: z.string(),
      email: z.string().optional(),
      firstName: z.string().optional(),
      lastName: z.string().optional(),
      country: z.string().describe('ISO 3166-1 alpha-2').optional(),
      city: z.string().optional(),
      region: z.string().optional(),
      device: z.string().optional(),
      browser: z.string().optional(),
    }),
    execute: async (input) => {
      const builder = clix(ch)
        .select<IClickhouseProfile>([
          'id',
          'email',
          'first_name',
          'last_name',
          'properties',
        ])
        .from(TABLE_NAMES.profiles)
        .where('project_id', '=', projectId);

      if (input.email) {
        builder.where('email', 'LIKE', `%${input.email}%`);
      }

      if (input.firstName) {
        builder.where('first_name', 'LIKE', `%${input.firstName}%`);
      }

      if (input.lastName) {
        builder.where('last_name', 'LIKE', `%${input.lastName}%`);
      }

      if (input.country) {
        builder.where(`properties['country']`, '=', input.country);
      }

      if (input.city) {
        builder.where(`properties['city']`, '=', input.city);
      }

      if (input.region) {
        builder.where(`properties['region']`, '=', input.region);
      }

      if (input.device) {
        builder.where(`properties['device']`, '=', input.device);
      }

      if (input.browser) {
        builder.where(`properties['browser']`, '=', input.browser);
      }

      const profiles = await builder.limit(1).execute();

      const profile = profiles[0];
      if (!profile) {
        return {
          error: 'Profile not found',
        };
      }

      const events = await clix(ch)
        .select<IClickhouseEvent>([])
        .from(TABLE_NAMES.events)
        .where('project_id', '=', input.projectId)
        .where('profile_id', '=', profile.id)
        .limit(5)
        .orderBy('created_at', 'DESC')
        .execute();

      return {
        profile,
        events,
      };
    },
  });
}

export function getEvents({
  projectId,
}: {
  projectId: string;
}) {
  return tool({
    description: 'Get events for a project or specific profile',
    parameters: z.object({
      projectId: z.string(),
      profileId: z.string().optional(),
      take: z.number().optional().default(10),
      eventNames: z.array(z.string()).optional(),
      referrer: z.string().optional(),
      referrerName: z.string().optional(),
      referrerType: z.string().optional(),
      device: z.string().optional(),
      country: z.string().optional(),
      city: z.string().optional(),
      os: z.string().optional(),
      browser: z.string().optional(),
      properties: z.record(z.string(), z.string()).optional(),
      startDate: z.string().optional().describe('ISO date string'),
      endDate: z.string().optional().describe('ISO date string'),
    }),
    execute: async (input) => {
      const builder = clix(ch)
        .select<IClickhouseEvent>([])
        .from(TABLE_NAMES.events)
        .where('project_id', '=', projectId);

      if (input.profileId) {
        builder.where('profile_id', '=', input.profileId);
      }

      if (input.eventNames) {
        builder.where('name', 'IN', input.eventNames);
      }

      if (input.referrer) {
        builder.where('referrer', '=', input.referrer);
      }

      if (input.referrerName) {
        builder.where('referrer_name', '=', input.referrerName);
      }

      if (input.referrerType) {
        builder.where('referrer_type', '=', input.referrerType);
      }

      if (input.device) {
        builder.where('device', '=', input.device);
      }

      if (input.country) {
        builder.where('country', '=', input.country);
      }

      if (input.city) {
        builder.where('city', '=', input.city);
      }

      if (input.os) {
        builder.where('os', '=', input.os);
      }

      if (input.browser) {
        builder.where('browser', '=', input.browser);
      }

      if (input.properties) {
        for (const [key, value] of Object.entries(input.properties)) {
          builder.where(`properties['${key}']`, '=', value);
        }
      }

      if (input.startDate && input.endDate) {
        builder.where('created_at', 'BETWEEN', [
          clix.datetime(input.startDate),
          clix.datetime(input.endDate),
        ]);
      } else {
        builder.where('created_at', 'BETWEEN', [
          clix.datetime(new Date(Date.now() - 1000 * 60 * 60 * 24 * 7)),
          clix.datetime(new Date()),
        ]);
      }

      return await builder.limit(input.take).execute();
    },
  });
}

export function getSessions({
  projectId,
}: {
  projectId: string;
}) {
  return tool({
    description: 'Get sessions for a project or specific profile',
    parameters: z.object({
      projectId: z.string(),
      profileId: z.string().optional(),
      take: z.number().optional().default(10),
      referrer: z.string().optional(),
      referrerName: z.string().optional(),
      referrerType: z.string().optional(),
      device: z.string().optional(),
      country: z.string().optional(),
      city: z.string().optional(),
      os: z.string().optional(),
      browser: z.string().optional(),
      properties: z.record(z.string(), z.string()).optional(),
      startDate: z.string().optional().describe('ISO date string'),
      endDate: z.string().optional().describe('ISO date string'),
    }),
    execute: async (input) => {
      const builder = clix(ch)
        .select<IClickhouseSession>([])
        .from(TABLE_NAMES.sessions)
        .where('project_id', '=', projectId)
        .where('sign', '=', 1);

      if (input.profileId) {
        builder.where('profile_id', '=', input.profileId);
      }

      if (input.referrer) {
        builder.where('referrer', '=', input.referrer);
      }

      if (input.referrerName) {
        builder.where('referrer_name', '=', input.referrerName);
      }

      if (input.referrerType) {
        builder.where('referrer_type', '=', input.referrerType);
      }

      if (input.device) {
        builder.where('device', '=', input.device);
      }

      if (input.country) {
        builder.where('country', '=', input.country);
      }

      if (input.city) {
        builder.where('city', '=', input.city);
      }

      if (input.os) {
        builder.where('os', '=', input.os);
      }

      if (input.browser) {
        builder.where('browser', '=', input.browser);
      }

      if (input.properties) {
        for (const [key, value] of Object.entries(input.properties)) {
          builder.where(`properties['${key}']`, '=', value);
        }
      }

      if (input.startDate && input.endDate) {
        builder.where('created_at', 'BETWEEN', [
          clix.datetime(input.startDate),
          clix.datetime(input.endDate),
        ]);
      } else {
        builder.where('created_at', 'BETWEEN', [
          clix.datetime(new Date(Date.now() - 1000 * 60 * 60 * 24 * 7)),
          clix.datetime(new Date()),
        ]);
      }

      return await builder.limit(input.take).execute();
    },
  });
}

export function getAllEventNames({
  projectId,
}: {
  projectId: string;
}) {
  return tool({
    description: 'Get the top 50 event names in a comma separated list',
    parameters: z.object({}),
    execute: async () => {
      return getCache(`top-event-names:${projectId}`, 60 * 10, async () => {
        const events = await clix(ch)
          .select<IClickhouseEvent>(['name', 'count() as count'])
          .from(TABLE_NAMES.event_names_mv)
          .where('project_id', '=', projectId)
          .groupBy(['name'])
          .orderBy('count', 'DESC')
          .limit(50)
          .execute();

        return events.map((event) => event.name).join(',');
      });
    },
  });
}
