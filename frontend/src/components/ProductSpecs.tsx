"use client";

import { useSiteConfig } from "@/components/SiteConfigProvider";
import { UnitToggle } from "@/components/UnitToggle";
import { cardSpecs, specText, useUnitSystem } from "@/lib/units";
import type { ProductSpec } from "@/lib/types";

type Row = [string, string];

/** Key parameters beside the gallery, in the visitor's unit system. */
export function ProductKeySpecs({ specs }: { specs: ProductSpec[] }) {
  const system = useUnitSystem(useSiteConfig().defaultUnitSystem);
  const rows = cardSpecs(specs);
  if (!rows.length) return null;
  return <div className="detail-meta">{rows.map((spec) => <div key={spec.key}><span>{spec.label}</span><strong>{specText(spec, system)}</strong></div>)}</div>;
}

export function ProductSpecTable({ specs, basic, commercial }: { specs: ProductSpec[]; basic: Row[]; commercial: Row[] }) {
  const system = useUnitSystem(useSiteConfig().defaultUnitSystem);
  const groups = [
    { title: "Basic information", items: basic },
    { title: "Parameters", items: specs.map((spec): Row => [spec.label, specText(spec, system)]) },
    { title: "Commercial information", items: commercial },
  ].map((group) => ({ ...group, items: group.items.filter(([, value]) => Boolean(value)) })).filter((group) => group.items.length);
  return <>
    {specs.some((spec) => spec.quantity) && <div className="catalog-controls" style={{ marginBottom: 12 }}><UnitToggle /></div>}
    <div className="spec-groups">{groups.map((group) => <details className="spec-group" open key={group.title}><summary>{group.title}<span aria-hidden="true">＋</span></summary><div className="table-scroll"><table className="spec-table"><tbody>{group.items.map(([key, value]) => <tr key={key}><td>{key}</td><td>{value}</td></tr>)}</tbody></table></div></details>)}</div>
  </>;
}
