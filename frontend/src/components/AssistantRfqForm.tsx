"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { apiBase } from "@/lib/data";
import { addAttributionToForm } from "@/lib/attribution";
import { apiErrorMessage } from "@/lib/form-errors";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";
import { clearSubmissionKey, getSubmissionKey } from "@/lib/submission";
import { CATEGORY_FIELD_PREFIX, packRfqForm } from "@/lib/rfq-form";
import type { RfqDraft } from "@/lib/assistant";
import type { Category, UnitSystem } from "@/lib/types";
import { RfqCategoryFields } from "@/components/RfqCategoryFields";
import { TurnstileWidget, turnstileEnabled } from "@/components/TurnstileWidget";

/**
 * The RFQ the assistant prepared, for the visitor to check and submit. It
 * posts to the same endpoint as the RFQ page, with the same verification,
 * duplicate protection and notifications.
 */
export function AssistantRfqForm({ draft, conversationId, system, onDone }: { draft: RfqDraft; conversationId: string; system: UnitSystem; onDone: (reference: string) => void }) {
  const [categories, setCategories] = useState<Category[]>([]);
  const [selected, setSelected] = useState<string[]>(draft.requested_categories ?? []);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [captchaReset, setCaptchaReset] = useState(0);
  const pending = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetchWithTimeout(`${apiBase}/categories/`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() : [])
      .then((payload: unknown) => { if (Array.isArray(payload)) setCategories(payload as Category[]); })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending.current) return;
    setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    packRfqForm(form);
    form.set("items", JSON.stringify((draft.products ?? []).map((product) => ({ product_id: product.id, quantity: 1 }))));
    form.set("assistant_conversation", conversationId);
    addAttributionToForm(form);
    if (turnstileEnabled && !form.get("turnstile_token")) {
      setError("Complete the security verification before submitting.");
      return;
    }
    pending.current = true;
    setSubmitting(true);
    try {
      const submissionKey = await getSubmissionKey("assistant-rfq", form);
      const response = await fetchWithTimeout(`${apiBase}/inquiries/`, { method: "POST", body: form, headers: { "Idempotency-Key": submissionKey } }, 90_000);
      const result = await response.json().catch(() => ({})) as Record<string, unknown>;
      if (!response.ok) throw new Error(apiErrorMessage(result, "Unable to submit the RFQ."));
      const reference = typeof result.reference === "string" ? result.reference : "";
      if (!reference) throw new Error("We could not confirm your RFQ reference. Please try again.");
      clearSubmissionKey("assistant-rfq", submissionKey);
      onDone(reference);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to submit the RFQ. Please try again.");
      setCaptchaReset((value) => value + 1);
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  };

  return <form className="assistant-rfq" method="post" onSubmit={submit}>
    <h3>Review your quotation request</h3>
    {error && <p className="form-error" role="alert">{error}</p>}
    {(draft.products ?? []).length > 0 && <ul className="assistant-rfq-products">{(draft.products ?? []).map((product) => <li key={product.id}>{product.title} · {product.model}</li>)}</ul>}
    <div className="field"><label htmlFor="assistant-rfq-name">Full name *</label><input id="assistant-rfq-name" name="name" required maxLength={120} defaultValue={draft.name ?? ""} autoComplete="name" /></div>
    <div className="field"><label htmlFor="assistant-rfq-email">Work email *</label><input id="assistant-rfq-email" name="email" type="email" required maxLength={254} defaultValue={draft.email ?? ""} autoComplete="email" /></div>
    <div className="field"><label htmlFor="assistant-rfq-company">Company</label><input id="assistant-rfq-company" name="company" maxLength={160} defaultValue={draft.company ?? ""} /></div>
    <div className="field"><label htmlFor="assistant-rfq-phone">Phone / WhatsApp</label><input id="assistant-rfq-phone" name="phone" type="tel" maxLength={60} defaultValue={draft.phone ?? ""} /></div>
    <div className="field"><label htmlFor="assistant-rfq-country">Country</label><input id="assistant-rfq-country" name="country" maxLength={100} defaultValue={draft.country ?? ""} /></div>
    <div className="field"><label htmlFor="assistant-rfq-quantity">Quantity</label><input id="assistant-rfq-quantity" name="quantity" type="number" min="1" max="100000" defaultValue={draft.quantity ?? ""} /></div>
    <div className="field"><label htmlFor="assistant-rfq-port">Destination port</label><input id="assistant-rfq-port" name="destination_port" maxLength={150} defaultValue={draft.destination_port ?? ""} /></div>
    {draft.application && <input type="hidden" name="application" value={draft.application} />}
    {categories.filter((category) => selected.includes(category.slug)).map((category) => <label className="checkbox" key={category.slug}><input type="checkbox" name={`${CATEGORY_FIELD_PREFIX}${category.slug}`} value="true" checked onChange={(event) => { if (!event.target.checked) setSelected((current) => current.filter((slug) => slug !== category.slug)); }} />{category.name}</label>)}
    <RfqCategoryFields categories={categories} selected={selected} system={system} values={draft.specifications ?? {}} />
    <div className="field"><label htmlFor="assistant-rfq-message">Requirement</label><textarea id="assistant-rfq-message" name="message" maxLength={10000} rows={4} defaultValue={draft.message ?? ""} /></div>
    <TurnstileWidget action="rfq" resetSignal={captchaReset} />
    <input type="hidden" name="website" defaultValue="" />
    <label className="checkbox"><input name="privacy_consent" value="true" type="checkbox" required />I agree that this information may be used to respond to my enquiry, as described in the <Link href="/privacy-policy">privacy policy</Link>.</label>
    <button className="button" type="submit" disabled={submitting}>{submitting ? "Submitting…" : "Submit quotation request"}</button>
  </form>;
}
