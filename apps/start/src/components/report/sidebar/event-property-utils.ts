import type { IChartSeries } from '@openpanel/validation';

/** A wildcard (including one inside a custom event) needs project-wide fields. */
export function getReportPropertyEvents(
  series: IChartSeries
): string[] | undefined {
  const names = series.flatMap((item) =>
    item.type === 'event' && !item.customEventId
      ? (item.eventNames ?? [item.name])
      : []
  );
  if (names.includes('*')) return undefined;
  if (
    names.length === 0 &&
    !series.some((item) => item.type === 'event' && item.customEventId)
  )
    return undefined;
  return [...new Set(names)].sort();
}

export function getReportPropertyCustomEventIds(
  series: IChartSeries
): string[] {
  return [
    ...new Set(
      series.flatMap((item) =>
        item.type === 'event' && item.customEventId ? [item.customEventId] : []
      )
    ),
  ].sort();
}

// SDK/build context and historical user attributes can live on event payloads.
// Keep their original keys selectable; grouping must never alter tracking data.
const SHARED_CONTEXT_KEYS = new Set([
  'app_version',
  'build_number',
  'appVersion',
  'buildNumber',
  'environment',
  'sdk',
  'sdk_version',
  'sdkVersion',
  'deviceId',
  'deviceType',
  'operatingSystem',
  'dateTracked',
  'profileId',
  'userId',
  'email',
  'userEmail',
  'userName',
  'userCreatedDate',
  'userType',
  'studentType',
  'userSubscriptionPlan',
  'userSubscriptionStatus',
  'userSubscriptionPeriodType',
  'userNumOfMonthlyGenerationsLeft',
  'numOfSetsOfCardsGenerated',
  'whereDidUserComeFrom',
  'userReferrerId',
  'didUserComeFromMetaAd',
  'googleCampaignId',
  'tiktokCampaignId',
  'influencerCampaignId',
  'redditCampaignId',
  'universityId',
  'universityName',
  'totalXpEarned',
  'currentLevel',
  'currentTreeXp',
  'unlockedTrees',
  'numOfBuddiesUnlocked',
  'characterTeamSelected',
  'characterWithAccessorySelected',
  'isUserNew-LessThanOrEqualTo3Days',
]);

export type EventPropertySection =
  | 'Event properties'
  | 'Shared context'
  | 'Default properties';
export const EVENT_PROPERTY_SECTIONS: EventPropertySection[] = [
  'Event properties',
  'Shared context',
  'Default properties',
];

export function getEventPropertySection(
  property: string,
  profileProperties: string[] = []
): EventPropertySection {
  if (!property.startsWith('properties.')) return 'Default properties';
  const key = property.slice('properties.'.length);
  const root = key.split(/[.\[]/)[0] ?? key;
  // Context comes from SDK metadata or profile attributes copied onto events.
  const isKnownSharedContextKey = SHARED_CONTEXT_KEYS.has(root);
  const hasSharedContextPrefix =
    /^(\$|__|utm_|initial_|mp_|amplitude_|\[Amplitude\])/.test(root);
  const matchesProfileProperty = profileProperties.some(
    (name) => name.replace(/^profile\./, '') === property
  );
  if (
    isKnownSharedContextKey ||
    hasSharedContextPrefix ||
    matchesProfileProperty
  ) {
    return 'Shared context';
  }
  return 'Event properties';
}
