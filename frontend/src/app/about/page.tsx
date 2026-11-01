import Link from "next/link";
import Image from "next/image";
import { PageHero } from "@/components/PageHero";
import { pageMetadata } from "@/lib/seo";
import { getCompanyProfile, getSiteSettings } from "@/lib/api";

export async function generateMetadata() {
  const profile = await getCompanyProfile();
  return pageMetadata({
    title: profile?.meta_title || "About us",
    description: profile?.meta_description || profile?.summary || "A configuration-led trailer parts partner for manufacturers, distributors and professional buyers.",
    path: "/about",
  });
}

const projectSteps = [
  ["Tell us about the trailer", "Share the intended use, required load capacity, operating environment and destination market. A drawing or reference photo is a useful starting point."],
  ["Confirm the configuration", "Review axle type, hub face, spring centre, wheel interfaces and brakes. For replacement parts, include the model or part number when available."],
  ["Define the purchasing requirements", "Include quantities, preferred finish, delivery destination and target schedule. Tell us about any packaging or documentation requirements."],
  ["Review the quotation", "Confirm the proposed specification, commercial terms and delivery details with our team before placing an order."],
];

const requestedInformation = [
  ["Product specifications", "Dimensions, capacity and component options"],
  ["Drawings and reference images", "Check mounting points and part interfaces"],
  ["Quality and test information", "Ask what is available for the selected product"],
  ["Packing and delivery details", "Confirm against your order requirements"],
];

export default async function AboutPage() {
  const [profile, site] = await Promise.all([getCompanyProfile(), getSiteSettings()]);
  const businessSections = [
    ["Main products", profile?.main_products],
    ["Target markets", profile?.markets],
    ["Our strengths", profile?.strengths],
    ["Quality information", profile?.quality_information],
  ].filter((section): section is [string, string] => Boolean(section[1]?.trim()));
  const photos = (profile?.photos ?? []).filter((photo) => photo.image_url);
  return <main>
    <PageHero
      eyebrow="Company profile"
      title={profile?.headline || "Trailer components for your next build or parts order."}
      description={profile?.summary || "Discuss axle assemblies, brakes, hubs and suspension components with a focus on fit, operating conditions and your purchasing requirements."}
      crumb="About us"
    />
    {profile?.introduction && <section className="section">
      <div className="shell">
        <p className="eyebrow">{profile.business_role_label || "About us"}</p>
        <h2 className="home-heading">{site.legalName || site.name}</h2>
        {site.chineseName && <p lang="zh-CN">{site.chineseName}</p>}
        <CompanyCopy value={profile.introduction} />
        {businessSections.length > 0 && <div className="company-information-grid">
          {businessSections.map(([title, content]) => <section className="content-card" key={title}>
            <h3>{title}</h3><CompanyCopy value={content} />
          </section>)}
        </div>}
        {photos.length > 0 && <div className="company-photo-grid">
          {photos.map((photo) => <figure key={photo.id}>
            <div className="company-photo"><Image src={photo.image_url} alt={photo.alt_text} fill sizes="(max-width: 700px) 100vw, 50vw" /></div>
            {photo.caption && <figcaption>{photo.caption}</figcaption>}
          </figure>)}
        </div>}
      </div>
    </section>}
    <section className="section">
      <div className="shell split">
        <div>
          <p className="eyebrow">Who we serve</p>
          <h2 className="home-heading">From a drawing or part reference to a clear quotation.</h2>
          <p className="section-copy">For trailer manufacturers, importers, distributors and replacement-parts buyers, the starting point may be a complete specification or a single photo. Share what you have and identify the details that still need confirmation.</p>
          <div className="process-list home-process-list">
            {projectSteps.map(([title, copy], index) => <div className="process-item" key={title}>
              <span className="process-number">{String(index + 1).padStart(2, "0")}</span>
              <div><h3>{title}</h3><p style={{ color: "var(--muted)" }}>{copy}</p></div>
            </div>)}
          </div>
        </div>
        <aside className="content-card" aria-labelledby="quote-information">
          <p className="eyebrow">Support your purchasing decision</p>
          <h2 id="quote-information">Information to request with your quote</h2>
          <p>Let us know which documents your purchasing and engineering teams need. Availability depends on the product and configuration.</p>
          {requestedInformation.map(([title, copy]) => <div className="document-row" key={title}><strong>{title}</strong><span>{copy}</span></div>)}
          <p>Need a custom component? Include your drawing revision, key dimensions, material or finish requirements and expected quantity.</p>
          <Link className="link-arrow" href="/request-a-quote">Discuss your project</Link>
        </aside>
      </div>
    </section>
    <section className="cta"><div className="shell cta-inner"><div><p className="eyebrow">Start with the specifications</p><h2>Have a trailer axle project to discuss?</h2></div><Link className="button" href="/request-a-quote">Request a quote</Link></div></section>
  </main>;
}

function CompanyCopy({ value }: { value: string }) {
  return <div className="company-copy">{value.trim().split(/\n\s*\n/).map((paragraph, index) => <p key={index}>{paragraph}</p>)}</div>;
}
