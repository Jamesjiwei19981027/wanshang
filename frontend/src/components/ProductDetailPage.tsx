import type { Metadata } from "next";
import Link from "next/link";
import { notFound, permanentRedirect } from "next/navigation";
import { DocumentDownloadLink } from "@/components/DocumentDownloadLink";
import { ProductCard } from "@/components/ProductCard";
import { ProductDetailActions } from "@/components/ProductDetailActions";
import { ProductGallery } from "@/components/ProductGallery";
import { ProductInquiryForm } from "@/components/ProductInquiryForm";
import { getProductDetail, getRelatedProducts } from "@/lib/api";
import { getProductPath } from "@/lib/product-url";
import { formatDimension } from "@/lib/units";
import {
  breadcrumbJsonLd,
  faqJsonLd,
  productJsonLd,
  safeHref,
  serializeJsonLd,
} from "@/lib/structured-data";

const normalizeSpecKey = (key: string) => key.trim().toLocaleLowerCase("en").replaceAll(" ", "_");
const specLabel = (key: string) => key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const primaryProductImage = (product: Awaited<ReturnType<typeof getProductDetail>>) => {
  if (!product) return "";
  return product.images?.find((image) => Boolean(image.image_url))?.image_url
    || product.image_url
    || "";
};

export async function getProductMetadata(slug: string): Promise<Metadata> {
  const product = await getProductDetail(slug);
  if (!product) notFound();
  const primaryImage = primaryProductImage(product);
  return {
    title: product.meta_title || product.title,
    description: product.meta_description || product.short_description,
    alternates: { canonical: getProductPath(product) },
    keywords: [
      product.model,
      product.sku,
      product.axle_type,
      product.category.name,
      "trailer axle",
      product.brake_type,
      product.bolt_pattern,
    ].filter(Boolean),
    openGraph: {
      title: product.meta_title || product.title,
      description: product.meta_description || product.short_description,
      images: primaryImage ? [primaryImage] : undefined,
      type: "website",
      url: getProductPath(product),
    },
  };
}

export async function ProductDetailPage({
  slug,
  expectedCategory,
}: {
  slug: string;
  expectedCategory?: string;
}) {
  const product = await getProductDetail(slug);
  if (!product) notFound();

  const productPath = getProductPath(product);
  if (expectedCategory && expectedCategory !== product.category.slug) {
    permanentRedirect(productPath);
  }

  const specValues = new Map(Object.entries(product.axle_spec ?? {}).map(([key, value]) => [normalizeSpecKey(key), value]));
  const takeSpec = (...keys: string[]) => keys.map((key) => specValues.get(key)).find((value) => Boolean(value)) || "";
  const specGroups = [
    ["Basic information", [
      ["Product model", product.model],
      ["SKU", product.sku],
      ["Axle type", product.axle_type],
      ["Rated capacity", product.rated_capacity ? `${product.rated_capacity.toLocaleString()} ${product.capacity_unit || "lb"}` : ""],
      ["Application", product.applications.map((item) => item.name).join(", ")],
      ["Product status", `${product.made_to_order ? "Made to order" : product.is_customizable ? "Customizable" : "Standard product"}${product.oem_available ? " · OEM available" : ""}`],
    ]],
    ["Dimensions", [
      ["Hub face length", formatDimension(product.hub_face_length, product.dimension_unit)],
      ["Spring centre", formatDimension(product.spring_center, product.dimension_unit)],
      ["Overall length", formatDimension(takeSpec("overall_length"), product.dimension_unit)],
      ["Tube shape", takeSpec("tube_shape")],
      ["Tube size", product.tube_size],
      ["Tube wall thickness", takeSpec("tube_wall_thickness", "tube_wall")],
      ["Drop height", takeSpec("drop_height")],
    ]],
    ["Hub & spindle", [
      ["Hub type", takeSpec("hub_type")],
      ["Bolt pattern", product.bolt_pattern],
      ["Stud size", product.stud_size || ""],
      ["Spindle type", takeSpec("spindle_type", "spindle")],
      ["Inner bearing", takeSpec("inner_bearing")],
      ["Outer bearing", takeSpec("outer_bearing")],
      ["Seal size", takeSpec("seal_size")],
      ["Grease cap", product.grease_cap_type || ""],
    ]],
    ["Brake specifications", [
      ["Brake type", product.brake_type],
      ["Brake size", takeSpec("brake_size")],
      ["Brake voltage", takeSpec("brake_voltage")],
    ]],
    ["Material & finish", [
      ["Axle material", takeSpec("axle_material")],
      ["Surface finish", product.surface_finish],
      ["Corrosion protection", takeSpec("corrosion_protection")],
    ]],
    ["Commercial information", [
      ["MOQ", takeSpec("moq")],
      ["Lead time", takeSpec("lead_time")],
      ["Loading port", takeSpec("loading_port")],
      ["Packaging", product.packaging || ""],
    ]],
  ].map(([title, items]) => ({
    title: title as string,
    items: (items as string[][]).filter(([, value]) => Boolean(value)),
  })).filter((group) => group.items.length);
  const documents = (product.documents ?? []).filter((document) => Boolean(document.url));
  const productImages = (product.images ?? []).filter((image) => Boolean(image.image_url)).map((image) => ({
    url: image.image_url,
    alt: image.alt_text || `${product.title} product image`,
    caption: image.caption,
  }));
  const primaryImage = primaryProductImage(product);
  const galleryImages = productImages.length
    ? productImages
    : primaryImage ? [{ url: primaryImage, alt: `${product.title} technical product image` }] : [];
  const faqs = [
    {
      question: `Can ${product.model} be configured to different mounting dimensions?`,
      answer: product.is_customizable
        ? "Yes. Submit hub-face, spring-centre and overall dimensions for drawing review before production release."
        : "This is presented as a standard configuration. Contact engineering to confirm whether an alternative is available.",
    },
    {
      question: "What information is needed for final axle confirmation?",
      answer: "Provide complete trailer geometry, actual gross weight and load distribution, wheels and tyres, mounting dimensions, operating environment, braking requirements and destination-market regulations.",
    },
    {
      question: "Are technical drawings available?",
      answer: documents.length
        ? "Published documents are listed on this page. Request controlled drawings or a configuration-specific approval drawing through the RFQ."
        : "Request a controlled drawing through the RFQ. Availability depends on the selected configuration and document status.",
    },
  ];
  const related = await getRelatedProducts(product);
  const categoryPath = `/products/${encodeURIComponent(product.category.slug)}`;
  const structuredData = serializeJsonLd({
    "@context": "https://schema.org",
    "@graph": [
      productJsonLd(product, productPath, primaryImage),
      breadcrumbJsonLd([
        { name: "Home", path: "/" },
        { name: "Products", path: "/products" },
        { name: product.category.name, path: categoryPath },
        { name: product.title, path: productPath },
      ]),
      faqJsonLd(faqs),
    ],
  });

  return <main>
    <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: structuredData }} />
    <section className="page-hero"><div className="shell"><div className="breadcrumbs"><Link href="/">Home</Link><span>/</span><Link href="/products">Products</Link><span>/</span><Link href={categoryPath}>{product.category.name}</Link><span>/</span><span>{product.model}</span></div><div className="page-heading"><p className="eyebrow">Trailer axle platform</p><h1>{product.title}</h1><p>{product.short_description}</p></div></div></section>
    <section className="shell detail"><div className="detail-grid"><ProductGallery images={galleryImages} productTitle={product.title} /><div className="detail-info"><span className="detail-kicker">MODEL {product.model} · SKU {product.sku}</span><h2>{product.title}</h2><p>{product.description}</p><ProductDetailActions product={product} /><div className="detail-meta"><div><span>Rated capacity</span><strong>{product.rated_capacity?.toLocaleString() ?? "—"} {product.capacity_unit || "lb"}</strong></div><div><span>Axle profile</span><strong>{product.axle_type}</strong></div><div><span>Brake type</span><strong>{product.brake_type || "—"}</strong></div><div><span>Bolt pattern</span><strong>{product.bolt_pattern || "—"}</strong></div><div><span>Hub face</span><strong>{formatDimension(product.hub_face_length, product.dimension_unit)}</strong></div><div><span>Spring centre</span><strong>{formatDimension(product.spring_center, product.dimension_unit)}</strong></div></div></div></div>
      <div className="detail-content"><div><section className="content-card"><h2>Technical specification</h2><div className="spec-groups">{specGroups.map((group) => <details className="spec-group" open key={group.title}><summary>{group.title}<span aria-hidden="true">＋</span></summary><div className="table-scroll"><table className="spec-table"><tbody>{group.items.map(([key, value]) => <tr key={key}><td>{specLabel(key)}</td><td>{value}</td></tr>)}</tbody></table></div></details>)}</div></section><section className="content-card"><h2>Available configuration</h2><div className="configuration-grid"><div><span>Surface finish</span><strong>{product.surface_finish || "Confirm with RFQ"}</strong></div><div><span>Product status</span><strong>{product.made_to_order ? "Made to order" : product.is_customizable ? "Customizable" : "Standard product"}</strong></div><div><span>OEM availability</span><strong>{product.oem_available ? "Available" : "Contact sales"}</strong></div><div><span>Application</span><strong>{product.applications.map((item) => item.name).join(", ") || "Confirm with RFQ"}</strong></div></div></section><section className="content-card"><h2>Quality, packing &amp; delivery</h2><p>Confirm inspection requirements, traceability records, packaging, lead time and loading port with your quotation. Ask which inspection reports and supporting documents are available for your selected configuration.</p></section><section className="content-card"><h2>Configuration note</h2><p>A final trailer axle selection should account for complete trailer geometry, actual load distribution, wheels/tires, required braking and the operating environment.</p></section>
        {documents.length ? <section className="content-card"><h2>Documents</h2>{documents.map((document) => {
          const href = safeHref(document.url);
          const previewHref = href ? `${href}${href.includes("?") ? "&" : "?"}preview=1` : "";
          return <div className="document-row" key={document.id}><div><strong>{document.title}</strong><span> · {document.document_type} · {document.version}</span></div><div className="button-row">{document.previewable && previewHref && <a className="button-outline" href={previewHref} target="_blank" rel="noopener noreferrer">Preview</a>}<DocumentDownloadLink url={document.url} title={document.title} productModel={product.model} /></div></div>;
        })}</section> : null}
        <section className="content-card"><h2>Frequently asked questions</h2><div className="faq-list">{faqs.map((faq) => <details key={faq.question}><summary>{faq.question}</summary><p>{faq.answer}</p></details>)}</div></section>
        <section className="content-card product-inquiry"><h2>Enquire about this product</h2><p>The model, product ID and your selected options are recorded with the RFQ.</p><ProductInquiryForm product={product} /></section>
      </div><aside><div className="sidebar-cta"><h2>Need a custom axle?</h2><p>Send the hub face, spring centre, capacity and brake requirement. Add a drawing if available.</p><Link className="button" href="/request-a-quote">Build an RFQ</Link></div></aside></div>
    </section>
    {related.length > 0 && <section className="section section-alt"><div className="shell"><div className="section-head"><div><p className="eyebrow">Related direction</p><h2>Compare other platforms</h2></div><Link className="link-arrow" href={categoryPath}>All {product.category.name.toLocaleLowerCase("en")}</Link></div><div className="product-grid">{related.map((item) => <ProductCard key={item.id} product={item} />)}</div></div></section>}
  </main>;
}
