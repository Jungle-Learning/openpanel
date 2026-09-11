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

const propertyApiMock = vi.hoisted(() => ({
  fetch: vi.fn(),
  customEvents: vi.fn(),
}));
vi.mock('@/hooks/use-app-params', () => ({
  useAppParams: () => ({ projectId: 'project' }),
}));
vi.mock('@/hooks/use-profile-properties', () => ({
  useProfileProperties: () => [],
}));
vi.mock('@/integrations/trpc/react', () => ({
  useTRPC: () => ({
    event: {
      customEvents: {
        queryOptions: (_input: unknown, options: object) => ({
          queryKey: ['customEvents'],
          queryFn: () => propertyApiMock.customEvents(),
          ...options,
        }),
      },
    },
    chart: {
      properties: {
        queryOptions: (input: unknown, options: object) => ({
          queryKey: ['properties', input],
          queryFn: () => propertyApiMock.fetch(input),
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

function mount(
  events = ['voice session ended'],
  customEventIds: string[] = []
) {
  const select = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PropertiesCombobox
        events={events}
        customEventIds={customEventIds}
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
  propertyApiMock.fetch.mockReset();
  propertyApiMock.customEvents.mockReset();
});

describe('event property menu', () => {
  it('loads selected-event fields, puts defaults last, and preserves selection keys', async () => {
    propertyApiMock.fetch.mockResolvedValue([
      'os',
      'properties.app_version',
      'properties.durationSeconds',
      'city',
    ]);
    const { select } = mount();
    await screen.findByText('durationSeconds');
    expect(propertyApiMock.fetch).toHaveBeenCalledWith({
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
    propertyApiMock.fetch.mockImplementation(async ({ event }) =>
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
    propertyApiMock.fetch.mockResolvedValue(['city', 'properties.endReason']);
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

  it('resolves a saved custom event by ID before fetching source fields', async () => {
    propertyApiMock.customEvents.mockResolvedValue([
      { id: 'custom-1', eventNames: ['source-a', 'source-b'] },
    ]);
    propertyApiMock.fetch.mockResolvedValue(['properties.sourceField']);
    mount([], ['custom-1']);
    await screen.findByText('sourceField');
    expect(
      propertyApiMock.fetch.mock.calls.map(([input]) => input.event).sort()
    ).toEqual(['source-a', 'source-b']);
  });

  it('shows an error for missing custom events instead of querying their display name', async () => {
    propertyApiMock.customEvents.mockResolvedValue([]);
    mount([], ['deleted-custom']);
    await screen.findByRole('alert');
    expect(propertyApiMock.fetch).not.toHaveBeenCalled();
  });

  it('keeps a loading indicator while another selected event is pending', async () => {
    let resolveSecond!: (properties: string[]) => void;
    propertyApiMock.fetch.mockImplementation(({ event }) =>
      event === 'a'
        ? Promise.resolve(['properties.firstField'])
        : new Promise((resolve) => {
            resolveSecond = resolve;
          })
    );
    mount(['a', 'b']);
    await screen.findByText('firstField');
    expect(screen.queryByText('Loading more properties…')).not.toBeNull();
    resolveSecond(['properties.secondField']);
    await screen.findByText('secondField');
    expect(screen.queryByText('Loading more properties…')).toBeNull();
  });

  it('mounts offscreen results for keyboard navigation and expands long lists', async () => {
    propertyApiMock.fetch.mockResolvedValue(
      Array.from(
        { length: 150 },
        (_, i) => `properties.field${String(i).padStart(3, '0')}`
      )
    );
    mount();
    await screen.findByText('field098');
    expect(screen.queryByText('field149')).toBeNull();
    fireEvent.click(screen.getByText(/Show more properties/));
    await screen.findByText('field149');
    await waitFor(() =>
      expect(document.activeElement?.textContent).toBe('field099')
    );
  });

  it('distinguishes loading and failure from an empty list, and retries', async () => {
    let reject!: (error: Error) => void;
    propertyApiMock.fetch.mockImplementationOnce(
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
    propertyApiMock.fetch.mockResolvedValue(['properties.endReason']);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('endReason');
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});
