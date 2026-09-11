// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { NuqsTestingAdapter, type UrlUpdateEvent } from 'nuqs/adapters/testing';
import { StrictMode, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DashboardTimePreferences,
  dashboardTimeStorageKey,
  parseSavedDashboardTime,
} from './dashboard-time-preferences';
import { useDashboardOptions } from './use-dashboard-options';

vi.mock('@/components/full-page-loading-state', () => ({
  default: () => <div>Restoring</div>,
}));
const key = dashboardTimeStorageKey('user', 'org', 'project', 'dashboard');
const saved = { start: null, end: null, range: '30d', overrideInterval: 'day' };

function Controls() {
  const options = useDashboardOptions();
  return (
    <>
      <output data-testid="selection">
        {JSON.stringify({
          range: options.range,
          start: options.startDate,
          end: options.endDate,
          interval: options.interval,
        })}
      </output>
      <button onClick={() => options.setRange('7d')}>Last week</button>
      <button onClick={() => options.setInterval('hour')}>Hourly</button>
      <button onClick={() => options.setRange(null)}>Default</button>
    </>
  );
}

function mount(storageKey = key, searchParams = '') {
  window.history.replaceState({}, '', `/${searchParams}`);
  const onUrlUpdate = vi.fn();
  function TestRouter() {
    const [search, setSearch] = useState(searchParams);
    return (
      <NuqsTestingAdapter
        searchParams={search}
        onUrlUpdate={(event: UrlUpdateEvent) => {
          onUrlUpdate(event);
          const nextSearch = event.searchParams.size
            ? `?${event.searchParams}`
            : '';
          window.history.replaceState({}, '', `/${nextSearch}`);
          setSearch(nextSearch);
        }}
      >
        <StrictMode>
          <DashboardTimePreferences key={storageKey} storageKey={storageKey}>
            <Controls />
          </DashboardTimePreferences>
        </StrictMode>
      </NuqsTestingAdapter>
    );
  }
  const view = render(<TestRouter />);
  return { ...view, onUrlUpdate };
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, '', '/');
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('dashboard time preferences', () => {
  it('restores a saved time period and scale before mounting reports', async () => {
    localStorage.setItem(key, JSON.stringify(saved));
    const view = mount();
    await waitFor(() =>
      expect(screen.getByTestId('selection').textContent).toContain(
        '"range":"30d"'
      )
    );
    expect(screen.getByTestId('selection').textContent).toContain(
      '"interval":"day"'
    );
    expect(view.onUrlUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        options: expect.objectContaining({ history: 'replace' }),
      })
    );
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual(saved);
  });

  it('honors explicit linked settings and remembers subsequent selections and Default', async () => {
    localStorage.setItem(key, JSON.stringify(saved));
    mount(key, '?range=7d&overrideInterval=hour');
    await waitFor(() =>
      expect(screen.getByTestId('selection').textContent).toContain(
        '"range":"7d"'
      )
    );
    expect(screen.getByTestId('selection').textContent).toContain(
      '"interval":"hour"'
    );
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(key)!).range).toBe('7d')
    );
    await act(async () => screen.getByText('Default').click());
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(key)!)).toEqual({
        start: null,
        end: null,
        range: null,
        overrideInterval: null,
      })
    );
    cleanup();
    mount();
    await waitFor(() =>
      expect(screen.getByTestId('selection').textContent).toContain(
        '"range":null'
      )
    );
  });

  it('saves changed scale and range independently per dashboard and user', async () => {
    mount();
    await act(async () => screen.getByText('Last week').click());
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(key)!).range).toBe('7d')
    );
    await act(async () => screen.getByText('Hourly').click());
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(key)!)).toEqual({
        ...saved,
        range: '7d',
        overrideInterval: 'hour',
      })
    );
    cleanup();
    const other = dashboardTimeStorageKey('user', 'org', 'project', 'other');
    mount(other);
    await waitFor(() =>
      expect(screen.getByTestId('selection').textContent).toContain(
        '"range":null'
      )
    );
    expect(
      dashboardTimeStorageKey('another-user', 'org', 'project', 'dashboard')
    ).not.toBe(key);
    expect(JSON.parse(localStorage.getItem(key)!).overrideInterval).toBe(
      'hour'
    );
  });

  it('restores custom dates and tolerates disabled storage', async () => {
    localStorage.setItem(
      key,
      JSON.stringify({
        ...saved,
        range: 'custom',
        start: '2026-09-01 00:00:00',
        end: '2026-09-10 23:59:59',
      })
    );
    mount();
    await waitFor(() =>
      expect(screen.getByTestId('selection').textContent).toContain(
        '2026-09-10'
      )
    );
    cleanup();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    mount();
    await waitFor(() =>
      expect(screen.getByTestId('selection').textContent).toContain(
        '"range":null'
      )
    );
  });

  it.each([
    'bad json',
    '{}',
    JSON.stringify({ ...saved, range: 'unknown' }),
    JSON.stringify({ ...saved, range: 'custom' }),
    JSON.stringify({ ...saved, start: 'not a date', end: '2026-09-11' }),
  ])('rejects invalid saved state %s', (raw) => {
    expect(parseSavedDashboardTime(raw)).toBeNull();
  });
});
