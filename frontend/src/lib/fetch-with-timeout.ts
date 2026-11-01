const DEFAULT_TIMEOUT_MS = 15_000;

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const externalSignal = init.signal;
  const propagateAbort = () => controller.abort(externalSignal?.reason);

  if (externalSignal?.aborted) {
    propagateAbort();
  } else {
    externalSignal?.addEventListener("abort", propagateAbort, { once: true });
  }

  const timer = globalThis.setTimeout(() => {
    controller.abort(new DOMException(
      "The request timed out. Please try again.",
      "TimeoutError",
    ));
  }, timeoutMs);

  try {
    // Same-origin requests otherwise include the full page URL in Referer.
    // The API may use that header when an explicit attribution value is empty.
    return await fetch(input, { referrerPolicy: "strict-origin", ...init, signal: controller.signal });
  } finally {
    globalThis.clearTimeout(timer);
    externalSignal?.removeEventListener("abort", propagateAbort);
  }
}
