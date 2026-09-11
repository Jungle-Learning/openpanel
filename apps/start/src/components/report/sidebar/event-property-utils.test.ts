import type { IChartSeries } from '@openpanel/validation';
import { describe, expect, it } from 'vitest';
import {
  getEventPropertySection,
  getReportPropertyEvents,
} from './event-property-utils';

const event = (name: string) => ({
  type: 'event' as const,
  name,
  segment: 'event' as const,
  filters: [],
});

describe('report property event scope', () => {
  it('uses only the selected events, de-duplicating names', () => {
    expect(
      getReportPropertyEvents([
        event('voice session ended'),
        event('voice session ended'),
      ])
    ).toEqual(['voice session ended']);
  });
  it('uses every custom-event source and ignores formulas', () => {
    const series = [
      { ...event('custom event'), eventNames: ['b', 'a'] },
      event('c'),
      { type: 'formula', formula: 'A/B', id: 'formula' },
    ] as IChartSeries;
    expect(getReportPropertyEvents(series)).toEqual(['a', 'b', 'c']);
  });
  it('keeps project-wide discovery for All events or an empty report', () => {
    expect(
      getReportPropertyEvents([event('*'), event('voice session ended')])
    ).toBeUndefined();
    expect(getReportPropertyEvents([])).toBeUndefined();
  });
});

describe('event property categories', () => {
  it('prioritizes event-specific fields without losing their paths', () => {
    for (const key of [
      'durationSeconds',
      'endReason',
      'latency.p95',
      'userSpeakingSeconds',
    ]) {
      expect(getEventPropertySection(`properties.${key}`)).toBe(
        'Event properties'
      );
    }
  });
  it('separates built-in columns from shared payload context', () => {
    for (const key of ['os', 'path', 'city', 'name', 'revenue']) {
      expect(getEventPropertySection(key)).toBe('Default properties');
    }
    for (const key of [
      'app_version',
      'userSubscriptionPlan',
      '__query.utm_source',
      '$device_id',
      'unlockedTrees[*]',
    ]) {
      expect(getEventPropertySection(`properties.${key}`)).toBe(
        'Shared context'
      );
    }
  });
  it('recognizes custom profile attributes duplicated onto events', () => {
    expect(
      getEventPropertySection('properties.accountTier', [
        'profile.properties.accountTier',
      ])
    ).toBe('Shared context');
    expect(
      getEventPropertySection('properties.durationSeconds', [
        'properties.accountTier',
      ])
    ).toBe('Event properties');
  });
});
