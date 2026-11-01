import { ContactForm } from "@/components/ContactForm";
import { PageHero } from "@/components/PageHero";
import { pageMetadata } from "@/lib/seo";
import Link from "next/link";
import { TrackedContactLink } from "@/components/TrackedContactLink";
import { getSiteSettings } from "@/lib/api";
import { safeMailto } from "@/lib/site";
import { contactPageJsonLd, serializeJsonLd } from "@/lib/structured-data";

export const metadata = pageMetadata({ title: "Contact", description: "Contact the technical sales team for trailer components and RFQ assistance.", path: "/contact" });
export default async function ContactPage() {
  const site = await getSiteSettings();
  const salesMailto = safeMailto(site.salesEmail);
  const structuredData = serializeJsonLd(contactPageJsonLd(site));
  return <main>
    <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: structuredData }} />
    <PageHero eyebrow="Contact WANSHANG" title="Discuss your trailer axle and parts requirements" description="Contact our team about trailer axles, suspension systems and related components. Include a drawing or part reference when available." crumb="Contact" />
    <section className="shell form-page"><div className="form-layout">
      <div className="form-card"><h2>Contact the team</h2><ContactForm /></div>
      <aside className="rfq-summary">
        <h2>{site.legalName || site.name}</h2>
        {site.chineseName && <p lang="zh-CN">{site.chineseName}</p>}
        {site.contactPerson && <p><strong>Contact person</strong><br />{site.contactPerson}{site.contactTitle && <> · {site.contactTitle}</>}</p>}
        {site.address && <p><strong>Address</strong><br />{site.address}</p>}
        {salesMailto && <p><strong>Email</strong><br /><TrackedContactLink href={salesMailto} placement="contact_page">{site.salesEmail}</TrackedContactLink></p>}
        {site.salesPhone && <p><strong>Phone</strong><br /><TrackedContactLink href={`tel:${site.salesPhone.replace(/[^\d+]/g, "")}`} placement="contact_page">{site.salesPhone}</TrackedContactLink></p>}
        {site.websiteUrl && <p><strong>Website</strong><br /><a href={site.websiteUrl} target="_blank" rel="noopener noreferrer">{new URL(site.websiteUrl).hostname}</a></p>}
        {site.whatsappUrl && <p><TrackedContactLink href={site.whatsappUrl} placement="contact_page" channel="whatsapp">Contact by WhatsApp</TrackedContactLink></p>}
        {site.businessHours && <p>{site.businessHours}</p>}
        {site.mapUrl && <p><a href={site.mapUrl} target="_blank" rel="noopener noreferrer">View map</a></p>}
        <p>For a quotation, include the product type, quantity, required load capacity, key dimensions and delivery destination.</p>
        <Link className="button" href="/request-a-quote">Request a quote</Link>
      </aside>
    </div></section>
  </main>;
}
