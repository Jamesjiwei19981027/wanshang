import type { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { notFound } from "next/navigation";
import { getAllProducts, getApplications } from "@/lib/api";
import { PageHero } from "@/components/PageHero";
import { ProductCard } from "@/components/ProductCard";
import { pageMetadata } from "@/lib/seo";
import { getApplicationGuidance } from "@/lib/application-guidance";

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const slug = (await params).slug;
  const applications = await getApplications();
  const application = applications?.find((item) => item.slug === slug);
  if (applications && !application) notFound();
  if (!application) return { title: "Applications temporarily unavailable", robots: { index: false, follow: false } };
  return pageMetadata({ title: application.name, description: application.summary, path: `/applications/${slug}` });
}

export default async function ApplicationPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const applications = await getApplications();
  if (!applications) return <main className="not-found"><h1>Application data is temporarily unavailable</h1><p>Please try again shortly.</p></main>;
  const application = applications.find((item) => item.slug === slug);
  if (!application) notFound();
  const guidance = getApplicationGuidance(application.slug, application.recommended_finish);
  const recommended = await getAllProducts({ application: application.slug });
  return <main>
    <PageHero eyebrow="Trailer application" title={application.name} description={application.summary} crumb="Applications" />
    <section className="section"><div className="shell split"><div>
      <p className="eyebrow">Configuration factors</p><h2 style={{ margin: 0, fontSize: "2.25rem", letterSpacing: "-.04em" }}>Start with the working environment.</h2>
      <p className="section-copy">Review actual load, trailer geometry, wheel and tyre specification, braking needs, maintenance expectations and corrosion exposure.</p>
      <div className="content-card" style={{ marginTop: 26 }}><h2>Configuration direction</h2><div className="table-scroll"><table className="spec-table"><tbody><tr><td>Common structure</td><td>{guidance.structure}</td></tr><tr><td>Load range</td><td>{guidance.loadRange}</td></tr><tr><td>Axle direction</td><td>{guidance.axleDirection}</td></tr><tr><td>Capacity direction</td><td>{guidance.capacityDirection}</td></tr><tr><td>Brake direction</td><td>{guidance.brakeDirection}</td></tr><tr><td>Bolt pattern direction</td><td>{guidance.boltDirection}</td></tr><tr><td>Finish direction</td><td>{guidance.finishDirection}</td></tr><tr><td>Final approval</td><td>Technical review required</td></tr></tbody></table></div></div>
      <div className="content-card"><h2>Operating-environment checklist</h2><ul>{guidance.considerations.map((item) => <li key={item}>{item}</li>)}</ul></div>
      <Link className="button" href={`/request-a-quote?application=${application.slug}`}>Request a configuration</Link>
    </div><div className="image-panel">{application.image_url ? <Image src={application.image_url} alt={`${application.name} trailer application`} fill sizes="(max-width: 1000px) 100vw, 50vw" /> : <div className="media-placeholder" role="img" aria-label={`${application.name} image pending publication`}>Application image pending</div>}<div className="metric-card"><b>Application-led</b><span>Use case informs axle, brake and finish choices</span></div></div></div></section>
    <section className="section section-alt"><div className="shell"><div className="section-head"><div><p className="eyebrow">Starting platforms</p><h2>Relevant catalogue products</h2></div><Link className="link-arrow" href={`/products/trailer-axles?application=${application.slug}`}>Filtered catalogue</Link></div>
      {recommended === undefined
        ? <div className="form-error" role="alert"><h2>Recommended products are temporarily unavailable</h2><p>The catalogue could not be loaded. Please try again shortly or send the application details for technical review.</p></div>
        : recommended.length
          ? <div className="product-grid">{recommended.map((product) => <ProductCard product={product} key={product.id} />)}</div>
          : <div className="empty"><h2>Configuration-led enquiry</h2><p>No standard model is currently assigned to this application. Send a drawing or technical requirement for review.</p></div>}
    </div></section>
    <section className="section"><div className="shell"><div className="section-head"><div><p className="eyebrow">Common questions</p><h2>{application.name} configuration FAQ</h2></div></div><div className="faq-list">{guidance.faqs.map(([question, answer]) => <details key={question}><summary>{question}</summary><p>{answer}</p></details>)}</div></div></section>
  </main>;
}
