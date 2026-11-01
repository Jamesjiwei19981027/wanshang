/** Shared RFQ form helpers: category parameter fields and payload building. */

import type { SpecFieldDefinition, UnitSystem } from "@/lib/types";

export const CATEGORY_FIELD_PREFIX = "requested_categories.";
export const SPEC_FIELD_PREFIX = "specifications.";

export function specFieldName(section: string, key: string) {
  return `${SPEC_FIELD_PREFIX}${section}.${key}`;
}

export function fieldUnitLabel(field: SpecFieldDefinition, system: UnitSystem): string {
  const code = field.default_unit?.[system];
  if (!code) return "";
  return field.units?.find((unit) => unit.code === code)?.label ?? code;
}

/**
 * Move "requested_categories.<slug>" checkboxes and
 * "specifications.<section>.<key>" inputs into the two JSON fields the API
 * expects, removing the flat entries from the form data.
 */
export function packRfqForm(form: FormData) {
  const categories: string[] = [];
  const specifications: Record<string, Record<string, string>> = {};
  for (const [name, value] of [...form.entries()]) {
    if (name.startsWith(CATEGORY_FIELD_PREFIX)) {
      if (value === "true") categories.push(name.slice(CATEGORY_FIELD_PREFIX.length));
      form.delete(name);
    } else if (name.startsWith(SPEC_FIELD_PREFIX)) {
      form.delete(name);
      const [section, ...rest] = name.slice(SPEC_FIELD_PREFIX.length).split(".");
      const key = rest.join(".");
      const text = typeof value === "string" ? value.trim() : "";
      if (!section || !key || !text) continue;
      (specifications[section] ??= {})[key] = text;
    }
  }
  // Parameters of a category the buyer deselected are not sent.
  for (const section of Object.keys(specifications)) {
    if (section !== "general" && !categories.includes(section)) delete specifications[section];
  }
  form.set("requested_categories", JSON.stringify(categories));
  form.set("specifications", JSON.stringify(specifications));
  return { categories, specifications };
}
