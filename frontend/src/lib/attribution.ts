import { apiBase } from "@/lib/data";
import { storageGet, storageRemove, storageSet } from "@/lib/browser-storage";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";
import { trackingPagePath, trackingPageUrl, trackingReferrerOrigin } from "@/lib/tracking-url";

const ATTRIBUTION_KEY = "axleforge-attribution";
const CONSENT_KEY = "axleforge-cookie-consent";

export type Attribution = {
  session_id: string;
  first_touch_source: string;
  last_touch_source: string;
  utm_source: string;
  utm_medium: string;
  utm_campaign: string;
  utm_term: string;
  utm_content: string;
  referrer_url: string;
  first_landing_page: string;
  last_landing_page: string;
};

const clean = (value: string | null, limit = 500) => (value ?? "").trim().slice(0, limit);
const storedText = (
  value: unknown,
  limit: number,
) => typeof value === "string" ? clean(value, limit) : "";

function externalReferrer(referrer: string, currentOrigin: string) {
  if (!referrer) return "";
  try {
    const parsed = new URL(referrer);
    return parsed.origin === currentOrigin ? "" : trackingReferrerOrigin(referrer);
  } catch {
    return "";
  }
}

function sourceFrom(params: URLSearchParams, referrer: string, currentOrigin: string) {
  const campaignSource = clean(params.get("utm_source"), 120);
  if (campaignSource) return campaignSource;
  const external = externalReferrer(referrer, currentOrigin);
  if (!external) return "direct";
  try {
    return new URL(external).hostname.replace(/^www\./, "").slice(0, 120) || "referral";
  } catch {
    return "referral";
  }
}

export function captureAttribution(): Attribution | undefined {
  if (typeof window === "undefined") return undefined;
  const params = new URLSearchParams(window.location.search);
  const path = trackingPagePath(window.location.href);
  let previous: Partial<Attribution> = {};
  try {
    const parsed = JSON.parse(storageGet("sessionStorage", ATTRIBUTION_KEY) ?? "{}") as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      previous = parsed as Partial<Attribution>;
    }
  } catch {
    storageRemove("sessionStorage", ATTRIBUTION_KEY);
  }
  const referrer = externalReferrer(document.referrer, window.location.origin);
  const source = sourceFrom(params, document.referrer, window.location.origin);
  const previousSessionId = storedText(previous.session_id, 100);
  const next: Attribution = {
    session_id: /^[A-Za-z0-9._:-]{8,100}$/.test(previousSessionId)
      ? previousSessionId
      : crypto.randomUUID(),
    first_touch_source: storedText(previous.first_touch_source, 120) || source,
    last_touch_source: source === "direct" && storedText(previous.last_touch_source, 120)
      ? storedText(previous.last_touch_source, 120)
      : source,
    utm_source: clean(params.get("utm_source"), 120) || storedText(previous.utm_source, 120),
    utm_medium: clean(params.get("utm_medium"), 120) || storedText(previous.utm_medium, 120),
    utm_campaign: clean(params.get("utm_campaign"), 150) || storedText(previous.utm_campaign, 150),
    utm_term: clean(params.get("utm_term"), 150) || storedText(previous.utm_term, 150),
    utm_content: clean(params.get("utm_content"), 150) || storedText(previous.utm_content, 150),
    referrer_url: trackingReferrerOrigin(storedText(previous.referrer_url, 1000)) || referrer,
    first_landing_page: trackingPagePath(storedText(previous.first_landing_page, 500)) || path,
    last_landing_page: path,
  };
  storageSet("sessionStorage", ATTRIBUTION_KEY, JSON.stringify(next));
  return next;
}

export function addAttributionToForm(form: FormData) {
  if (typeof window === "undefined") return;
  form.set("source_url", trackingPageUrl(window.location.href));
  const attribution = captureAttribution();
  if (!attribution) return;
  for (const [key, value] of Object.entries(attribution)) {
    if (value) form.set(key, value);
  }
}

export function hasAnalyticsConsent() {
  return typeof window !== "undefined"
    && storageGet("localStorage", CONSENT_KEY) === "accepted";
}

export function recordPageView(title: string) {
  if (!hasAnalyticsConsent()) return;
  const attribution = captureAttribution();
  if (!attribution) return;
  const path = window.location.pathname.slice(0, 500);
  void fetchWithTimeout(`${apiBase}/visit-sessions/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...attribution, consent_status: "accepted" }),
    keepalive: true,
  }, 10_000).then((response) => {
    // Consent can be withdrawn while the session request is still in flight.
    // Do not start a new optional request after that choice has changed.
    if (!response.ok || !hasAnalyticsConsent()) return;
    return fetchWithTimeout(`${apiBase}/page-views/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: attribution.session_id,
        path,
        page_title: title.slice(0, 255),
      }),
      keepalive: true,
    }, 10_000);
  }).catch(() => undefined);
}
