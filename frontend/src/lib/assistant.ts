import { apiBase } from "@/lib/data";
import { storageGet, storageSet } from "@/lib/browser-storage";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";

export type AssistantConfig = {
  enabled: boolean;
  welcome: string;
  privacy_notice: string;
  max_question_chars: number;
  quick_prompts: Record<string, string[]>;
};

export type RfqDraft = {
  name?: string;
  email?: string;
  phone?: string;
  company?: string;
  country?: string;
  destination_port?: string;
  message?: string;
  quantity?: number;
  application?: string;
  requested_categories?: string[];
  products?: { id: number; slug: string; title: string; model: string; sku: string; category: string }[];
  specifications?: Record<string, Record<string, string>>;
};

export type Citation = { title: string; url: string };

export type AssistantEvent =
  | { type: "conversation"; id: string }
  | { type: "delta"; text: string }
  | { type: "lookup"; tool: string }
  | { type: "rfq_draft"; draft: RfqDraft }
  | { type: "error"; code: string; message: string; partial?: boolean }
  | { type: "done"; message_id: number; citations: Citation[]; remaining: number };

const VISITOR_KEY = "wanshang-assistant-visitor";

/** A random, per-browser identifier for daily question limits; not personal data. */
export function visitorId(): string {
  const stored = storageGet("localStorage", VISITOR_KEY);
  if (stored && /^[A-Za-z0-9_-]{16,64}$/.test(stored)) return stored;
  const created = crypto.randomUUID().replaceAll("-", "");
  storageSet("localStorage", VISITOR_KEY, created);
  return created;
}

export function pageType(pathname: string): string {
  if (pathname === "/") return "home";
  if (/^\/products\/[^/]+\/[^/]+\/?$/.test(pathname)) return "product";
  if (pathname.startsWith("/products")) return "products";
  if (pathname.startsWith("/request-a-quote")) return "rfq";
  return "other";
}

export async function loadAssistantConfig(signal?: AbortSignal): Promise<AssistantConfig | null> {
  try {
    const response = await fetchWithTimeout(`${apiBase}/assistant/config/`, { signal, cache: "no-store" }, 8_000);
    if (!response.ok) return null;
    const payload = await response.json() as Partial<AssistantConfig>;
    if (typeof payload.enabled !== "boolean") return null;
    return {
      enabled: payload.enabled,
      welcome: String(payload.welcome ?? ""),
      privacy_notice: String(payload.privacy_notice ?? ""),
      max_question_chars: Number(payload.max_question_chars) || 500,
      quick_prompts: payload.quick_prompts && typeof payload.quick_prompts === "object" ? payload.quick_prompts : {},
    };
  } catch {
    return null;
  }
}

/** Send one question and hand each streamed event to onEvent. */
export async function askAssistant(
  body: { message: string; conversation_id?: string; page: string },
  onEvent: (event: AssistantEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${apiBase}/assistant/chat/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, visitor_id: visitorId() }),
    signal,
    referrerPolicy: "strict-origin-when-cross-origin",
  });
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({})) as { code?: string; message?: string };
    onEvent({ type: "error", code: payload.code ?? "unavailable", message: payload.message ?? "The assistant is not available right now. Please use the RFQ form or contact our sales team." });
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (block.startsWith("data: ")) {
        try {
          onEvent(JSON.parse(block.slice(6)) as AssistantEvent);
        } catch {
          // Ignore a malformed block; the stream continues.
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

/** Only same-site paths and the document download endpoint are linked. */
export function safeCitationHref(url: string): string {
  if (/^\/(?!\/)[A-Za-z0-9/_.~%-]*$/.test(url)) {
    if (url.startsWith("/api/")) return `${apiBase.replace(/\/api\/?$/, "")}${url}`;
    return url;
  }
  return "";
}
