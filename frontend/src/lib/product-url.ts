import type { Product } from "@/lib/types";

type ProductPathFields = Pick<Product, "slug" | "category">;

export function getProductPath(product: ProductPathFields): string {
  return `/products/${encodeURIComponent(product.category.slug)}/${encodeURIComponent(product.slug)}`;
}
