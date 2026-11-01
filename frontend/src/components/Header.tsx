"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { useRfqItems } from "@/lib/rfq";
import { TrackedContactLink } from "@/components/TrackedContactLink";
import { useSiteConfig } from "@/components/SiteConfigProvider";
import { safeMailto } from "@/lib/site";
import type { Application, Category } from "@/lib/types";

export function Brand() {
  const site = useSiteConfig();
  return <Link href="/" className="brand" aria-label={`${site.name} home`}>{site.logoUrl ? <Image className="brand-logo" src={site.logoUrl} alt="" width={48} height={48} priority /> : <span className="brand-mark" aria-hidden="true" />}<span className="brand-copy">{site.name.toLocaleUpperCase("en")}<span>{site.tagline.toLocaleUpperCase("en")}</span></span></Link>;
}

export function Header({ categories = [], applications = [] }: { categories?: Category[]; applications?: Application[] }) {
  const site = useSiteConfig();
  const salesMailto = safeMailto(site.salesEmail);
  const [menuOpen, setMenuOpen] = useState(false);
  const headerRef = useRef<HTMLElement>(null);
  const count = useRfqItems().length;

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        headerRef.current?.querySelectorAll("details[open]").forEach((details) => details.removeAttribute("open"));
      }
    };
    const closeOutside = (event: PointerEvent) => {
      if (headerRef.current && !headerRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
        headerRef.current.querySelectorAll("details[open]").forEach((details) => details.removeAttribute("open"));
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("pointerdown", closeOutside);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("pointerdown", closeOutside);
    };
  }, []);

  return <>
    <div className="topbar"><div className="shell topbar-inner"><span>Technical trailer components for OEM &amp; distribution enquiries</span><div className="topbar-links">{salesMailto && <TrackedContactLink className="topbar-link" href={salesMailto} placement="topbar">{site.salesEmail}</TrackedContactLink>}{site.salesPhone && <TrackedContactLink className="topbar-link" href={`tel:${site.salesPhone.replace(/[^\d+]/g, "")}`} placement="topbar">{site.salesPhone}</TrackedContactLink>}{site.whatsappUrl && <TrackedContactLink className="topbar-link" href={site.whatsappUrl} placement="topbar" channel="whatsapp">WhatsApp</TrackedContactLink>}<Link className="topbar-link" href="/downloads">Downloads</Link><Link className="topbar-link" href="/contact">Contact engineering</Link></div></div></div>
    <header className="header" ref={headerRef}>
      <div className="shell header-inner">
        <Brand />
        <nav
          id="primary-navigation"
          className={`main-nav ${menuOpen ? "open" : ""}`}
          aria-label="Primary"
          onClick={(event) => {
            if ((event.target as HTMLElement).closest("a")) {
              setMenuOpen(false);
              headerRef.current?.querySelectorAll("details[open]").forEach((details) => details.removeAttribute("open"));
            }
          }}
        >
          <details className="nav-dropdown">
            <summary>Products</summary>
            <div className="nav-dropdown-menu">
              <Link href="/products">All products</Link>
              {categories.map((category) => <Link href={`/products/${category.slug}`} key={category.id}>{category.name}</Link>)}
            </div>
          </details>
          {applications.length > 0 && <details className="nav-dropdown">
            <summary>Applications</summary>
            <div className="nav-dropdown-menu">
              <Link href="/applications">All applications</Link>
              {applications.map((application) => <Link href={`/applications/${application.slug}`} key={application.id}>{application.name}</Link>)}
            </div>
          </details>}
          <Link href="/news">News</Link>
          <Link href="/faq">FAQ</Link>
          <Link href="/about">About us</Link>
          <Link className="nav-mobile-only" href="/contact">Contact</Link>
        </nav>
        <div className="header-actions">
          <Link className="button-quiet" href="/search" aria-label="Search products and news">Search</Link>
          <Link className="button-quiet" href="/request-a-quote">RFQ <span className="rfq-count">{count}</span></Link>
          <Link className="button" href="/request-a-quote">Request a quote</Link>
          <button className="menu-toggle" type="button" onClick={() => setMenuOpen((value) => !value)} aria-label={menuOpen ? "Close navigation" : "Open navigation"} aria-expanded={menuOpen} aria-controls="primary-navigation">{menuOpen ? "×" : "☰"}</button>
        </div>
      </div>
    </header>
  </>;
}
