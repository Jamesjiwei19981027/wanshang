"use client";

import Link from "next/link";
import { FormEvent, useRef, useState } from "react";
import { apiBase } from "@/lib/data";
import { addAttributionToForm } from "@/lib/attribution";
import { apiErrorMessage } from "@/lib/form-errors";
import { trackEvent } from "@/lib/events";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";
import { TurnstileWidget, turnstileEnabled } from "@/components/TurnstileWidget";
import type { Product } from "@/lib/types";
import { AttachmentInput } from "@/components/AttachmentInput";
import { validateInquiryAttachments } from "@/lib/inquiry-attachments";
import { clearSubmissionKey, getSubmissionKey } from "@/lib/submission";

export function ProductInquiryForm({ product }: { product: Product }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [reference, setReference] = useState("");
  const [captchaReset, setCaptchaReset] = useState(0);
  const started = useRef(false);
  const pending = useRef(false);

  const markStarted = () => {
    if (started.current) return;
    started.current = true;
    trackEvent("start_inquiry", { product_id: product.id, placement: "product_detail_form" });
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending.current) return;
    setError("");
    const formElement = event.currentTarget;
    const attachments = formElement.elements.namedItem("attachments");
    if (attachments instanceof HTMLInputElement) {
      const attachmentError = validateInquiryAttachments(attachments.files ?? []);
      attachments.setCustomValidity(attachmentError);
      if (attachmentError) {
        attachments.reportValidity();
        return;
      }
    }
    setSubmitting(true);
    const form = new FormData(formElement);
    const quantity = Math.max(1, Math.min(100_000, Number(form.get("quantity")) || 1));
    const selectedOptions = String(form.get("selected_options") ?? "").trim();
    const selectedOptionsPayload = selectedOptions ? { notes: selectedOptions } : {};
    if (JSON.stringify(selectedOptionsPayload).length > 4000) {
      setError("Selected options are too long. Shorten the notes or add the detail in an attachment.");
      setSubmitting(false);
      return;
    }
    form.delete("selected_options");
    form.set("items", JSON.stringify([{
      product_id: product.id,
      quantity,
      selected_options: selectedOptionsPayload,
      requested_specs: {},
    }]));
    // The product's own parameters are recorded server-side from the
    // selected item; the buyer's requirements stay separate.
    form.set("specifications", JSON.stringify({ general: { selected_product: product.model } }));
    if (product.category?.slug) form.set("requested_categories", JSON.stringify([product.category.slug]));
    addAttributionToForm(form);
    if (turnstileEnabled && !form.get("turnstile_token")) {
      setError("Complete the security verification before submitting.");
      setSubmitting(false);
      return;
    }
    pending.current = true;
    try {
      const scope = `product-${product.id}`;
      const submissionKey = await getSubmissionKey(scope, form);
      const response = await fetchWithTimeout(
        `${apiBase}/inquiries/`,
        { method: "POST", body: form, headers: { "Idempotency-Key": submissionKey } },
        90_000,
      );
      const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
      if (!response.ok) throw new Error(apiErrorMessage(payload, "Unable to submit this product enquiry."));
      const nextReference = typeof payload.reference === "string" ? payload.reference : "";
      if (!nextReference) throw new Error("We could not confirm your RFQ reference. Please try again.");
      clearSubmissionKey(scope, submissionKey);
      setReference(nextReference);
      formElement.reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to submit this product enquiry. Please try again.");
      setCaptchaReset((value) => value + 1);
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  };

  if (reference) return <div className="form-success"><h2>Product enquiry received</h2><p>Your RFQ reference is <strong>{reference}</strong>.</p><Link className="button" href="/products">Continue exploring</Link></div>;

  return <form method="post" onSubmit={submit} onFocus={markStarted} onInput={markStarted}>
    <noscript><p className="form-error">Enable JavaScript to submit this form.</p></noscript>
    {error && <p className="form-error" role="alert" aria-live="assertive">{error}</p>}
    <div className="form-grid">
      <div className="field"><label htmlFor="product-rfq-name">Name *</label><input id="product-rfq-name" name="name" required maxLength={120} autoComplete="name" /></div>
      <div className="field"><label htmlFor="product-rfq-email">Business email *</label><input id="product-rfq-email" name="email" type="email" required maxLength={254} autoComplete="email" /></div>
      <div className="field"><label htmlFor="product-rfq-company">Company</label><input id="product-rfq-company" name="company" maxLength={160} autoComplete="organization" /></div>
      <div className="field"><label htmlFor="product-rfq-phone">Phone / WhatsApp</label><input id="product-rfq-phone" name="phone" type="tel" maxLength={60} autoComplete="tel" /></div>
      <div className="field"><label htmlFor="product-rfq-country">Country</label><input id="product-rfq-country" name="country" maxLength={100} autoComplete="country-name" /></div>
      <div className="field"><label htmlFor="product-rfq-quantity">Required quantity</label><input id="product-rfq-quantity" name="quantity" type="number" min="1" max="100000" inputMode="numeric" defaultValue="1" /></div>
      <div className="field form-wide"><label htmlFor="product-rfq-options">Selected options / dimensions</label><textarea id="product-rfq-options" name="selected_options" maxLength={4000} placeholder="Finish, hub face, spring centre, brake or other requested configuration." /></div>
      <div className="field form-wide"><label htmlFor="product-rfq-message">Project message</label><textarea id="product-rfq-message" name="message" maxLength={10000} /></div>
      <div className="field form-wide"><label htmlFor="product-rfq-attachments">Drawing or photo</label><AttachmentInput id="product-rfq-attachments" /></div>
    </div>
    <TurnstileWidget action="rfq" resetSignal={captchaReset} />
    <input type="hidden" name="website" defaultValue="" />
    <label className="checkbox"><input name="privacy_consent" value="true" type="checkbox" required />I agree that this information may be used to respond to the enquiry, as described in the <Link href="/privacy-policy">privacy policy</Link>.</label>
    <button className="button" disabled={submitting} type="submit" style={{ marginTop: 18 }}>{submitting ? "Submitting…" : `Enquire about ${product.model}`}</button>
  </form>;
}
