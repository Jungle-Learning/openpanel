import { describe, expect, it } from 'vitest';
import {
  buildProfileSetFormula,
  parseProfileSetFormula,
} from './profile-set-formula';

describe('profile set formulas', () => {
  it('parses union and intersection formulas case-insensitively', () => {
    expect(parseProfileSetFormula('UNION(A, B, C)')).toEqual({
      operation: 'union',
      referenceIds: ['A', 'B', 'C'],
    });
    expect(parseProfileSetFormula(' intersection( a, b ) ')).toEqual({
      operation: 'intersection',
      referenceIds: ['A', 'B'],
    });
    expect(parseProfileSetFormula('A | B | C')).toEqual({
      operation: 'union',
      referenceIds: ['A', 'B', 'C'],
    });
  });

  it.each([
    'UNION(A)',
    'UNION(A, A)',
    'UNION(A, 1)',
    'A | A',
    'A & B',
    '',
  ])('rejects invalid profile set formula %s', (formula) => {
    expect(parseProfileSetFormula(formula)).toBeNull();
  });

  it('builds a readable formula', () => {
    expect(buildProfileSetFormula('intersection', ['A', 'B'])).toBe(
      'INTERSECTION(A, B)',
    );
  });
});
