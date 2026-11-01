import type { ProductSpec, UnitSystem } from "@/lib/types";

// The site default (Americas): pounds and inches. Staff can change the
// default in the admin; it reaches visitors through site settings.
export const DEFAULT_UNIT_SYSTEM: UnitSystem = "imperial";

export const isUnitSystem = (value: unknown): value is UnitSystem => value === "imperial" || value === "metric";

export function specText(spec: ProductSpec, system: UnitSystem): string {
  return spec.display?.[system] || spec.display?.imperial || "—";
}

export function cardSpecs(specs: ProductSpec[] | undefined, limit = 6): ProductSpec[] {
  const list = specs ?? [];
  const marked = list.filter((spec) => spec.show_on_card);
  return (marked.length ? marked : list).slice(0, limit);
}
