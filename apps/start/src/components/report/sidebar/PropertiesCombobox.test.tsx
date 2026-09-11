// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PropertiesCombobox } from './PropertiesCombobox';

const api = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock('@/hooks/use-app-params', () => ({
  useAppParams: () => ({ projectId: 'project' }),
}));
vi.mock('@/hooks/use-profile-properties', () => ({
  useProfileProperties: () => [],
}));
vi.mock('@/integrations/trpc/react', () => ({
  useTRPC: () => ({
    chart: {
      properties: {
        queryOptions: (input: unknown, options: object) => ({
          queryKey: ['properties', input],
          queryFn: () => api.fetch(input),
          ...options,
        }),
      },
    },
    group: {
      properties: {
        queryOptions: () => ({
          queryKey: ['groups'],
          queryFn: async () => [],
          enabled: false,
        }),
      },
    },
  }),
}));

function mount(events = ['voice session ended']) {
  const select = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PropertiesCombobox
        events={events}
        categories={['event']}
        onSelect={select}
      >
        {(setOpen) => (
          <button type="button" onClick={() => setOpen(true)}>
            Select breakdown
          </button>
        )}
      </PropertiesCombobox>
    </QueryClientProvider>
  );
  fireEvent.click(screen.getByRole('button', { name: 'Select breakdown' }));
  return { select, client };
}

afterEach(cleanup);
beforeEach(() => {
  api.fetch.mockReset();
});

describe('event property menu', () => {
  it('loads selected-event fields, puts defaults last, and preserves selection keys', async () => {
    api.fetch.mockResolvedValue([
      'os',
      'properties.app_version',
      'properties.durationSeconds',
      'city',
    ]);
    const { select } = mount();
    await screen.findByText('durationSeconds');
    expect(api.fetch).toHaveBeenCalledWith({
      projectId: 'project',
      event: 'voice session ended',
    });
    const text = screen.getByRole('menu').textContent ?? '';
    expect(text.indexOf('Event properties')).toBeLessThan(
      text.indexOf('Shared context')
    );
    expect(text.indexOf('Shared context')).toBeLessThan(
      text.indexOf('Default properties')
    );
    fireEvent.click(screen.getByText('durationSeconds'));
    expect(select).toHaveBeenCalledWith(
      expect.objectContaining({ value: 'properties.durationSeconds' })
    );
  });
  it('unions the report events and searches default fields without duplicate sections', async () => {
    api.fetch.mockImplementation(async ({ event }) =>
      event === 'a'
        ? ['city', 'properties.durationSeconds']
        : ['city', 'properties.endReason']
    );
    mount(['a', 'b']);
    await screen.findByText('endReason');
    expect(screen.getAllByText('city')).toHaveLength(1);
    fireEvent.change(screen.getByPlaceholderText('Search'), {
      target: { value: 'city' },
    });
    expect(screen.queryByText('durationSeconds')).toBeNull();
    expect(screen.queryByText('Event properties')).toBeNull();
    expect(screen.queryByText('Default properties')).not.toBeNull();
  });
  it('lets keyboard users move from search to a result and select it', async () => {
    api.fetch.mockResolvedValue(['city', 'properties.endReason']);
    const { select } = mount();
    await screen.findByText('endReason');
    const search = screen.getByPlaceholderText('Search');
    fireEvent.change(search, { target: { value: 'city' } });
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    const city = screen.getByRole('menuitem', { name: 'city' });
    expect(document.activeElement).toBe(city);
    fireEvent.keyDown(city, { key: 'Enter' });
    expect(select).toHaveBeenCalledWith(
      expect.objectContaining({ value: 'city' })
    );
  });

  it('distinguishes loading and failure from an empty list, and retries', async () => {
    let reject!: (error: Error) => void;
    api.fetch.mockImplementationOnce(
      () =>
        new Promise((_, fail) => {
          reject = fail;
        })
    );
    mount();
    expect(screen.queryByText('Loading properties…')).not.toBeNull();
    reject(new Error('unavailable'));
    await screen.findByRole('alert');
    expect(screen.queryByText('No properties found')).toBeNull();
    api.fetch.mockResolvedValue(['properties.endReason']);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('endReason');
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});
