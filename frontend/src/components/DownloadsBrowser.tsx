"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { DocumentDownloadLink } from "@/components/DocumentDownloadLink";
import { formatDateSafe } from "@/lib/dates";
import { safeHref } from "@/lib/structured-data";
import type { ProductDocument } from "@/lib/types";

function optionLabel(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function previewUrl(url: string): string {
  const href = safeHref(url);
  if (!href) return "";
  return `${href}${href.includes("?") ? "&" : "?"}preview=1`;
}

function fileSizeLabel(size?: number | null) {
  if (!size) return "External";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function DownloadsBrowser({
  documents,
}: {
  documents: ProductDocument[] | undefined;
}) {
  const [query, setQuery] = useState("");
  const [documentType, setDocumentType] = useState("");
  const [productId, setProductId] = useState("");
  const [fileFormat, setFileFormat] = useState("");

  const documentTypes = useMemo(
    () => [...new Set((documents ?? []).map((document) => document.document_type).filter(Boolean))].sort(),
    [documents],
  );
  const productOptions = useMemo(
    () => [...new Map((documents ?? []).map((document) => [
      String(document.product_id ?? ""),
      `${document.product_model} · ${document.product_title}`,
    ])).entries()].filter(([id]) => id).sort((a, b) => a[1].localeCompare(b[1])),
    [documents],
  );
  const fileFormats = useMemo(
    () => [...new Set((documents ?? []).map((document) => document.file_format).filter((value): value is string => Boolean(value)))].sort(),
    [documents],
  );
  const filteredDocuments = useMemo(() => {
    const terms = query.trim().toLocaleLowerCase("en").split(/\s+/).filter(Boolean);
    return (documents ?? []).filter((document) => {
      if (documentType && document.document_type !== documentType) return false;
      if (productId && String(document.product_id) !== productId) return false;
      if (fileFormat && document.file_format !== fileFormat) return false;
      if (!terms.length) return true;
      const searchable = [
        document.title,
        document.version,
        document.document_type,
        document.product_model,
        document.product_title,
        document.file_format ?? "",
      ].join(" ").toLocaleLowerCase("en");
      return terms.every((term) => searchable.includes(term));
    });
  }, [documentType, documents, fileFormat, productId, query]);

  if (!documents) {
    return <div className="form-error" role="alert">
      Documents are temporarily unavailable. Please try again shortly.
    </div>;
  }

  if (documents.length === 0) {
    return <div className="empty">
      <h2>No public documents are available yet</h2>
      <p>Ask the technical team for a current drawing, catalogue or installation document for your configuration.</p>
      <Link className="button" href="/request-a-quote">Request technical documents</Link>
    </div>;
  }

  const clearFilters = () => {
    setQuery("");
    setDocumentType("");
    setProductId("");
    setFileFormat("");
  };

  return <>
    <div className="form-card">
      <h2>Find a document</h2>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="document-search">Keyword</label>
          <input
            id="document-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Title, model or version"
          />
        </div>
        <div className="field">
          <label htmlFor="document-type">Document type</label>
          <select id="document-type" value={documentType} onChange={(event) => setDocumentType(event.target.value)}>
            <option value="">All document types</option>
            {documentTypes.map((type) => <option value={type} key={type}>{optionLabel(type)}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="document-product">Related product</label>
          <select id="document-product" value={productId} onChange={(event) => setProductId(event.target.value)}>
            <option value="">All products</option>
            {productOptions.map(([id, name]) => <option value={id} key={id}>{name}</option>)}
          </select>
        </div>
        <div className="field"><label htmlFor="document-format">File format</label><select id="document-format" value={fileFormat} onChange={(event) => setFileFormat(event.target.value)}><option value="">All formats</option>{fileFormats.map((format) => <option value={format} key={format}>{format}</option>)}</select></div>
      </div>
      {(query || documentType || productId || fileFormat) && <button className="button-outline" type="button" onClick={clearFilters} style={{ marginTop: 16 }}>Clear filters</button>}
    </div>

    {filteredDocuments.length === 0
      ? <div className="empty">
        <h2>No documents match these filters</h2>
        <p>Clear the filters or ask the technical team for the current file for your product configuration.</p>
        <div className="button-row" style={{ justifyContent: "center" }}>
          <button className="button-outline" type="button" onClick={clearFilters}>Clear filters</button>
          <Link className="button" href="/request-a-quote">Request a document</Link>
        </div>
      </div>
      : <div className="content-card downloads-card">
        <div className="section-head">
          <div>
            <p className="eyebrow">Public files</p>
            <h2>{filteredDocuments.length} document{filteredDocuments.length === 1 ? "" : "s"} available</h2>
          </div>
        </div>
        <table className="spec-table downloads-table">
          <thead><tr><th>Type</th><th>Document</th><th>Format / size</th><th>Updated</th><th>Product</th><th>Actions</th></tr></thead>
          <tbody>{filteredDocuments.map((document) => <tr key={document.id}>
            <td data-label="Type">{optionLabel(document.document_type)}</td>
            <td data-label="Document"><strong>{document.title}</strong><br /><span>{document.version || "Current version"}</span></td>
            <td data-label="Format / size">{document.file_format || "—"}<br /><span>{fileSizeLabel(document.file_size)}</span></td>
            <td data-label="Updated">{formatDateSafe(document.updated_at, { dateStyle: "medium", timeZone: "UTC" }, "—")}</td>
            <td data-label="Product">{document.product_model} · {document.product_title}</td>
            <td data-label="Actions"><div className="button-row downloads-actions">
              {document.previewable && previewUrl(document.url) && <a className="button-outline" href={previewUrl(document.url)} target="_blank" rel="noopener noreferrer" aria-label={`Preview ${document.title} in a new tab`}>Preview</a>}
              <DocumentDownloadLink url={document.url} title={document.title} productModel={document.product_model} />
            </div></td>
          </tr>)}</tbody>
        </table>
      </div>}
  </>;
}
