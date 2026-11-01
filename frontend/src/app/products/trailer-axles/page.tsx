import { Suspense } from "react";
import type { Metadata } from "next";
import { ProductCatalog } from "@/components/ProductCatalog";
import { pageMetadata } from "@/lib/seo";
import { getCategory, getProductPage } from "@/lib/api";
import { hasCatalogueQuery, productApiQuery, type RawSearchParams } from "@/lib/catalog-query";

const fallbackHeading = "Trailer axle assemblies";
const fallbackDescription = "Straight, drop and custom axle platforms. Capacity, brake and dimensional requirements must be confirmed for the final trailer design.";
const fallbackMetaDescription = "Filter straight, drop and torsion trailer axle platforms by capacity, brakes and application.";

async function getTrailerAxlesCategory() {
  try {
    return await getCategory("trailer-axles");
  } catch {
    return undefined;
  }
}

export async function generateMetadata({ searchParams }: { searchParams: Promise<RawSearchParams> }): Promise<Metadata> {
  const [query, category] = await Promise.all([searchParams, getTrailerAxlesCategory()]);
  const metadata = pageMetadata({
    title: category?.meta_title || category?.name || fallbackHeading,
    description: category?.meta_description || category?.description || fallbackMetaDescription,
    path: "/products/trailer-axles",
  });
  return { ...metadata, robots: hasCatalogueQuery(query) ? { index: false, follow: true } : undefined };
}

export default async function TrailerAxlesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const [query, category] = await Promise.all([searchParams, getTrailerAxlesCategory()]);
  const initialPage = await getProductPage(productApiQuery(query, "trailer-axles"));
  return <Suspense fallback={<main className="not-found"><p>Loading catalogue…</p></main>}><ProductCatalog initialPage={initialPage} initialFailed={!initialPage} category="trailer-axles" heading={category?.name || fallbackHeading} description={category?.description || fallbackDescription} /></Suspense>;
}
