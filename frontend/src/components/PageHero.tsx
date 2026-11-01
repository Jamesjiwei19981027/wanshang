import Link from "next/link";

export function PageHero({ eyebrow, title, description, crumb = "" }: { eyebrow: string; title: string; description: string; crumb?: string }) {
  return <section className="page-hero"><div className="shell"><div className="breadcrumbs"><Link href="/">Home</Link>{crumb && <><span>/</span><span>{crumb}</span></>}</div><div className="page-heading"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div></div></section>;
}
