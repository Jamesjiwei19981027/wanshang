import "server-only";

import { cache } from "react";
import {
  apiBase,
  applications as starterApplications,
  categories as starterCategories,
  findProduct,
  products as starterProducts,
  starterFallbackEnabled,
  withStarterApplicationImage,
  withStarterCategoryImage,
  withStarterImage,
} from "@/lib/data";
import type { Application, Category, Product, ProductDocument } from "@/lib/types";
import {
  newsArticles as starterNewsArticles,
  normalizeCmsNews,
  type CmsNewsArticle,
  type NewsArticle,
} from "@/lib/news";
import { safePublicHttpsUrl, siteConfig, type PublicSiteConfig } from "@/lib/site";
import { dimensionMatches } from "@/lib/units";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";

const serverApiBase = process.env.API_BASE_URL ?? apiBase;

export type PaginatedProducts = {
  count: number;
  next: string | null;
  previous: string | null;
  results: Product[];
};

type PaginatedResponse<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type Banner = {
  id: number;
  title: string;
  subtitle: string;
  desktop_image_url: string;
  mobile_image_url: string;
  button_text: string;
  button_link: string;
  secondary_button_text: string;
  secondary_button_link: string;
};

type SiteSettingsResponse = {
  site_name?: string;
  legal_name?: string;
  chinese_name?: string;
  contact_person?: string;
  contact_title?: string;
  website_url?: string;
  tagline?: string;
  site_logo_url?: string;
  contact_email?: string;
  phone_number?: string;
  whatsapp_link?: string;
  address?: string;
  business_hours?: string;
  map_url?: string;
  linkedin_url?: string;
  global_seo_title?: string;
  global_seo_description?: string;
  default_og_image_url?: string;
  google_analytics_id?: string;
  google_tag_manager_id?: string;
  footer_text?: string;
};

export type HomeData = {
  categories: Category[];
  featured_products: Product[];
  applications: Application[];
};

export type CompanyProfile = {
  headline?: string;
  summary?: string;
  business_role_label?: string;
  introduction?: string;
  main_products?: string;
  markets?: string;
  strengths?: string;
  quality_information?: string;
  meta_title?: string;
  meta_description?: string;
  photos?: { id: number; image_url: string; alt_text: string; caption: string }[];
};

export const getCompanyProfile = cache(async (): Promise<CompanyProfile | undefined> => {
  try {
    return await apiJson<CompanyProfile>("/company-profile/", { next: { revalidate: 60 } });
  } catch {
    return undefined;
  }
});

class ApiResponseError extends Error {
  constructor(path: string, readonly status: number) {
    super(`API ${path} returned ${status}`);
  }
}

async function apiJson<T>(
  path: string,
  options?: RequestInit & { next?: { revalidate?: number } },
): Promise<T> {
  const response = await fetchWithTimeout(`${serverApiBase}${path}`, options);
  if (!response.ok) throw new ApiResponseError(path, response.status);
  const payload = await response.json() as unknown;
  if (
    payload
    && typeof payload === "object"
    && "success" in payload
    && "data" in payload
    && (payload as { success?: unknown }).success === true
  ) {
    return (payload as { data: T }).data;
  }
  return payload as T;
}

export async function getCategories(): Promise<Category[] | undefined> {
  try {
    return (await apiJson<Category[]>("/categories/", { next: { revalidate: 60 } }))
      .map(withStarterCategoryImage);
  } catch {
    return starterFallbackEnabled ? starterCategories : undefined;
  }
}

export const getCategory = cache(async (slug: string): Promise<Category | undefined> => {
  try {
    return withStarterCategoryImage(await apiJson<Category>(
      `/categories/${encodeURIComponent(slug)}/`,
      { next: { revalidate: 60 } },
    ));
  } catch (error) {
    const fallback = starterFallbackEnabled
      ? starterCategories.find((category) => category.slug === slug)
      : undefined;
    if (error instanceof ApiResponseError && error.status === 404) return fallback;
    if (fallback) return fallback;
    throw error;
  }
});

export const getProductDetail = cache(async (slug: string): Promise<Product | undefined> => {
  try {
    return withStarterImage(await apiJson<Product>(
      `/products/${encodeURIComponent(slug)}/`,
      { next: { revalidate: 120 } },
    ));
  } catch (error) {
    const fallback = starterFallbackEnabled ? findProduct(slug) : undefined;
    if (error instanceof ApiResponseError && error.status === 404) return fallback;
    if (fallback) return fallback;
    throw error;
  }
});

export async function getRelatedProducts(product: Product): Promise<Product[]> {
  try {
    return (await apiJson<Product[]>(
      `/products/${encodeURIComponent(product.slug)}/related/`,
      { next: { revalidate: 60 } },
    )).map(withStarterImage).slice(0, 3);
  } catch {
    return starterFallbackEnabled
      ? starterProducts.filter((item) => item.id !== product.id && item.category.slug === product.category.slug).slice(0, 3)
      : [];
  }
}

export async function getHomeData(): Promise<HomeData | undefined> {
  try {
    const payload = await apiJson<HomeData>("/home/", { next: { revalidate: 60 } });
    return {
      categories: payload.categories.map(withStarterCategoryImage),
      featured_products: payload.featured_products.map(withStarterImage),
      applications: payload.applications.map(withStarterApplicationImage),
    };
  } catch {
    if (!starterFallbackEnabled) return undefined;
    return {
      categories: starterCategories,
      featured_products: starterProducts.filter((product) => product.featured).slice(0, 3),
      applications: starterApplications,
    };
  }
}

export async function getApplications(): Promise<Application[] | undefined> {
  try {
    return (await apiJson<Application[]>("/applications/", { next: { revalidate: 60 } }))
      .map(withStarterApplicationImage);
  } catch {
    return starterFallbackEnabled ? starterApplications : undefined;
  }
}

export async function getAllProducts(query: Record<string, string> = {}): Promise<Product[] | undefined> {
  try {
    const results: Product[] = [];
    let page = 1;
    while (page <= 100) {
      const params = new URLSearchParams({ ...query, page: String(page) });
      const payload = await apiJson<PaginatedProducts>(`/products/?${params}`, { next: { revalidate: 60 } });
      results.push(...payload.results.map(withStarterImage));
      if (!payload.next) break;
      page += 1;
    }
    return results;
  } catch {
    if (!starterFallbackEnabled) return undefined;
    const normalizedQuery = query.q?.trim().toLocaleLowerCase("en") ?? "";
    return starterProducts.filter((product) =>
      (!query.category || product.category.slug === query.category)
      && (!query.application || product.applications.some((item) => item.slug === query.application))
      && (!normalizedQuery || [
        product.title,
        product.model,
        product.sku,
        product.short_description,
        product.category.name,
        product.brake_type,
        product.bolt_pattern,
        product.surface_finish,
        product.axle_type,
        ...product.applications.map((item) => item.name),
      ].join(" ").toLocaleLowerCase("en").includes(normalizedQuery))
    );
  }
}

export async function getProductPage(query: Record<string, string> = {}): Promise<PaginatedProducts | undefined> {
  const params = new URLSearchParams(query);
  if (!params.has("page")) params.set("page", "1");
  try {
    const payload = await apiJson<PaginatedProducts>(`/products/?${params}`, { next: { revalidate: 60 } });
    return { ...payload, results: payload.results.map(withStarterImage) };
  } catch {
    if (!starterFallbackEnabled) return undefined;
    const legacyCapacity = Number(query.capacity || 0);
    const capacityMin = Number(query.capacity_min || 0);
    const capacityMax = Number(query.capacity_max || 0);
    const capacityUnit = query.capacity_unit || query.unit || "";
    const normalizedQuery = query.q?.trim().toLocaleLowerCase("en") ?? "";
    const productCapacity = (product: Product) => (product.rated_capacity ?? 0)
      * (product.capacity_unit === "kg" ? 2.2046226218 : 1)
      / (capacityUnit === "kg" ? 2.2046226218 : 1);
    let results = starterProducts.filter((product) =>
      (!query.category || product.category.slug === query.category)
      && (!query.application || product.applications.some((item) => item.slug === query.application))
      && (!normalizedQuery || [
        product.title,
        product.model,
        product.sku,
        product.short_description,
        product.category.name,
        product.brake_type,
        product.bolt_pattern,
        product.surface_finish,
        product.axle_type,
        ...product.applications.map((item) => item.name),
      ].join(" ").toLocaleLowerCase("en").includes(normalizedQuery))
      && (!query.brake || product.brake_type === query.brake)
      && (!query.type || product.axle_type === query.type)
      && (!query.bolt_pattern || product.bolt_pattern === query.bolt_pattern)
      && dimensionMatches(product.hub_face_length, query.hub_face || "", product.dimension_unit)
      && dimensionMatches(product.spring_center, query.spring_center || "", product.dimension_unit)
      && (!query.tube_shape || Object.entries(product.axle_spec ?? {}).some(([key, value]) => key.toLocaleLowerCase("en").replaceAll(" ", "_") === "tube_shape" && value.toLocaleLowerCase("en") === query.tube_shape.toLocaleLowerCase("en")))
      && (!query.finish || product.surface_finish === query.finish)
      && (!(legacyCapacity > 0) || ((!capacityUnit || (product.capacity_unit || "lb") === capacityUnit) && (product.rated_capacity ?? 0) >= legacyCapacity))
      && (!(capacityMin > 0) || productCapacity(product) >= capacityMin)
      && (!(capacityMax > 0) || productCapacity(product) <= capacityMax)
      && (!query.customizable || product.is_customizable === (query.customizable === "true"))
      && (!query.made_to_order || Boolean(product.made_to_order) === (query.made_to_order === "true"))
      && (!query.oem || product.oem_available === (query.oem === "true"))
    );
    const asLb = (product: Product) => (product.rated_capacity ?? 0) * (product.capacity_unit === "kg" ? 2.2046226218 : 1);
    if (query.sort === "capacity_asc") results = [...results].sort((a, b) => asLb(a) - asLb(b));
    if (query.sort === "capacity_desc") results = [...results].sort((a, b) => asLb(b) - asLb(a));
    if (query.sort === "model") results = [...results].sort((a, b) => a.model.localeCompare(b.model));
    const page = Math.max(1, Number(query.page) || 1);
    const start = (page - 1) * 12;
    return {
      count: results.length,
      next: start + 12 < results.length ? "fallback-next" : null,
      previous: page > 1 ? "fallback-previous" : null,
      results: results.slice(start, start + 12),
    };
  }
}

export async function getDocuments(): Promise<ProductDocument[] | undefined> {
  try {
    return await apiJson<ProductDocument[]>("/documents/", { next: { revalidate: 60 } });
  } catch {
    return starterFallbackEnabled ? [] : undefined;
  }
}

export async function getBanners(): Promise<Banner[]> {
  try {
    return await apiJson<Banner[]>("/banners/", { next: { revalidate: 60 } });
  } catch {
    return [];
  }
}

export async function getNewsArticles(
  { limit, featuredFirst = false }: { limit?: number; featuredFirst?: boolean } = {},
): Promise<NewsArticle[] | undefined> {
  if (limit !== undefined && (!Number.isSafeInteger(limit) || limit < 0)) {
    throw new RangeError("News limit must be a non-negative integer.");
  }
  if (limit === 0) return [];
  const select = (articles: NewsArticle[]) => {
    const sorted = featuredFirst ? [...articles].sort((a, b) =>
      Number(b.isFeatured) - Number(a.isFeatured)
      || (Date.parse(b.publishedAt ?? "") || 0) - (Date.parse(a.publishedAt ?? "") || 0)) : articles;
    return limit === undefined ? sorted : sorted.slice(0, limit);
  };
  try {
    const articles: CmsNewsArticle[] = [];
    let page = 1;
    while (page <= 100) {
      const params = new URLSearchParams({ page: String(page) });
      if (featuredFirst) params.set("featured_first", "true");
      if (limit !== undefined) params.set("page_size", String(Math.min(limit, 12)));
      const payload = await apiJson<PaginatedResponse<CmsNewsArticle> | CmsNewsArticle[]>(
        `/news/?${params}`,
        { next: { revalidate: 60 } },
      );
      if (Array.isArray(payload)) {
        articles.push(...payload);
        break;
      }
      articles.push(...payload.results);
      if (limit !== undefined && articles.length >= limit) break;
      if (!payload.next) break;
      page += 1;
    }
    if (!articles.length && starterFallbackEnabled) return select(starterNewsArticles);
    return select(articles.map(normalizeCmsNews));
  } catch {
    return starterFallbackEnabled ? select(starterNewsArticles) : undefined;
  }
}

export const getNewsArticle = cache(async (slug: string): Promise<NewsArticle | undefined> => {
  try {
    return normalizeCmsNews(await apiJson<CmsNewsArticle>(
      `/news/${encodeURIComponent(slug)}/`,
      { cache: "no-store" },
    ));
  } catch (error) {
    const fallback = starterFallbackEnabled
      ? starterNewsArticles.find((article) => article.slug === slug)
      : undefined;
    if (error instanceof ApiResponseError && error.status === 404) return fallback;
    if (fallback) return fallback;
    throw error;
  }
});

export const getSiteSettings = cache(async (): Promise<PublicSiteConfig> => {
  try {
    const settings = await apiJson<SiteSettingsResponse>("/site-settings/", { next: { revalidate: 60 } });
    return {
      ...siteConfig,
      name: settings.site_name?.trim() || siteConfig.name,
      legalName: settings.legal_name?.trim() || siteConfig.legalName,
      chineseName: settings.chinese_name?.trim() || siteConfig.chineseName,
      contactPerson: settings.contact_person?.trim() || siteConfig.contactPerson,
      contactTitle: settings.contact_title?.trim() || siteConfig.contactTitle,
      websiteUrl: safePublicHttpsUrl(settings.website_url) || siteConfig.websiteUrl,
      tagline: settings.tagline?.trim() || siteConfig.tagline,
      logoUrl: settings.site_logo_url || siteConfig.logoUrl,
      salesEmail: settings.contact_email?.trim() || siteConfig.salesEmail,
      contactEmail: settings.contact_email?.trim() || siteConfig.contactEmail,
      salesPhone: settings.phone_number?.trim() || siteConfig.salesPhone,
      whatsappUrl: safePublicHttpsUrl(settings.whatsapp_link) || siteConfig.whatsappUrl,
      address: settings.address?.trim() || siteConfig.address,
      businessHours: settings.business_hours?.trim() || siteConfig.businessHours,
      mapUrl: safePublicHttpsUrl(settings.map_url) || siteConfig.mapUrl,
      linkedInUrl: safePublicHttpsUrl(settings.linkedin_url) || siteConfig.linkedInUrl,
      footerText: settings.footer_text?.trim() || siteConfig.footerText,
      defaultSeoTitle: settings.global_seo_title?.trim() || siteConfig.defaultSeoTitle,
      defaultSeoDescription: settings.global_seo_description?.trim() || siteConfig.defaultSeoDescription,
      defaultOgImageUrl: settings.default_og_image_url || siteConfig.defaultOgImageUrl,
      googleAnalyticsId: settings.google_analytics_id?.trim() || siteConfig.googleAnalyticsId,
      googleTagManagerId: settings.google_tag_manager_id?.trim() || siteConfig.googleTagManagerId,
    };
  } catch {
    return siteConfig;
  }
});
