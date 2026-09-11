import { describe, expect, it } from 'vitest';
import {
  formatTooltipNumber,
  useNumber,
  useTooltipNumber,
} from './use-numer-formatter';

describe('chart tooltip precision', () => {
  it.each([
    [0, '0'],
    [1, '1'],
    [1.2, '1.2'],
    [7.456, '7.46'],
    [9.999, '10'],
    [10, '10'],
    [12.7, '13'],
    [1234.56, '1,235'],
    [-7.456, '-7.46'],
    [-12.7, '-13'],
    [null, 'N/A'],
    [undefined, 'N/A'],
  ])('formats %s as %s', (value, expected) => {
    expect(formatTooltipNumber(value)).toBe(expected);
  });

  it('uses displayed percentage values to choose precision', () => {
    const number = useTooltipNumber();
    expect(number.formatWithUnit(0.07456, '%')).toBe('7.46 %');
    expect(number.formatWithUnit(0.127, '%')).toBe('13 %');
    expect(number.formatWithUnit(-0.127, '%')).toBe('-13 %');
  });

  it('preserves unit suffixes, missing values, and duration formatting', () => {
    const number = useTooltipNumber();
    expect(number.formatWithUnit(7.456, 'USD')).toBe('7.46 USD');
    expect(number.formatWithUnit(12.7, 'USD')).toBe('13 USD');
    expect(number.formatWithUnit(null, '%')).toBe('N/A');
    expect(number.formatWithUnit(125, 'min')).toBe('2m 5s');
  });

  it('leaves non-tooltip number formatting unchanged', () => {
    expect(useNumber().format(1234.56)).toBe('1,234.56');
  });
});
