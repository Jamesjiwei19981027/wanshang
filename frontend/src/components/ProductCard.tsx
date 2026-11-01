"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { addToRfq } from "@/lib/rfq";
import { trackEvent } from "@/lib/events";
import { getProductPath } from "@/lib/product-url";
import { cardSpecs, specText, useUnitSystem } from "@/lib/units";
import { useSiteConfig } from "@/components/SiteConfigProvider";
import type { Product } from "@/lib/types";

export function ProductCard({ product, eagerImage = false }: { product: Product; eagerImage?: boolean }) {
  const [added, setAdded] = useState(false);
  const system = useUnitSystem(useSiteConfig().defaultUnitSystem);
  const addedTimerRef = useRef<number | null>(null);
  useEffect(() => () => {
    if (addedTimerRef.current !== null) window.clearTimeout(addedTimerRef.current);
  }, []);
  const add = () => {
    if (!addToRfq(product)) return;
    trackEvent("add_to_inquiry_list", { product_id: product.id, model: product.model });
    setAdded(true);
    if (addedTimerRef.current !== null) window.clearTimeout(addedTimerRef.current);
    addedTimerRef.current = window.setTimeout(() => setAdded(false), 1400);
  };
  return <article className="product-card"><div className="product-media">{product.image_url ? <Image src={product.image_url} alt={`${product.title} product view`} fill loading={eagerImage ? "eager" : "lazy"} sizes="(max-width: 700px) 100vw, (max-width: 1000px) 50vw, 33vw" /> : <div className="media-placeholder" role="img" aria-label={`${product.title} image pending publication`}>Product image pending</div>}<span className="badge">{product.made_to_order ? "Made to order" : product.is_customizable ? "Configurable" : "Standard"}</span>{product.oem_available && <span className="badge orange" style={{ left: "auto", right: 13 }}>OEM</span>}</div><div className="product-body"><div className="product-number"><span>P/N: {product.sku}</span><span>{product.model}</span></div><h3 className="product-title">{product.title}</h3><ul className="specs">{cardSpecs(product.specs).map((spec) => <li key={spec.key}><span>{spec.label}</span><strong>{specText(spec, system)}</strong></li>)}<li><span>Application</span><strong>{product.applications.slice(0, 2).map((item) => item.name).join(", ") || "Confirm"}</strong></li><li><span>Status</span><strong>{product.made_to_order ? "Made to order" : product.is_customizable ? "Customizable" : "Standard"}{product.oem_available ? " · OEM" : ""}</strong></li></ul><div className="product-actions"><Link className="button-outline" href={getProductPath(product)}>View details</Link><button className="button" type="button" onClick={add} aria-label={`Add ${product.title} to RFQ`}>{added ? "Added" : "Add RFQ"}</button></div></div></article>;
}
