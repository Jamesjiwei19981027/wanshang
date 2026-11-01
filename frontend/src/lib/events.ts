import { apiBase } from "@/lib/data";
import { captureAttribution, hasAnalyticsConsent } from "@/lib/attribution";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";

type EventName =
  | "page_view"
  | "view_product"
  | "search_product"
  | "use_product_filter"
  | "add_to_inquiry_list"
  | "remove_from_inquiry_list"
  | "download_document"
  | "start_inquiry"
  | "submit_inquiry"
  | "submit_contact"
  | "click_whatsapp"
  | "click_email"
  | "click_phone";

export function trackEvent(eventName: EventName, metadata: Record<string, string | number | boolean> = {}) {
  if (typeof window === "undefined" || !hasAnalyticsConsent()) return;
  const attribution = captureAttribution();
  void fetchWithTimeout(`${apiBase}/events/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_name: eventName, path: window.location.pathname, session_id: attribution?.session_id ?? "", metadata }),
    keepalive: true,
  }, 10_000).catch(() => undefined);
}
