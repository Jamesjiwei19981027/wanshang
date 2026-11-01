"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { trackEvent } from "@/lib/events";
import { recordPageView } from "@/lib/attribution";

export function PageViewTracker() {
  const pathname = usePathname();
  useEffect(() => {
    const record = () => {
      recordPageView(document.title);
      trackEvent("page_view");
    };
    record();
    window.addEventListener("axleforge-cookie-change", record);
    return () => window.removeEventListener("axleforge-cookie-change", record);
  }, [pathname]);
  return null;
}
