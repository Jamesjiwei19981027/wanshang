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
    trackEvent("search_product", { query_length: query.length, results: resultCount, placement: "site_search" });
  }, [available, query, resultCount]);

  return null;
}
