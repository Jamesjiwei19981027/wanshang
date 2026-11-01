export default function Loading() {
  return <main aria-busy="true" aria-label="Loading page">
    <section className="page-hero loading-hero"><div className="shell">
      <div className="skeleton-line short" />
      <div className="skeleton-line title" />
      <div className="skeleton-line" />
    </div></section>
    <section className="shell section">
      <div className="product-grid skeleton-grid" aria-hidden="true">
        {Array.from({ length: 3 }, (_, index) => <div className="product-card skeleton-card" key={index}>
          <div className="skeleton-media" />
          <div className="skeleton-body">
            <span className="skeleton-line short" />
            <span className="skeleton-line title" />
            <span className="skeleton-line" />
          </div>
        </div>)}
      </div>
    </section>
  </main>;
}
