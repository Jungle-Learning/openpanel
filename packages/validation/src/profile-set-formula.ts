import { alphabetIds } from '@openpanel/constants';

export const profileSetOperations = ['union', 'intersection'] as const;
export type ProfileSetOperation = (typeof profileSetOperations)[number];

export interface ParsedProfileSetFormula {
  operation: ProfileSetOperation;
  referenceIds: string[];
}

const PROFILE_SET_FUNCTION_PATTERN =
  /^\s*(UNION|INTERSECTION)\s*\(\s*([A-Z](?:\s*,\s*[A-Z])+)\s*\)\s*$/i;
const LEGACY_UNION_PATTERN = /^\s*([A-Z](?:\s*\|\s*[A-Z])+)\s*$/i;

export function parseProfileSetFormula(
  formula: string,
): ParsedProfileSetFormula | null {
  const functionMatch = PROFILE_SET_FUNCTION_PATTERN.exec(formula);
  const legacyUnionMatch = LEGACY_UNION_PATTERN.exec(formula);
  if (!(functionMatch || legacyUnionMatch)) {
    return null;
  }

  // Older reports commonly used A | B expecting a set union. mathjs treats
  // that operator as a bitwise OR of the two aggregate numbers, which can
  // look plausible while being completely unrelated to unique profiles.
  // Preserve those reports as a backwards-compatible union alias.
  const operation = functionMatch
    ? (functionMatch[1]?.toLowerCase() as ProfileSetOperation)
    : 'union';
  const referenceList = functionMatch?.[2] ?? legacyUnionMatch?.[1] ?? '';
  const referenceIds = referenceList
    .split(functionMatch ? ',' : '|')
    .map((referenceId) => referenceId.trim().toUpperCase());
  const uniqueReferenceIds = [...new Set(referenceIds)];

  if (
    uniqueReferenceIds.length < 2 ||
    uniqueReferenceIds.length !== referenceIds.length ||
    uniqueReferenceIds.some(
      (referenceId) =>
        !alphabetIds.includes(referenceId as (typeof alphabetIds)[number]),
    )
  ) {
    return null;
  }

  return { operation, referenceIds: uniqueReferenceIds };
}

export function buildProfileSetFormula(
  operation: ProfileSetOperation,
  referenceIds: string[],
): string {
  return `${operation.toUpperCase()}(${referenceIds.join(', ')})`;
}
