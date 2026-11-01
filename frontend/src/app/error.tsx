"use client";

import Link from "next/link";

export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <main className="not-found" role="alert">
    <div className="shell">
      <p className="eyebrow">Temporary problem</p>
      <h1 style={{ fontSize: "clamp(2.4rem, 7vw, 5rem)", lineHeight: 1 }}>This page could not be loaded.</h1>
      <p>Retry the page. If the problem continues, use the contact page to reach the technical team.</p>
      <div className="button-row" style={{ justifyContent: "center" }}>
        <button className="button" type="button" onClick={reset}>Try again</button>
        <Link className="button-outline" href="/contact">Contact us</Link>
      </div>
    </div>
  </main>;
}
