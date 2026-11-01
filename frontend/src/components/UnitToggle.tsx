"use client";

import { setUnitSystem, useUnitSystem } from "@/lib/units";
import { useSiteConfig } from "@/components/SiteConfigProvider";

export function UnitToggle() {
  const { defaultUnitSystem } = useSiteConfig();
  const system = useUnitSystem(defaultUnitSystem);
  return <div className="view-toggle" role="group" aria-label="Units">
    <button type="button" aria-pressed={system === "imperial"} className={system === "imperial" ? "active" : ""} onClick={() => setUnitSystem("imperial")}>lb · in</button>
    <button type="button" aria-pressed={system === "metric"} className={system === "metric" ? "active" : ""} onClick={() => setUnitSystem("metric")}>kg · mm</button>
  </div>;
}
