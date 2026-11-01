"use client";

import { useEffect, useRef } from "react";
import { trackEvent } from "@/lib/events";

export function SearchEventTracker({
  query,
  resultCount,
  available = true,
}: {
  query: string;
  resultCount: number;
  available?: boolean;
}) {
  const trackedQuery = useRef("");

  useEffect(() => {
    if (!query || !available || trackedQuery.current === query) return;
    trackedQuery.current = query;
    // Search terms are visitor-entered text and can contain personal data or
    // pasted secrets. Measure usage without copying that text into event logs.
    // Only a term that found nothing is sent (consenting visitors only), so
    // staff can add synonyms or products; anything resembling contact data,
    // links or tokens is never sent.
    const shareable = resultCount === 0 && !/[@=]|:\/\/|\d{6,}/.test(query) && query.length <= 120;
    trackEvent("search_product", {
      query_length: query.length,
      results: resultCount,
      placement: "site_search",
      ...(shareable ? { query } : {}),
    });
  }, [available, query, resultCount]);

  return null;
}
