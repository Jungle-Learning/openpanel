// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DateRangerPicker from './date-ranger-picker';

vi.mock('.', () => ({ popModal: vi.fn() }));
vi.mock('./Modal/Container', () => ({
  ModalContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}));

describe('DateRangerPicker', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 7, 18, 12));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('navigates the start and end calendars independently', () => {
    render(<DateRangerPicker onChange={vi.fn()} />);

    let yearDropdowns = screen.getAllByLabelText('Choose the Year');
    expect(yearDropdowns).toHaveLength(2);
    expect((yearDropdowns[0] as HTMLSelectElement).value).toBe('2026');
    expect((yearDropdowns[1] as HTMLSelectElement).value).toBe('2026');

    fireEvent.change(yearDropdowns[0], { target: { value: '2025' } });

    yearDropdowns = screen.getAllByLabelText('Choose the Year');
    expect((yearDropdowns[0] as HTMLSelectElement).value).toBe('2025');
    expect((yearDropdowns[1] as HTMLSelectElement).value).toBe('2026');
    expect(screen.queryByText('July 2025')).not.toBeNull();
    expect(screen.queryByText('August 2026')).not.toBeNull();

    const monthDropdowns = screen.getAllByLabelText('Choose the Month');
    fireEvent.change(monthDropdowns[1], { target: { value: '6' } });

    expect(
      (screen.getAllByLabelText('Choose the Month')[1] as HTMLSelectElement)
        .value
    ).toBe('6');
    expect(screen.queryByText('July 2026')).not.toBeNull();
  });

  it('shows the selected range in month/day/year order', () => {
    render(
      <DateRangerPicker
        endDate={new Date(2026, 7, 18)}
        onChange={vi.fn()}
        startDate={new Date(2025, 6, 4)}
      />
    );

    expect(
      screen.queryByRole('button', {
        name: 'Select 07/04/2025 - 08/18/2026',
      })
    ).not.toBeNull();
  });
});
