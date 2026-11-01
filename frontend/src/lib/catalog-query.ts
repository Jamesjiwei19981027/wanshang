export type RawSearchParams = Record<string, string | string[] | undefined>;

const allowed = [
  "q",
  "capacity",
  "capacity_min",
  "capacity_max",
  "brake",
  "type",
  "application",
  "category",
  "bolt_pattern",
  "hub_face",
  "spring_center",
  "tube_shape",
  "finish",
  "customizable",
  "made_to_order",
  "oem",
  "sort",
  "page",
] as const;

const maxLengths: Record<(typeof allowed)[number], number> = {
  q: 120,
  capacity: 32,
  capacity_min: 32,
  capacity_max: 32,
  brake: 80,
  type: 16,
  application: 50,
  category: 50,
  bolt_pattern: 60,
  hub_face: 60,
  spring_center: 60,
  tube_shape: 40,
  finish: 80,
  customizable: 5,
  made_to_order: 5,
  oem: 5,
  sort: 30,
  page: 10,
};

const firstValue = (value: string | string[] | null | undefined) =>
  (Array.isArray(value) ? value[0] : value ?? "").trim();

export const MAX_CATALOGUE_PAGE = 10_000;

export function parseCataloguePage(value: string | string[] | null | undefined): number {
  const parsed = Number(firstValue(value));
  return Number.isSafeInteger(parsed) && parsed >= 1 && parsed <= MAX_CATALOGUE_PAGE
    ? parsed
    : 1;
}

export function productApiQuery(params: RawSearchParams, fixedCategory?: string): Record<string, string> {
  const query: Record<string, string> = {};
  for (const key of allowed) {
    const value = firstValue(params[key]);
    if (key === "page") {
      if (value) query.page = String(parseCataloguePage(value));
      continue;
    }
    if (value) query[key] = value.slice(0, maxLengths[key]);
  }
  const unit = firstValue(params.unit) || firstValue(params.capacity_unit);
  if (unit) query.capacity_unit = unit.slice(0, 8);
  if (fixedCategory) query.category = fixedCategory;
  return query;
}

export function hasCatalogueQuery(params: RawSearchParams) {
  return Object.values(params).some((value) => firstValue(value).length > 0);
}
