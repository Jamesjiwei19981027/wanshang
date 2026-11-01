"use client";

import { trackEvent } from "@/lib/events";
import { safeHref } from "@/lib/structured-data";

export function DocumentDownloadLink({
  url,
  title,
  productModel,
}: {
  url: string;
  title: string;
  productModel?: string;
}) {
  const href = safeHref(url);
  if (!href) return null;
  return <a
    className="link-arrow"
    href={href}
    onClick={() => trackEvent("download_document", { title, product_model: productModel ?? "" })}
  >
    Download
  </a>;
}
