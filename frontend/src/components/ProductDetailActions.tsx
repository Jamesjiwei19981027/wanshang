"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { addToRfq } from "@/lib/rfq";
import { trackEvent } from "@/lib/events";
import type { Product } from "@/lib/types";
import { useSiteConfig } from "@/components/SiteConfigProvider";

export function ProductDetailActions({ product }: { product: Product }) {
  const site = useSiteConfig();
  const whatsappUrl = site.whatsappUrl;
  const viewed = useRef(false);
  useEffect(() => {
    if (viewed.current) return;
    viewed.current = true;
    trackEvent("view_product", { product_id: product.id, model: product.model });
  }, [product.id, product.model]);
  const addAndTrack = () => {
    if (addToRfq(product)) {
      trackEvent("add_to_inquiry_list", { product_id: product.id, model: product.model, placement: "product_detail" });
    }
  };
  return <>
    <div className="detail-actions"><Link href="/request-a-quote" className="button" onClick={addAndTrack}>Request this configuration</Link><button className="button-outline" type="button" onClick={() => window.print()}>Print / save PDF</button></div>
    <Link className="desktop-product-float" href="/request-a-quote" onClick={addAndTrack}>Request a quote</Link>
    <div className="mobile-product-bar" aria-label="Product contact actions">
      <Link href="/request-a-quote" className="button" onClick={addAndTrack}>Request quote</Link>
      <Link
        href={whatsappUrl || "/contact"}
        className="button-whatsapp"
        target={whatsappUrl.startsWith("http") ? "_blank" : undefined}
        rel={whatsappUrl.startsWith("http") ? "noopener noreferrer" : undefined}
        onClick={() => { if (whatsappUrl) trackEvent("click_whatsapp", { placement: "product_detail", product_id: product.id }); }}
      >{whatsappUrl ? "WhatsApp" : "Contact us"}</Link>
    </div>
  </>;
}
