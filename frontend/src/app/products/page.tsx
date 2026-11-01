import { Suspense } from "react";
import type { Metadata } from "next";
import { ProductCatalog } from "@/components/ProductCatalog";
import { pageMetadata } from "@/lib/seo";
import { getProductPage } from "@/lib/api";
import { hasCatalogueQuery, productApiQuery, type RawSearchParams } from "@/lib/catalog-query";

const baseMetadata = pageMetadata({ title: "Trailer parts catalogue", description: "Filter trailer axle assemblies and running gear by technical requirements.", path: "/products" });

export async function generateMetadata({ searchParams }: { searchParams: Promise<RawSearchParams> }): Promise<Metadata> {
  return { ...baseMetadata, robots: hasCatalogueQuery(await searchParams) ? { index: false, follow: true } : undefined };
}

export default async function ProductsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const initialPage = await getProductPage(productApiQuery(await searchParams));
  return <Suspense fallback={<main className="not-found"><p>Loading catalogue…</p></main>}><ProductCatalog initialPage={initialPage} initialFailed={!initialPage} heading="Trailer parts catalogue" description="Explore trailer axles, suspension systems, wheel hubs, brake drums and landing gear. Send your drawings and required configuration for a quotation." /></Suspense>;
}
