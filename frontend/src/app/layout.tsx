import type { Metadata } from "next";
import "./globals.css";
import { CookieBanner } from "@/components/CookieBanner";
import { PageViewTracker } from "@/components/PageViewTracker";
import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";
import { FloatingContact } from "@/components/FloatingContact";
import { BackToTop } from "@/components/BackToTop";
import { RfqToast } from "@/components/RfqToast";
import { SiteConfigProvider } from "@/components/SiteConfigProvider";
import { getApplications, getCategories, getSiteSettings } from "@/lib/api";
import { ConsentAnalytics } from "@/components/ConsentAnalytics";
import { organizationJsonLd, serializeJsonLd, webSiteJsonLd } from "@/lib/structured-data";

export async function generateMetadata(): Promise<Metadata> {
  const site = await getSiteSettings();
  const title = site.defaultSeoTitle || `${site.name} | Trailer Parts & Axle Assemblies`;
  const description = site.defaultSeoDescription || "Trailer axle assemblies, brakes, hubs and custom trailer components for OEM and aftermarket requirements.";
  return {
    title: { default: title, template: `%s | ${site.name}` },
    description,
    metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
    alternates: { canonical: "/" },
    icons: { icon: "/icon.svg" },
    openGraph: {
      type: "website",
      siteName: site.name,
      title,
      description,
      url: "/",
      images: site.defaultOgImageUrl ? [site.defaultOgImageUrl] : undefined,
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: site.defaultOgImageUrl ? [site.defaultOgImageUrl] : undefined,
    },
  };
}

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const [site, categories, applications] = await Promise.all([getSiteSettings(), getCategories(), getApplications()]);
  const siteGraph = serializeJsonLd({
    "@context": "https://schema.org",
    "@graph": [organizationJsonLd(site), webSiteJsonLd(site)],
  });
  return <html lang="en" data-scroll-behavior="smooth"><body><SiteConfigProvider value={site}><script type="application/ld+json" dangerouslySetInnerHTML={{ __html: siteGraph }} /><a className="skip-link" href="#main-content">Skip to main content</a><Header categories={categories ?? []} applications={applications ?? []} /><div id="main-content" tabIndex={-1}>{children}</div><Footer categories={categories ?? []} /><FloatingContact /><BackToTop /><RfqToast /><CookieBanner /><PageViewTracker /><ConsentAnalytics /></SiteConfigProvider></body></html>;
}
