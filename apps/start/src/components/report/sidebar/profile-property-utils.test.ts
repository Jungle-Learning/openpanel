import { describe, expect, it } from 'vitest';
import {
  getProfilePropertyNames,
  normalizeProfilePropertyName,
} from './profile-property-utils';

describe('profile property helpers', () => {
  it('prefixes properties returned by the profile endpoint', () => {
    expect(normalizeProfilePropertyName('properties.userSubscriptionPlan')).toBe(
      'profile.properties.userSubscriptionPlan',
    );
  });

  it('keeps already-prefixed profile properties unchanged', () => {
    expect(normalizeProfilePropertyName('profile.email')).toBe(
      'profile.email',
    );
  });

  it('includes fixed fields and de-duplicates discovered properties', () => {
    const propertyNames = getProfilePropertyNames({
      eventProperties: ['profile.properties.userSubscriptionPlan'],
      profileProperties: [
        'properties.userSubscriptionPlan',
        'properties.studentType',
      ],
      includedProperties: ['profile.properties.studentType'],
    });

    expect(propertyNames).toContain('profile.email');
    expect(propertyNames).toContain(
      'profile.properties.userSubscriptionPlan',
    );
    expect(propertyNames).toContain('profile.properties.studentType');
    expect(
      propertyNames.filter(
        (property) => property === 'profile.properties.userSubscriptionPlan',
      ),
    ).toHaveLength(1);
  });
});
