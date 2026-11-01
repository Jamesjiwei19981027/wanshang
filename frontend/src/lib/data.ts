import { Application, Category, Product } from "@/lib/types";

export const image = {
  hero: "/images/workshop.svg",
  axle: "/images/axle-assembly.svg",
  brake: "/images/brake-assembly.svg",
  workshop: "/images/workshop.svg",
  boat: "/images/marine-axle.svg",
  agriculture: "/images/agricultural-trailer.svg",
  highway: "/images/hauler.svg",
};

export const categories: Category[] = [
  { id: 1, name: "Trailer Axles", slug: "trailer-axles", description: "American-type and German-type axles, drum and disc brake axles, single-tyre, lightweight, spoke-wheel, heavy-duty, unbraked and special axles.", icon: "◉", image_url: image.axle },
  { id: 2, name: "Suspension Systems", slug: "suspension-parts", description: "Mechanical, air, rigid and single-point suspension systems.", icon: "⌁", image_url: image.workshop },
  { id: 3, name: "Wheel Hubs", slug: "wheel-hubs", description: "Wheel hubs for trailer axle assemblies.", icon: "⊙", image_url: image.brake },
  { id: 4, name: "Brake Drums", slug: "brake-drums", description: "Trailer brake drums matched to your axle and brake configuration.", icon: "◌", image_url: image.brake },
  { id: 5, name: "Automotive Parts", slug: "automotive-parts", description: "Automotive components matched by part number, vehicle model or drawing.", icon: "⚙", image_url: image.workshop },
  { id: 6, name: "Trailer Landing Gear", slug: "landing-gear", description: "Trailer support legs and landing gear.", icon: "⌑", image_url: image.hero },
];

export const applications: Application[] = [
  { id: 1, name: "Utility Trailers", slug: "utility-trailers", summary: "Balanced, repeatable running gear for everyday utility and landscaping trailers.", image_url: image.highway, recommended_finish: "E-Coated or Powder-Coated" },
  { id: 2, name: "Boat Trailers", slug: "boat-trailers", summary: "Corrosion-conscious components for fresh water and saltwater exposure.", image_url: image.boat, recommended_finish: "Hot-Dip Galvanized" },
  { id: 3, name: "Agricultural Trailers", slug: "agricultural-trailers", summary: "Reliable axle configurations for uneven terrain and variable loads.", image_url: image.agriculture, recommended_finish: "Heavy-duty paint" },
  { id: 4, name: "Car Haulers", slug: "car-haulers", summary: "Brake-ready axle assemblies for controlled, stable vehicle transport.", image_url: image.highway, recommended_finish: "Powder-Coated" },
  { id: 5, name: "RV & Caravan", slug: "rv-caravan", summary: "Compact running gear selected for travel, comfort and serviceability.", image_url: image.highway, recommended_finish: "E-Coated" },
  { id: 6, name: "Heavy-Duty Transport", slug: "heavy-duty-transport", summary: "Robust assemblies for demanding work cycles and engineered trailers.", image_url: image.workshop, recommended_finish: "Custom protection" },
];

const axleCategory = categories[0];

export const products: Product[] = [
  {
    id: 1, title: "3,500 lb Electric Brake Straight Axle", slug: "ta-3500-electric-brake-straight-axle", model: "TA-3500-EB", sku: "AX-35-EB-S", short_description: "A configurable 3,500 lb axle assembly with electric drum brakes for utility and car-hauler builds.", description: "Built around a straight axle tube with serviceable hub and brake components. Confirm hub face, spring centre, wheel and brake configuration with our engineering team before release.", image_url: image.axle, specs: [{ key: "rated_capacity", label: "Rated capacity", type: "number", display: { imperial: '3,500 lb', metric: '1,588 kg' }, value: null, show_on_card: true }, { key: "axle_type", label: "Axle type", type: "text", display: { imperial: 'Straight', metric: 'Straight' }, value: null, show_on_card: true }, { key: "brake_type", label: "Brake", type: "text", display: { imperial: 'Electric drum', metric: 'Electric drum' }, value: null, show_on_card: true }, { key: "bolt_pattern", label: "Bolt pattern", type: "text", display: { imperial: '5 on 4.5', metric: '5 on 4.5' }, value: null, show_on_card: true }, { key: "hub_face", label: "Hub face", type: "number", display: { imperial: '95 in', metric: '2,413 mm' }, value: null, show_on_card: true }, { key: "spring_center", label: "Spring centre", type: "number", display: { imperial: '80 in', metric: '2,032 mm' }, value: null, show_on_card: true }, { key: "surface_finish", label: "Surface finish", type: "text", display: { imperial: 'E-coated', metric: 'E-coated' }, value: null, show_on_card: false }], is_customizable: true, oem_available: true, made_to_order: true, featured: true, category: axleCategory, applications: [applications[0], applications[3]],
    documents: [{ id: 1, title: "TA-3500 dimensional sheet", document_type: "drawing", version: "Sample", url: "" }],
  },
  {
    id: 2, title: '7,000 lb Hydraulic Disc 4" Drop Axle', slug: "ta-7000-hydraulic-disc-drop-axle", model: "TA-7000-HD", sku: "AX-70-HD-D4", short_description: "A heavy-duty drop axle platform with hydraulic disc braking and an 8-lug hub configuration.", description: "A configuration-led axle platform for car haulers and commercial trailers. Dimensions, bearings, brake components and coating are confirmed against the build drawing.", image_url: image.brake, specs: [{ key: "rated_capacity", label: "Rated capacity", type: "number", display: { imperial: '7,000 lb', metric: '3,175 kg' }, value: null, show_on_card: true }, { key: "axle_type", label: "Axle type", type: "text", display: { imperial: 'Drop', metric: 'Drop' }, value: null, show_on_card: true }, { key: "brake_type", label: "Brake", type: "text", display: { imperial: 'Hydraulic disc', metric: 'Hydraulic disc' }, value: null, show_on_card: true }, { key: "bolt_pattern", label: "Bolt pattern", type: "text", display: { imperial: '8 on 6.5', metric: '8 on 6.5' }, value: null, show_on_card: true }, { key: "hub_face", label: "Hub face", type: "number", display: { imperial: '95 in', metric: '2,413 mm' }, value: null, show_on_card: true }, { key: "spring_center", label: "Spring centre", type: "number", display: { imperial: '80 in', metric: '2,032 mm' }, value: null, show_on_card: true }, { key: "surface_finish", label: "Surface finish", type: "text", display: { imperial: 'Powder-coated', metric: 'Powder-coated' }, value: null, show_on_card: false }], is_customizable: true, oem_available: true, made_to_order: true, featured: true, category: axleCategory, applications: [applications[3], applications[5]],
  },
  {
    id: 3, title: "2,200 lb Galvanized Idler Boat Axle", slug: "ta-2200-galvanised-idler-boat-axle", model: "TA-2200-M", sku: "AX-22-ID-G", short_description: "A no-brake galvanized axle option for boat trailer applications where corrosion resistance matters.", description: "Specify use environment, hub face and spring centres to start an engineering review. Final saltwater suitability depends on the complete selected configuration and service routine.", image_url: image.boat, specs: [{ key: "rated_capacity", label: "Rated capacity", type: "number", display: { imperial: '2,200 lb', metric: '997.9 kg' }, value: null, show_on_card: true }, { key: "axle_type", label: "Axle type", type: "text", display: { imperial: 'Straight', metric: 'Straight' }, value: null, show_on_card: true }, { key: "brake_type", label: "Brake", type: "text", display: { imperial: 'No brake', metric: 'No brake' }, value: null, show_on_card: true }, { key: "bolt_pattern", label: "Bolt pattern", type: "text", display: { imperial: '5 on 4.5', metric: '5 on 4.5' }, value: null, show_on_card: true }, { key: "hub_face", label: "Hub face", type: "number", display: { imperial: '89 in', metric: '2,261 mm' }, value: null, show_on_card: true }, { key: "spring_center", label: "Spring centre", type: "number", display: { imperial: '74 in', metric: '1,880 mm' }, value: null, show_on_card: true }, { key: "surface_finish", label: "Surface finish", type: "text", display: { imperial: 'Hot-dip galvanized', metric: 'Hot-dip galvanized' }, value: null, show_on_card: false }], is_customizable: true, oem_available: true, made_to_order: true, featured: true, category: axleCategory, applications: [applications[1]],
  },
  {
    id: 4, title: "5,200 lb Straight Electric Brake Axle", slug: "ta-5200-electric-brake-straight-axle", model: "TA-5200-EB", sku: "AX-52-EB-S", short_description: "A configurable 5,200 lb brake axle for car haulers and utility trailers.", description: "A configurable platform. All final capacity and installation decisions require technical validation.", image_url: image.axle, specs: [{ key: "rated_capacity", label: "Rated capacity", type: "number", display: { imperial: '5,200 lb', metric: '2,359 kg' }, value: null, show_on_card: true }, { key: "axle_type", label: "Axle type", type: "text", display: { imperial: 'Straight', metric: 'Straight' }, value: null, show_on_card: true }, { key: "brake_type", label: "Brake", type: "text", display: { imperial: 'Electric drum', metric: 'Electric drum' }, value: null, show_on_card: true }, { key: "bolt_pattern", label: "Bolt pattern", type: "text", display: { imperial: '6 on 5.5', metric: '6 on 5.5' }, value: null, show_on_card: true }, { key: "hub_face", label: "Hub face", type: "number", display: { imperial: '95 in', metric: '2,413 mm' }, value: null, show_on_card: true }, { key: "spring_center", label: "Spring centre", type: "number", display: { imperial: '80 in', metric: '2,032 mm' }, value: null, show_on_card: true }, { key: "surface_finish", label: "Surface finish", type: "text", display: { imperial: 'E-coated', metric: 'E-coated' }, value: null, show_on_card: false }], is_customizable: true, oem_available: true, made_to_order: true, featured: false, category: axleCategory, applications: [applications[0], applications[3]],
  },
];

export const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";
export const starterFallbackEnabled =
  process.env.NODE_ENV !== "production"
  && process.env.NEXT_PUBLIC_ENABLE_STARTER_FALLBACK === "true";

export const findProduct = (slug: string) => products.find((product) => product.slug === slug);
export const withStarterApplicationImage = (application: Application): Application => ({
  ...application,
  image_url: !starterFallbackEnabled ? application.image_url : application.image_url
    || applications.find((item) => item.slug === application.slug)?.image_url
    || image.highway,
});
export const withStarterCategoryImage = (category: Category): Category => ({
  ...category,
  image_url: !starterFallbackEnabled ? category.image_url : category.image_url
    || categories.find((item) => item.slug === category.slug)?.image_url
    || image.workshop,
});

/** Keep the deliberately blank demo API assets usable without a third-party image dependency. */
export const withStarterImage = (product: Product): Product => {
  if (!starterFallbackEnabled) return product;
  const starter = findProduct(product.slug);
  if (!product.image_url || product.image_url.includes("images.unsplash.com")) {
    return { ...product, image_url: starter?.image_url ?? image.axle };
  }
  return product;
};
