const publicValue = (value: string | undefined) => value?.trim() ?? "";

const isPublicHostname = (hostnameValue: string): boolean => {
  const hostname = hostnameValue.replace(/^\[|\]$/g, "").toLocaleLowerCase("en");
  const labels = hostname.split(".");
  return (
    hostname.length <= 253
    && !hostname.includes(":")
    && !/^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*$/i.test(hostname)
    && labels.length >= 2
    && labels.every((label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))
    && !["localhost", "local", "internal", "test", "invalid"].includes(labels.at(-1) ?? "")
  );
};

const EMAIL_LOCAL_PATTERN = /^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$/i;
const EMAIL_DOMAIN_PATTERN = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/i;

/** Build a mailto: href only for a clean single-line email address. */
export function safeMailto(email: string): string {
  const trimmed = email.trim();
  if (!trimmed || trimmed.length > 254) return "";
  const parts = trimmed.split("@");
  if (parts.length !== 2) return "";
  const [local, domain] = parts;
  if (local.length > 64 || !EMAIL_LOCAL_PATTERN.test(local) || !EMAIL_DOMAIN_PATTERN.test(domain)) return "";
  // A valid email local part can itself contain ?, &, # or %. They must be
  // encoded as address data, otherwise mail clients interpret URI headers such
  // as ?bcc= or %0d%0a as part of a pre-filled outgoing message.
  return `mailto:${encodeURIComponent(local)}@${domain.toLowerCase()}`;
}

export const safePublicHttpsUrl = (value: string | undefined): string => {
  const candidate = publicValue(value);
  if (!candidate) return "";
  try {
    const parsed = new URL(candidate);
    return parsed.protocol === "https:"
      && !parsed.username
      && !parsed.password
      && isPublicHostname(parsed.hostname)
      ? parsed.href
      : "";
  } catch {
    return "";
  }
};

/** Verified company identity; CMS and environment values remain editable. */
export const DEFAULT_BRAND_NAME = "WANSHANG";

export type PublicSiteConfig = {
  name: string;
  legalName: string;
  chineseName: string;
  contactPerson: string;
  contactTitle: string;
  websiteUrl: string;
  tagline: string;
  logoUrl: string;
  salesEmail: string;
  contactEmail: string;
  salesPhone: string;
  whatsappUrl: string;
  address: string;
  businessHours: string;
  mapUrl: string;
  linkedInUrl: string;
  footerText: string;
  defaultSeoTitle: string;
  defaultSeoDescription: string;
  defaultOgImageUrl: string;
  googleAnalyticsId: string;
  googleTagManagerId: string;
};

export const siteConfig: PublicSiteConfig = {
  name: publicValue(process.env.NEXT_PUBLIC_COMPANY_NAME)
    || DEFAULT_BRAND_NAME,
  legalName: "Shandong Wanshang International Trading Co., Ltd.",
  chineseName: "山东万商国际商贸有限公司",
  contactPerson: "Si Mingzhu (司明珠)",
  contactTitle: "Chairperson",
  websiteUrl: "https://www.wan-shang.com",
  tagline: publicValue(process.env.NEXT_PUBLIC_COMPANY_TAGLINE)
    || "Trailer Axles & Suspension Systems",
  logoUrl: "",
  salesEmail: publicValue(process.env.NEXT_PUBLIC_SALES_EMAIL) || "simingzhu727@gmail.com",
  contactEmail: publicValue(process.env.NEXT_PUBLIC_SALES_EMAIL) || "simingzhu727@gmail.com",
  salesPhone: publicValue(process.env.NEXT_PUBLIC_SALES_PHONE) || "+86 15253754552",
  whatsappUrl: safePublicHttpsUrl(process.env.NEXT_PUBLIC_WHATSAPP_URL),
  address: publicValue(process.env.NEXT_PUBLIC_COMPANY_ADDRESS) || "Liangshan Subdistrict, Liangshan County, Shandong Province, China",
  businessHours: publicValue(process.env.NEXT_PUBLIC_BUSINESS_HOURS),
  mapUrl: safePublicHttpsUrl(process.env.NEXT_PUBLIC_MAP_URL),
  linkedInUrl: safePublicHttpsUrl(process.env.NEXT_PUBLIC_LINKEDIN_URL),
  footerText: "A manufacturer of trailer axles, suspension systems and related components based in Liangshan, Shandong, China.",
  defaultSeoTitle: "WANSHANG | Trailer Axle & Suspension Manufacturer",
  defaultSeoDescription: "Shandong Wanshang International Trading Co., Ltd. manufactures trailer axles, suspension systems and related components in Liangshan, Shandong, China.",
  defaultOgImageUrl: "",
  googleAnalyticsId: "",
  googleTagManagerId: "",
};
