/** Keep URL secrets and personal query values out of enquiry and analytics logs. */
export function trackingPagePath(value: string): string {
  if (!value || (!value.startsWith("/") && !/^https?:\/\//i.test(value))) return "";
  try {
    const parsed = new URL(value, "https://tracking.invalid");
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.pathname.slice(0, 500) : "";
  } catch {
    return "";
  }
}

export function trackingPageUrl(value: string): string {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? `${parsed.origin}${parsed.pathname}`.slice(0, 500) : "";
  } catch {
    return "";
  }
}

export function trackingReferrerOrigin(value: string): string {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.origin.slice(0, 1000) : "";
  } catch {
    return "";
  }
}
