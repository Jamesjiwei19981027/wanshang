"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { trackEvent } from "@/lib/events";
import { useSiteConfig } from "@/components/SiteConfigProvider";

export function FloatingContact() {
  const pathname = usePathname();
  const site = useSiteConfig();
  if (/^\/products\/[^/]+\/[^/]+\/?$/.test(pathname)) return null;
  const whatsappUrl = site.whatsappUrl;
  return <Link
    className={`whatsapp-float${whatsappUrl ? "" : " contact-float"}`}
    href={whatsappUrl || "/contact"}
    target={whatsappUrl.startsWith("http") ? "_blank" : undefined}
    rel={whatsappUrl.startsWith("http") ? "noopener noreferrer" : undefined}
    onClick={() => { if (whatsappUrl) trackEvent("click_whatsapp", { placement: "floating" }); }}
    aria-label={whatsappUrl ? "Contact the sales team by WhatsApp" : "Contact the sales team"}
  ><span aria-hidden="true">{whatsappUrl ? "WA" : "?"}</span><strong>{whatsappUrl ? "WhatsApp" : "Contact us"}</strong></Link>;
}
