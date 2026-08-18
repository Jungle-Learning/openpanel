import type { FastifyRequest } from 'fastify';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const verifyPassword = vi.fn();
const getClientByIdCached = vi.fn();
const getCache = vi.fn();

vi.mock('@openpanel/common/server', () => ({
  verifyPassword: (...args: unknown[]) => verifyPassword(...args),
}));
vi.mock('@openpanel/db', () => ({
  ClientType: { write: 'write' },
  getClientByIdCached: (...args: unknown[]) => getClientByIdCached(...args),
}));
vi.mock('@openpanel/redis', () => ({
  getCache: (...args: unknown[]) => getCache(...args),
}));

const { validateSdkRequest } = await import('./auth');

const clientId = '123e4567-e89b-42d3-a456-426614174000';

function makeClient() {
  return {
    id: clientId,
    secret: 'hashed-secret',
    ignoreCorsAndSecret: false,
    project: {
      id: 'project-1',
      filters: [],
      cors: ['app.example.com'],
      allowUnsafeRevenueTracking: false,
    },
  };
}

function makeRequest(clientSecret: string, origin?: string) {
  return {
    headers: {
      'openpanel-client-id': clientId,
      'openpanel-client-secret': clientSecret,
      origin,
    },
    clientIp: '203.0.113.10',
    body: {
      type: 'track',
      payload: { name: 'screen_view', properties: {} },
    },
  } as unknown as FastifyRequest;
}

beforeEach(() => {
  verifyPassword.mockReset();
  getClientByIdCached.mockReset();
  getCache.mockReset();
  getClientByIdCached.mockResolvedValue(makeClient());
});

describe('validateSdkRequest client-secret authentication', () => {
  it('does not mark an origin-authenticated request when its supplied secret is invalid', async () => {
    getCache.mockResolvedValue(false);
    const request = makeRequest('forged-secret', 'https://app.example.com');

    await expect(validateSdkRequest(request as never)).resolves.toMatchObject({
      id: clientId,
    });
    expect(request.clientSecretAuth).toBeUndefined();
  });

  it('marks a request only after its client secret verifies', async () => {
    getCache.mockResolvedValue(true);
    const request = makeRequest('valid-secret');

    await expect(validateSdkRequest(request as never)).resolves.toMatchObject({
      id: clientId,
    });
    expect(request.clientSecretAuth).toBe(true);
  });

  it('rejects revenue on an origin-authenticated request with a forged secret', async () => {
    getCache.mockResolvedValue(false);
    const request = makeRequest('forged-secret', 'https://app.example.com');
    request.body = {
      type: 'track',
      payload: {
        name: 'subscription payment received',
        properties: { __revenue: 10 },
      },
    } as never;

    await expect(validateSdkRequest(request as never)).rejects.toThrow(
      'Revenue tracking is not allowed without a client secret'
    );
    expect(request.clientSecretAuth).toBeUndefined();
  });
});
