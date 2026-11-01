export type RawSearchParams = Record<string, string | string[] | undefined>;

const allowed = [
  "q",
  "application",
  "category",
  "customizable",
  "made_to_order",
  "oem",
  "sort",
  "page",
  "units",
] as const;

const maxLengths: Record<(typeof allowed)[number], number> = {
  q: 120,
  application: 50,
  category: 50,
  customizable: 5,
  made_to_order: 5,
  oem: 5,
  sort: 30,
  page: 10,
  units: 10,
};

// Category parameter filters: spec.<key>, spec.<key>__min, __max, __unit.
const SPEC_PARAMETER = /^spec\.[a-z0-9_-]{1,60}$/;
const MAX_SPEC_PARAMETERS = 24;

const firstValue = (value: string | string[] | null | undefined) =>
  (Array.isArray(value) ? value[0] : value ?? "").trim();

export const MAX_CATALOGUE_PAGE = 10_000;

export function parseCataloguePage(value: string | string[] | null | undefined): number {
  const parsed = Number(firstValue(value));
  return Number.isSafeInteger(parsed) && parsed >= 1 && parsed <= MAX_CATALOGUE_PAGE
    ? parsed
    : 1;
}

export function specParameters(params: RawSearchParams | URLSearchParams): Record<string, string> {
  const entries = params instanceof URLSearchParams ? [...params.entries()] : Object.entries(params);
  const result: Record<string, string> = {};
  for (const [key, raw] of entries) {
    if (!SPEC_PARAMETER.test(key)) continue;
    const value = firstValue(raw).slice(0, 160);
    if (value) result[key] = value;
    if (Object.keys(result).length >= MAX_SPEC_PARAMETERS) break;
  }
  return result;
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
  Object.assign(query, specParameters(params));
  if (fixedCategory) query.category = fixedCategory;
  return query;
}

export function hasCatalogueQuery(params: RawSearchParams) {
  return Object.values(params).some((value) => firstValue(value).length > 0);
}
