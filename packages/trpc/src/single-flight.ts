const inFlightRequests = new Map<string, Promise<unknown>>();

/**
 * Share one promise while an identical request is running in this process.
 * This is intentionally not a value cache: success and failure both remove
 * the key so Redis remains the source of truth after the request completes.
 */
export function runSingleFlight<T>(
  key: string,
  createRequest: () => Promise<T>
): Promise<T> {
  const existingRequest = inFlightRequests.get(key);
  if (existingRequest) {
    return existingRequest as Promise<T>;
  }

  const request = Promise.resolve().then(createRequest);
  inFlightRequests.set(key, request);

  const clearRequest = () => {
    if (inFlightRequests.get(key) === request) {
      inFlightRequests.delete(key);
    }
  };
  request.then(clearRequest, clearRequest);

  return request;
}
