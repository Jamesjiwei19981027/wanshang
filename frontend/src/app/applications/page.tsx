import Image from "next/image";
import Link from "next/link";
import { getApplications } from "@/lib/api";
import { PageHero } from "@/components/PageHero";
import { pageMetadata } from "@/lib/seo";

export const metadata = pageMetadata({ title: "Trailer applications", description: "Trailer axle and running gear recommendations by application.", path: "/applications" });
export const dynamic = "force-dynamic";
export default async function ApplicationsPage() {
  const applications = await getApplications();
  if (!applications) return <main className="not-found"><h1>Applications are temporarily unavailable</h1><p>Please try again shortly.</p></main>;
  return <main><PageHero eyebrow="Applications" title="Find components for the way your trailer works" description="Explore axle and running gear options by trailer use. Share your loads, operating environment and mounting dimensions to confirm a configuration for your project." crumb="Applications" /><section className="section"><div className="shell">{applications.length
    ? <div className="application-grid">{applications.map((application, index) => <Link href={`/applications/${application.slug}`} className="application-card" key={application.id}>
      {application.image_url
        ? <Image className="application-card-image" src={application.image_url} alt={`${application.name} trailer application`} fill loading={index === 0 ? "eager" : "lazy"} sizes="(max-width: 700px) 100vw, (max-width: 1000px) 50vw, 33vw" />
        : <span className="card-media-placeholder" aria-hidden="true" />}
      <div className="application-card-content"><p className="eyebrow">Recommended finish · {application.recommended_finish}</p><h3>{application.name}</h3><p>{application.summary}</p></div>
    </Link>)}</div>
    : <div className="empty"><h2>Tell us how your trailer will be used</h2><p>Browse the catalogue or share your loads and operating conditions so we can review your requirements.</p><div className="button-row"><Link className="button-outline" href="/products">Browse products</Link><Link className="button" href="/request-a-quote">Request a configuration</Link></div></div>}
  </div></section><section className="cta"><div className="shell cta-inner"><div><p className="eyebrow">Have a different application?</p><h2>Start with a drawing, photo or description of your trailer.</h2><p>Tell us about the load, terrain and exposure to water. Include any destination-market requirements with your enquiry.</p></div><Link className="button" href="/request-a-quote">Discuss your application</Link></div></section></main>;
}
