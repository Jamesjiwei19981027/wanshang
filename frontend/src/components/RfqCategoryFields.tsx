import type { Category, SpecFieldDefinition, UnitSystem } from "@/lib/types";
import { fieldUnitLabel, specFieldName } from "@/lib/rfq-form";

function ParameterInput({ section, field, system, defaultValue = "" }: { section: string; field: SpecFieldDefinition; system: UnitSystem; defaultValue?: string }) {
  const name = specFieldName(section, field.key);
  const id = `rfq-${section}-${field.key}`;
  const unit = field.type === "number" ? fieldUnitLabel(field, system) : "";
  const label = `${field.label}${unit ? ` (${unit})` : ""}`;
  if (field.type === "choice") {
    return <div className="field"><label htmlFor={id}>{label}</label><select id={id} name={name} defaultValue={defaultValue}><option value="">Not sure</option>{(field.choices ?? []).map((choice) => <option key={choice}>{choice}</option>)}</select></div>;
  }
  if (field.type === "boolean") {
    return <div className="field"><label htmlFor={id}>{label}</label><select id={id} name={name} defaultValue={defaultValue}><option value="">Not sure</option><option value="yes">Yes</option><option value="no">No</option><option value="discuss">Discuss</option></select></div>;
  }
  return <div className="field"><label htmlFor={id}>{label}</label><input id={id} name={name} maxLength={160} defaultValue={defaultValue} inputMode={field.type === "number" ? "decimal" : undefined} placeholder={field.help_text || (unit ? `Value in ${unit}, or add another unit` : "")} /></div>;
}

/** Technical questions for each selected category, from its parameter list. */
export function RfqCategoryFields({
  categories,
  selected,
  system,
  values = {},
}: {
  categories: Category[];
  selected: string[];
  system: UnitSystem;
  values?: Record<string, Record<string, string>>;
}) {
  const chosen = categories.filter((category) => selected.includes(category.slug) && (category.rfq_fields?.length ?? 0) > 0);
  if (!chosen.length) return null;
  return <>{chosen.map((category) => <div key={category.slug}>
    <h3 className="rfq-section-note">{category.name}</h3>
    <div className="form-grid three">
      {(category.rfq_fields ?? []).map((field) => <ParameterInput key={field.key} section={category.slug} field={field} system={system} defaultValue={values[category.slug]?.[field.key] ?? ""} />)}
    </div>
  </div>)}</>;
}
