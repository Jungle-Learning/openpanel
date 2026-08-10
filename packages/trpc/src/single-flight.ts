const inFlightRequests = new Map<string, Promise<unknown>>();

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
  request.then(
    () => {
      if (inFlightRequests.get(key) === request) {
        inFlightRequests.delete(key);
      }
    },
    () => {
      if (inFlightRequests.get(key) === request) {
        inFlightRequests.delete(key);
      }
    }
  );
  return request;
}
