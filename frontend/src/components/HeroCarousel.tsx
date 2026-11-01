"use client";

import { getImageProps } from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { image, starterFallbackEnabled } from "@/lib/data";
import type { Banner } from "@/lib/api";
import { useSiteConfig } from "@/components/SiteConfigProvider";
import { safePublicHttpsUrl } from "@/lib/site";

const fallbackSlides = [
  {
    eyebrow: "Trailer axle manufacturer · OEM & custom enquiries",
    title: "Trailer axle assemblies built around your application.",
    copy: "Technical trailer components for manufacturers, distributors and professional buyers — from standard axle platforms to drawing-led configurations.",
    image: image.hero,
    imageAlt: "Industrial trailer axle manufacturing environment",
    primaryLabel: "View trailer axles",
    primaryHref: "/products/trailer-axles",
  },
  {
    eyebrow: "American-type · German-type · special axles",
    title: "Brake and hub configurations matched to the complete build.",
    copy: "Compare capacity, axle profile, brake system, bolt pattern and critical mounting dimensions before technical confirmation.",
    image: image.brake,
    imageAlt: "Trailer brake and hub assembly technical view",
    primaryLabel: "Compare products",
    primaryHref: "/products/trailer-axles",
  },
  {
    eyebrow: "Drawing-led OEM & ODM support",
    title: "Turn your dimensions into an RFQ engineering can review.",
    copy: "Send hub face, spring centre, wheel pattern, operating environment and drawings in one configuration-led enquiry.",
    image: image.axle,
    imageAlt: "Trailer axle assembly prepared for configuration review",
    primaryLabel: "Explore axle platforms",
    primaryHref: "/products/trailer-axles",
  },
] as const;

const safeBannerLink = (value: string, fallback: string) => {
  const link = value.trim();
  if (link.startsWith("/") && !link.startsWith("//") && !link.includes("\\")) {
    const parsed = new URL(link, "https://internal.invalid");
    if (parsed.origin === "https://internal.invalid") return link;
  }
  const publicUrl = safePublicHttpsUrl(link);
  if (publicUrl) return publicUrl;
  return fallback;
};

function HeroImage({ desktop, mobile, alt }: { desktop: string; mobile: string; alt: string }) {
  const common = { alt, fill: true, sizes: "100vw", loading: "eager" as const, fetchPriority: "high" as const };
  const { props: desktopProps } = getImageProps({ ...common, src: desktop });
  const mobileProps = mobile && mobile !== desktop
    ? getImageProps({ ...common, src: mobile }).props
    : undefined;

  return <picture>
    {mobileProps && <source media="(max-width: 700px)" srcSet={mobileProps.srcSet || mobileProps.src} sizes={mobileProps.sizes} />}
    <img {...desktopProps} alt={alt} />
  </picture>;
}

export function HeroCarousel({ banners = [] }: { banners?: Banner[] }) {
  const site = useSiteConfig();
  const slides = banners.length ? banners.map((banner, index) => ({
    key: `banner-${banner.id}`,
    eyebrow: "Featured capability",
    title: banner.title,
    copy: banner.subtitle,
    image: banner.desktop_image_url || banner.mobile_image_url || (starterFallbackEnabled ? fallbackSlides[index % fallbackSlides.length].image : ""),
    mobileImage: banner.mobile_image_url || banner.desktop_image_url,
    imageAlt: `${banner.title} banner`,
    primaryLabel: banner.button_text || "Explore products",
    primaryHref: safeBannerLink(banner.button_link, "/products"),
    secondaryLabel: banner.secondary_button_text || "Request a quote",
    secondaryHref: safeBannerLink(banner.secondary_button_link, "/request-a-quote"),
  })) : (starterFallbackEnabled ? fallbackSlides.map((slide, index) => ({
    key: `fallback-${index}`,
    ...slide,
    mobileImage: "",
    secondaryLabel: "Request a quote",
    secondaryHref: "/request-a-quote",
  })) : [{
    key: "catalogue-empty-state",
    eyebrow: "WANSHANG · Trailer component manufacturer",
    title: "Trailer axles and suspension systems, manufactured in Liangshan.",
    copy: "Explore American-type and German-type axles, suspension systems and trailer parts. Send your drawings, required configuration and delivery destination for a quotation.",
    image: "",
    mobileImage: "",
    imageAlt: "",
    primaryLabel: "View products",
    primaryHref: "/products",
    secondaryLabel: "Request a quote",
    secondaryHref: "/request-a-quote",
  }]);
  const [active, setActive] = useState(0);
  const [userPaused, setUserPaused] = useState(false);
  const [interactionPaused, setInteractionPaused] = useState(false);
  const paused = userPaused || interactionPaused;

  useEffect(() => {
    if (paused || slides.length < 2) return;
    const timer = window.setInterval(
      () => setActive((current) => (current + 1) % slides.length),
      6500,
    );
    return () => window.clearInterval(timer);
  }, [paused, slides.length]);

  const slide = slides[Math.min(active, slides.length - 1)];

  return (
    <section
      className="hero"
      aria-roledescription="carousel"
      aria-label={`${site.name} capabilities`}
      onMouseEnter={() => setInteractionPaused(true)}
      onMouseLeave={() => setInteractionPaused(false)}
      onFocusCapture={() => setInteractionPaused(true)}
      onBlurCapture={() => setInteractionPaused(false)}
    >
      <div className="hero-media">
        {slide.image ? <HeroImage desktop={slide.image} mobile={slide.mobileImage} alt={slide.imageAlt} /> : <div className="hero-media-empty" aria-hidden="true" />}
      </div>
      <div className="shell hero-copy" aria-live="off">
        <p className="eyebrow">{slide.eyebrow}</p>
        <h1>{slide.title}</h1>
        <p>{slide.copy}</p>
        <div className="button-row">
          <Link className="button" href={slide.primaryHref}>{slide.primaryLabel}</Link>
          <Link className="button-outline light" href={slide.secondaryHref}>{slide.secondaryLabel}</Link>
        </div>
      </div>
      <div className="hero-rules" role="group" aria-label="Choose banner slide">
        <button
          className="hero-pause"
          type="button"
          aria-label={userPaused ? "Resume banner rotation" : "Pause banner rotation"}
          aria-pressed={userPaused}
          onClick={() => setUserPaused((value) => !value)}
        >{userPaused ? "Play" : "Pause"}</button>
        {slides.map((item, index) => (
          <button
            type="button"
            className={index === active ? "active" : ""}
            aria-label={`Show slide ${index + 1}: ${item.title}`}
            aria-pressed={index === active}
            onClick={() => setActive(index)}
            key={item.key}
          />
        ))}
      </div>
    </section>
  );
}
