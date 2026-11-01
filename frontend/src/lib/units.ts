export function formatDimension(
  value: string | number | null | undefined,
  unit = "in",
): string {
  if (value === null || value === undefined || String(value).trim() === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const formatted = new Intl.NumberFormat("en", { maximumFractionDigits: 2 }).format(numeric);
  return `${formatted} ${unit === "mm" ? "mm" : "in"}`;
}

function parseProductNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const parsed = Number.parseFloat(String(value).trim());
  return Number.isFinite(parsed) ? parsed : null;
}

export function dimensionMatches(
  productValue: string | number | null | undefined,
  requestedValue: string,
  productUnit = "in",
): boolean {
  const requested = requestedValue.trim();
  if (!requested) return true;
  const match = requested.match(/^([0-9]+(?:\.[0-9]+)?)\s*(mm|in|inch|inches|")?$/i);
  const productNumber = parseProductNumber(productValue);
  if (!match) {
    if (productNumber !== null && Number.isFinite(Number.parseFloat(requested))) {
      return Math.abs(productNumber - Number.parseFloat(requested)) < 0.01;
    }
    return String(productValue ?? "").toLocaleLowerCase("en").includes(requested.toLocaleLowerCase("en"));
  }
  if (productNumber === null) {
    return String(productValue ?? "").toLocaleLowerCase("en").includes(requested.toLocaleLowerCase("en"));
  }
  let numeric = Number(match[1]);
  const requestedUnit = (match[2] || productUnit).toLocaleLowerCase("en");
  const normalizedRequestedUnit = requestedUnit === "mm" ? "mm" : "in";
  if (normalizedRequestedUnit !== productUnit) {
    numeric = productUnit === "mm" ? numeric * 25.4 : numeric / 25.4;
  }
  return Math.abs(productNumber - numeric) < 0.01;
}
