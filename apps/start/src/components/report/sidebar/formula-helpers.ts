import { alphabetIds } from '@openpanel/constants';
import type { IChartEventItem } from '@openpanel/validation';
import {
  buildProfileSetFormula,
  type ProfileSetOperation,
  parseProfileSetFormula,
} from '@openpanel/validation';

export const formulaTemplates = [
  {
    id: 'sum',
    label: 'Sum',
    example: 'A + B',
    description: 'Adds the values from two series in each time bucket.',
  },
  {
    id: 'difference',
    label: 'Difference',
    example: 'A - B',
    description: 'Subtracts the second series from the first.',
  },
  {
    id: 'ratio',
    label: 'Ratio',
    example: 'A / B',
    description: 'Divides the first series by the second.',
  },
  {
    id: 'percentage',
    label: 'Percentage',
    example: '(A / B) * 100',
    description: 'Shows the first series as a percentage of the second.',
  },
  {
    id: 'union',
    label: 'Union (unique users)',
    example: 'UNION(A, B)',
    description:
      'Counts each unique user once when they did any selected event.',
  },
  {
    id: 'intersection',
    label: 'Intersection (unique users)',
    example: 'INTERSECTION(A, B)',
    description:
      'Counts unique users who did every selected event in the same time bucket.',
  },
  {
    id: 'custom',
    label: 'Custom expression',
    example: 'e.g. (A + B) / C',
    description: 'Starts with a blank expression for advanced arithmetic.',
  },
] as const;

export type FormulaTemplateId = (typeof formulaTemplates)[number]['id'];

function getEarlierReferenceIds(formulaIndex: number): string[] {
  return alphabetIds.slice(0, formulaIndex);
}

function getEligibleProfileSetReferenceIds(
  series: IChartEventItem[],
  formulaIndex: number,
): string[] {
  const seenEventNames = new Set<string>();
  const referenceIds: string[] = [];

  series.slice(0, formulaIndex).forEach((definition, index) => {
    if (
      definition.type !== 'event' ||
      definition.name === '*' ||
      definition.customEventId ||
      definition.eventNames ||
      definition.filters.length > 0 ||
      definition.property ||
      seenEventNames.has(definition.name)
    ) {
      return;
    }

    const referenceId = alphabetIds[index];
    if (referenceId) {
      seenEventNames.add(definition.name);
      referenceIds.push(referenceId);
    }
  });

  return referenceIds.slice(0, 10);
}

export function buildFormulaFromTemplate(options: {
  templateId: FormulaTemplateId;
  series: IChartEventItem[];
  formulaIndex: number;
}): string | null {
  const { templateId, series, formulaIndex } = options;
  if (templateId === 'custom') {
    return '';
  }

  if (templateId === 'union' || templateId === 'intersection') {
    const referenceIds = getEligibleProfileSetReferenceIds(
      series,
      formulaIndex,
    );
    if (referenceIds.length < 2) {
      return null;
    }
    return buildProfileSetFormula(templateId, referenceIds);
  }

  const [firstReferenceId, secondReferenceId] =
    getEarlierReferenceIds(formulaIndex);
  if (!(firstReferenceId && secondReferenceId)) {
    return null;
  }

  switch (templateId) {
    case 'sum':
      return `${firstReferenceId} + ${secondReferenceId}`;
    case 'difference':
      return `${firstReferenceId} - ${secondReferenceId}`;
    case 'ratio':
      return `${firstReferenceId} / ${secondReferenceId}`;
    case 'percentage':
      return `(${firstReferenceId} / ${secondReferenceId}) * 100`;
  }
}

export interface ProfileSetFormulaResolution {
  operation: ProfileSetOperation;
  referenceIds: string[];
  eventNames?: string[];
  error?: string;
}

export function resolveProfileSetFormula(options: {
  formula: string;
  formulaIndex: number;
  series: IChartEventItem[];
}): ProfileSetFormulaResolution | null {
  const parsedFormula = parseProfileSetFormula(options.formula);
  if (!parsedFormula) {
    return null;
  }

  const sourceEvents: Extract<IChartEventItem, { type: 'event' }>[] = [];
  for (const referenceId of parsedFormula.referenceIds) {
    const sourceIndex = alphabetIds.indexOf(
      referenceId as (typeof alphabetIds)[number],
    );
    const sourceDefinition = options.series[sourceIndex];
    if (
      sourceIndex < 0 ||
      sourceIndex >= options.formulaIndex ||
      !sourceDefinition
    ) {
      return {
        ...parsedFormula,
        error: `${referenceId} must refer to an earlier series.`,
      };
    }
    if (sourceDefinition.type !== 'event') {
      return {
        ...parsedFormula,
        error: `${referenceId} is another formula; set operations require tracked events.`,
      };
    }
    if (
      sourceDefinition.name === '*' ||
      sourceDefinition.customEventId ||
      sourceDefinition.eventNames
    ) {
      return {
        ...parsedFormula,
        error: `${referenceId} is not a raw tracked event.`,
      };
    }
    if (sourceDefinition.filters.length > 0 || sourceDefinition.property) {
      return {
        ...parsedFormula,
        error: `${referenceId} has a filter or property metric; set operations currently require unfiltered events.`,
      };
    }
    sourceEvents.push(sourceDefinition);
  }

  const eventNames = sourceEvents.map((sourceEvent) => sourceEvent.name);
  if (new Set(eventNames).size !== eventNames.length) {
    return {
      ...parsedFormula,
      error: 'Set operations require different source events.',
    };
  }

  return { ...parsedFormula, eventNames };
}
