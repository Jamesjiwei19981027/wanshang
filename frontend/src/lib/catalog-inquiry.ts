export type CatalogueInquiryFilters = {
  q?: string;
  category?: string;
  application?: string;
  customizable?: string;
  madeToOrder?: string;
  oem?: string;
  /** Selected parameter filters, already described for people, e.g. "Rated capacity: at least 3,000 lb". */
  parameterNotes?: string[];
};

/** Carry catalogue constraints into the RFQ as notes; ranges are not ratings. */
export function buildCatalogueInquiryHref(filters: CatalogueInquiryFilters): string {
  const params = new URLSearchParams();
  const notes: string[] = [];
  const value = (text: string | undefined) => text?.trim() ?? "";
  if (value(filters.application)) params.set("application", value(filters.application));
  if (value(filters.category)) params.set("category", value(filters.category));
  if (value(filters.oem) === "true" || value(filters.oem) === "false") {
    params.set("oem_requirement", value(filters.oem) === "true" ? "yes" : "no");
  }
  if (value(filters.q)) notes.push(`Keyword / model: ${value(filters.q)}`);
  notes.push(...(filters.parameterNotes ?? []).map((note) => note.trim()).filter(Boolean));
  for (const [selected, label] of [
    [filters.customizable, "Customizable configuration"],
    [filters.madeToOrder, "Made to order"],
    [filters.oem, "OEM availability"],
  ] as const) {
    const choice = value(selected);
    if (choice === "true" || choice === "false") notes.push(`${label}: ${choice === "true" ? "yes" : "no"}`);
  }
  if (notes.length) params.set("special_requirements", `Please review these catalogue requirements:\n${notes.join("\n")}`);
  const query = params.toString();
  return `/request-a-quote${query ? `?${query}` : ""}`;
}
