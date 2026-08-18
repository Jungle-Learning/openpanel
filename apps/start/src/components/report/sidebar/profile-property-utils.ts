const FIXED_PROFILE_PROPERTIES = [
  'profile.id',
  'profile.first_name',
  'profile.last_name',
  'profile.email',
  'profile.created_at',
  'profile.last_seen_at',
];

export function normalizeProfilePropertyName(property: string) {
  return property.startsWith('profile.') ? property : `profile.${property}`;
}

export function getProfilePropertyNames({
  eventProperties,
  profileProperties,
  includedProperties,
}: {
  eventProperties: string[];
  profileProperties: string[];
  includedProperties: string[];
}) {
  return Array.from(
    new Set([
      ...FIXED_PROFILE_PROPERTIES,
      ...profileProperties.map(normalizeProfilePropertyName),
      ...eventProperties.filter((property) => property.startsWith('profile.')),
      ...includedProperties.filter((property) =>
        property.startsWith('profile.'),
      ),
    ]),
  );
}
