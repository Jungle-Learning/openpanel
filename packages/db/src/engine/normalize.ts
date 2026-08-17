import { alphabetIds } from '@openpanel/constants';
import type {
  IChartEvent,
  IReportInput,
  IReportInputWithDates,
} from '@openpanel/validation';
import {
  buildProfileSetFormula,
  parseProfileSetFormula,
} from '@openpanel/validation';
import { db } from '../prisma-client';
import { getChartStartEndDate } from '../services/date.service';
import { getSettingsForProject } from '../services/organization.service';
import type { SeriesDefinition } from './types';

export type NormalizedInput = Awaited<ReturnType<typeof normalize>>;

/**
 * Normalize a chart input into a clean structure with dates and normalized series
 */
export async function normalize(
  input: IReportInput,
): Promise<IReportInputWithDates & { series: SeriesDefinition[] }> {
  const { timezone } = await getSettingsForProject(input.projectId);
  const { startDate, endDate } = getChartStartEndDate(
    {
      range: input.range,
      startDate: input.startDate ?? undefined,
      endDate: input.endDate ?? undefined,
    },
    timezone,
  );

  // Get series from input (handles both 'series' and 'events' fields)
  // The schema preprocessing should have already converted 'events' to 'series', but handle both for safety
  const rawSeries = (input as any).series ?? (input as any).events ?? [];

  const customEventIds = rawSeries
    .filter(
      (item: unknown) =>
        item &&
        typeof item === 'object' &&
        'customEventId' in item &&
        typeof item.customEventId === 'string',
    )
    .map((item: { customEventId: string }) => item.customEventId);
  const customEvents =
    customEventIds.length > 0
      ? await db.customEvent.findMany({
          where: {
            projectId: input.projectId,
            id: { in: customEventIds },
          },
        })
      : [];
  const customEventsById = new Map(
    customEvents.map((customEvent) => [customEvent.id, customEvent]),
  );

  // Normalize each series item
  const normalizedSeries: SeriesDefinition[] = rawSeries.map(
    (item: any, index: number) => {
      // If item already has type field, it's the new format
      if (item && typeof item === 'object' && 'type' in item) {
        if (item.type === 'event' && item.customEventId) {
          const customEvent = customEventsById.get(item.customEventId);
          if (!customEvent) {
            throw new Error(
              `Saved custom event ${item.customEventId} does not exist in this project`,
            );
          }
          return {
            ...item,
            id: item.id ?? alphabetIds[index] ?? `series-${index}`,
            name: customEvent.name,
            displayName: item.displayName || customEvent.name,
            segment: 'user',
            property: undefined,
            eventNames: customEvent.eventNames,
            setOperation: customEvent.operation,
          } as SeriesDefinition;
        }
        const {
          eventNames: _eventNames,
          setOperation: _setOperation,
          ...itemWithoutResolvedSetFields
        } = item;
        return {
          ...itemWithoutResolvedSetFields,
          id: item.id ?? alphabetIds[index] ?? `series-${index}`,
        } as SeriesDefinition;
      }

      // Old format without type field - assume it's an event
      const event = item as Partial<IChartEvent>;
      return {
        type: 'event',
        id: event.id ?? alphabetIds[index] ?? `series-${index}`,
        name: event.name || 'unknown_event',
        segment: event.segment ?? 'event',
        filters: event.filters ?? [],
        displayName: event.displayName,
        property: event.property,
      } as SeriesDefinition;
    },
  );

  for (let index = 0; index < normalizedSeries.length; index++) {
    const definition = normalizedSeries[index];
    if (definition?.type !== 'formula') {
      continue;
    }

    const parsedProfileSet = parseProfileSetFormula(definition.formula);
    if (!parsedProfileSet) {
      continue;
    }

    const sourceEvents = parsedProfileSet.referenceIds.map((referenceId) => {
      const sourceIndex = alphabetIds.indexOf(
        referenceId as (typeof alphabetIds)[number],
      );
      const sourceDefinition = normalizedSeries[sourceIndex];

      if (sourceIndex < 0 || sourceIndex >= index || !sourceDefinition) {
        throw new Error(
          `${parsedProfileSet.operation.toUpperCase()} references ${referenceId}, but set formulas can only use earlier series`,
        );
      }
      if (sourceDefinition.type !== 'event') {
        throw new Error(
          `${parsedProfileSet.operation.toUpperCase()} can only combine event series; ${referenceId} is another formula`,
        );
      }
      if (sourceDefinition.customEventId || sourceDefinition.eventNames) {
        throw new Error(
          `${parsedProfileSet.operation.toUpperCase()} cannot nest a saved custom event in ${referenceId}`,
        );
      }
      if (sourceDefinition.name === '*') {
        throw new Error(
          `${parsedProfileSet.operation.toUpperCase()} cannot use “Any events” as ${referenceId}`,
        );
      }
      if (sourceDefinition.filters.length > 0 || sourceDefinition.property) {
        throw new Error(
          `${parsedProfileSet.operation.toUpperCase()} currently requires unfiltered event series; remove the filters or property metric from ${referenceId}`,
        );
      }

      return sourceDefinition;
    });
    const eventNames = sourceEvents.map((sourceEvent) => sourceEvent.name);
    if (new Set(eventNames).size !== eventNames.length) {
      throw new Error(
        `${parsedProfileSet.operation.toUpperCase()} requires different source events`,
      );
    }

    normalizedSeries[index] = {
      ...definition,
      formula: buildProfileSetFormula(
        parsedProfileSet.operation,
        parsedProfileSet.referenceIds,
      ),
      eventNames,
      setOperation: parsedProfileSet.operation,
    };
  }

  return {
    ...input,
    series: normalizedSeries,
    startDate,
    endDate,
  };
}
