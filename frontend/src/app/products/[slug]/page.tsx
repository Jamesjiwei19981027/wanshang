import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import { Suspense } from "react";
import { getProductMetadata } from "@/components/ProductDetailPage";
import { ProductCatalog } from "@/components/ProductCatalog";
import { getCategory, getProductDetail, getProductPage } from "@/lib/api";
import { hasCatalogueQuery, productApiQuery, type RawSearchParams } from "@/lib/catalog-query";
import { getProductPath } from "@/lib/product-url";
import { pageMetadata } from "@/lib/seo";
import { breadcrumbJsonLd, categoryJsonLd, serializeJsonLd } from "@/lib/structured-data";

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<RawSearchParams>;
}): Promise<Metadata> {
  const [{ slug }, query] = await Promise.all([params, searchParams]);
  const category = await getCategory(slug);
  if (category) {
    const metadata = pageMetadata({
      title: category.meta_title || category.name,
      description: category.meta_description || category.description || `Browse published ${category.name.toLowerCase()} catalogue data.`,
      path: `/products/${category.slug}`,
    });
    return { ...metadata, robots: hasCatalogueQuery(query) ? { index: false, follow: true } : undefined };
  }
  return getProductMetadata(slug);
}

export default async function CategoryOrLegacyProductPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<RawSearchParams>;
}) {
  const [{ slug }, query] = await Promise.all([params, searchParams]);
  const category = await getCategory(slug);
  if (category) {
    const initialPage = await getProductPage(productApiQuery(query, category.slug));
    const categoryPath = `/products/${encodeURIComponent(category.slug)}`;
    const structuredData = serializeJsonLd({
      "@context": "https://schema.org",
      "@graph": [
        categoryJsonLd(category, categoryPath),
        breadcrumbJsonLd([
          { name: "Home", path: "/" },
          { name: "Products", path: "/products" },
          { name: category.name, path: categoryPath },
        ]),
      ],
    });
    return <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: structuredData }} />
      <Suspense fallback={<main className="not-found"><p>Loading catalogue…</p></main>}>
        <ProductCatalog
          initialPage={initialPage}
          initialFailed={!initialPage}
          category={category.slug}
          heading={category.name}
          description={category.description || `Browse published ${category.name.toLowerCase()} configurations.`}
        />
      </Suspense>
    </>;
  }
  const product = await getProductDetail(slug);
  if (!product) notFound();
  permanentRedirect(getProductPath(product));
}
