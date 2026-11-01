"use client";

import Image from "next/image";
import Link from "next/link";
import { useMemo, useState } from "react";
import { formatDateSafe } from "@/lib/dates";
import { DEFAULT_BRAND_NAME } from "@/lib/site";
import type { NewsArticle } from "@/lib/news";

export function NewsBrowser({ articles }: { articles: NewsArticle[] }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const categories = useMemo(() => [...new Set(articles.map((article) => article.category))].sort(), [articles]);
  const filteredArticles = useMemo(() => {
    const terms = query.trim().toLocaleLowerCase("en").split(/\s+/).filter(Boolean);
    return articles.filter((article) => {
      if (category && article.category !== category) return false;
      if (!terms.length) return true;
      const searchable = [article.category, article.title, article.summary, article.intro, ...(article.tags ?? []), ...article.sections.flat()]
        .join(" ")
        .toLocaleLowerCase("en");
      return terms.every((term) => searchable.includes(term));
    });
  }, [articles, category, query]);
  const clearFilters = () => { setQuery(""); setCategory(""); };

  return <>
    <section className="section"><div className="shell"><div className="form-card"><h2>Find news</h2><div className="form-grid"><div className="field"><label htmlFor="news-search">Keyword</label><input id="news-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="e.g. products, OEM or marine" /></div><div className="field"><label htmlFor="news-category">News category</label><select id="news-category" value={category} onChange={(event) => setCategory(event.target.value)}><option value="">All categories</option>{categories.map((item) => <option value={item} key={item}>{item}</option>)}</select></div></div>{(query || category) && <button className="button-outline" type="button" onClick={clearFilters} style={{ marginTop: 16 }}>Clear filters</button>}</div>
      {filteredArticles.length === 0 ? <div className="empty"><h2>No news matches these filters</h2><p>Try a broader keyword or clear the category filter.</p><button className="button-outline" type="button" onClick={clearFilters}>Clear filters</button></div> : <><div className="section-head"><div><p className="eyebrow">Latest updates</p><h2>{filteredArticles.length} article{filteredArticles.length === 1 ? "" : "s"}</h2></div></div><div className="article-grid">{filteredArticles.map((article) => <article className="article-card" key={article.slug}>{article.coverImageUrl && <div className="article-cover"><Image src={article.coverImageUrl} alt="" fill sizes="(max-width: 700px) 100vw, 33vw" /></div>}<p className="eyebrow">{article.category}</p><h2>{article.title}</h2><p>{article.summary}</p><p className="article-meta">{article.author || DEFAULT_BRAND_NAME}{article.publishedAt ? ` · ${formatDateSafe(article.publishedAt, { dateStyle: "medium", timeZone: "UTC" })}` : ""}</p>{(article.tags ?? []).length > 0 && <div className="article-tags" aria-label="Article tags">{article.tags?.map((tag) => <span key={tag}>{tag}</span>)}</div>}<Link className="link-arrow" href={`/news/${article.slug}`}>Read article</Link></article>)}</div></>}</div></section>
    <section className="cta"><div className="shell cta-inner"><div><p className="eyebrow">Have a project in mind?</p><h2>Send your trailer requirements, dimensions or drawing for a configuration review.</h2><p>Our team can help you move from an initial idea to a complete RFQ.</p></div><Link className="button" href="/request-a-quote">Request a quote</Link></div></section>
  </>;
}
