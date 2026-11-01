"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { apiBase } from "@/lib/data";
import { clearRfq, useRfqItems } from "@/lib/rfq";
import { trackEvent } from "@/lib/events";
import { addAttributionToForm } from "@/lib/attribution";
import { TurnstileWidget, turnstileEnabled } from "@/components/TurnstileWidget";
import { apiErrorMessage } from "@/lib/form-errors";
import { storageGet, storageRemove, storageSet } from "@/lib/browser-storage";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";
import { AttachmentInput } from "@/components/AttachmentInput";
import { validateInquiryAttachments } from "@/lib/inquiry-attachments";
import { clearSubmissionKey, getSubmissionKey } from "@/lib/submission";
import { RfqCategoryFields } from "@/components/RfqCategoryFields";
import { useSiteConfig } from "@/components/SiteConfigProvider";
import { CATEGORY_FIELD_PREFIX, packRfqForm } from "@/lib/rfq-form";
import { useUnitSystem } from "@/lib/units";
import type { Application, Category } from "@/lib/types";

const DRAFT_KEY = "axleforge-rfq-draft";
const NON_DRAFT_FIELDS = new Set(["attachments", "website", "turnstile_token", "cf-turnstile-response"]);

type DraftControl = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

function readDraft(): Record<string, unknown> {
  const parsed = JSON.parse(storageGet("sessionStorage", DRAFT_KEY) ?? "{}") as unknown;
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
}

/** Put saved values back into the form; onlyEmpty keeps what the buyer typed since. */
function applyDraft(form: HTMLFormElement, draft: Record<string, unknown>, searchParams: URLSearchParams, onlyEmpty: boolean) {
  for (const [name, value] of Object.entries(draft)) {
    const control = form.elements.namedItem(name);
    if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement)) continue;
    const queryKey = name.replace(/^specifications\.general\./, "");
    if (searchParams.has(queryKey)) continue;
    if (control instanceof HTMLInputElement && control.type === "checkbox") {
      if (!onlyEmpty) control.checked = Boolean(value);
    } else if (typeof value === "string" && (!onlyEmpty || !control.value)) {
      control.value = value;
    }
  }
}

export function RFQForm({ categories = [], applications = [] }: { categories?: Category[]; applications?: Application[] } = {}) {
  const searchParams = useSearchParams();
  const items = useRfqItems();
  const formRef = useRef<HTMLFormElement>(null);
  const system = useUnitSystem(useSiteConfig().defaultUnitSystem);
  const linkedCategories = (searchParams.get("category") ?? "").split(",").map((slug) => slug.trim()).filter(Boolean);
  const initialCategories = [...new Set([...linkedCategories, ...items.map((item) => item.category ?? "")])]
    .filter((slug) => categories.some((category) => category.slug === slug));
  const [selectedCategories, setSelectedCategories] = useState<string[]>(initialCategories);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [reference, setReference] = useState("");
  const [captchaReset, setCaptchaReset] = useState(0);
  const started = useRef(false);
  const restoredDraft = useRef(false);
  const pending = useRef(false);

  useEffect(() => {
    if (restoredDraft.current) return;
    restoredDraft.current = true;
    const form = formRef.current;
    if (!form) return;
    try {
      const draft = readDraft();
      let removedTransientFields = false;
      for (const name of NON_DRAFT_FIELDS) {
        if (Object.hasOwn(draft, name)) {
          delete draft[name];
          removedTransientFields = true;
        }
      }
      if (removedTransientFields) storageSet("sessionStorage", DRAFT_KEY, JSON.stringify(draft));
      applyDraft(form, draft, searchParams, false);
      const restored = Object.entries(draft)
        .filter(([name, value]) => name.startsWith(CATEGORY_FIELD_PREFIX) && value === true)
        .map(([name]) => name.slice(CATEGORY_FIELD_PREFIX.length));
      if (restored.length) setTimeout(() => setSelectedCategories((current) => [...new Set([...current, ...restored])]), 0);
    } catch {
      storageRemove("sessionStorage", DRAFT_KEY);
    }
    // Keep restored or linked requirements visible while retaining every field in FormData.
    for (const section of form.querySelectorAll<HTMLDetailsElement>("details[data-rfq-optional]")) {
      const controls = section.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input, select, textarea");
      if (Array.from(controls).some((control) => Boolean(control.value.trim()))) {
        section.open = true;
      }
    }
  }, [searchParams]);

  // Category sections render after their checkbox is restored; refill them.
  const selectedKey = selectedCategories.join(",");
  useEffect(() => {
    const form = formRef.current;
    if (!form || !selectedKey) return;
    try {
      applyDraft(form, readDraft(), searchParams, true);
    } catch {
      storageRemove("sessionStorage", DRAFT_KEY);
    }
  }, [searchParams, selectedKey]);

  const toggleCategory = (slug: string, checked: boolean) => {
    setSelectedCategories((current) => checked ? [...new Set([...current, slug])] : current.filter((item) => item !== slug));
  };

  const markStarted = () => {
    if (started.current) return;
    started.current = true;
    trackEvent("start_inquiry", { selected_products: items.length });
  };

  const saveDraft = () => {
    const form = formRef.current;
    if (!form) return;
    const draft: Record<string, string | boolean> = {};
    for (const control of Array.from(form.elements)) {
      if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement)) continue;
      if (!control.name || NON_DRAFT_FIELDS.has(control.name)) continue;
      draft[control.name] = control instanceof HTMLInputElement && control.type === "checkbox"
        ? control.checked
        : control.value;
    }
    storageSet("sessionStorage", DRAFT_KEY, JSON.stringify(draft));
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
    packRfqForm(form);
    form.set("items", JSON.stringify(items.map(({ id, quantity }) => ({ product_id: id, quantity }))));
    addAttributionToForm(form);
    if (turnstileEnabled && !form.get("turnstile_token")) {
      setError("Complete the security verification before submitting.");
      setSubmitting(false);
      return;
    }
    pending.current = true;
    try {
      const submissionKey = await getSubmissionKey("rfq", form);
      const response = await fetchWithTimeout(
        `${apiBase}/inquiries/`,
        { method: "POST", body: form, headers: { "Idempotency-Key": submissionKey } },
        90_000,
      );
      const result = await response.json().catch(() => ({})) as Record<string, unknown>;
      if (!response.ok) {
        throw new Error(apiErrorMessage(result, "Unable to submit RFQ."));
      }
      const nextReference = typeof result.reference === "string" ? result.reference : "";
      if (!nextReference) throw new Error("We could not confirm your RFQ reference. Please try again.");
      clearSubmissionKey("rfq", submissionKey);
      clearRfq();
      storageRemove("sessionStorage", DRAFT_KEY);
      setReference(nextReference);
      formElement.reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to submit RFQ. Please try again.");
      setCaptchaReset((value) => value + 1);
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  };

  if (reference) return <div className="form-success"><h2>RFQ received</h2><p>Your reference is <strong>{reference}</strong>. A sales engineer can now review the information you supplied.</p><Link href="/products" className="button">Continue exploring</Link></div>;

  return <form method="post" ref={formRef} onSubmit={submit} onChange={saveDraft} onFocus={markStarted} onInput={markStarted} onInvalidCapture={(event) => {
    // Optional number/date fields can still be invalid inside a closed section.
    let ancestor = event.target instanceof HTMLElement ? event.target.parentElement : null;
    while (ancestor && ancestor !== event.currentTarget) {
      if (ancestor instanceof HTMLDetailsElement) ancestor.open = true;
      ancestor = ancestor.parentElement;
    }
  }}>
    <noscript><p className="form-error">Enable JavaScript to submit this form.</p></noscript>
    {error && <p className="form-error" role="alert" aria-live="assertive">{error}</p>}
    <p className="draft-note">Your unsent draft is kept in this browser tab until it is submitted.</p>
    <div className="form-card"><h2>Your contact details</h2><div className="form-grid">
      <div className="field"><label htmlFor="rfq-name">Full name *</label><input id="rfq-name" name="name" required maxLength={120} autoComplete="name" /></div>
      <div className="field"><label htmlFor="rfq-email">Work email *</label><input id="rfq-email" name="email" required type="email" maxLength={254} autoComplete="email" /></div>
      <div className="field"><label htmlFor="rfq-company">Company</label><input id="rfq-company" name="company" maxLength={160} autoComplete="organization" /></div>
      <div className="field"><label htmlFor="rfq-phone">Phone / WhatsApp</label><input id="rfq-phone" name="phone" type="tel" maxLength={60} autoComplete="tel" /></div>
    </div></div>
    <div className="form-card"><h2>What do you need?</h2><p className="rfq-section-note">Share what you know. You can send a drawing or let our team help with the technical details.</p><div className="form-grid three">
      <div className="field form-wide"><label htmlFor="rfq-message">Project overview</label><textarea id="rfq-message" name="message" maxLength={10000} placeholder="Tell us about your trailer, selected products or the problem you need to solve." /></div>
      {applications.length > 0 && <div className="field"><label htmlFor="rfq-application">Application</label><select id="rfq-application" name="application" defaultValue={searchParams.get("application") ?? ""}><option value="">Select application</option>{applications.map((item) => <option value={item.slug} key={item.id}>{item.name}</option>)}<option value="other">Other</option></select></div>}
      {categories.length > 0 && <fieldset className="field form-wide"><legend>Product categories</legend><div className="form-grid three">{categories.map((category) => <label className="checkbox" key={category.slug}><input type="checkbox" name={`${CATEGORY_FIELD_PREFIX}${category.slug}`} value="true" checked={selectedCategories.includes(category.slug)} onChange={(event) => toggleCategory(category.slug, event.target.checked)} />{category.parent ? "— " : ""}{category.name}</label>)}</div></fieldset>}
    </div></div>
    <div className="form-card"><h2>Drawings or photos <span className="optional-label">Optional</span></h2><div className="file-drop"><label htmlFor="rfq-attachments">Attach a drawing, photo or specification</label><AttachmentInput id="rfq-attachments" /></div></div>
    <details className="form-card rfq-optional" data-rfq-optional>
      <summary><strong>Technical details</strong><span>Optional · parameters of the selected categories</span></summary>
      {selectedCategories.length === 0 && <p className="rfq-section-note">Select a product category above to see its technical questions.</p>}
      <RfqCategoryFields categories={categories} selected={selectedCategories} system={system} />
      <div className="form-grid">
      <div className="field form-wide"><label htmlFor="rfq-special-requirements">Special requirements</label><textarea id="rfq-special-requirements" name="specifications.general.special_requirements" maxLength={4000} defaultValue={searchParams.get("special_requirements") ?? ""} /></div>
      </div>
    </details>
    <details className="form-card rfq-optional" data-rfq-optional>
      <summary><strong>Order &amp; delivery details</strong><span>Optional · quantity, destination and OEM / ODM</span></summary>
      <div className="form-grid">
      <div className="field"><label htmlFor="rfq-country">Country</label><input id="rfq-country" name="country" maxLength={100} autoComplete="country-name" /></div>
      <div className="field"><label htmlFor="rfq-destination-port">Destination port</label><input id="rfq-destination-port" name="destination_port" maxLength={150} /></div>
      <div className="field"><label htmlFor="rfq-quantity">Required quantity</label><input id="rfq-quantity" name="quantity" type="number" min="1" max="100000" inputMode="numeric" /></div>
      <div className="field"><label htmlFor="rfq-delivery-date">Target delivery date</label><input id="rfq-delivery-date" name="target_delivery_date" type="date" /></div>
      <div className="field"><label htmlFor="rfq-oem">OEM requirement</label><select id="rfq-oem" name="specifications.general.oem_requirement" defaultValue={searchParams.get("oem_requirement") ?? ""}><option value="">Not specified</option><option value="yes">Required</option><option value="no">Not required</option><option value="discuss">Discuss options</option></select></div>
      <div className="field"><label htmlFor="rfq-odm">ODM requirement</label><select id="rfq-odm" name="specifications.general.odm_requirement" defaultValue={searchParams.get("odm_requirement") ?? ""}><option value="">Not specified</option><option value="yes">Required</option><option value="no">Not required</option><option value="discuss">Discuss options</option></select></div>
      </div>
    </details>
    <TurnstileWidget action="rfq" resetSignal={captchaReset} />
    <input type="hidden" name="website" defaultValue="" />
    <label className="checkbox"><input name="privacy_consent" value="true" type="checkbox" required />I agree that the information submitted may be used to respond to this enquiry, as described in the <Link href="/privacy-policy">privacy policy</Link>.</label>
    <div style={{ marginTop: 20 }}><button className="button" disabled={submitting} type="submit">{submitting ? "Submitting…" : "Submit quote request"}</button></div>
  </form>;
}
