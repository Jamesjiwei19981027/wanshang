export type CatalogueInquiryFilters = Partial<Record<
  | "q" | "category" | "application" | "capacityMin" | "capacityMax" | "capacityUnit"
  | "type" | "brake" | "boltPattern" | "hubFace" | "springCenter" | "tubeShape"
  | "finish" | "customizable" | "madeToOrder" | "oem",
  string
>>;

/** Carry catalogue constraints into the RFQ without treating a range as a rating. */
export function buildCatalogueInquiryHref(filters: CatalogueInquiryFilters): string {
  const params = new URLSearchParams();
  const notes: string[] = [];
  const value = (key: keyof CatalogueInquiryFilters) => filters[key]?.trim() ?? "";
  const copy = (key: keyof CatalogueInquiryFilters, queryKey: string) => {
    const text = value(key);
    if (text) params.set(queryKey, text);
  };
  const describe = (key: keyof CatalogueInquiryFilters, label: string) => {
    const text = value(key);
    if (text) notes.push(`${label}: ${text}`);
  };

  for (const [key, queryKey] of [
    ["application", "application"], ["brake", "brake"], ["boltPattern", "bolt_pattern"],
    ["hubFace", "hub_face"], ["springCenter", "spring_center"], ["finish", "finish"],
  ] as const) copy(key, queryKey);

  if (value("type")) {
    params.set("type", ["straight", "drop", "torsion"].includes(value("type")) ? value("type") : "");
  }
  const tubeShape = value("tubeShape").toLocaleLowerCase("en");
  if (tubeShape) params.set("tube_shape", ["round", "square"].includes(tubeShape) ? tubeShape : "");
  if (value("oem") === "true" || value("oem") === "false") {
    params.set("oem_requirement", value("oem") === "true" ? "yes" : "no");
  }

  const minimum = value("capacityMin");
  const maximum = value("capacityMax");
  const capacityUnit = value("capacityUnit") || "lb";
  // A changed unit must not reinterpret an exact capacity from an older draft.
  if (value("capacityUnit") || minimum || maximum) params.set("required_axle_capacity", "");
  if ((value("capacityUnit") || minimum || maximum) && ["lb", "kg"].includes(capacityUnit)) {
    params.set("capacity_unit", capacityUnit);
  }
  if (minimum || maximum) {
    const range = minimum && maximum
      ? `${minimum}–${maximum}`
      : minimum ? `at least ${minimum}` : `up to ${maximum}`;
    notes.push(`Catalogue capacity range: ${range} ${capacityUnit}. Confirm the required rated axle capacity.`);
  }

  const dimensions = [value("hubFace"), value("springCenter")].filter(Boolean);
  const units = dimensions.map((dimension) => /\bmm\s*$/i.test(dimension)
    ? "mm" : /(?:\b(?:in|inch|inches)|")\s*$/i.test(dimension) ? "in" : "");
  if (units.length && units.every((unit) => unit && unit === units[0])) {
    params.set("dimension_unit", units[0]);
  }

  for (const [key, label] of [
    ["q", "Keyword / model"], ["category", "Product category"], ["application", "Application"],
    ["type", "Axle type"], ["brake", "Brake"], ["boltPattern", "Bolt pattern"],
    ["hubFace", "Hub face (as entered)"], ["springCenter", "Spring centre (as entered)"],
    ["tubeShape", "Tube shape"], ["finish", "Finish"],
  ] as const) describe(key, label);
  for (const [key, label] of [
    ["customizable", "Customizable configuration"], ["madeToOrder", "Made to order"], ["oem", "OEM availability"],
  ] as const) {
    const selected = value(key);
    if (selected === "true" || selected === "false") notes.push(`${label}: ${selected === "true" ? "yes" : "no"}`);
  }
  if (notes.length) params.set("special_requirements", `Please review these catalogue requirements:\n${notes.join("\n")}`);
  const query = params.toString();
  return `/request-a-quote${query ? `?${query}` : ""}`;
}
