"use client";

import Link from "next/link";
import { FormEvent, useRef, useState } from "react";
import { apiBase } from "@/lib/data";
import { addAttributionToForm } from "@/lib/attribution";
import { TurnstileWidget, turnstileEnabled } from "@/components/TurnstileWidget";
import { apiErrorMessage } from "@/lib/form-errors";
import { trackEvent } from "@/lib/events";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";
import { useSiteConfig } from "@/components/SiteConfigProvider";
import { clearSubmissionKey, getSubmissionKey } from "@/lib/submission";

export function ContactForm() {
  const site = useSiteConfig();
  const [state, setState] = useState<"idle" | "sent">("idle");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [captchaReset, setCaptchaReset] = useState(0);
  const [reference, setReference] = useState("");
  const pending = useRef(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending.current) return;
    setError("");
    setSubmitting(true);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    addAttributionToForm(form);
    if (turnstileEnabled && !form.get("turnstile_token")) {
      setError("Complete the security verification before sending your message.");
      setSubmitting(false);
      return;
    }
    pending.current = true;
    try {
      const submissionKey = await getSubmissionKey("contact", form);
      const response = await fetchWithTimeout(
        `${apiBase}/contact/`,
        { method: "POST", body: form, headers: { "Idempotency-Key": submissionKey } },
        90_000,
      );
      const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
      if (!response.ok) {
        throw new Error(apiErrorMessage(payload, "Unable to send your message."));
      }
      const nextReference = typeof payload.reference === "string" ? payload.reference : "";
      if (!nextReference) throw new Error("We could not confirm your message reference. Please try again.");
      clearSubmissionKey("contact", submissionKey);
      setReference(nextReference);
      setState("sent");
      formElement.reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to send your message.");
      setCaptchaReset((value) => value + 1);
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  };

  if (state === "sent") return <div className="form-success"><h2>Message sent</h2><p>Thank you. Your enquiry is now available for a sales or engineering follow-up.{reference && <> Your reference is <strong>{reference}</strong>.</>}</p><div className="button-row"><Link className="button" href="/products">Browse products</Link><Link className="button-outline" href="/">Return home</Link>{site.whatsappUrl && <a className="button-outline" href={site.whatsappUrl} target="_blank" rel="noopener noreferrer" onClick={() => trackEvent("click_whatsapp", { placement: "contact_success" })}>WhatsApp</a>}</div></div>;

  return <form method="post" onSubmit={submit}>
    <noscript><p className="form-error">Enable JavaScript to submit this form.</p></noscript>
    {error && <p className="form-error" role="alert" aria-live="assertive">{error}</p>}
    <div className="form-grid">
      <div className="field"><label htmlFor="contact-name">Full name *</label><input id="contact-name" name="name" required maxLength={120} /></div>
      <div className="field"><label htmlFor="contact-company">Company</label><input id="contact-company" name="company" maxLength={160} /></div>
      <div className="field"><label htmlFor="contact-email">Email *</label><input id="contact-email" name="email" required type="email" maxLength={254} /></div>
      <div className="field"><label htmlFor="contact-phone">Phone / WhatsApp</label><input id="contact-phone" name="phone" maxLength={60} /></div>
      <div className="field"><label htmlFor="contact-country">Country</label><input id="contact-country" name="country" maxLength={100} /></div>
      <div className="field"><label htmlFor="contact-subject">Subject</label><input id="contact-subject" name="subject" maxLength={255} /></div>
      <div className="field form-wide"><label htmlFor="contact-message">How can we help? *</label><textarea id="contact-message" name="message" required maxLength={10000} /></div>
    </div>
    <TurnstileWidget action="contact" resetSignal={captchaReset} />
    <input type="hidden" name="website" defaultValue="" />
    <label className="checkbox" style={{ marginTop: 16 }}><input name="privacy_consent" value="true" type="checkbox" required />I agree to the <Link href="/privacy-policy">privacy policy</Link>.</label>
    <button className="button" disabled={submitting} style={{ marginTop: 20 }} type="submit">{submitting ? "Sending…" : "Send message"}</button>
  </form>;
}
