"use client";

import { createContext, useContext } from "react";
import { siteConfig, type PublicSiteConfig } from "@/lib/site";

const SiteConfigContext = createContext<PublicSiteConfig>(siteConfig);

export function SiteConfigProvider({
  value,
  children,
}: {
  value: PublicSiteConfig;
  children: React.ReactNode;
}) {
  return <SiteConfigContext.Provider value={value}>{children}</SiteConfigContext.Provider>;
}

export function useSiteConfig() {
  return useContext(SiteConfigContext);
}
