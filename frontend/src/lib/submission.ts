import { storageGet, storageRemove, storageSet } from "@/lib/browser-storage";

const TRANSIENT_FIELDS = new Set([
  "turnstile_token", "cf-turnstile-response", "session_id", "source_url",
  "referrer_url", "first_touch_source", "last_touch_source", "utm_source",
  "utm_medium", "utm_campaign", "utm_term", "utm_content",
  "first_landing_page", "last_landing_page",
]);
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const storageKey = (scope: string) => `axleforge-submission:${scope}`;

async function checksum(value: BufferSource): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", value);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function getSubmissionKey(scope: string, form: FormData): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error("Secure form submission is unavailable. Reload this page over HTTPS and try again.");
  }
  const entries: [string, unknown][] = [];
  for (const [name, value] of form.entries()) {
    if (TRANSIENT_FIELDS.has(name)) continue;
    entries.push([name, typeof value === "string" ? value : {
      name: value.name,
      size: value.size,
      type: value.type,
      checksum: await checksum(await value.arrayBuffer()),
    }]);
  }
  entries.sort(([left], [right]) => left.localeCompare(right, "en"));
  const fingerprint = await checksum(new TextEncoder().encode(JSON.stringify(entries)));
  try {
    const stored = JSON.parse(storageGet("sessionStorage", storageKey(scope)) ?? "null");
    if (stored?.fingerprint === fingerprint && typeof stored.key === "string" && UUID_V4.test(stored.key)) {
      return stored.key;
    }
  } catch {
    // Replace an invalid browser value with a fresh identifier.
  }
  const key = crypto.randomUUID();
  storageSet("sessionStorage", storageKey(scope), JSON.stringify({ key, fingerprint }));
  return key;
}

export function clearSubmissionKey(scope: string, key: string): void {
  try {
    const stored = JSON.parse(storageGet("sessionStorage", storageKey(scope)) ?? "null");
    if (stored?.key === key) storageRemove("sessionStorage", storageKey(scope));
  } catch {
    storageRemove("sessionStorage", storageKey(scope));
  }
}
