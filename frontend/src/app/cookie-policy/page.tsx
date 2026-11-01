import { pageMetadata } from "@/lib/seo";
import Link from "next/link";
import { CookiePreferencesButton } from "@/components/CookieBanner";
import { getSiteSettings } from "@/lib/api";
import { privacyNotice } from "@/lib/privacy-notice";
import { safeMailto } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Cookie policy",
  description: "Browser storage, enquiry drafts, Cloudflare verification and optional analytics on axle.wan-shang.com.",
  path: "/cookie-policy",
  index: false,
});

export default async function CookiePolicyPage() {
  const site = await getSiteSettings();
  const googleServices = [
    /^G-[A-Z0-9]+$/i.test(site.googleAnalyticsId) ? "Google Analytics" : "",
    /^GTM-[A-Z0-9]+$/i.test(site.googleTagManagerId) ? "Google Tag Manager" : "",
  ].filter(Boolean);
  const privacyMailto = safeMailto(privacyNotice.contactEmail);

  return <main><article className="shell policy">
    <p className="eyebrow">Privacy &amp; browser storage</p>
    <h1>Cookie policy</h1>
    <p className="policy-meta">Last updated: <time dateTime={privacyNotice.updated}>{privacyNotice.updatedLabel}</time></p>
    <p><strong>{privacyNotice.operatorName}</strong> operates {privacyNotice.website}. This page explains cookies and similar browser storage used for your RFQ list, draft, privacy choice, enquiry context and optional measurement.</p>
    <div className="policy-choice"><p>You can decline optional measurement and still browse the catalogue or send an enquiry. Security verification remains required for form submissions.</p><CookiePreferencesButton className="button-outline" /></div>

    <h2>1. Your measurement choice</h2>
    <ul>
      <li><strong>Essential only:</strong> optional page-view and interaction measurement stays off. The RFQ list, draft, your preference and form security continue to work.</li>
      <li><strong>Accept:</strong> allows first-party page-view and interaction measurement and any configured optional Google measurement services.</li>
    </ul>
    <p>Before a choice is made, optional measurement is off. Submitting an enquiry is separate: it can send its page, source and campaign context and create a business submission record even when optional measurement is off. See the <Link href="/privacy-policy#purposes">Privacy policy</Link> for how enquiry information is used.</p>

    <h2>2. Storage used by this website</h2>
    <p>Local storage stays in the browser without a programmed expiry. Session storage normally lasts for the current tab session and survives reloads; browser session-restore features may also restore it. Browser settings can shorten or block storage.</p>
    <div className="policy-table-scroll" role="region" aria-label="Browser storage details" tabIndex={0}>
      <table className="policy-table">
        <thead><tr><th scope="col">Storage</th><th scope="col">Purpose and information</th><th scope="col">Duration and clearing</th></tr></thead>
        <tbody>
          <tr><th scope="row"><code>axleforge-cookie-consent</code><span>Local storage</span></th><td>Remembers Essential only or Accept.</td><td>No programmed expiry. Replaced when you choose again, or removed through browser controls.</td></tr>
          <tr><th scope="row"><code>axleforge-rfq</code><span>Local storage</span></th><td>Your selected product IDs, names, models, configuration summaries and quantities.</td><td>No programmed expiry. Removing products or successfully submitting the RFQ updates the list; browser controls can clear it.</td></tr>
          <tr><th scope="row"><code>axleforge-rfq-draft</code><span>Session storage</span></th><td>Contact, technical and delivery fields you enter in the RFQ form. File contents and verification responses are excluded from the saved draft.</td><td>Current tab session. Removed after a successful RFQ submission. Clearing browser storage also removes it.</td></tr>
          <tr><th scope="row"><code>axleforge-submission:*</code><span>Session storage</span></th><td>A random submission identifier and a content fingerprint help prevent duplicate enquiries when a request is retried. Contact text, files and verification responses are not stored in this value.</td><td>Current tab session. Replaced when submission content changes and removed after a successful submission.</td></tr>
          <tr><th scope="row"><code>axleforge-attribution</code><span>Session storage</span></th><td>A random session ID, first and latest source, campaign values, page paths and referring website. Used with consented measurement or when an enquiry is submitted.</td><td>Current tab session. Declining or withdrawing measurement clears the current value; submitting an enquiry can create it again.</td></tr>
        </tbody>
      </table>
    </div>
    <p>On a shared device, clear the RFQ list and draft when you no longer want them available to the next user.</p>

    <h2>3. Optional analytics</h2>
    <p>After Accept, first-party measurement can record page paths and titles, search activity, product or download interactions and enquiry-related events. Built-in search analytics record query length and result count rather than the search text. Measurement uses the session context above. The built-in page URL tracking omits query strings and fragments; campaign fields are handled separately.</p>
    {googleServices.length > 0 ? <>
      <p>The currently configured Google services are {googleServices.join(" and ")}. Their scripts load only after analytics consent. Google Analytics can use cookies such as <code>_ga</code> and <code>_ga_&lt;id&gt;</code> for visitor and session measurement. Their lifetimes depend on the Google settings and browser limits; see <a href="https://support.google.com/analytics/answer/11397207" target="_blank" rel="noopener noreferrer">Google’s cookie information</a>.</p>
      {googleServices.includes("Google Tag Manager") && <p>Google Tag Manager loads the configured tags; any such tags must follow the measurement choice. Google’s own handling is described in its <a href="https://policies.google.com/privacy" target="_blank" rel="noopener noreferrer">Privacy Policy</a>.</p>}
    </> : <p>Google Analytics and Google Tag Manager are not currently enabled. No Google analytics cookies are required for the first-party measurement described above.</p>}

    <h2>4. Verification and staff sign-in</h2>
    <p>Our forms use Cloudflare Turnstile. It processes technical signals, including network and browser information, to detect automated abuse, independently of optional analytics consent. Cloudflare may use security cookies or similar storage depending on the verification and protection enabled for the site. See the <a href="https://www.cloudflare.com/turnstile-privacy-policy/" target="_blank" rel="noopener noreferrer">Turnstile Privacy Addendum</a> and <a href="https://www.cloudflare.com/cookie-policy/" target="_blank" rel="noopener noreferrer">Cloudflare Cookie Policy</a>.</p>
    <p>The administration area also uses session and CSRF security cookies for authorised staff. Visitors do not need a customer account to browse or submit an enquiry.</p>

    <h2>5. Changing your choice or clearing storage</h2>
    <p>Use the Cookie preferences button on this page or in the footer to choose again. Withdrawing measurement consent stops future optional collection by this website, reloads the page to stop previously loaded tags and attempts to expire accessible Google analytics cookies. It cannot remove every third-party or browser-protected cookie.</p>
    <p>You can also delete cookies, local storage and session storage in your browser settings. This may remove your RFQ list, draft and privacy preference. Changing a choice or clearing your browser does not recall requests already sent or delete records already stored on the server.</p>
    <p>For access, correction or deletion requests, contact {privacyNotice.operatorName}{privacyMailto && <> at <a href={privacyMailto}>{privacyNotice.contactEmail}</a></>} or use the <Link href="/contact">contact form</Link>. More detail is in our <Link href="/privacy-policy#requests">Privacy policy</Link>.</p>
  </article></main>;
}
