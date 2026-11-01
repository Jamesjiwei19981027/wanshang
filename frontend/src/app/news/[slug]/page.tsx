import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHero } from "@/components/PageHero";
import { getNewsArticle, getNewsArticles } from "@/lib/api";
import { formatDateSafe } from "@/lib/dates";
import { absoluteUrl, serializeJsonLd } from "@/lib/structured-data";

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const slug = (await params).slug;
  const article = await getNewsArticle(slug);
  if (!article) notFound();
  return {
    title: article.seoTitle || article.title,
    description: article.seoDescription || article.intro,
    keywords: article.seoKeywords?.split(",").map((keyword) => keyword.trim()).filter(Boolean),
    alternates: { canonical: `/news/${slug}` },
    openGraph: { title: article.seoTitle || article.title, description: article.seoDescription || article.intro, type: "article", url: `/news/${slug}`, images: article.coverImageUrl ? [article.coverImageUrl] : undefined, publishedTime: article.publishedAt, modifiedTime: article.updatedAt, authors: article.author ? [article.author] : undefined },
  };
}

export default async function NewsArticlePage({ params }: { params: Promise<{ slug: string }> }) {
  const slug = (await params).slug;
  const [article, allArticles] = await Promise.all([getNewsArticle(slug), getNewsArticles()]);
  if (!article) notFound();
  const articleUrl = absoluteUrl(`/news/${encodeURIComponent(slug)}`);
  const structuredData = serializeJsonLd({
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    headline: article.title,
    description: article.intro,
    articleSection: article.category,
    author: article.author ? { "@type": "Organization", name: article.author } : undefined,
    datePublished: article.publishedAt,
    dateModified: article.updatedAt || article.publishedAt,
    image: article.coverImageUrl || undefined,
    inLanguage: "en",
    mainEntityOfPage: { "@type": "WebPage", "@id": articleUrl },
    url: articleUrl,
  });
  const related = (allArticles ?? []).filter((item) => item.slug !== article.slug).sort((a, b) => Number(b.category === article.category) - Number(a.category === article.category)).slice(0, 3);
  const publishedLabel = formatDateSafe(article.publishedAt, { dateStyle: "long", timeZone: "UTC" });
  const linkedInShare = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(articleUrl)}`;
  const emailShare = `mailto:?subject=${encodeURIComponent(article.title)}&body=${encodeURIComponent(articleUrl)}`;

  return <main><script type="application/ld+json" dangerouslySetInnerHTML={{ __html: structuredData }} /><PageHero eyebrow={article.category} title={article.title} description={article.intro} crumb="News" /><article className="shell policy news-article"><div className="news-byline"><span>{article.category}</span>{article.author && <span>By {article.author}</span>}{publishedLabel && <time dateTime={article.publishedAt}>{publishedLabel}</time>}{article.readingMinutes && <span>{article.readingMinutes} min read</span>}</div>{article.coverImageUrl && <div className="news-cover"><Image src={article.coverImageUrl} alt={`${article.title} cover`} fill priority sizes="(max-width: 1000px) 100vw, 900px" /></div>}{(article.tags ?? []).length > 0 && <div className="article-tags" aria-label="Article tags">{article.tags?.map((tag) => <span key={tag}>{tag}</span>)}</div>}{article.sections.length ? article.sections.map(([heading, copy], index) => <section key={`${heading}-${index}`}>{heading && <h2>{heading}</h2>}<p>{copy}</p></section>) : <section><p>{article.intro}</p></section>}<div className="article-share"><strong>Share this article</strong><a className="button-outline" href={linkedInShare} target="_blank" rel="noopener noreferrer">LinkedIn</a><a className="button-outline" href={emailShare}>Email</a></div></article>{related.length > 0 && <section className="section section-alt"><div className="shell"><div className="section-head"><div><p className="eyebrow">Continue reading</p><h2>Related news</h2></div><Link className="link-arrow" href="/news">All news</Link></div><div className="article-grid">{related.map((item) => <article className="article-card" key={item.slug}><p className="eyebrow">{item.category}</p><h2>{item.title}</h2><p>{item.summary}</p><Link className="link-arrow" href={`/news/${item.slug}`}>Read article</Link></article>)}</div></div></section>}</main>;
}
