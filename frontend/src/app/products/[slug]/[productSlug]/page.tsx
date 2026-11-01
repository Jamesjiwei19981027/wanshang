import type { Metadata } from "next";
import { ProductDetailPage, getProductMetadata } from "@/components/ProductDetailPage";

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string; productSlug: string }>;
}): Promise<Metadata> {
  return getProductMetadata((await params).productSlug);
}

export default async function CategorizedProductPage({
  params,
}: {
  params: Promise<{ slug: string; productSlug: string }>;
}) {
  const { slug: category, productSlug } = await params;
  return <ProductDetailPage slug={productSlug} expectedCategory={category} />;
}
