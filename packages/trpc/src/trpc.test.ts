import { describe, expect, it, vi } from 'vitest';
import { runSingleFlight } from './single-flight';

describe('runSingleFlight', () => {
  it('shares one in-flight request for the same key', async () => {
    let resolveRequest!: (value: string) => void;
    const createRequest = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          resolveRequest = resolve;
        })
    );

    const first = runSingleFlight('same-key', createRequest);
    const second = runSingleFlight('same-key', createRequest);
    await Promise.resolve();

    expect(createRequest).toHaveBeenCalledTimes(1);
    resolveRequest('done');
    await expect(Promise.all([first, second])).resolves.toEqual([
      'done',
      'done',
    ]);
  });

  it('does not coalesce different keys', async () => {
    const createRequest = vi.fn(async () => 'done');

    await Promise.all([
      runSingleFlight('first-key', createRequest),
      runSingleFlight('second-key', createRequest),
    ]);

    expect(createRequest).toHaveBeenCalledTimes(2);
  });

  it('clears a rejected request so a retry can run', async () => {
    const failedRequest = runSingleFlight('retry-key', () =>
      Promise.reject(new Error('failed'))
    );
    await expect(failedRequest).rejects.toThrow('failed');

    await expect(
      runSingleFlight('retry-key', () => Promise.resolve('recovered'))
    ).resolves.toBe('recovered');
  });
});
