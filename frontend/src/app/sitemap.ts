import type { MetadataRoute } from "next";
import { getAllProducts, getApplications, getCategories, getNewsArticles } from "@/lib/api";
import { getProductPath } from "@/lib/product-url";
import { toDateSafe } from "@/lib/dates";

export const revalidate = 300;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const base = (process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000").replace(/\/+$/, "");
  const routes = ["", "/products", "/products/trailer-axles", "/applications", "/news", "/downloads", "/about", "/contact", "/request-a-quote", "/faq"];
  // Degrade instead of 500: always emit static routes, then append whatever
  // catalogue data is currently reachable.
  const [products, categories, applications, newsArticles] = await Promise.all([
    getAllProducts().catch(() => undefined),
    getCategories().catch(() => undefined),
    getApplications().catch(() => undefined),
    getNewsArticles().catch(() => undefined),
  ]);
  return [
    ...routes.map((route) => ({ url: `${base}${route}`, changeFrequency: "weekly" as const, priority: route === "" ? 1 : .7 })),
    ...(categories ?? []).filter((category) => category.slug !== "trailer-axles").map((category) => ({ url: `${base}/products/${category.slug}`, changeFrequency: "weekly" as const, priority: .7 })),
    ...(products ?? []).map((product) => ({ url: `${base}${getProductPath(product)}`, lastModified: toDateSafe(product.updated_at), changeFrequency: "weekly" as const, priority: .8 })),
    ...(applications ?? []).map((application) => ({ url: `${base}/applications/${application.slug}`, changeFrequency: "monthly" as const, priority: .6 })),
    ...(newsArticles ?? []).map((article) => ({ url: `${base}/news/${article.slug}`, changeFrequency: "monthly" as const, priority: .6 })),
  ];
}
