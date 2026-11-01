"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  apiBase,
  applications as starterApplications,
  categories as starterCategories,
  products as fallbackProducts,
  starterFallbackEnabled,
  withStarterImage,
} from "@/lib/data";
import { ProductCard } from "@/components/ProductCard";
import { trackEvent } from "@/lib/events";
import type { Application, Category, Product } from "@/lib/types";
import type { PaginatedProducts } from "@/lib/api";
import { dimensionMatches } from "@/lib/units";
import { apiErrorMessage } from "@/lib/form-errors";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";
import { parseCataloguePage } from "@/lib/catalog-query";
import { buildCatalogueInquiryHref } from "@/lib/catalog-inquiry";

type Props = { category?: string; heading?: string; description?: string; initialPage?: PaginatedProducts; initialFailed?: boolean };
type Sort = "" | "newest" | "capacity_asc" | "capacity_desc" | "model";
type ViewMode = "grid" | "list";
type ProductResponse = {
  count: number;
  next: string | null;
  previous: string | null;
  results: Product[];
};
type FilterOptions = {
  capacities: number[];
  capacities_by_unit?: Record<string, number[]>;
  capacity_units?: { value: string; label: string }[];
  brake_types: string[];
  axle_types: { value: string; label: string }[];
  bolt_patterns: string[];
  surface_finishes: string[];
  tube_shapes?: string[];
};

const PAGE_SIZE = 12;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isOptionList = <T extends { id: number; name: string; slug: string }>(value: unknown): value is T[] =>
  Array.isArray(value)
  && value.every((item) => isRecord(item)
    && typeof item.id === "number"
    && typeof item.name === "string"
    && typeof item.slug === "string");

const isFilterOptions = (value: unknown): value is FilterOptions =>
  isRecord(value)
  && Array.isArray(value.capacities)
  && Array.isArray(value.brake_types)
  && Array.isArray(value.axle_types)
  && Array.isArray(value.bolt_patterns)
  && Array.isArray(value.surface_finishes);

const isProductResponse = (value: unknown): value is ProductResponse =>
  isRecord(value)
  && typeof value.count === "number"
  && Array.isArray(value.results);

const valuesFromSearch = (params: Pick<URLSearchParams, "get">) => ({
  q: (params.get("q") ?? "").slice(0, 120),
  capacity: (params.get("capacity_min") ?? params.get("capacity") ?? "").slice(0, 32),
  capacityMax: (params.get("capacity_max") ?? "").slice(0, 32),
  capacityUnit: (params.get("unit") ?? params.get("capacity_unit") ?? (params.get("capacity") ? "lb" : "")).slice(0, 8),
  brake: (params.get("brake") ?? "").slice(0, 80),
  type: (params.get("type") ?? "").slice(0, 16),
  application: (params.get("application") ?? "").slice(0, 50),
  category: (params.get("category") ?? "").slice(0, 50),
  boltPattern: (params.get("bolt_pattern") ?? "").slice(0, 60),
  hubFace: (params.get("hub_face") ?? "").slice(0, 60),
  springCenter: (params.get("spring_center") ?? "").slice(0, 60),
  tubeShape: (params.get("tube_shape") ?? "").slice(0, 40),
  finish: (params.get("finish") ?? "").slice(0, 80),
  customizable: (params.get("customizable") ?? "").slice(0, 5),
  madeToOrder: (params.get("made_to_order") ?? "").slice(0, 5),
  oem: (params.get("oem") ?? "").slice(0, 5),
  sort: (params.get("sort") ?? "").slice(0, 30) as Sort,
  page: parseCataloguePage(params.get("page")),
});

function sortFallbackProducts(items: Product[], sort: Sort): Product[] {
  const result = [...items];
  const asLb = (product: Product) => (product.rated_capacity ?? 0) * (product.capacity_unit === "kg" ? 2.2046226218 : 1);
  if (sort === "capacity_asc") return result.sort((a, b) => asLb(a) - asLb(b));
  if (sort === "capacity_desc") return result.sort((a, b) => asLb(b) - asLb(a));
  if (sort === "model") return result.sort((a, b) => a.model.localeCompare(b.model));
  return result;
}

export function ProductCatalog(props: Props) {
  const searchParams = useSearchParams();
  const queryString = searchParams.toString();
  const [filterOpen, setFilterOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");

  useEffect(() => {
    if (!filterOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setFilterOpen(false);
        window.requestAnimationFrame(() => document.getElementById("catalog-filter-trigger")?.focus());
      }
      if (event.key === "Tab") {
        const panel = document.getElementById("catalog-filters");
        const focusable = panel?.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]");
        if (!focusable?.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [filterOpen]);

  return <ProductCatalogContent
    {...props}
    initial={valuesFromSearch(searchParams)}
    queryString={queryString}
    filterOpen={filterOpen}
    setFilterOpen={setFilterOpen}
    viewMode={viewMode}
    setViewMode={setViewMode}
  />;
}

function ProductCatalogContent({
  category,
  heading = "Trailer axle assemblies",
  description = "Filter catalogue data by the technical characteristics that matter to your build.",
  initial,
  initialPage,
  initialFailed = false,
  queryString,
  filterOpen,
  setFilterOpen,
  viewMode,
  setViewMode,
}: Props & {
  initial: ReturnType<typeof valuesFromSearch>;
  queryString: string;
  filterOpen: boolean;
  setFilterOpen: (open: boolean) => void;
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
}) {
  const router = useRouter();
  const pathname = usePathname();

  const [products, setProducts] = useState<Product[]>(initialPage?.results ?? []);
  const [total, setTotal] = useState(initialPage?.count ?? 0);
  const [loading, setLoading] = useState(!initialPage);
  const [failed, setFailed] = useState(initialFailed);
  const [filterRequestError, setFilterRequestError] = useState("");
  const [q, setQ] = useState(initial.q);
  const [capacity, setCapacity] = useState(initial.capacity);
  const [capacityMax, setCapacityMax] = useState(initial.capacityMax);
  const [capacityUnit, setCapacityUnit] = useState(initial.capacityUnit);
  const [brake, setBrake] = useState(initial.brake);
  const [type, setType] = useState(initial.type);
  const [application, setApplication] = useState(initial.application);
  const [categoryFilter, setCategoryFilter] = useState(category ?? initial.category);
  const [boltPattern, setBoltPattern] = useState(initial.boltPattern);
  const [hubFace, setHubFace] = useState(initial.hubFace);
  const [springCenter, setSpringCenter] = useState(initial.springCenter);
  const [tubeShape, setTubeShape] = useState(initial.tubeShape);
  const [finish, setFinish] = useState(initial.finish);
  const [customizable, setCustomizable] = useState(initial.customizable);
  const [madeToOrder, setMadeToOrder] = useState(initial.madeToOrder);
  const [oem, setOem] = useState(initial.oem);
  const [sort, setSort] = useState<Sort>(initial.sort);
  const [page, setPage] = useState(initial.page);
  const [retryVersion, setRetryVersion] = useState(0);
  const [filterMetadataFailed, setFilterMetadataFailed] = useState(false);
  const trackedFilterSignature = useRef("");
  const skipInitialFetch = useRef(Boolean(initialPage));
  const selfWrittenQueryRef = useRef<string | null>(null);
  const lastObservedQueryRef = useRef(queryString);
  const lastObservedCategoryRef = useRef(category);
  const [applicationOptions, setApplicationOptions] = useState(starterFallbackEnabled ? starterApplications : []);
  const [categoryOptions, setCategoryOptions] = useState<Category[]>(starterFallbackEnabled ? starterCategories : []);
  const [filterOptions, setFilterOptions] = useState<FilterOptions>({
    capacities: [2200, 3500, 5200, 7000],
    capacities_by_unit: { lb: [2200, 3500, 5200, 7000], kg: [] },
    capacity_units: [{ value: "lb", label: "Pounds (lb)" }, { value: "kg", label: "Kilograms (kg)" }],
    brake_types: ["No Brake", "Electric Drum", "Hydraulic Drum", "Hydraulic Disc", "Other"],
    axle_types: [{ value: "straight", label: "Straight axle" }, { value: "drop", label: "Drop axle" }, { value: "torsion", label: "Torsion axle" }, { value: "other", label: "Other / engineering review" }],
    bolt_patterns: ["5 on 4.5", "6 on 5.5", "8 on 6.5"],
    surface_finishes: ["Painted", "E-Coated", "Powder-Coated", "Zinc Plated", "Hot-Dip Galvanized", "Custom Finish"],
    tube_shapes: ["Round", "Square", "Other"],
  });
  const capacityMinimum = capacity === "" ? null : Number(capacity);
  const capacityMaximum = capacityMax === "" ? null : Number(capacityMax);
  const capacityError = capacityMinimum !== null && (!Number.isFinite(capacityMinimum) || capacityMinimum < 0)
    ? "Minimum capacity must be zero or greater."
    : capacityMaximum !== null && (!Number.isFinite(capacityMaximum) || capacityMaximum < 0)
      ? "Maximum capacity must be zero or greater."
      : capacityMinimum !== null && capacityMaximum !== null && capacityMinimum > capacityMaximum
        ? "Minimum capacity cannot exceed maximum capacity."
        : "";

  // Keep external same-route links plus browser back/forward navigation in sync
  // without remounting the form (which would make text inputs lose focus).
  useEffect(() => {
    const categoryChanged = lastObservedCategoryRef.current !== category;
    const queryChanged = lastObservedQueryRef.current !== queryString;
    if (!categoryChanged && !queryChanged) return;

    lastObservedCategoryRef.current = category;
    lastObservedQueryRef.current = queryString;
    if (!categoryChanged && selfWrittenQueryRef.current === queryString) {
      selfWrittenQueryRef.current = null;
      return;
    }
    selfWrittenQueryRef.current = null;

    const timer = window.setTimeout(() => {
      const next = valuesFromSearch(new URLSearchParams(queryString));
      setQ(next.q);
      setCapacity(next.capacity);
      setCapacityMax(next.capacityMax);
      setCapacityUnit(next.capacityUnit);
      setBrake(next.brake);
      setType(next.type);
      setApplication(next.application);
      setCategoryFilter(category ?? next.category);
      setBoltPattern(next.boltPattern);
      setHubFace(next.hubFace);
      setSpringCenter(next.springCenter);
      setTubeShape(next.tubeShape);
      setFinish(next.finish);
      setCustomizable(next.customizable);
      setMadeToOrder(next.madeToOrder);
      setOem(next.oem);
      setSort(next.sort);
      setPage(next.page);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [category, queryString]);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const [applicationsResponse, filtersResponse, categoriesResponse] = await Promise.all([
          fetchWithTimeout(`${apiBase}/applications/`, { signal: controller.signal }),
          fetchWithTimeout(`${apiBase}/filters/`, { signal: controller.signal }),
          fetchWithTimeout(`${apiBase}/categories/`, { signal: controller.signal }),
        ]);
        let incomplete = false;
        if (applicationsResponse.ok) {
          const applicationsPayload: unknown = await applicationsResponse.json();
          if (isOptionList<Application>(applicationsPayload)) setApplicationOptions(applicationsPayload);
          else incomplete = true;
        } else incomplete = true;
        if (filtersResponse.ok) {
          const filtersPayload: unknown = await filtersResponse.json();
          if (isFilterOptions(filtersPayload)) setFilterOptions(filtersPayload);
          else incomplete = true;
        } else incomplete = true;
        if (categoriesResponse.ok) {
          const categoriesPayload: unknown = await categoriesResponse.json();
          if (isOptionList<Category>(categoriesPayload)) setCategoryOptions(categoriesPayload);
          else incomplete = true;
        } else incomplete = true;
        if (!controller.signal.aborted) setFilterMetadataFailed(incomplete);
      } catch (error) {
        if (!controller.signal.aborted && (error as Error).name !== "AbortError") {
          setFilterMetadataFailed(true);
        }
      }
    })();
    return () => controller.abort();
  }, [retryVersion]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (capacity) params.set("capacity_min", capacity);
    if (capacityMax) params.set("capacity_max", capacityMax);
    if (capacityUnit) params.set("unit", capacityUnit);
    if (brake) params.set("brake", brake);
    if (type) params.set("type", type);
    if (application) params.set("application", application);
    if (!category && categoryFilter) params.set("category", categoryFilter);
    if (boltPattern) params.set("bolt_pattern", boltPattern);
    if (hubFace) params.set("hub_face", hubFace);
    if (springCenter) params.set("spring_center", springCenter);
    if (tubeShape) params.set("tube_shape", tubeShape);
    if (finish) params.set("finish", finish);
    if (customizable) params.set("customizable", customizable);
    if (madeToOrder) params.set("made_to_order", madeToOrder);
    if (oem) params.set("oem", oem);
    if (sort) params.set("sort", sort);
    if (page > 1) params.set("page", String(page));
    const nextUrl = params.size ? `${pathname}?${params}` : pathname;
    const timer = window.setTimeout(() => {
      if (nextUrl !== `${pathname}${queryString ? `?${queryString}` : ""}`) {
        selfWrittenQueryRef.current = params.toString();
        router.replace(nextUrl, { scroll: false });
      }
    }, 180);
    return () => window.clearTimeout(timer);
  }, [application, boltPattern, brake, capacity, capacityMax, capacityUnit, category, categoryFilter, customizable, finish, hubFace, madeToOrder, oem, page, pathname, q, queryString, router, sort, springCenter, tubeShape, type]);

  useEffect(() => {
    if (capacityError) {
      skipInitialFetch.current = false;
      const timer = window.setTimeout(() => {
        setLoading(false);
        setFailed(false);
      }, 0);
      return () => window.clearTimeout(timer);
    }
    if (skipInitialFetch.current) {
      skipInitialFetch.current = false;
      return;
    }
    const controller = new AbortController();
    let active = true;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setFailed(false);
      setFilterRequestError("");
      const params = new URLSearchParams();
      if (categoryFilter) params.set("category", categoryFilter);
      if (q) params.set("q", q);
      if (capacity) params.set("capacity_min", capacity);
      if (capacityMax) params.set("capacity_max", capacityMax);
      if (capacityUnit) params.set("capacity_unit", capacityUnit);
      if (brake) params.set("brake", brake);
      if (type) params.set("type", type);
      if (application) params.set("application", application);
      if (boltPattern) params.set("bolt_pattern", boltPattern);
      if (hubFace) params.set("hub_face", hubFace);
      if (springCenter) params.set("spring_center", springCenter);
      if (tubeShape) params.set("tube_shape", tubeShape);
      if (finish) params.set("finish", finish);
      if (customizable) params.set("customizable", customizable);
      if (madeToOrder) params.set("made_to_order", madeToOrder);
      if (oem) params.set("oem", oem);
      if (sort) params.set("sort", sort);
      params.set("page", String(page));
      try {
        const response = await fetchWithTimeout(
          `${apiBase}/products/?${params}`,
          { signal: controller.signal },
        );
        if (!response.ok) {
          if (response.status === 404 && page > 1) {
            if (active) {
              setFilterRequestError("That catalogue page is no longer available. Returning to the first page.");
              setPage(1);
            }
            return;
          }
          if (response.status === 400) {
            const payload = await response.json().catch(() => ({}));
            if (active) {
              setFilterRequestError(apiErrorMessage(payload, "Check the selected product filters."));
              setProducts([]);
              setTotal(0);
            }
            return;
          }
          throw new Error("Catalogue API unavailable");
        }
        const payload: unknown = await response.json();
        if (!isProductResponse(payload)) {
          throw new Error("Catalogue API returned an unexpected payload");
        }
        if (active) {
          setProducts(payload.results.map(withStarterImage));
          setTotal(payload.count);
        }
      } catch (error) {
        if (active && (error as Error).name !== "AbortError") {
          setFailed(true);
          if (starterFallbackEnabled) {
            const requestedUnit = capacityUnit || "lb";
            const productCapacity = (product: Product) => (product.rated_capacity ?? 0)
              * (product.capacity_unit === "kg" ? 2.2046226218 : 1)
              / (requestedUnit === "kg" ? 2.2046226218 : 1);
            const filtered = sortFallbackProducts(fallbackProducts.filter((product) =>
              (!categoryFilter || product.category.slug === categoryFilter)
              && (!q || [
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
              ].join(" ").toLocaleLowerCase("en").includes(q.toLocaleLowerCase("en")))
              && (!capacity || productCapacity(product) >= Number(capacity))
              && (!capacityMax || productCapacity(product) <= Number(capacityMax))
              && (!brake || product.brake_type === brake)
              && (!type || product.axle_type === type)
               && (!application || product.applications.some((item) => item.slug === application))
               && (!boltPattern || product.bolt_pattern === boltPattern)
               && dimensionMatches(product.hub_face_length, hubFace, product.dimension_unit)
               && dimensionMatches(product.spring_center, springCenter, product.dimension_unit)
               && (!tubeShape || Object.entries(product.axle_spec ?? {}).some(([key, value]) => key.toLowerCase().replaceAll(" ", "_") === "tube_shape" && value.toLowerCase() === tubeShape.toLowerCase()))
               && (!finish || product.surface_finish === finish)
              && (!customizable || product.is_customizable === (customizable === "true"))
              && (!madeToOrder || Boolean(product.made_to_order) === (madeToOrder === "true"))
              && (!oem || product.oem_available === (oem === "true"))
            ), sort);
            setTotal(filtered.length);
            setProducts(filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE));
          } else {
            setTotal(0);
            setProducts([]);
          }
        }
      } finally {
        if (active) setLoading(false);
      }
    }, 220);
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [application, boltPattern, brake, capacity, capacityError, capacityMax, capacityUnit, categoryFilter, customizable, finish, hubFace, madeToOrder, oem, page, q, retryVersion, sort, springCenter, tubeShape, type]);

  const countLabel = useMemo(
    () => loading ? "Loading catalogue…" : `${total} configuration${total === 1 ? "" : "s"} found`,
    [loading, total],
  );
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const resetPage = <T,>(setter: (value: T) => void, value: T) => {
    setter(value);
    setPage(1);
  };
  const removeFilter = <T,>(setter: (value: T) => void, value: T) => () => resetPage(setter, value);
  const clear = () => {
    setQ("");
    setCapacity("");
    setCapacityMax("");
    setCapacityUnit("");
    setBrake("");
    setType("");
    setApplication("");
    if (!category) setCategoryFilter("");
    setBoltPattern("");
    setHubFace("");
    setSpringCenter("");
    setTubeShape("");
    setFinish("");
    setCustomizable("");
    setMadeToOrder("");
    setOem("");
    setSort("");
    setPage(1);
    setFilterRequestError("");
  };
  const applicationLabel = applicationOptions.find((item) => item.slug === application)?.name ?? application;
  const categoryLabel = categoryOptions.find((item) => item.slug === categoryFilter)?.name ?? categoryFilter;
  const axleTypeLabel = filterOptions.axle_types.find((item) => item.value === type)?.label ?? type;
  const customInquiryHref = buildCatalogueInquiryHref({
    q, category: categoryFilter, application, capacityMin: capacity, capacityMax, capacityUnit,
    type, brake, boltPattern, hubFace, springCenter, tubeShape, finish, customizable, madeToOrder, oem,
  });
  const activeFilters = [
    q ? { key: "q", label: `Search: ${q}`, remove: removeFilter(setQ, "") } : null,
    !category && categoryFilter ? { key: "category", label: `Category: ${categoryLabel}`, remove: removeFilter(setCategoryFilter, "") } : null,
    application ? { key: "application", label: `Application: ${applicationLabel}`, remove: removeFilter(setApplication, "") } : null,
    capacity || capacityMax ? { key: "capacity", label: `Capacity: ${capacity ? Number(capacity).toLocaleString() : "0"}–${capacityMax ? Number(capacityMax).toLocaleString() : "any"} ${capacityUnit || "lb"}`, remove: () => { setCapacity(""); setCapacityMax(""); setPage(1); } } : null,
    capacityUnit && !capacity && !capacityMax ? { key: "capacity-unit", label: `Capacity unit: ${capacityUnit}`, remove: removeFilter(setCapacityUnit, "") } : null,
    brake ? { key: "brake", label: `Brake: ${brake}`, remove: removeFilter(setBrake, "") } : null,
    type ? { key: "type", label: `Axle: ${axleTypeLabel}`, remove: removeFilter(setType, "") } : null,
    boltPattern ? { key: "bolt", label: `Bolt pattern: ${boltPattern}`, remove: removeFilter(setBoltPattern, "") } : null,
    hubFace ? { key: "hub-face", label: `Hub face: ${hubFace}`, remove: removeFilter(setHubFace, "") } : null,
    springCenter ? { key: "spring-center", label: `Spring centre: ${springCenter}`, remove: removeFilter(setSpringCenter, "") } : null,
    tubeShape ? { key: "tube-shape", label: `Tube shape: ${tubeShape}`, remove: removeFilter(setTubeShape, "") } : null,
    finish ? { key: "finish", label: `Finish: ${finish}`, remove: removeFilter(setFinish, "") } : null,
    customizable ? { key: "customizable", label: customizable === "true" ? "Customizable" : "Standard only", remove: removeFilter(setCustomizable, "") } : null,
    madeToOrder ? { key: "made-to-order", label: madeToOrder === "true" ? "Made to order" : "Stock/standard production", remove: removeFilter(setMadeToOrder, "") } : null,
    oem ? { key: "oem", label: oem === "true" ? "OEM available" : "Non-OEM", remove: removeFilter(setOem, "") } : null,
  ].filter((item): item is { key: string; label: string; remove: () => void } => item !== null);
  const filterSignature = activeFilters.map((item) => item.label).join("|");

  useEffect(() => {
    if (loading || failed || !filterSignature || trackedFilterSignature.current === filterSignature) return;
    const timer = window.setTimeout(() => {
      trackedFilterSignature.current = filterSignature;
      trackEvent(q ? "search_product" : "use_product_filter", {
        filters: activeFilters.length,
        results: total,
        placement: "product_catalogue",
      });
    }, 350);
    return () => window.clearTimeout(timer);
  }, [activeFilters.length, failed, filterSignature, loading, q, total]);
  const openFilters = () => {
    setFilterOpen(true);
    window.requestAnimationFrame(() => document.getElementById("catalog-filter-close")?.focus());
  };
  const closeFilters = () => {
    setFilterOpen(false);
    window.requestAnimationFrame(() => document.getElementById("catalog-filter-trigger")?.focus());
  };

  return <>
    <section className="page-hero">
      <div className="shell">
        <div className="breadcrumbs"><Link href="/">Home</Link><span>/</span><span>Products</span></div>
        <div className="page-heading"><p className="eyebrow">Technical product catalogue</p><h1>{heading}</h1><p>{description}</p></div>
      </div>
    </section>
    <div className="shell catalog-layout">
      <button
        id="catalog-filter-trigger"
        className="button-outline filter-drawer-trigger"
        type="button"
        aria-expanded={filterOpen}
        aria-controls="catalog-filters"
        onClick={openFilters}
      >
        Filters{activeFilters.length > 0 ? ` (${activeFilters.length})` : ""}
      </button>
      {filterOpen && <button className="filter-backdrop" type="button" tabIndex={-1} aria-label="Close product filters" onClick={closeFilters} />}
      <aside
        id="catalog-filters"
        className={`filter-panel ${filterOpen ? "open" : ""}`}
        role={filterOpen ? "dialog" : "complementary"}
        aria-modal={filterOpen || undefined}
        aria-labelledby="catalog-filter-title"
      >
        <div className="filter-drawer-head">
          <h2 id="catalog-filter-title">Technical filters</h2>
          <button id="catalog-filter-close" className="filter-drawer-close" type="button" aria-label="Close filters" onClick={closeFilters}>×</button>
        </div>
        <div className="field"><label htmlFor="product-search">Keyword / model</label><input id="product-search" value={q} maxLength={120} onChange={(event) => resetPage(setQ, event.target.value)} placeholder="e.g. TA-3500" /></div>
        {!category && <div className="field"><label htmlFor="product-category">Category</label><select id="product-category" value={categoryFilter} onChange={(event) => resetPage(setCategoryFilter, event.target.value)}><option value="">All categories</option>{categoryOptions.map((item) => <option value={item.slug} key={item.id}>{item.name}</option>)}</select></div>}
        <div className="field"><label htmlFor="application">Application</label><select id="application" value={application} onChange={(event) => resetPage(setApplication, event.target.value)}><option value="">All applications</option>{applicationOptions.map((item) => <option value={item.slug} key={item.id}>{item.name}</option>)}</select></div>
        <div className="field"><label htmlFor="capacity-unit">Capacity unit</label><select id="capacity-unit" value={capacityUnit} onChange={(event) => { resetPage(setCapacityUnit, event.target.value); setCapacity(""); setCapacityMax(""); }}><option value="">Pounds (lb) default</option>{(filterOptions.capacity_units ?? [{ value: "lb", label: "Pounds (lb)" }, { value: "kg", label: "Kilograms (kg)" }]).map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></div>
        <div className="field"><label htmlFor="capacity">Minimum capacity</label><input id="capacity" type="number" min="0" step="any" inputMode="decimal" list="catalog-capacities" value={capacity} aria-invalid={Boolean(capacityError)} aria-describedby={capacityError ? "capacity-range-error" : undefined} onChange={(event) => { if (event.target.value && !capacityUnit) setCapacityUnit("lb"); resetPage(setCapacity, event.target.value); }} placeholder="e.g. 3000" /><datalist id="catalog-capacities">{(filterOptions.capacities_by_unit?.[capacityUnit || "lb"] ?? filterOptions.capacities).map((item) => <option value={item} key={item} />)}</datalist></div>
        <div className="field"><label htmlFor="capacity-max">Maximum capacity</label><input id="capacity-max" type="number" min="0" step="any" inputMode="decimal" value={capacityMax} aria-invalid={Boolean(capacityError)} aria-describedby={capacityError ? "capacity-range-error" : undefined} onChange={(event) => { if (event.target.value && !capacityUnit) setCapacityUnit("lb"); resetPage(setCapacityMax, event.target.value); }} placeholder="e.g. 5000" />{capacityError && <p id="capacity-range-error" className="field-error" role="alert">{capacityError}</p>}</div>
        <div className="field"><label htmlFor="brake">Brake type</label><select id="brake" value={brake} onChange={(event) => resetPage(setBrake, event.target.value)}><option value="">All brake types</option>{filterOptions.brake_types.map((item) => <option key={item}>{item}</option>)}</select></div>
        <div className="field"><label htmlFor="axle-type">Axle type</label><select id="axle-type" value={type} onChange={(event) => resetPage(setType, event.target.value)}><option value="">All axle types</option>{filterOptions.axle_types.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></div>
        <div className="field"><label htmlFor="bolt-pattern">Bolt pattern</label><select id="bolt-pattern" value={boltPattern} onChange={(event) => resetPage(setBoltPattern, event.target.value)}><option value="">All bolt patterns</option>{filterOptions.bolt_patterns.map((item) => <option key={item}>{item}</option>)}</select></div>
        <div className="field"><label htmlFor="hub-face">Hub face length</label><input id="hub-face" value={hubFace} maxLength={40} onChange={(event) => resetPage(setHubFace, event.target.value)} placeholder={'e.g. 95"'} /></div>
        <div className="field"><label htmlFor="spring-center">Spring centre</label><input id="spring-center" value={springCenter} maxLength={40} onChange={(event) => resetPage(setSpringCenter, event.target.value)} placeholder={'e.g. 80"'} /></div>
        <div className="field"><label htmlFor="tube-shape">Tube shape</label><select id="tube-shape" value={tubeShape} onChange={(event) => resetPage(setTubeShape, event.target.value)}><option value="">All tube shapes</option>{(filterOptions.tube_shapes ?? []).map((item) => <option key={item}>{item}</option>)}</select></div>
        <div className="field"><label htmlFor="surface-finish">Surface finish</label><select id="surface-finish" value={finish} onChange={(event) => resetPage(setFinish, event.target.value)}><option value="">All finishes</option>{filterOptions.surface_finishes.map((item) => <option key={item}>{item}</option>)}</select></div>
        <div className="field"><label htmlFor="customizable">Configuration</label><select id="customizable" value={customizable} onChange={(event) => resetPage(setCustomizable, event.target.value)}><option value="">Any configuration</option><option value="true">Customizable</option><option value="false">Standard only</option></select></div>
        <div className="field"><label htmlFor="made-to-order">Made to order</label><select id="made-to-order" value={madeToOrder} onChange={(event) => resetPage(setMadeToOrder, event.target.value)}><option value="">Any production mode</option><option value="true">Made to order</option><option value="false">Not made to order</option></select></div>
        <div className="field"><label htmlFor="oem">OEM availability</label><select id="oem" value={oem} onChange={(event) => resetPage(setOem, event.target.value)}><option value="">Any</option><option value="true">OEM available</option><option value="false">Non-OEM</option></select></div>
        <div className="filter-panel-actions">
          <button className="button-outline" type="button" onClick={clear}>Clear filters</button>
          <button className="button filter-drawer-apply" type="button" onClick={closeFilters}>View results</button>
        </div>
      </aside>
      <main id="catalog-results" className="catalog-results" aria-busy={loading}>
        <div className="catalog-main-head">
          <p aria-live="polite">{countLabel}</p>
          <div className="catalog-controls">
            <div className="field"><label htmlFor="sort">Sort</label><select id="sort" value={sort} onChange={(event) => resetPage(setSort, event.target.value as Sort)}><option value="">Recommended</option><option value="newest">Newest</option><option value="capacity_asc">Capacity: low to high</option><option value="capacity_desc">Capacity: high to low</option><option value="model">Model A–Z</option></select></div>
            <div className="view-toggle" role="group" aria-label="Product layout">
              <button type="button" aria-pressed={viewMode === "grid"} className={viewMode === "grid" ? "active" : ""} onClick={() => setViewMode("grid")}>Grid</button>
              <button type="button" aria-pressed={viewMode === "list"} className={viewMode === "list" ? "active" : ""} onClick={() => setViewMode("list")}>List</button>
            </div>
          </div>
        </div>
        {activeFilters.length > 0 && <div className="active-filters" aria-label="Selected product filters">
          <span>Selected filters</span>
          {activeFilters.map((item) => <button type="button" key={item.key} onClick={item.remove} aria-label={`Remove ${item.label}`}>{item.label}<b aria-hidden="true">×</b></button>)}
          <button className="clear-filter-chip" type="button" onClick={clear}>Clear all</button>
        </div>}
        {filterMetadataFailed && <div className="status-message" role="status">
          <span>Some filter options are temporarily unavailable. Core technical options remain available, and catalogue results can still be browsed.</span>
          <button className="button-outline" type="button" onClick={() => setRetryVersion((value) => value + 1)}>Retry filter options</button>
        </div>}
        {failed && !capacityError && <div className="status-message" role="status">
          <span>{starterFallbackEnabled ? "The catalogue API is unavailable, so local demonstration data is shown." : "The catalogue is temporarily unavailable."}</span>
          <button className="button-outline" type="button" onClick={() => setRetryVersion((value) => value + 1)}>Retry</button>
        </div>}
        {capacityError
          ? <div className="empty"><h2>Check the capacity range</h2><p>{capacityError}</p></div>
          : filterRequestError
            ? <div className="empty"><h2>Check the product filters</h2><p>{filterRequestError}</p><button className="button-outline" type="button" onClick={clear}>Clear filters</button></div>
            : loading
            ? <CatalogueSkeleton viewMode={viewMode} />
            : products.length === 0
              ? <div className="empty"><h2>No standard configuration found</h2><p>Send your selected requirements for a custom configuration review. Your filters will be included in the enquiry.</p><Link className="button" href={customInquiryHref}>Request custom axle</Link></div>
              : <div className={`product-grid ${viewMode === "list" ? "product-list" : ""}`}>{products.map((product, index) => <ProductCard key={product.id} product={product} eagerImage={index < 3} />)}</div>}
        {!capacityError && !loading && pageCount > 1 && <nav className="pagination" aria-label="Catalogue pages">
          <button className="button-outline" type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</button>
          <span>Page {page} of {pageCount}</span>
          <button className="button-outline" type="button" disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}>Next</button>
        </nav>}
      </main>
    </div>
  </>;
}

function CatalogueSkeleton({ viewMode }: { viewMode: ViewMode }) {
  return <div className={`product-grid skeleton-grid ${viewMode === "list" ? "product-list" : ""}`} aria-hidden="true">
    {Array.from({ length: 6 }, (_, index) => <div className="product-card skeleton-card" key={index}>
      <div className="skeleton-media" />
      <div className="skeleton-body">
        <span className="skeleton-line short" />
        <span className="skeleton-line title" />
        <span className="skeleton-line" />
        <span className="skeleton-line" />
        <span className="skeleton-line actions" />
      </div>
    </div>)}
  </div>;
}
