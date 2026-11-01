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
import { UnitToggle } from "@/components/UnitToggle";
import { useSiteConfig } from "@/components/SiteConfigProvider";
import { trackEvent } from "@/lib/events";
import type { Application, Category, Product, SpecFieldDefinition, UnitSystem } from "@/lib/types";
import type { PaginatedProducts } from "@/lib/api";
import { useUnitSystem } from "@/lib/units";
import { apiErrorMessage } from "@/lib/form-errors";
import { fetchWithTimeout } from "@/lib/fetch-with-timeout";
import { parseCataloguePage, specParameters } from "@/lib/catalog-query";
import { buildCatalogueInquiryHref } from "@/lib/catalog-inquiry";

type Props = { category?: string; heading?: string; description?: string; initialPage?: PaginatedProducts; initialFailed?: boolean };
type Sort = "" | "newest" | "model";
type ViewMode = "grid" | "list";
type ProductResponse = {
  count: number;
  next: string | null;
  previous: string | null;
  results: Product[];
};
type FilterParameter = SpecFieldDefinition & {
  values: string[] | Record<UnitSystem, string[]>;
};
type FilterOptions = { parameters: FilterParameter[] };

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
  && Array.isArray(value.parameters)
  && value.parameters.every((item) => isRecord(item) && typeof item.key === "string" && typeof item.label === "string");

const isProductResponse = (value: unknown): value is ProductResponse =>
  isRecord(value)
  && typeof value.count === "number"
  && Array.isArray(value.results);

const valuesFromSearch = (params: URLSearchParams) => ({
  q: (params.get("q") ?? "").slice(0, 120),
  application: (params.get("application") ?? "").slice(0, 50),
  category: (params.get("category") ?? "").slice(0, 50),
  specs: specParameters(params),
  customizable: (params.get("customizable") ?? "").slice(0, 5),
  madeToOrder: (params.get("made_to_order") ?? "").slice(0, 5),
  oem: (params.get("oem") ?? "").slice(0, 5),
  sort: (["newest", "model"].includes(params.get("sort") ?? "") ? params.get("sort") : "") as Sort,
  page: parseCataloguePage(params.get("page")),
});

const optionValues = (parameter: FilterParameter, system: UnitSystem): string[] =>
  Array.isArray(parameter.values) ? parameter.values : parameter.values?.[system] ?? [];

const unitLabel = (parameter: FilterParameter, code: string) =>
  parameter.units?.find((unit) => unit.code === code)?.label ?? code;

/** Human description of one parameter filter, also used in RFQ notes. */
function describeParameter(parameter: FilterParameter | undefined, key: string, specs: Record<string, string>, system: UnitSystem): string {
  const label = parameter?.label ?? key;
  const base = `spec.${key}`;
  if (parameter?.type === "number") {
    const unit = specs[`${base}__unit`] || parameter.default_unit?.[system] || "";
    const suffix = unit ? ` ${unitLabel(parameter, unit)}` : "";
    if (specs[base]) return `${label}: ${specs[base]}${/[a-z"]/i.test(specs[base]) ? "" : suffix}`;
    const minimum = specs[`${base}__min`];
    const maximum = specs[`${base}__max`];
    if (minimum && maximum) return `${label}: ${minimum}–${maximum}${suffix}`;
    if (minimum) return `${label}: at least ${minimum}${suffix}`;
    if (maximum) return `${label}: up to ${maximum}${suffix}`;
    return "";
  }
  const value = specs[base];
  if (!value) return "";
  return `${label}: ${parameter?.type === "boolean" ? (value === "true" ? "Yes" : "No") : value}`;
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
    initial={valuesFromSearch(new URLSearchParams(queryString))}
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
  const system = useUnitSystem(useSiteConfig().defaultUnitSystem);

  const [products, setProducts] = useState<Product[]>(initialPage?.results ?? []);
  const [total, setTotal] = useState(initialPage?.count ?? 0);
  const [loading, setLoading] = useState(!initialPage);
  const [failed, setFailed] = useState(initialFailed);
  const [filterRequestError, setFilterRequestError] = useState("");
  const [q, setQ] = useState(initial.q);
  const [application, setApplication] = useState(initial.application);
  const [categoryFilter, setCategoryFilter] = useState(category ?? initial.category);
  const [specs, setSpecs] = useState<Record<string, string>>(initial.specs);
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
  const [applicationOptions, setApplicationOptions] = useState<Application[]>(starterFallbackEnabled ? starterApplications : []);
  const [categoryOptions, setCategoryOptions] = useState<Category[]>(starterFallbackEnabled ? starterCategories : []);
  const [parameters, setParameters] = useState<FilterParameter[]>([]);
  const specsKey = JSON.stringify(specs);

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
      setApplication(next.application);
      setCategoryFilter(category ?? next.category);
      setSpecs(next.specs);
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
        const [applicationsResponse, categoriesResponse] = await Promise.all([
          fetchWithTimeout(`${apiBase}/applications/`, { signal: controller.signal }),
          fetchWithTimeout(`${apiBase}/categories/`, { signal: controller.signal }),
        ]);
        let incomplete = false;
        if (applicationsResponse.ok) {
          const applicationsPayload: unknown = await applicationsResponse.json();
          if (isOptionList<Application>(applicationsPayload)) setApplicationOptions(applicationsPayload);
          else incomplete = true;
        } else incomplete = true;
        if (categoriesResponse.ok) {
          const categoriesPayload: unknown = await categoriesResponse.json();
          if (isOptionList<Category>(categoriesPayload)) setCategoryOptions(categoriesPayload);
          else incomplete = true;
        } else incomplete = true;
        if (!controller.signal.aborted && incomplete) setFilterMetadataFailed(true);
      } catch (error) {
        if (!controller.signal.aborted && (error as Error).name !== "AbortError") {
          setFilterMetadataFailed(true);
        }
      }
    })();
    return () => controller.abort();
  }, [retryVersion]);

  // Parameter filters depend on the selected category and unit system.
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const params = new URLSearchParams({ units: system });
        if (categoryFilter) params.set("category", categoryFilter);
        const response = await fetchWithTimeout(`${apiBase}/filters/?${params}`, { signal: controller.signal });
        const payload: unknown = response.ok ? await response.json() : null;
        if (isFilterOptions(payload)) {
          setParameters(payload.parameters);
          setFilterMetadataFailed(false);
        } else if (!controller.signal.aborted) {
          setParameters([]);
          setFilterMetadataFailed(response.status !== 400);
        }
      } catch (error) {
        if (!controller.signal.aborted && (error as Error).name !== "AbortError") setFilterMetadataFailed(true);
      }
    })();
    return () => controller.abort();
  }, [categoryFilter, retryVersion, system]);

  const listParams = useMemo(() => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (application) params.set("application", application);
    for (const [key, value] of Object.entries(specs)) if (value) params.set(key, value);
    if (customizable) params.set("customizable", customizable);
    if (madeToOrder) params.set("made_to_order", madeToOrder);
    if (oem) params.set("oem", oem);
    if (sort) params.set("sort", sort);
    return params;
    // specsKey stands in for the specs object identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application, customizable, madeToOrder, oem, q, sort, specsKey]);

  useEffect(() => {
    const params = new URLSearchParams(listParams);
    if (!category && categoryFilter) params.set("category", categoryFilter);
    if (page > 1) params.set("page", String(page));
    const nextUrl = params.size ? `${pathname}?${params}` : pathname;
    const timer = window.setTimeout(() => {
      if (nextUrl !== `${pathname}${queryString ? `?${queryString}` : ""}`) {
        selfWrittenQueryRef.current = params.toString();
        router.replace(nextUrl, { scroll: false });
      }
    }, 180);
    return () => window.clearTimeout(timer);
  }, [category, categoryFilter, listParams, page, pathname, queryString, router]);

  useEffect(() => {
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
      const params = new URLSearchParams(listParams);
      if (categoryFilter) params.set("category", categoryFilter);
      params.set("units", system);
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
            const words = q.toLocaleLowerCase("en").split(/\s+/).filter(Boolean);
            const filtered = fallbackProducts.filter((product) => {
              const text = [product.title, product.model, product.sku, product.short_description, product.category.name, ...product.specs.map((spec) => spec.display.imperial)].join(" ").toLocaleLowerCase("en");
              return (!categoryFilter || product.category.slug === categoryFilter)
                && (!application || product.applications.some((item) => item.slug === application))
                && words.every((word) => text.includes(word));
            });
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
  }, [application, categoryFilter, listParams, page, q, retryVersion, system]);

  const countLabel = useMemo(
    () => loading ? "Loading catalogue…" : `${total} configuration${total === 1 ? "" : "s"} found`,
    [loading, total],
  );
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const resetPage = <T,>(setter: (value: T) => void, value: T) => {
    setter(value);
    setPage(1);
  };
  const setSpec = (name: string, value: string) => {
    setSpecs((current) => {
      const next = { ...current };
      if (value) next[name] = value;
      else delete next[name];
      return next;
    });
    setPage(1);
  };
  const removeParameter = (key: string) => () => {
    setSpecs((current) => Object.fromEntries(Object.entries(current).filter(([name]) => name !== `spec.${key}` && !name.startsWith(`spec.${key}__`))));
    setPage(1);
  };
  const removeFilter = <T,>(setter: (value: T) => void, value: T) => () => resetPage(setter, value);
  const clear = () => {
    setQ("");
    setApplication("");
    if (!category) setCategoryFilter("");
    setSpecs({});
    setCustomizable("");
    setMadeToOrder("");
    setOem("");
    setSort("");
    setPage(1);
    setFilterRequestError("");
  };
  const changeCategory = (value: string) => {
    // Parameters differ between categories; drop the previous category's filters.
    setSpecs({});
    resetPage(setCategoryFilter, value);
  };
  const parameterByKey = new Map(parameters.map((parameter) => [parameter.key, parameter]));
  const selectedParameterKeys = [...new Set(Object.keys(specs).map((name) => name.slice(5).replace(/__(?:min|max|unit)$/, "")))];
  const parameterNotes = selectedParameterKeys.map((key) => describeParameter(parameterByKey.get(key), key, specs, system)).filter(Boolean);
  const applicationLabel = applicationOptions.find((item) => item.slug === application)?.name ?? application;
  const categoryLabel = categoryOptions.find((item) => item.slug === categoryFilter)?.name ?? categoryFilter;
  const customInquiryHref = buildCatalogueInquiryHref({
    q, category: categoryFilter, application, customizable, madeToOrder, oem, parameterNotes,
  });
  const activeFilters = [
    q ? { key: "q", label: `Search: ${q}`, remove: removeFilter(setQ, "") } : null,
    !category && categoryFilter ? { key: "category", label: `Category: ${categoryLabel}`, remove: () => changeCategory("") } : null,
    application ? { key: "application", label: `Application: ${applicationLabel}`, remove: removeFilter(setApplication, "") } : null,
    ...selectedParameterKeys.map((key) => {
      const label = describeParameter(parameterByKey.get(key), key, specs, system);
      return label ? { key: `spec-${key}`, label, remove: removeParameter(key) } : null;
    }),
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

  const parameterField = (parameter: FilterParameter) => {
    const base = `spec.${parameter.key}`;
    const id = `filter-${parameter.key}`;
    const values = optionValues(parameter, system);
    if (parameter.type === "number") {
      const unit = parameter.default_unit?.[system] ?? "";
      const suffix = unit ? ` (${unitLabel(parameter, unit)})` : "";
      const listId = `${id}-values`;
      const setRange = (name: string, value: string) => {
        setSpec(name, value);
        // Values typed in the visitor's current unit system.
        if (unit) setSpec(`${base}__unit`, value || specs[`${base}__${name.endsWith("min") ? "max" : "min"}`] ? unit : "");
      };
      return <div className="field" key={parameter.key}>
        <label htmlFor={`${id}-min`}>{parameter.label}{suffix}</label>
        <div className="form-grid">
          <input id={`${id}-min`} type="number" min="0" step="any" inputMode="decimal" list={listId} aria-label={`Minimum ${parameter.label}`} value={specs[`${base}__min`] ?? ""} onChange={(event) => setRange(`${base}__min`, event.target.value)} placeholder="Min" />
          <input id={`${id}-max`} type="number" min="0" step="any" inputMode="decimal" list={listId} aria-label={`Maximum ${parameter.label}`} value={specs[`${base}__max`] ?? ""} onChange={(event) => setRange(`${base}__max`, event.target.value)} placeholder="Max" />
        </div>
        <datalist id={listId}>{values.map((item) => <option value={item} key={item} />)}</datalist>
      </div>;
    }
    if (parameter.type === "boolean") {
      return <div className="field" key={parameter.key}><label htmlFor={id}>{parameter.label}</label><select id={id} value={specs[base] ?? ""} onChange={(event) => setSpec(base, event.target.value)}><option value="">Any</option><option value="true">Yes</option><option value="false">No</option></select></div>;
    }
    return <div className="field" key={parameter.key}><label htmlFor={id}>{parameter.label}</label><select id={id} value={specs[base] ?? ""} onChange={(event) => setSpec(base, event.target.value)}><option value="">All</option>{values.map((item) => <option key={item}>{item}</option>)}</select></div>;
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
        {!category && <div className="field"><label htmlFor="product-category">Category</label><select id="product-category" value={categoryFilter} onChange={(event) => changeCategory(event.target.value)}><option value="">All categories</option>{categoryOptions.map((item) => <option value={item.slug} key={item.id}>{item.parent ? "— " : ""}{item.name}</option>)}</select></div>}
        {applicationOptions.length > 0 && <div className="field"><label htmlFor="application">Application</label><select id="application" value={application} onChange={(event) => resetPage(setApplication, event.target.value)}><option value="">All applications</option>{applicationOptions.map((item) => <option value={item.slug} key={item.id}>{item.name}</option>)}</select></div>}
        {parameters.map(parameterField)}
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
            <UnitToggle />
            <div className="field"><label htmlFor="sort">Sort</label><select id="sort" value={sort} onChange={(event) => resetPage(setSort, event.target.value as Sort)}><option value="">Recommended</option><option value="newest">Newest</option><option value="model">Model A–Z</option></select></div>
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
          <span>Some filter options are temporarily unavailable. Catalogue results can still be browsed.</span>
          <button className="button-outline" type="button" onClick={() => setRetryVersion((value) => value + 1)}>Retry filter options</button>
        </div>}
        {failed && <div className="status-message" role="status">
          <span>{starterFallbackEnabled ? "The catalogue API is unavailable, so local demonstration data is shown." : "The catalogue is temporarily unavailable."}</span>
          <button className="button-outline" type="button" onClick={() => setRetryVersion((value) => value + 1)}>Retry</button>
        </div>}
        {filterRequestError
          ? <div className="empty"><h2>Check the product filters</h2><p>{filterRequestError}</p><button className="button-outline" type="button" onClick={clear}>Clear filters</button></div>
          : loading
            ? <CatalogueSkeleton viewMode={viewMode} />
            : products.length === 0
              ? <div className="empty"><h2>No standard configuration found</h2><p>Send your selected requirements for a custom configuration review. Your filters will be included in the enquiry.</p><Link className="button" href={customInquiryHref}>Request custom axle</Link></div>
              : <div className={`product-grid ${viewMode === "list" ? "product-list" : ""}`}>{products.map((product, index) => <ProductCard key={product.id} product={product} eagerImage={index < 3} />)}</div>}
        {!loading && pageCount > 1 && <nav className="pagination" aria-label="Catalogue pages">
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
