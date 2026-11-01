import { pageMetadata } from "@/lib/seo";
import Link from "next/link";
import { getSiteSettings } from "@/lib/api";
import { privacyNotice } from "@/lib/privacy-notice";
import { safeMailto } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Privacy policy",
  description: `How ${privacyNotice.operatorName} handles business enquiries, drawings, website information and privacy requests.`,
  path: "/privacy-policy",
  index: false,
});

export default async function PrivacyPolicyPage() {
  const site = await getSiteSettings();
  const googleServices = [
    /^G-[A-Z0-9]+$/i.test(site.googleAnalyticsId) ? "Google Analytics" : "",
    /^GTM-[A-Z0-9]+$/i.test(site.googleTagManagerId) ? "Google Tag Manager" : "",
  ].filter(Boolean);
  const privacyMailto = safeMailto(privacyNotice.contactEmail);

  return <main><article className="shell policy">
    <p className="eyebrow">Privacy &amp; enquiries</p>
    <h1>Privacy policy</h1>
    <p className="policy-meta">Last updated: <time dateTime={privacyNotice.updated}>{privacyNotice.updatedLabel}</time></p>
    <p><strong>{privacyNotice.operatorName}</strong> operates {privacyNotice.website}. In this policy, “we” and “our” refer to that company. This notice explains how we handle information when you browse our catalogue, contact us or request a quotation.</p>

    <nav className="policy-contents" aria-label="Privacy policy sections">
      <a href="#information">Information we handle</a>
      <a href="#purposes">How we use it</a>
      <a href="#providers">Who receives it</a>
      <a href="#retention">Retention</a>
      <a href="#requests">Your choices and requests</a>
    </nav>

    <h2 id="information">1. Information we handle</h2>
    <ul>
      <li><strong>Contact and enquiry details:</strong> your name, email address, company, telephone or WhatsApp number, country, subject and message, where you provide them.</li>
      <li><strong>Technical and commercial requirements:</strong> selected products, quantities, dimensions, capacity, brake and mounting requirements, delivery preferences, drawings, images and other files you submit.</li>
      <li><strong>Enquiry records:</strong> a reference number, submission time, privacy acknowledgement, communication history, quotation status and staff follow-up notes.</li>
      <li><strong>Website and security information:</strong> page paths, referring website, campaign values, a random session identifier, browser and device information, network addresses and technical security events.</li>
    </ul>
    <p>Required form fields are marked with an asterisk. We need the required contact details and security verification to receive a form submission. Optional fields and files help us understand your requirements; send only information relevant to your enquiry.</p>
    <p>Enquiry and visit records use a keyed fingerprint of the IP address. Short-lived rate limits and security providers also process network addresses. A fingerprint or random session identifier can still be personal information; it is not a guarantee of anonymity.</p>

    <h2 id="purposes">2. How we use information</h2>
    <p>We use enquiry information to respond to questions, review drawings and product requirements, prepare quotations, communicate about your request and maintain related business records. We also use technical information to operate the website, prevent spam and abuse, scan files and investigate faults.</p>
    <p>Where a legal basis is required, the relevant basis may be steps you request before a contract, our legitimate interests in responding to business enquiries and securing the service, consent for optional measurement, or an applicable legal obligation. Acknowledging this notice does not subscribe you to a mailing list.</p>

    <h2>3. Enquiry context and optional measurement</h2>
    <p>When you submit a contact form, product enquiry or RFQ, we record its page and source context, which may include campaign values, first and latest source, referring website and a session identifier. We also record that a submission occurred. These records accompany the enquiry even if you choose Essential only.</p>
    <p>Optional page-view and interaction measurement is separate. It starts only after you select Accept in Cookie preferences. It can record page titles and paths, search activity, product or document interactions and enquiry-related events. Built-in search analytics record query length and result count. When a search finds nothing, the search words are kept in a list of unanswered searches so we can improve the catalogue; words that look like an email address, phone number, link or code are not recorded. Our built-in page URL tracking removes query strings and fragments; campaign fields are handled separately.</p>
    {googleServices.length > 0
      ? <p>The currently configured optional Google services are {googleServices.join(" and ")}. They load only after analytics consent. Their cookies and processing are described in the <Link href="/cookie-policy">Cookie policy</Link> and <a href="https://policies.google.com/privacy" target="_blank" rel="noopener noreferrer">Google Privacy Policy</a>.</p>
      : <p>Google Analytics and Google Tag Manager are not currently enabled on this website. Optional first-party measurement can still be enabled by choosing Accept.</p>}

    <h2 id="providers">4. Access and service providers</h2>
    <p>Authorised sales, technical and administrative staff can access information needed to handle enquiries and maintain the service. Website hosting, database storage and backups support this work.</p>
    <ul>
      <li><strong>Google / Gmail:</strong> we use Gmail SMTP to send enquiry notifications and customer confirmations. Notifications can contain contact details, messages, product and technical summaries, file names, scan status and authorised download links. File contents are not sent as email attachments by this website. Email handling is also covered by the <a href="https://policies.google.com/privacy" target="_blank" rel="noopener noreferrer">Google Privacy Policy</a>.</li>
      <li><strong>Cloudflare Turnstile:</strong> our forms use this service to distinguish legitimate visitors from automated abuse. Cloudflare processes technical signals such as IP address, browser information and the website origin; our server sends the verification response and client IP for validation. See the <a href="https://www.cloudflare.com/turnstile-privacy-policy/" target="_blank" rel="noopener noreferrer">Turnstile Privacy Addendum</a>.</li>
      <li><strong>DeepSeek (website assistant):</strong> questions you type into the website assistant, the recent messages of that conversation and the published website content needed to answer are sent to DeepSeek to generate the reply. Do not include information you do not want processed this way.</li>
      <li><strong>File scanning:</strong> uploaded files are scanned by a ClamAV service within our server environment, rather than uploaded by this website to a public scanning service.</li>
    </ul>
    <p>Google, Cloudflare and DeepSeek operate services internationally, so information they handle may be processed outside your country. Their notices describe their processing arrangements. You can contact us about the hosting, recipients and transfer arrangements relevant to your enquiry. We may also disclose records when required by an applicable legal obligation or to establish or defend a legal claim.</p>

    <h2>5. Uploaded files and access controls</h2>
    <p>Enquiry attachments are held in private storage. We validate file type and size and keep files unavailable for download until a malware scan reports them clean. Files that fail scanning or cannot be checked remain restricted. Only authorised staff can use the protected download route, and successful staff downloads are logged.</p>
    <p>A clean scan reduces risk but cannot guarantee that a file is harmless. Please avoid including unrelated personal information or material you are not entitled to share.</p>

    <h2 id="retention">6. Retention and deletion</h2>
    <p>Retention depends on whether an enquiry or quotation is still active, related business follow-up is needed, records are required for an applicable legal obligation, or a dispute remains unresolved. Enquiries, attachments, follow-up records and server-side measurement records do not currently have an automatic expiry. They remain until removed through a review or an applicable deletion request; marking an enquiry closed does not delete it.</p>
    <p>Website assistant conversations are saved. A conversation without contact details is deleted automatically 90 days after its last message. A conversation in which you left an email address or phone number but did not submit an RFQ is kept for one year so our sales team can follow up. A conversation that led to an RFQ is kept with that RFQ. Staff may review answers to correct our product information.</p>
    <p>Short-lived abuse-prevention records expire according to their security limits. For example, a fingerprint used to prevent repeated use of a verified Turnstile response is retained in the shared cache for five minutes. Browser storage has separate lifetimes, explained in the <Link href="/cookie-policy">Cookie policy</Link>.</p>
    <p>Email copies, security logs and backups are separate from the live enquiry database. Removing a live record does not instantly erase those copies. A deletion request is reviewed against these separate copies and any applicable recovery, security or record-keeping requirement.</p>

    <h2 id="requests">7. Your choices and privacy requests</h2>
    <p>You can change optional measurement consent at any time using Cookie preferences in the footer. This stops future optional collection by this website; it does not undo requests already sent or automatically erase stored enquiries and measurement records.</p>
    <p>Depending on the law that applies, you may request access, correction, deletion, restriction or a portable copy of your information, object to certain processing, or withdraw consent. You may also raise a concern with the relevant data protection authority. These rights can have conditions and exceptions.</p>
    <p>Send privacy requests to {privacyNotice.operatorName}{privacyMailto && <> at <a href={privacyMailto}>{privacyNotice.contactEmail}</a></>}, or use our <Link href="/contact">contact form</Link> with the subject “Privacy request”. Include your enquiry reference if available and describe the request. We may need proportionate information to confirm your identity or authority before disclosing or changing records.</p>

    <h2>8. Updates to this notice</h2>
    <p>We will update this page when our services or information handling change. The date above identifies the current notice. See our <Link href="/cookie-policy">Cookie policy</Link> for browser storage and measurement choices.</p>
  </article></main>;
}
