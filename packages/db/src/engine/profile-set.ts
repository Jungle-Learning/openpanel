import type {
  IChartEvent,
  IChartEventItem,
  IChartFormula,
} from '@openpanel/validation';

type ProfileSetDefinition = IChartEventItem & {
  eventNames: string[];
  setOperation: 'union' | 'intersection';
};

export function isProfileSetDefinition(
  definition: IChartEventItem,
): definition is ProfileSetDefinition {
  return (
    definition.eventNames !== undefined &&
    definition.eventNames.length >= 2 &&
    definition.setOperation !== undefined
  );
}

export function isQueryBackedDefinition(definition: IChartEventItem): boolean {
  return definition.type === 'event' || isProfileSetDefinition(definition);
}

export function toQueryEvent(definition: IChartEventItem): IChartEvent {
  if (definition.type === 'event') {
    return definition;
  }

  const profileSetFormula = definition as IChartFormula & ProfileSetDefinition;
  return {
    id: profileSetFormula.id,
    name: profileSetFormula.formula,
    displayName: profileSetFormula.displayName || profileSetFormula.formula,
    segment: 'user',
    filters: [],
    eventNames: profileSetFormula.eventNames,
    setOperation: profileSetFormula.setOperation,
  };
}
