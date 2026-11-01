import { getAllProducts, getApplications, getCategories, getSiteSettings } from "@/lib/api";
import { getProductPath } from "@/lib/product-url";

export const revalidate = 300;

export async function GET(): Promise<Response> {
  const [site, categories, applications, products] = await Promise.all([
    getSiteSettings(),
    getCategories(),
    getApplications(),
    getAllProducts(),
  ]);
  const name = site.name || "Trailer Components";
  const lines: string[] = [
    `# ${name} — full catalogue summary`,
    "",
    "> Extended reference for generative engines. Values below are published catalogue data, not final engineering approval.",
    "",
    "## Site purpose",
    "",
    `${name} supplies trailer axle assemblies and running gear for OEM, aftermarket and distribution buyers. Product pages expose measurable configuration parameters and an RFQ workflow for technical confirmation.`,
    "",
    "## Product categories",
    "",
  ];

  for (const category of categories ?? []) {
    lines.push(`### ${category.name}`);
    lines.push("");
    lines.push(category.description || "Published product category.");
    lines.push("");
  }

  lines.push("## Applications");
  lines.push("");
  for (const application of applications ?? []) {
    lines.push(`- **${application.name}** — ${application.summary}`);
  }
  lines.push("");

  lines.push("## Published products");
  lines.push("");
  for (const product of products ?? []) {
    lines.push(`### ${product.model} (${product.sku})`);
    lines.push("");
    lines.push(`- Title: ${product.title}`);
    lines.push(`- Category: ${product.category.name}`);
    for (const spec of product.specs ?? []) {
      const { imperial, metric } = spec.display;
      lines.push(`- ${spec.label}: ${imperial}${metric && metric !== imperial ? ` (${metric})` : ""}`);
    }
    lines.push(`- Path: ${getProductPath(product)}`);
    lines.push(`- Summary: ${product.short_description}`);
    lines.push("");
  }

  lines.push("## Disclaimer");
  lines.push("");
  lines.push("Final trailer axle selection requires complete trailer geometry, actual loads, wheels/tyres, mounting interfaces, braking requirements and destination-market regulations. Do not present catalogue values as certified performance claims.");

  return new Response(`${lines.join("\n")}\n`, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
