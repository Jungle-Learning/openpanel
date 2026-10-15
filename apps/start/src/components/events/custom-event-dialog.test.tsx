// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CustomEventDialog } from './custom-event-dialog';

const api = vi.hoisted(() => ({ detail: vi.fn(), update: vi.fn() }));
const identity = {
  projectId: 'project',
  id: '57a989ff-38b8-46d2-8130-321da33ba3ba',
};
const queryKey = ['customEvent', identity];
const original = {
  ...identity,
  name: 'Active Action',
  description: 'Meaningful activity',
  operation: 'union' as const,
  eventNames: ['answer', 'signup'],
  createdAt: new Date('2026-08-17T21:37:15.691Z'),
  updatedAt: new Date('2026-10-15T16:50:28.978Z'),
};

vi.mock('@/hooks/use-app-params', () => ({
  useAppParams: () => ({
    organizationId: 'organization',
    projectId: 'project',
  }),
}));
vi.mock('@/components/ui/button', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/ui/button')>()),
  LinkButton: ({ children }: { children: ReactNode }) => (
    <a href="/events/custom">{children}</a>
  ),
}));
vi.mock('@/integrations/trpc/react', () => ({
  useTRPC: () => ({
    event: {
      customEvent: {
        queryKey: (input: unknown) => ['customEvent', input],
        queryFilter: (input: unknown) => ({ queryKey: ['customEvent', input] }),
        queryOptions: (input: unknown) => ({
          queryKey: ['customEvent', input],
          queryFn: () => api.detail(),
        }),
      },
      customEvents: { queryFilter: () => ({ queryKey: ['customEvents'] }) },
      updateCustomEvent: {
        mutationOptions: (options: object) => ({
          mutationFn: api.update,
          ...options,
        }),
      },
    },
    chart: {
      pathFilter: () => ({ queryKey: ['chart'] }),
      events: {
        queryOptions: () => ({
          queryKey: ['sources'],
          queryFn: async () => [],
        }),
      },
    },
  }),
}));

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  api.detail.mockResolvedValue(original);
});

it('keeps the draft revision through background refresh and adopts the latest revision only after reload', async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CustomEventDialog
        {...identity}
        customEventId={identity.id}
        defaultEditing
      >
        <button type="button">Edit test definition</button>
      </CustomEventDialog>
    </QueryClientProvider>
  );
  fireEvent.click(screen.getByRole('button', { name: 'Edit test definition' }));
  const name = await screen.findByLabelText('Name', { exact: true });
  fireEvent.change(name, { target: { value: 'My unsaved draft' } });
  const remote = {
    ...original,
    name: 'Saved by another editor',
    updatedAt: new Date('2026-10-15T17:00:00Z'),
  };
  api.detail.mockResolvedValue(remote);
  await act(async () => {
    await client.refetchQueries({ queryKey });
  });
  expect((name as HTMLInputElement).value).toBe('My unsaved draft');
  api.update.mockRejectedValueOnce(
    Object.assign(new Error('This custom event changed'), {
      data: { code: 'CONFLICT' },
    })
  );
  fireEvent.submit(name.closest('form')!);
  await screen.findByRole('alert');
  expect(api.update).toHaveBeenCalledWith(
    expect.objectContaining({
      name: 'My unsaved draft',
      updatedAt: original.updatedAt,
    }),
    expect.anything()
  );

  fireEvent.click(screen.getByRole('button', { name: 'Reload definition' }));
  await screen.findByRole('heading', { name: remote.name });
  fireEvent.click(screen.getByRole('button', { name: 'Edit definition' }));
  const reloaded = await screen.findByLabelText('Name', { exact: true });
  expect((reloaded as HTMLInputElement).value).toBe(remote.name);
  api.update.mockResolvedValueOnce(remote);
  fireEvent.submit(reloaded.closest('form')!);
  await waitFor(() =>
    expect(api.update).toHaveBeenLastCalledWith(
      expect.objectContaining({
        name: remote.name,
        updatedAt: remote.updatedAt,
      }),
      expect.anything()
    )
  );
  client.clear();
});
