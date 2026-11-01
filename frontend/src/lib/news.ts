import { DEFAULT_BRAND_NAME } from "@/lib/site";

export type NewsArticle = {
  id?: number;
  category: string;
  title: string;
  summary: string;
  slug: string;
  intro: string;
  sections: [string, string][];
  tags?: string[];
  coverImageUrl?: string;
  author?: string;
  publishedAt?: string;
  updatedAt?: string;
  readingMinutes?: number;
  isFeatured?: boolean;
  seoTitle?: string;
  seoDescription?: string;
  seoKeywords?: string;
};

export const newsArticles: NewsArticle[] = [
  {
    category: "Product news",
    title: "New trailer axle configurations are now available",
    summary: "The catalogue now highlights configurable axle platforms for utility, marine and heavy-duty trailer applications.",
    slug: "new-trailer-axle-configurations",
    intro: "We have expanded the published catalogue with clearer configuration details to help buyers start a product enquiry.",
    sections: [
      ["What is new", "Published product pages now make capacity, brake, hub and mounting information easier to compare."],
      ["How to enquire", "Select a relevant platform, then send the dimensions, operating environment and drawing requirements with your RFQ."],
    ],
    tags: ["products", "trailer axles"],
    author: DEFAULT_BRAND_NAME,
    publishedAt: "2026-01-15T00:00:00Z",
    readingMinutes: 2,
    isFeatured: true,
  },
  {
    category: "Company news",
    title: "Expanded support for drawing-led OEM enquiries",
    summary: "Our enquiry workflow now captures the details needed to begin a configuration review for custom trailer components.",
    slug: "expanded-oem-enquiry-support",
    intro: "OEM and custom projects often begin with a drawing and a short list of operating requirements. The updated workflow keeps those inputs together.",
    sections: [
      ["Project inputs", "Include the intended trailer application, load, critical dimensions, required finish and any available drawings or photographs."],
      ["Next step", "Our sales team will review the request and follow up on the configuration information needed for a quotation."],
    ],
    tags: ["oem", "custom components"],
    author: DEFAULT_BRAND_NAME,
    publishedAt: "2025-12-08T00:00:00Z",
    readingMinutes: 2,
  },
  {
    category: "Product news",
    title: "Marine axle options added to the catalogue",
    summary: "New marine-oriented axle listings make corrosion-conscious product options easier to find for boat trailer enquiries.",
    slug: "marine-axle-options-added",
    intro: "Boat trailer projects have distinct requirements around water exposure, surface protection and maintenance planning.",
    sections: [
      ["Catalogue update", "Marine-oriented product pages now identify relevant axle, hub and finish considerations for an initial review."],
      ["Configuration confirmation", "Final suitability still depends on the complete trailer design, operating environment and maintenance plan."],
    ],
    tags: ["marine", "boat trailers"],
    author: DEFAULT_BRAND_NAME,
    publishedAt: "2025-11-21T00:00:00Z",
    readingMinutes: 2,
  },
];

export function findNewsArticle(slug: string): NewsArticle | undefined {
  return newsArticles.find((article) => article.slug === slug);
}

export function searchNewsArticles(query: string, articles: NewsArticle[] = newsArticles): NewsArticle[] {
  const terms = query.trim().toLocaleLowerCase("en").split(/\s+/).filter(Boolean);
  if (!terms.length) return [];

  return articles.filter((article) => {
    const searchable = [
      article.category,
      article.title,
      article.summary,
      article.intro,
      ...(article.tags ?? []),
      ...article.sections.flat(),
    ].join(" ").toLocaleLowerCase("en");
    return terms.every((term) => searchable.includes(term));
  });
}

export type CmsNewsArticle = {
  id: number;
  title: string;
  slug: string;
  summary: string;
  category?: string;
  tags?: unknown;
  reading_minutes?: number;
  cover_image_url?: string;
  author?: string;
  is_featured?: boolean;
  published_at?: string;
  updated_at?: string;
  seo_title?: string;
  seo_description?: string;
  seo_keywords?: string;
  body?: unknown;
};

function safeTags(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean).slice(0, 20)
    : [];
}

function bodySections(value: unknown): [string, string][] {
  const sections: [string, string][] = [];
  const push = (heading: unknown, copy: unknown) => {
    if (typeof copy !== "string" || !copy.trim()) return;
    sections.push([typeof heading === "string" ? heading.trim() : "", copy.trim()]);
  };
  if (Array.isArray(value)) {
    for (const entry of value) {
      if (Array.isArray(entry)) {
        push(entry[0], entry[1]);
      } else if (typeof entry === "string") {
        push("", entry);
      } else if (entry && typeof entry === "object") {
        const item = entry as Record<string, unknown>;
        push(item.heading ?? item.title, item.copy ?? item.text ?? item.content ?? item.body);
      }
    }
  } else if (value && typeof value === "object") {
    for (const [heading, copy] of Object.entries(value as Record<string, unknown>)) push(heading, copy);
  } else if (typeof value === "string") {
    push("", value);
  }
  return sections;
}

export function normalizeCmsNews(article: CmsNewsArticle): NewsArticle {
  const sections = bodySections(article.body);
  return {
    id: article.id,
    category: article.category?.trim() || "Company news",
    title: article.title,
    summary: article.summary,
    slug: article.slug,
    intro: article.summary,
    sections,
    tags: safeTags(article.tags),
    coverImageUrl: article.cover_image_url || "",
    author: article.author || "",
    publishedAt: article.published_at,
    updatedAt: article.updated_at,
    readingMinutes: Math.max(1, article.reading_minutes || 1),
    isFeatured: Boolean(article.is_featured),
    seoTitle: article.seo_title || "",
    seoDescription: article.seo_description || "",
    seoKeywords: article.seo_keywords || "",
  };
}
