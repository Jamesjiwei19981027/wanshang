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
import { ProductKeySpecs, ProductSpecTable } from "@/components/ProductSpecs";
import {
  breadcrumbJsonLd,
  faqJsonLd,
  productJsonLd,
  safeHref,
  serializeJsonLd,
} from "@/lib/structured-data";

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
      product.category.name,
      ...product.specs.filter((spec) => spec.type !== "number").map((spec) => spec.display.imperial),
    ].filter(Boolean).slice(0, 12),
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

  const basicRows: [string, string][] = [
    ["Product model", product.model],
    ["SKU", product.sku],
    ["Category", (product.category.path ?? [product.category]).map((item) => item.name).join(" › ")],
    ["Application", product.applications.map((item) => item.name).join(", ")],
    ["Product status", `${product.made_to_order ? "Made to order" : product.is_customizable ? "Customizable" : "Standard product"}${product.oem_available ? " · OEM available" : ""}`],
  ];
  const commercialRows: [string, string][] = [
    ["MOQ", product.moq || ""],
    ["Lead time", product.lead_time || ""],
    ["Loading port", product.loading_port || ""],
    ["Packaging", product.packaging || ""],
  ];
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
    <section className="shell detail"><div className="detail-grid"><ProductGallery images={galleryImages} productTitle={product.title} /><div className="detail-info"><span className="detail-kicker">MODEL {product.model} · SKU {product.sku}</span><h2>{product.title}</h2><p>{product.description}</p><ProductDetailActions product={product} /><ProductKeySpecs specs={product.specs} /></div></div>
      <div className="detail-content"><div><section className="content-card"><h2>Technical specification</h2><ProductSpecTable specs={product.specs} basic={basicRows} commercial={commercialRows} /></section><section className="content-card"><h2>Available configuration</h2><div className="configuration-grid"><div><span>Product status</span><strong>{product.made_to_order ? "Made to order" : product.is_customizable ? "Customizable" : "Standard product"}</strong></div><div><span>OEM availability</span><strong>{product.oem_available ? "Available" : "Contact sales"}</strong></div><div><span>Application</span><strong>{product.applications.map((item) => item.name).join(", ") || "Confirm with RFQ"}</strong></div></div></section><section className="content-card"><h2>Quality, packing &amp; delivery</h2><p>Confirm inspection requirements, traceability records, packaging, lead time and loading port with your quotation. Ask which inspection reports and supporting documents are available for your selected configuration.</p></section><section className="content-card"><h2>Configuration note</h2><p>A final trailer axle selection should account for complete trailer geometry, actual load distribution, wheels/tires, required braking and the operating environment.</p></section>
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
