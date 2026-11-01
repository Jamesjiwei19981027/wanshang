"use client";

import Link from "next/link";

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <html lang="en"><body>
    <main className="not-found" role="alert">
      <div className="shell">
        <p className="eyebrow">Site error</p>
        <h1 style={{ fontSize: "clamp(2.4rem, 7vw, 5rem)", lineHeight: 1 }}>The site needs a fresh start.</h1>
        <p>Retry now, or return to the home page.</p>
        <div className="button-row" style={{ justifyContent: "center" }}>
          <button className="button" type="button" onClick={reset}>Try again</button>
          <Link className="button-outline" href="/">Home</Link>
        </div>
      </div>
    </main>
  </body></html>;
}
