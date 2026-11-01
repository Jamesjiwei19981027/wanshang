import { NextRequest, NextResponse } from "next/server";

const apiBase = (process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api").replace(/\/+$/, "");
const siteBase = process.env.NEXT_PUBLIC_SITE_URL
  ?? (process.env.NODE_ENV !== "production" ? "http://localhost:3000" : "");

if (!siteBase) {
  throw new Error("NEXT_PUBLIC_SITE_URL is required in production.");
}

function routeNotFound(request: NextRequest) {
  // Rewriting to an ordinary App Router page can turn the response into a
  // streamed soft-404 (HTTP 200), even when that page calls notFound().
  // Next's built-in not-found endpoint retains the final HTTP 404 status.
  return NextResponse.rewrite(new URL("/_not-found", request.url));
}

function canonicalUrl(request: NextRequest, pathname: string) {
  const target = new URL(pathname, siteBase);
  target.search = request.nextUrl.search;
  return target;
}

async function apiResponse(path: string) {
  return fetch(`${apiBase}${path}`, {
    cache: "no-store",
    signal: AbortSignal.timeout(3000),
    headers: { Accept: "application/json" },
  });
}

function unwrapPayload<T>(payload: T | { success?: boolean; data?: T }): T {
  return payload && typeof payload === "object" && "success" in payload && payload.success === true && "data" in payload
    ? payload.data as T
    : payload as T;
}

type ProductRoutePayload = { slug: string; category?: { slug?: string } };

function readProductRoutePayload(payload: unknown): ProductRoutePayload | null {
  const product = unwrapPayload(payload as ProductRoutePayload | { success?: boolean; data?: ProductRoutePayload });
  if (!product || typeof product !== "object" || typeof product.slug !== "string") return null;
  return product;
}

export async function proxy(request: NextRequest) {
  const segments = request.nextUrl.pathname.split("/").filter(Boolean);
  try {
    if (segments[0] === "products") {
      if (segments.length === 1) {
        return NextResponse.next();
      }
      if (segments.length === 2) {
        const categoryResponse = await apiResponse(`/categories/${encodeURIComponent(segments[1])}/`);
        if (categoryResponse.ok) return NextResponse.next();
        if (categoryResponse.status !== 404) return NextResponse.next();
        const response = await apiResponse(`/products/${encodeURIComponent(segments[1])}/`);
        if (response.status === 404) return routeNotFound(request);
        if (!response.ok) return NextResponse.next();
        const product = readProductRoutePayload(await response.json());
        if (!product) return NextResponse.next();
        const category = product.category?.slug;
        if (!category) return routeNotFound(request);
        return NextResponse.redirect(canonicalUrl(request, `/products/${encodeURIComponent(category)}/${encodeURIComponent(product.slug)}`), 308);
      }
      if (segments.length === 3) {
        const response = await apiResponse(`/products/${encodeURIComponent(segments[2])}/`);
        if (response.status === 404) return routeNotFound(request);
        if (!response.ok) return NextResponse.next();
        const product = readProductRoutePayload(await response.json());
        if (!product) return NextResponse.next();
        const category = product.category?.slug;
        if (!category) return routeNotFound(request);
        if (category !== segments[1]) {
          return NextResponse.redirect(canonicalUrl(request, `/products/${encodeURIComponent(category)}/${encodeURIComponent(product.slug)}`), 308);
        }
      }
    }

    if (segments[0] === "news" && segments.length === 2) {
      const response = await apiResponse(`/news/${encodeURIComponent(segments[1])}/`);
      if (response.status === 404) return routeNotFound(request);
    }

    if (segments[0] === "applications" && segments.length === 2) {
      const response = await apiResponse("/applications/");
      if (!response.ok) return NextResponse.next();
      const payload = await response.json() as { results?: { slug: string }[] } | { slug: string }[];
      const applications = Array.isArray(payload) ? payload : payload.results ?? [];
      if (!applications.some((application) => application.slug === segments[1])) return routeNotFound(request);
    }
  } catch {
    // Do not turn a temporary API outage into a false 404; page-level error states
    // and starter data can still provide a useful response.
    return NextResponse.next();
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/products/:path*", "/news/:path*", "/applications/:path*"],
};
