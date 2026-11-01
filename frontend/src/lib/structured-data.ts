import type { Category, Product } from "@/lib/types";
import type { PublicSiteConfig } from "@/lib/site";

export function siteOrigin(): string {
  return (process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000").replace(/\/+$/, "");
}

export function absoluteUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    try {
      const parsed = new URL(path);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
    } catch {
      // fall through to site-relative handling
    }
  }
  return `${siteOrigin()}${path.startsWith("/") ? path : `/${path}`}`;
}

/** Only allow http(s) href values from API/CMS data; otherwise empty string. */
export function safeHref(url: string | null | undefined): string {
  const value = String(url ?? "").trim();
  if (!value) return "";
  // URL parsers normalize backslashes and ASCII controls. Validate before
  // parsing so an apparent local path cannot become a protocol-relative URL.
  if (/[\\\u0000-\u0020\u007f]/.test(value)) return "";
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try {
    const parsed = new URL(value);
    return !parsed.username && !parsed.password
      && (parsed.protocol === "https:" || parsed.protocol === "http:")
      ? parsed.href : "";
  } catch {
    return "";
  }
}

/** Escape `<` so JSON-LD cannot break out of a <script> tag. */
export function serializeJsonLd(value: unknown): string {
  return JSON.stringify(value).replaceAll("<", "\\u003c");
}

export function organizationJsonLd(site: PublicSiteConfig) {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    "@id": `${siteOrigin()}/#organization`,
    name: site.legalName || site.name,
    alternateName: [site.name, site.chineseName].filter(Boolean),
    url: `${siteOrigin()}/`,
    logo: site.logoUrl ? absoluteUrl(site.logoUrl) : undefined,
    email: site.salesEmail || undefined,
    telephone: site.salesPhone || undefined,
    address: site.address
      ? { "@type": "PostalAddress", streetAddress: site.address }
      : undefined,
    sameAs: [site.websiteUrl, site.linkedInUrl, site.mapUrl].filter(Boolean),
    description: site.defaultSeoDescription || undefined,
  };
}

export function webSiteJsonLd(site: PublicSiteConfig) {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "@id": `${siteOrigin()}/#website`,
    name: site.name,
    url: `${siteOrigin()}/`,
    inLanguage: "en",
    publisher: { "@id": `${siteOrigin()}/#organization` },
    potentialAction: {
      "@type": "SearchAction",
      target: {
        "@type": "EntryPoint",
        urlTemplate: `${siteOrigin()}/search?q={search_term_string}`,
      },
      "query-input": "required name=search_term_string",
    },
  };
}

export function breadcrumbJsonLd(items: { name: string; path: string }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: item.name,
      item: absoluteUrl(item.path),
    })),
  };
}

export function faqJsonLd(faqs: { question: string; answer: string }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((faq) => ({
      "@type": "Question",
      name: faq.question,
      acceptedAnswer: { "@type": "Answer", text: faq.answer },
    })),
  };
}

export function productJsonLd(product: Product, path: string, primaryImage: string, brandName?: string) {
  const additionalProperty = (product.specs ?? [])
    .filter((spec) => Boolean(spec.display?.imperial))
    .map((spec) => ({
      "@type": "PropertyValue",
      name: spec.label,
      value: spec.display.imperial,
    }));

  return {
    "@context": "https://schema.org",
    "@type": "Product",
    name: product.title,
    sku: product.sku,
    model: product.model,
    description: product.short_description,
    image: primaryImage ? absoluteUrl(primaryImage) : undefined,
    category: product.category.name,
    url: absoluteUrl(path),
    brand: { "@type": "Brand", name: brandName || "Trailer Components" },
    manufacturer: { "@type": "Organization", "@id": `${siteOrigin()}/#organization` },
    additionalProperty: additionalProperty.length ? additionalProperty : undefined,
  };
}

export function categoryJsonLd(category: Category, path: string) {
  return {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: category.name,
    description: category.description,
    url: absoluteUrl(path),
    isPartOf: { "@id": `${siteOrigin()}/#website` },
    about: { "@type": "Thing", name: category.name },
  };
}

export function contactPageJsonLd(site: PublicSiteConfig) {
  return {
    "@context": "https://schema.org",
    "@type": "ContactPage",
    name: `Contact ${site.name}`,
    url: absoluteUrl("/contact"),
    mainEntity: { "@id": `${siteOrigin()}/#organization` },
  };
}
