function collectMessages(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(collectMessages);
  if (value && typeof value === "object") return Object.values(value).flatMap(collectMessages);
  return [];
}

export function apiErrorMessage(payload: unknown, fallback: string): string {
  return collectMessages(payload).filter(Boolean).join(" ") || fallback;
}
