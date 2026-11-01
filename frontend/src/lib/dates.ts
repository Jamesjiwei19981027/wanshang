/** Format an ISO date for display; invalid values never throw. */
export function formatDateSafe(
  value: string | number | Date | null | undefined,
  options: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeZone: "UTC" },
  fallback = "",
): string {
  if (value === null || value === undefined || value === "") return fallback;
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return fallback;
  try {
    return new Intl.DateTimeFormat("en", options).format(date);
  } catch {
    return fallback;
  }
}

/** Valid Date for structured data / sitemap lastModified; otherwise undefined. */
export function toDateSafe(
  value: string | number | Date | null | undefined,
): Date | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date;
}
