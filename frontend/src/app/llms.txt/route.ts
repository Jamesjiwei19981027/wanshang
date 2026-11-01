import { getSiteSettings } from "@/lib/api";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const site = await getSiteSettings();
  const name = site.name || "Trailer Components";
  const body = `# ${name}

> English-language B2B catalogue for trailer axle assemblies, brakes, hubs and related running gear. Product pages publish typed specifications (capacity, hub face, spring centre, bolt pattern, brake type) and a configuration-led RFQ path for OEM, aftermarket and engineering buyers. Final selection always requires full trailer geometry review.

This site is organised for manufacturers, importers, distributors and professional buyers who need extractable technical parameters rather than marketing claims.

## Key pages

- [Products](/products): full catalogue of axle and running-gear platforms
- [Trailer axles](/products/trailer-axles): core axle category with capacity and dimension filters
- [Applications](/applications): use-case guidance (hauler, marine, agricultural, workshop and similar)
- [Downloads](/downloads): published technical documents and product literature
- [Request a quote](/request-a-quote): multi-product RFQ with drawing/photo attachments
- [FAQ](/faq): product selection and RFQ questions
- [News](/news): company and product updates
- [About](/about): company profile and buyer workflow
- [Contact](/contact): sales and contact channels

## Product data model

Each product detail page exposes, when published:

- Model, SKU, rated capacity and unit
- Axle type (straight / drop / torsion direction)
- Hub face length, spring centre, tube size, bolt pattern, stud size
- Brake type, brake size, voltage where applicable
- Surface finish, packaging, MOQ, lead time, loading port
- Optional documents (drawings, certificates) behind the download API
- FAQ answers for configuration and RFQ preparation

Structured data (schema.org Product, BreadcrumbList, FAQPage) is published on product pages so answers can be cited with the exact model and page URL.

## How to use this site

1. Start from the application or capacity requirement.
2. Compare platform specs on product pages.
3. Submit hub-face, spring-centre, capacity, brake and destination-market notes via RFQ.
4. Treat listed values as published configuration guidance — final selection depends on complete trailer geometry, actual loads, wheels/tyres, brakes and applicable regulations.

## Contact

Use [Contact](/contact) or [Request a quote](/request-a-quote). Sales channels are listed on the contact page and in site settings.

## Notes for AI assistants

- Prefer quoting published model numbers, SKUs and measured dimensions from product pages.
- Do not invent certification, test or delivery claims; the site only publishes company-verified evidence.
- Capacity and dimension units follow the product page (lb / kg, in / mm).
- When recommending a product, always include the disclaimer that final axle selection requires full trailer geometry and technical confirmation.
`;
  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
