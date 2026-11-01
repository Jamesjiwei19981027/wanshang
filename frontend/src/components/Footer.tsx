"use client";

import Link from "next/link";
import { Brand } from "@/components/Header";
import { CookiePreferencesButton } from "@/components/CookieBanner";
import { TrackedContactLink } from "@/components/TrackedContactLink";
import { useSiteConfig } from "@/components/SiteConfigProvider";
import { safeMailto } from "@/lib/site";
import type { Category } from "@/lib/types";

export function Footer({ categories = [] }: { categories?: Category[] }) {
  const site = useSiteConfig();
  const salesMailto = safeMailto(site.salesEmail);
  return <footer className="footer"><div className="shell">
    <div className="footer-grid">
      <div><Brand /><p>{site.footerText || "Configuration-led trailer axles and running gear for manufacturers, distributors and professional buyers."}</p>{site.legalName && <p>{site.legalName}</p>}{site.chineseName && <p lang="zh-CN">{site.chineseName}</p>}</div>
      <div><h3>Products</h3><ul><li><Link href="/products">All products</Link></li>{categories.slice(0, 6).map((category) => <li key={category.id}><Link href={`/products/${category.slug}`}>{category.name}</Link></li>)}</ul></div>
      <div><h3>Explore</h3><ul><li><Link href="/about">About WANSHANG</Link></li><li><Link href="/news">News</Link></li><li><Link href="/faq">FAQ</Link></li><li><Link href="/downloads">Download Centre</Link></li><li><Link href="/contact">Contact us</Link></li></ul></div>
      <div><h3>Contact</h3><ul>
        {site.contactPerson && <li>{site.contactPerson}{site.contactTitle && <> · {site.contactTitle}</>}</li>}
        {site.address && <li>{site.address}</li>}
        {salesMailto && <li><TrackedContactLink href={salesMailto} placement="footer">{site.salesEmail}</TrackedContactLink></li>}
        {site.salesPhone && <li><TrackedContactLink href={`tel:${site.salesPhone.replace(/[^\d+]/g, "")}`} placement="footer">{site.salesPhone}</TrackedContactLink></li>}
        {site.websiteUrl && <li><a href={site.websiteUrl} target="_blank" rel="noopener noreferrer">{new URL(site.websiteUrl).hostname}</a></li>}
        {site.whatsappUrl && <li><TrackedContactLink href={site.whatsappUrl} placement="footer" channel="whatsapp">WhatsApp</TrackedContactLink></li>}
        {site.businessHours && <li>{site.businessHours}</li>}
        {site.linkedInUrl && <li><a href={site.linkedInUrl} target="_blank" rel="noopener noreferrer">LinkedIn</a></li>}
        <li><Link href="/request-a-quote">Send an RFQ</Link></li>
      </ul></div>
    </div>
    <div className="footer-bottom"><span>© {new Date().getFullYear()} {site.legalName || site.name}</span><span><Link href="/privacy-policy">Privacy</Link> · <Link href="/cookie-policy">Cookies</Link> · <CookiePreferencesButton /></span></div>
  </div></footer>;
}
