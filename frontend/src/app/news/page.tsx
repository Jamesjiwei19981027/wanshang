import { NewsBrowser } from "@/components/NewsBrowser";
import { PageHero } from "@/components/PageHero";
import { getNewsArticles } from "@/lib/api";
import { pageMetadata } from "@/lib/seo";
import { DEFAULT_BRAND_NAME } from "@/lib/site";

export const metadata = pageMetadata({ title: "News", description: `Company and product news from ${DEFAULT_BRAND_NAME}.`, path: "/news" });
export const dynamic = "force-dynamic";

export default async function NewsPage() {
  const articles = await getNewsArticles();
  return <main><PageHero eyebrow="News" title="Latest company and product updates." description={`Read announcements, product updates and company news from ${DEFAULT_BRAND_NAME}.`} crumb="News" />{articles
    ? <NewsBrowser articles={articles} />
    : <section className="section"><div className="shell"><div className="form-error" role="alert"><h2>News is temporarily unavailable</h2><p>We could not load published articles. Please try again shortly.</p></div></div></section>}
  </main>;
}
