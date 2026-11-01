import Image from "next/image";
import Link from "next/link";
import { getBanners, getHomeData, getNewsArticles } from "@/lib/api";
import { ProductCard } from "@/components/ProductCard";
import { HeroCarousel } from "@/components/HeroCarousel";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const [home, banners, newsArticles] = await Promise.all([
    getHomeData(),
    getBanners(),
    getNewsArticles({ limit: 3, featuredFirst: true }),
  ]);
  if (!home) return <main className="not-found"><h1>Website data is temporarily unavailable</h1><p>Please try again shortly or contact the sales team directly.</p></main>;
  const { applications, categories, featured_products: products } = home;
  const recommendedNews = newsArticles ?? [];
  return <main>
    <HeroCarousel banners={banners} />

    <section className="quick-find">
      <div className="shell quick-find-inner">
        <div className="quick-find-title"><h2>Find an axle for your trailer</h2><p>Start with what you know. All filters are optional.</p></div>
        <form action="/products/trailer-axles" method="get" className="filter-grid home-quick-filter">
          <div className="field"><label htmlFor="application">Trailer application</label><select id="application" name="application"><option value="">Select application</option>{applications.map((item) => <option value={item.slug} key={item.id}>{item.name}</option>)}</select></div>
          <div className="field"><label htmlFor="capacity">Required capacity (kg)</label><input id="capacity" name="capacity_min" type="number" min="0" step="any" placeholder="Enter required axle load" /><input type="hidden" name="capacity_unit" value="kg" /></div>
          <div className="field"><label htmlFor="brake">Axle or brake description</label><input id="brake" name="q" placeholder="e.g. drum brake axle" /></div>
          <button className="button" type="submit">Search catalogue</button>
          <details className="quick-filter-advanced">
            <summary>Advanced filters: axle type and dimensions</summary>
            <div className="quick-filter-advanced-grid">
              <div className="field"><label htmlFor="axle">Axle type</label><select id="axle" name="type"><option value="">Any type</option><option value="straight">Straight axle</option><option value="drop">Drop axle</option><option value="torsion">Torsion axle</option></select></div>
              <div className="field"><label htmlFor="bolt-pattern">Bolt pattern</label><input id="bolt-pattern" name="bolt_pattern" placeholder="Enter wheel bolt pattern" /></div>
              <div className="field"><label htmlFor="hub-face">Hub face length</label><input id="hub-face" name="hub_face" inputMode="decimal" placeholder="Enter dimension and unit" /></div>
              <div className="field"><label htmlFor="spring-center">Spring centre</label><input id="spring-center" name="spring_center" inputMode="decimal" placeholder="Enter dimension and unit" /></div>
            </div>
          </details>
        </form>
        <div className="quick-find-help"><p>Not sure about the specifications?</p><Link className="link-arrow" href="/request-a-quote">Upload a drawing or photo for help</Link></div>
      </div>
    </section>

    <section className="section">
      <div className="shell">
        <SectionHead eyebrow="Product range" title="Core product categories" copy="Explore axle assemblies, brakes, hubs and suspension components for your trailer project." href="/products" link="View all products" />
        {categories.length > 0
          ? <div className="category-grid">
            {categories.map((category) => <Link href={`/products/${category.slug}`} className="category-card" key={category.id}>
              {category.image_url
                ? <Image className="category-card-image" src={category.image_url} alt={`${category.name} product category`} fill sizes="(max-width: 700px) 100vw, (max-width: 1000px) 50vw, 33vw" />
                : <span className="card-media-placeholder" aria-hidden="true" />}
              <div className="category-card-content">
                <div className="category-icon">{category.icon}</div><h3>{category.name}</h3><p>{category.description}</p><span className="category-meta">{category.product_count ? `${category.product_count} listed models` : "Discuss your requirements"} →</span>
              </div>
            </Link>)}
          </div>
          : <HomeEmptyState title="Tell us which trailer parts you need" copy="Send a part reference, drawing or target specification so we can review your requirements." primaryHref="/products" primaryLabel="Browse products" secondaryHref="/request-a-quote" secondaryLabel="Send an RFQ" />}
      </div>
    </section>

    <section className="section section-alt">
      <div className="shell">
        <SectionHead eyebrow="Technical components" title="Featured axle platforms" copy="Compare capacity, brakes and mounting dimensions, then send your intended use for configuration review." href="/products/trailer-axles" link="Browse axle catalogue" />
        {products.length > 0
          ? <div className="product-grid">{products.slice(0, 3).map((product) => <ProductCard product={product} key={product.id} />)}</div>
          : <HomeEmptyState title="Find the configuration your trailer needs" copy="Browse the axle catalogue or send your required capacity, dimensions and trailer use for review." primaryHref="/products" primaryLabel="Browse products" secondaryHref="/request-a-quote" secondaryLabel="Send an RFQ" />}
      </div>
    </section>

    <section className="section-tight" aria-label="Support for your enquiry"><div className="shell stats"><Stat value="Drawing review" label="Share dimensions & photos" /><Stat value="Axle options" label="Compare key configurations" /><Stat value="Project quotes" label="Include quantity & delivery needs" /><Stat value="One enquiry" label="Combine multiple parts" /></div></section>

    <section className="section">
      <div className="shell">
        <SectionHead eyebrow="Applications" title="Start with the operating environment" copy="Explore components by trailer use, load conditions and exposure to water or rough terrain." href="/applications" link="Explore applications" />
        {applications.length > 0
          ? <div className="application-grid">{applications.map((application) => <Link href={`/applications/${application.slug}`} className="application-card" key={application.id}>
            {application.image_url
              ? <Image className="application-card-image" src={application.image_url} alt={`${application.name} trailer application`} fill sizes="(max-width: 700px) 100vw, (max-width: 1000px) 50vw, 33vw" />
              : <span className="card-media-placeholder" aria-hidden="true" />}
            <div className="application-card-content"><h3>{application.name}</h3><p>{application.summary}</p></div>
          </Link>)}</div>
          : <HomeEmptyState title="Discuss your trailer application" copy="Browse the available product range or send your operating environment and required configuration for review." primaryHref="/products" primaryLabel="Browse products" secondaryHref="/request-a-quote" secondaryLabel="Send an RFQ" />}
      </div>
    </section>

    <section className="section section-alt">
      <div className="shell">
        <SectionHead eyebrow="News" title="Latest company and product updates" copy="Read our latest announcements, product updates and company news." href="/news" link="View all news" />
        {recommendedNews.length > 0
          ? <div className="article-grid">{recommendedNews.map((article) => <article className="article-card" key={article.slug}>
            {article.coverImageUrl && <div className="article-cover"><Image src={article.coverImageUrl} alt="" fill sizes="(max-width: 700px) 100vw, 33vw" /></div>}
            <p className="eyebrow">{article.category}</p>
            <h2>{article.title}</h2>
            <p>{article.summary}</p>
            <Link className="link-arrow" href={`/news/${article.slug}`}>Read article</Link>
          </article>)}</div>
          : <HomeEmptyState
            title={newsArticles === undefined ? "News is temporarily unavailable" : "No news has been published yet"}
            copy={newsArticles === undefined ? "Open the news page to try again, or send your project requirements with an RFQ." : "Published announcements and product updates will appear here. You can still send your project requirements now."}
            primaryHref="/news"
            primaryLabel="Open news"
            secondaryHref="/request-a-quote"
            secondaryLabel="Send an RFQ"
          />}
      </div>
    </section>

    <section className="cta"><div className="shell cta-inner"><div><p className="eyebrow">Drawing or specification ready?</p><h2>Get a quote for your trailer parts project.</h2><p>Share your dimensions, target quantity and delivery requirements. Attach drawings or photos so we can review the configuration and prepare a relevant quotation.</p></div><div className="button-row"><Link className="button" href="/request-a-quote">Request a quote</Link><Link className="button-outline light" href="/contact">Contact us</Link></div></div></section>
  </main>;
}

function SectionHead({ eyebrow, title, copy, href, link }: { eyebrow: string; title: string; copy: string; href: string; link: string }) {
  return <div className="section-head"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2><p className="section-copy">{copy}</p></div><Link className="link-arrow" href={href}>{link}</Link></div>;
}

function Stat({ value, label }: { value: string; label: string }) {
  return <div className="stat"><strong>{value}</strong><span>{label}</span></div>;
}

function HomeEmptyState({ title, copy, primaryHref, primaryLabel, secondaryHref, secondaryLabel }: {
  title: string;
  copy: string;
  primaryHref: string;
  primaryLabel: string;
  secondaryHref: string;
  secondaryLabel: string;
}) {
  return <div className="empty">
    <h2>{title}</h2>
    <p>{copy}</p>
    <div className="button-row">
      <Link className="button-outline" href={primaryHref}>{primaryLabel}</Link>
      <Link className="button" href={secondaryHref}>{secondaryLabel}</Link>
    </div>
  </div>;
}
