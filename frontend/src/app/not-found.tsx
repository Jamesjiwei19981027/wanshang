import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Page not found",
  robots: { index: false, follow: false },
};

export default function NotFound() { return <main className="not-found"><div><p className="eyebrow">404 · Page not found</p><h1>404</h1><h2>We could not find that technical page.</h2><p style={{ color: "var(--muted)" }}>Return to the product catalogue or send the team an RFQ.</p><div className="button-row" style={{ justifyContent: "center", marginTop: 22 }}><Link className="button" href="/products">Browse products</Link><Link className="button-outline" href="/request-a-quote">Request a quote</Link></div></div></main>; }
