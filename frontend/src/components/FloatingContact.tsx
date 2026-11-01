"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { trackEvent } from "@/lib/events";
import { useSiteConfig } from "@/components/SiteConfigProvider";
import { AssistantRfqForm } from "@/components/AssistantRfqForm";
import { safeMailto } from "@/lib/site";
import { useUnitSystem } from "@/lib/units";
import { storageGet, storageSet } from "@/lib/browser-storage";
import {
  askAssistant,
  loadAssistantConfig,
  pageType,
  safeCitationHref,
  type AssistantConfig,
  type Citation,
  type RfqDraft,
} from "@/lib/assistant";

type ChatMessage = {
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  error?: boolean;
  draft?: RfqDraft;
  reference?: string;
};

const SESSION_KEY = "wanshang-assistant-session";
const MAX_STORED_MESSAGES = 30;

function restoreSession(): { id: string; messages: ChatMessage[] } {
  try {
    const parsed = JSON.parse(storageGet("sessionStorage", SESSION_KEY) ?? "{}") as { id?: unknown; messages?: unknown };
    const messages = Array.isArray(parsed.messages)
      ? (parsed.messages as ChatMessage[]).filter((item) => (item.role === "user" || item.role === "assistant") && typeof item.text === "string").slice(-MAX_STORED_MESSAGES)
      : [];
    return { id: typeof parsed.id === "string" && /^[0-9a-f]{32}$/.test(parsed.id) ? parsed.id : "", messages };
  } catch {
    return { id: "", messages: [] };
  }
}

/**
 * The single floating entry. With the assistant switched on it opens a side
 * panel (questions, answers, RFQ drafts and the sales contacts); otherwise it
 * stays the plain WhatsApp / contact button.
 */
export function FloatingContact() {
  const pathname = usePathname();
  const site = useSiteConfig();
  const system = useUnitSystem(site.defaultUnitSystem);
  const [config, setConfig] = useState<AssistantConfig | null>(null);
  const [open, setOpen] = useState(false);
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const restored = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    void loadAssistantConfig(controller.signal).then((value) => { if (!controller.signal.aborted) setConfig(value); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!open || restored.current) return;
    restored.current = true;
    const session = restoreSession();
    setConversationId(session.id);
    setMessages(session.messages);
  }, [open]);

  useEffect(() => {
    if (!restored.current) return;
    storageSet("sessionStorage", SESSION_KEY, JSON.stringify({ id: conversationId, messages: messages.slice(-MAX_STORED_MESSAGES) }));
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [conversationId, messages]);

  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [open]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const whatsappUrl = site.whatsappUrl;
  const assistantOn = Boolean(config?.enabled);
  if (!assistantOn) {
    if (/^\/products\/[^/]+\/[^/]+\/?$/.test(pathname)) return null;
    return <Link
      className={`whatsapp-float${whatsappUrl ? "" : " contact-float"}`}
      href={whatsappUrl || "/contact"}
      target={whatsappUrl.startsWith("http") ? "_blank" : undefined}
      rel={whatsappUrl.startsWith("http") ? "noopener noreferrer" : undefined}
      onClick={() => { if (whatsappUrl) trackEvent("click_whatsapp", { placement: "floating" }); }}
      aria-label={whatsappUrl ? "Contact the sales team by WhatsApp" : "Contact the sales team"}
    ><span aria-hidden="true">{whatsappUrl ? "WA" : "?"}</span><strong>{whatsappUrl ? "WhatsApp" : "Contact us"}</strong></Link>;
  }

  const prompts = [...(config?.quick_prompts[pageType(pathname)] ?? []), ...(config?.quick_prompts.any ?? [])].slice(0, 4);
  const maxChars = config?.max_question_chars ?? 500;
  const mailto = safeMailto(site.salesEmail);

  const updateLast = (change: (message: ChatMessage) => ChatMessage) => {
    setMessages((current) => {
      const next = [...current];
      const last = next[next.length - 1];
      if (last?.role === "assistant") next[next.length - 1] = change(last);
      return next;
    });
  };

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    if (message.length > maxChars) {
      setStatus(`Please keep questions under ${maxChars} characters.`);
      return;
    }
    setQuestion("");
    setBusy(true);
    setStatus("");
    setMessages((current) => [...current, { role: "user", text: message }, { role: "assistant", text: "" }]);
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      await askAssistant({ message, conversation_id: conversationId || undefined, page: pathname }, (event) => {
        if (event.type === "conversation") setConversationId(event.id);
        else if (event.type === "delta") { setStatus(""); updateLast((last) => ({ ...last, text: last.text + event.text })); }
        else if (event.type === "lookup") setStatus(event.tool === "read_page" ? "Reading product information…" : "Searching the website…");
        else if (event.type === "rfq_draft") updateLast((last) => ({ ...last, draft: event.draft }));
        else if (event.type === "error") updateLast((last) => ({ ...last, text: last.text ? `${last.text}\n\n${event.message}` : event.message, error: true }));
        else if (event.type === "done") updateLast((last) => ({ ...last, citations: event.citations }));
      }, controller.signal);
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        updateLast((last) => ({ ...last, text: last.text || "The connection was interrupted. Please try again, or use the RFQ form.", error: true }));
      }
    } finally {
      controllerRef.current = null;
      setBusy(false);
      setStatus("");
    }
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void send(question);
  };

  return <>
    {!open && <button type="button" className="whatsapp-float contact-float" onClick={() => setOpen(true)} aria-haspopup="dialog" aria-label="Ask a question or contact the sales team"><span aria-hidden="true">?</span><strong>Ask us</strong></button>}
    {open && <aside className="assistant-panel" role="dialog" aria-modal="false" aria-labelledby="assistant-title">
      <header className="assistant-head">
        <h2 id="assistant-title">Product assistant</h2>
        <button type="button" className="filter-drawer-close" aria-label="Close assistant" onClick={() => setOpen(false)}>×</button>
      </header>
      <div className="assistant-log" ref={logRef} aria-live="polite">
        <p className="assistant-bubble assistant">{config?.welcome}</p>
        {messages.length === 0 && prompts.length > 0 && <div className="assistant-prompts">{prompts.map((prompt) => <button type="button" key={prompt} onClick={() => void send(prompt)}>{prompt}</button>)}</div>}
        {messages.map((message, index) => <div key={index} className={`assistant-bubble ${message.role}${message.error ? " error" : ""}`}>
          {message.text || (busy && index === messages.length - 1 ? "…" : "")}
          {message.citations && message.citations.length > 0 && <ul className="assistant-citations">{message.citations.map((citation) => {
            const href = safeCitationHref(citation.url);
            return href ? <li key={citation.url}><a href={href}>{citation.title}</a></li> : null;
          })}</ul>}
          {message.draft && !message.reference && <AssistantRfqForm draft={message.draft} conversationId={conversationId} system={system} onDone={(reference) => setMessages((current) => current.map((item, position) => position === index ? { ...item, reference } : item))} />}
          {message.reference && <p className="form-success">RFQ received. Your reference is <strong>{message.reference}</strong>.</p>}
        </div>)}
        {status && <p className="assistant-status" role="status">{status}</p>}
      </div>
      <form className="assistant-input" onSubmit={submit}>
        <label className="sr-only" htmlFor="assistant-question">Your question</label>
        <textarea id="assistant-question" rows={2} maxLength={maxChars} value={question} placeholder="Ask about products, specifications or quotations" onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(question); } }} />
        <div className="assistant-input-row"><span>{question.length}/{maxChars}</span><button className="button" type="submit" disabled={busy || !question.trim()}>{busy ? "Answering…" : "Send"}</button></div>
        <p className="assistant-notice">{config?.privacy_notice} <Link href="/privacy-policy">Privacy policy</Link></p>
      </form>
      <footer className="assistant-contacts">
        <span>Talk to sales:</span>
        {whatsappUrl && <a href={whatsappUrl} target="_blank" rel="noopener noreferrer" onClick={() => trackEvent("click_whatsapp", { placement: "assistant" })}>WhatsApp</a>}
        {mailto && <a href={mailto} onClick={() => trackEvent("click_email", { placement: "assistant" })}>Email</a>}
        {site.salesPhone && <a href={`tel:${site.salesPhone.replace(/[^+\d]/g, "")}`} onClick={() => trackEvent("click_phone", { placement: "assistant" })}>Phone</a>}
        <Link href="/request-a-quote">RFQ form</Link>
      </footer>
    </aside>}
  </>;
}
