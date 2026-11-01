"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";

type GalleryImage = {
  url: string;
  alt: string;
  caption?: string;
};

export function ProductGallery({
  images,
  productTitle,
}: {
  images: GalleryImage[];
  productTitle: string;
}) {
  const [selected, setSelected] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const active = images[selected] ?? images[0];

  useEffect(() => {
    if (!expanded) return;
    const previouslyFocused = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
      if (event.key === "Tab") {
        const focusable = modalRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex=\"-1\"])",
        );
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
    window.addEventListener("keydown", close);
    closeRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", close);
      document.body.style.overflow = previousOverflow;
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  }, [expanded]);

  if (!active) return <div className="product-gallery"><div className="detail-image detail-image-placeholder" role="img" aria-label={`${productTitle} image pending publication`}>Product image pending publication</div></div>;

  return <>
    <div className="product-gallery">
      <button
        className="detail-image"
        type="button"
        onClick={() => setExpanded(true)}
        aria-label={`Enlarge ${active.alt}`}
      >
        <Image src={active.url} alt={active.alt} fill priority sizes="(max-width: 1000px) 100vw, 55vw" />
        <span className="gallery-zoom">Enlarge image</span>
      </button>
      {images.length > 1 && <div className="gallery-thumbnails" aria-label={`${productTitle} image gallery`}>
        {images.map((image, index) => <button
          type="button"
          className={selected === index ? "active" : ""}
          onClick={() => setSelected(index)}
          aria-label={`Show ${image.alt}`}
          aria-pressed={selected === index}
          key={`${image.url}-${index}`}
        ><Image src={image.url} alt="" fill sizes="96px" /></button>)}
      </div>}
      {active.caption && <p className="gallery-caption">{active.caption}</p>}
    </div>
    {expanded && <div ref={modalRef} className="gallery-modal" role="dialog" aria-modal="true" aria-label={`${productTitle} enlarged image`} onMouseDown={(event) => {
      if (event.target === event.currentTarget) setExpanded(false);
    }}>
      <button ref={closeRef} className="gallery-close" type="button" onClick={() => setExpanded(false)} aria-label="Close enlarged image">×</button>
      <div className="gallery-modal-image"><Image src={active.url} alt={active.alt} fill sizes="95vw" /></div>
    </div>}
  </>;
}
