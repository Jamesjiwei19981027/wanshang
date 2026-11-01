import { storageGet, storageRemove, storageSet } from "@/lib/browser-storage";

export const CONSENT_KEY = "axleforge-cookie-consent";
export const CONSENT_EVENT = "axleforge-cookie-change";
const ATTRIBUTION_KEY = "axleforge-attribution";

type ConsentValue = "essential" | "accepted";

declare global {
  interface Window {
    dataLayer?: unknown[];
  }
}

const googleConsent = (granted: boolean) => ({
  analytics_storage: granted ? "granted" : "denied",
  ad_storage: "denied",
  ad_user_data: "denied",
  ad_personalization: "denied",
});

function googleTagCommand(...command: unknown[]) {
  window.dataLayer = window.dataLayer || [];
  // Google's gtag command queue consumes an Arguments object, as in its
  // documented function gtag(){dataLayer.push(arguments)} implementation.
  if (command.length) window.dataLayer.push(arguments);
}

export function updateGoogleConsent(granted: boolean) {
  googleTagCommand("consent", "update", googleConsent(granted));
  if (!granted) storageRemove("sessionStorage", ATTRIBUTION_KEY);
}

export function clearAnalyticsCookies() {
  const analyticsCookie = /^(?:__Secure-|__Host-)?(?:_ga|_gid|_gat)(?:_|$)/;
  for (const cookie of document.cookie.split(";")) {
    const name = cookie.split("=", 1)[0]?.trim();
    if (!name || !analyticsCookie.test(name)) continue;
    const attributes = "; Max-Age=0; Path=/; SameSite=Lax; Secure";
    document.cookie = `${name}=${attributes}`;
    // GA defaults to a parent-domain cookie (example.com when the page is
    // www.example.com). Deleting only the current hostname leaves it alive.
    // Browsers reject public-suffix domains, so no suffix list is required.
    if (!name.startsWith("__Host-")) {
      const labels = window.location.hostname.split(".");
      for (let index = 0; index < labels.length - 1; index += 1) {
        document.cookie = `${name}=${attributes}; Domain=${labels.slice(index).join(".")}`;
      }
    }
  }
}

export function setConsent(value: ConsentValue) {
  const previous = storageGet("localStorage", CONSENT_KEY);
  storageSet("localStorage", CONSENT_KEY, value);
  updateGoogleConsent(value === "accepted");
  if (value === "essential") clearAnalyticsCookies();
  window.dispatchEvent(new Event(CONSENT_EVENT));
  return previous;
}
