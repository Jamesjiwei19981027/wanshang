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

const DRAFT_KEY = "axleforge-rfq-draft";
const NON_DRAFT_FIELDS = new Set(["attachments", "website", "turnstile_token", "cf-turnstile-response"]);
const WEIGHT_SPECIFICATIONS = new Set(["capacity", "estimated_trailer_weight"]);
const DIMENSION_SPECIFICATIONS = new Set(["hub_face", "spring_center", "overall_length", "drop_height", "tube_size", "stud_size", "brake_size"]);
const FINISH_ALIASES: Record<string, string> = {
  "e-coat": "E-Coated",
  "e-coated": "E-Coated",
  "powder coat": "Powder-Coated",
  "powder-coated": "Powder-Coated",
  "zinc plated": "Zinc Plated",
  galvanised: "Hot-Dip Galvanized",
  galvanized: "Hot-Dip Galvanized",
  "hot-dip galvanised": "Hot-Dip Galvanized",
  "hot-dip galvanized": "Hot-Dip Galvanized",
  custom: "Custom Finish",
};

function finishFromSearch(value: string | null): string {
  if (!value) return "";
  const canonical = ["Painted", "E-Coated", "Powder-Coated", "Zinc Plated", "Hot-Dip Galvanized", "Custom Finish"]
    .find((item) => item.toLocaleLowerCase("en") === value.toLocaleLowerCase("en"));
  return canonical || FINISH_ALIASES[value.trim().toLocaleLowerCase("en")] || "";
}

export function RFQForm() {
  const searchParams = useSearchParams();
  const items = useRfqItems();
  const formRef = useRef<HTMLFormElement>(null);
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
      const parsed = JSON.parse(storageGet("sessionStorage", DRAFT_KEY) ?? "{}") as unknown;
      const draft = parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? parsed as Record<string, unknown>
        : {};
      let removedTransientFields = false;
      for (const name of NON_DRAFT_FIELDS) {
        if (Object.hasOwn(draft, name)) {
          delete draft[name];
          removedTransientFields = true;
        }
      }
      if (removedTransientFields) storageSet("sessionStorage", DRAFT_KEY, JSON.stringify(draft));
      const draftCapacityUnit = draft["specifications.capacity_unit"] === "kg" ? "kg" : "lb";
      const draftDimensionUnit = draft["specifications.dimension_unit"] === "mm" ? "mm" : "in";
      const capacityUnitChanged = searchParams.has("capacity_unit") && searchParams.get("capacity_unit") !== draftCapacityUnit;
      const dimensionUnitChanged = searchParams.has("dimension_unit") && searchParams.get("dimension_unit") !== draftDimensionUnit;
      for (const [name, value] of Object.entries(draft)) {
        const control = form.elements.namedItem(name);
        if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement)) continue;
        const queryKey = name.replace(/^specifications\./, "");
        const hasQueryOverride = searchParams.has(queryKey)
          || (queryKey === "capacity" && searchParams.has("required_axle_capacity"))
          || (queryKey === "axle_type" && searchParams.has("type"));
        // Draft measurements belong to their saved unit. When a link changes that
        // unit, leave uncovered measurements blank instead of relabelling their
        // numbers. Explicit query values still win; other draft fields survive.
        if (!hasQueryOverride && ((capacityUnitChanged && WEIGHT_SPECIFICATIONS.has(queryKey))
          || (dimensionUnitChanged && DIMENSION_SPECIFICATIONS.has(queryKey)))) continue;
        if (control instanceof HTMLInputElement && control.type === "checkbox") {
          if (!hasQueryOverride) control.checked = Boolean(value);
        } else if (!hasQueryOverride && typeof value === "string") {
          control.value = value;
        }
      }
    } catch {
      storageRemove("sessionStorage", DRAFT_KEY);
    }
    // Keep restored or linked requirements visible while retaining every field in FormData.
    for (const section of form.querySelectorAll<HTMLDetailsElement>("details[data-rfq-optional]")) {
      const controls = section.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input, select, textarea");
      if (Array.from(controls).some((control) => Boolean(control.value.trim())
        && !(control.name === "specifications.dimension_unit" && control.value === "in"))) {
        section.open = true;
      }
    }
  }, [searchParams]);

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
    const specifications = Object.fromEntries(
      [...form.entries()]
        .filter(([key]) => key.startsWith("specifications."))
        .map(([key, value]) => [key.replace("specifications.", ""), value]),
    );
    [...form.keys()]
      .filter((key) => key.startsWith("specifications."))
      .forEach((key) => form.delete(key));
    form.set("specifications", JSON.stringify(specifications));
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
      <div className="field"><label htmlFor="rfq-application">Application</label><select id="rfq-application" name="application" defaultValue={searchParams.get("application") ?? ""}><option value="">Select application</option><option value="utility-trailers">Utility Trailer</option><option value="boat-trailers">Boat Trailer</option><option value="agricultural-trailers">Agricultural Trailer</option><option value="car-haulers">Car Hauler</option><option value="rv-caravan">RV / Caravan</option><option value="construction-trailers">Construction Trailer</option><option value="heavy-duty-transport">Heavy-Duty Trailer</option><option value="other">Other</option></select></div>
      <div className="field"><label htmlFor="rfq-capacity">Desired axle capacity</label><input id="rfq-capacity" name="specifications.capacity" defaultValue={searchParams.get("required_axle_capacity") ?? searchParams.get("capacity") ?? ""} inputMode="decimal" placeholder="e.g. 7,000" /></div>
      <div className="field"><label htmlFor="rfq-capacity-unit">Weight unit</label><select id="rfq-capacity-unit" name="specifications.capacity_unit" defaultValue={searchParams.get("capacity_unit") ?? "lb"}><option value="lb">lb</option><option value="kg">kg</option></select></div>
    </div></div>
    <div className="form-card"><h2>Drawings or photos <span className="optional-label">Optional</span></h2><div className="file-drop"><label htmlFor="rfq-attachments">Attach a drawing, photo or specification</label><AttachmentInput id="rfq-attachments" /></div></div>
    <details className="form-card rfq-optional" data-rfq-optional>
      <summary><strong>Technical details</strong><span>Optional · dimensions, brakes and configuration</span></summary>
      <div className="form-grid three">
      <div className="field"><label htmlFor="rfq-trailer-weight">Estimated trailer weight</label><input id="rfq-trailer-weight" name="specifications.estimated_trailer_weight" defaultValue={searchParams.get("estimated_trailer_weight") ?? ""} inputMode="decimal" placeholder="e.g. 10,000" /></div>
      <div className="field"><label htmlFor="rfq-axle-count">Axle count</label><select id="rfq-axle-count" name="specifications.axle_count" defaultValue={searchParams.get("axle_count") ?? ""}><option value="">Not sure</option><option value="single">Single axle</option><option value="tandem">Tandem axle</option><option value="triple">Triple axle</option></select></div>
      <div className="field"><label htmlFor="rfq-axle-type">Axle type</label><select id="rfq-axle-type" name="specifications.axle_type" defaultValue={searchParams.get("type") ?? ""}><option value="">Not sure</option><option value="straight">Straight axle</option><option value="drop">Drop axle</option><option value="torsion">Torsion axle</option></select></div>
      <div className="field"><label htmlFor="rfq-brake">Preferred brake</label><select id="rfq-brake" name="specifications.brake" defaultValue={searchParams.get("brake") ?? ""}><option value="">Not sure</option><option>Electric Drum</option><option>Hydraulic Drum</option><option>Hydraulic Disc</option><option>No Brake</option></select></div>
      <div className="field"><label htmlFor="rfq-environment">Operating environment</label><input id="rfq-environment" name="specifications.environment" defaultValue={searchParams.get("environment") ?? ""} placeholder="e.g. saltwater" /></div>
      <div className="field"><label htmlFor="rfq-hub-face">Hub face length</label><input id="rfq-hub-face" name="specifications.hub_face" defaultValue={searchParams.get("hub_face") ?? ""} placeholder="e.g. 95" /></div>
      <div className="field"><label htmlFor="rfq-spring-centre">Spring centre</label><input id="rfq-spring-centre" name="specifications.spring_center" defaultValue={searchParams.get("spring_center") ?? ""} placeholder="e.g. 80" /></div>
      <div className="field"><label htmlFor="rfq-overall-length">Overall length</label><input id="rfq-overall-length" name="specifications.overall_length" defaultValue={searchParams.get("overall_length") ?? ""} placeholder="e.g. 97" /></div>
      <div className="field"><label htmlFor="rfq-tube-shape">Tube shape</label><select id="rfq-tube-shape" name="specifications.tube_shape" defaultValue={searchParams.get("tube_shape") ?? ""}><option value="">Not sure</option><option value="round">Round tube</option><option value="square">Square tube</option></select></div>
      <div className="field"><label htmlFor="rfq-tube-size">Tube size</label><input id="rfq-tube-size" name="specifications.tube_size" defaultValue={searchParams.get("tube_size") ?? ""} placeholder={'e.g. 3" round'} /></div>
      <div className="field"><label htmlFor="rfq-drop-height">Drop height</label><input id="rfq-drop-height" name="specifications.drop_height" defaultValue={searchParams.get("drop_height") ?? ""} placeholder="If applicable" /></div>
      <div className="field"><label htmlFor="rfq-dimension-unit">Dimension unit</label><select id="rfq-dimension-unit" name="specifications.dimension_unit" defaultValue={searchParams.get("dimension_unit") ?? "in"}><option value="in">inch</option><option value="mm">mm</option></select></div>
      <div className="field"><label htmlFor="rfq-bolt-pattern">Bolt pattern</label><input id="rfq-bolt-pattern" name="specifications.bolt_pattern" defaultValue={searchParams.get("bolt_pattern") ?? ""} placeholder="e.g. 5 on 4.5" /></div>
      <div className="field"><label htmlFor="rfq-stud-size">Stud size</label><input id="rfq-stud-size" name="specifications.stud_size" defaultValue={searchParams.get("stud_size") ?? ""} /></div>
      <div className="field"><label htmlFor="rfq-hub-type">Hub type</label><input id="rfq-hub-type" name="specifications.hub_type" defaultValue={searchParams.get("hub_type") ?? ""} /></div>
      <div className="field"><label htmlFor="rfq-bearing-type">Bearing type</label><input id="rfq-bearing-type" name="specifications.bearing_type" defaultValue={searchParams.get("bearing_type") ?? ""} /></div>
      <div className="field"><label htmlFor="rfq-brake-size">Brake size</label><input id="rfq-brake-size" name="specifications.brake_size" defaultValue={searchParams.get("brake_size") ?? ""} /></div>
      <div className="field"><label htmlFor="rfq-finish">Preferred finish</label><select id="rfq-finish" name="specifications.finish" defaultValue={finishFromSearch(searchParams.get("finish"))}><option value="">Not specified</option><option>Painted</option><option>E-Coated</option><option>Powder-Coated</option><option>Zinc Plated</option><option>Hot-Dip Galvanized</option><option>Custom Finish</option></select></div>
      <div className="field form-wide"><label htmlFor="rfq-special-requirements">Special requirements</label><textarea id="rfq-special-requirements" name="specifications.special_requirements" maxLength={4000} defaultValue={searchParams.get("special_requirements") ?? ""} /></div>
      </div>
    </details>
    <details className="form-card rfq-optional" data-rfq-optional>
      <summary><strong>Order &amp; delivery details</strong><span>Optional · quantity, destination and OEM / ODM</span></summary>
      <div className="form-grid">
      <div className="field"><label htmlFor="rfq-country">Country</label><input id="rfq-country" name="country" maxLength={100} autoComplete="country-name" /></div>
      <div className="field"><label htmlFor="rfq-destination-port">Destination port</label><input id="rfq-destination-port" name="destination_port" maxLength={150} /></div>
      <div className="field"><label htmlFor="rfq-quantity">Required quantity</label><input id="rfq-quantity" name="quantity" type="number" min="1" max="100000" inputMode="numeric" /></div>
      <div className="field"><label htmlFor="rfq-delivery-date">Target delivery date</label><input id="rfq-delivery-date" name="target_delivery_date" type="date" /></div>
      <div className="field"><label htmlFor="rfq-oem">OEM requirement</label><select id="rfq-oem" name="specifications.oem_requirement" defaultValue={searchParams.get("oem_requirement") ?? ""}><option value="">Not specified</option><option value="yes">Required</option><option value="no">Not required</option><option value="discuss">Discuss options</option></select></div>
      <div className="field"><label htmlFor="rfq-odm">ODM requirement</label><select id="rfq-odm" name="specifications.odm_requirement" defaultValue={searchParams.get("odm_requirement") ?? ""}><option value="">Not specified</option><option value="yes">Required</option><option value="no">Not required</option><option value="discuss">Discuss options</option></select></div>
      </div>
    </details>
    <TurnstileWidget action="rfq" resetSignal={captchaReset} />
    <input type="hidden" name="website" defaultValue="" />
    <label className="checkbox"><input name="privacy_consent" value="true" type="checkbox" required />I agree that the information submitted may be used to respond to this enquiry, as described in the <Link href="/privacy-policy">privacy policy</Link>.</label>
    <div style={{ marginTop: 20 }}><button className="button" disabled={submitting} type="submit">{submitting ? "Submitting…" : "Submit quote request"}</button></div>
  </form>;
}
