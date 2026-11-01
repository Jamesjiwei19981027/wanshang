import type { Metadata } from "next";
import Link from "next/link";
import { PageHero } from "@/components/PageHero";
import { ProductCard } from "@/components/ProductCard";
import { SearchEventTracker } from "@/components/SearchEventTracker";
import { getAllProducts, getNewsArticles } from "@/lib/api";
import { searchNewsArticles } from "@/lib/news";

export const metadata: Metadata = {
  title: "Search",
  description: "Search trailer products and news.",
  alternates: { canonical: "/search" },
  robots: { index: false, follow: true },
};

export const dynamic = "force-dynamic";

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string | string[] }>;
}) {
  const rawQuery = (await searchParams).q;
  const query = (Array.isArray(rawQuery) ? rawQuery[0] : rawQuery ?? "").trim().slice(0, 120);
  const [productResponse, newsResponse] = query
    ? await Promise.all([getAllProducts({ q: query }), getNewsArticles()])
    : [[], []];
  const productError = productResponse === undefined;
  const newsError = newsResponse === undefined;
  const products = productResponse ?? [];
  const articles = query && newsResponse ? searchNewsArticles(query, newsResponse) : [];
  const resultCount = products.length + articles.length;

  return <main>
    <SearchEventTracker query={query} resultCount={resultCount} available={!productError && !newsError} />
    <PageHero
      eyebrow="Site search"
      title="Search products and news"
      description="Search by product model, axle specification, application or a news topic."
      crumb="Search"
    />
    <section className="shell form-page">
      <form action="/search" method="get" className="form-card" role="search">
        <div className="field">
          <label htmlFor="site-search">Product or technical keyword</label>
          <input
            id="site-search"
            name="q"
            type="search"
            defaultValue={query}
            maxLength={120}
            placeholder="e.g. 3,500 lb electric brake or hub face"
          />
        </div>
        <button className="button" type="submit" style={{ marginTop: 16 }}>Search</button>
      </form>

      {!query && <div className="empty">
        <h2>Start with a keyword</h2>
        <p>Try a product model, capacity, brake type, trailer application or measurement topic.</p>
        <Link className="button-outline" href="/products">Browse the catalogue</Link>
      </div>}

      {query && (productError || newsError) && <div className="form-error" role="alert">
        {productError && newsError
          ? "Search is temporarily unavailable. Please try again shortly."
          : productError
            ? "Product search is temporarily unavailable. News results are still shown below."
            : "News search is temporarily unavailable. Product results are still shown below."}
      </div>}

      {query && !productError && !newsError && resultCount === 0 && <div className="empty">
        <h2>No results for “{query}”</h2>
        <p>Try a broader term, browse the product catalogue or send the technical team your requirements.</p>
        <div className="button-row" style={{ justifyContent: "center" }}>
          <Link className="button-outline" href="/products">Browse products</Link>
          <Link className="button" href="/request-a-quote">Request a quote</Link>
        </div>
      </div>}

      {query && products.length > 0 && <section className="section-tight">
        <div className="section-head">
          <div>
            <p className="eyebrow">Products</p>
            <h2>{products.length} matching configuration{products.length === 1 ? "" : "s"}</h2>
          </div>
          <Link className="link-arrow" href={`/products?q=${encodeURIComponent(query)}`}>Refine product filters</Link>
        </div>
        <div className="product-grid">{products.map((product) => <ProductCard key={product.id} product={product} />)}</div>
      </section>}

      {query && articles.length > 0 && <section className="section-tight">
        <div className="section-head">
          <div>
            <p className="eyebrow">News</p>
            <h2>{articles.length} matching article{articles.length === 1 ? "" : "s"}</h2>
          </div>
          <Link className="link-arrow" href="/news">All news</Link>
        </div>
        <div className="article-grid">{articles.map((article) => <article className="article-card" key={article.slug}>
          <p className="eyebrow">{article.category}</p>
          <h2>{article.title}</h2>
          <p>{article.summary}</p>
          <Link className="link-arrow" href={`/news/${article.slug}`}>Read article</Link>
        </article>)}</div>
      </section>}
    </section>
  </main>;
}
