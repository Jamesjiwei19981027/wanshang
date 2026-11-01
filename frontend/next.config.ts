import type { NextConfig } from "next";
import { isIP } from "node:net";

const NUMERIC_IPV4_HOST_PATTERN = /^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*$/i;

function isPublicHostname(hostnameValue: string): boolean {
  const hostname = hostnameValue.replace(/^\[|\]$/g, "").toLowerCase();
  const labels = hostname.split(".");
  return (
    isIP(hostname) === 0
    && hostname.length <= 253
    && !NUMERIC_IPV4_HOST_PATTERN.test(hostname)
    && labels.length >= 2
    && labels.every((label) => (
      /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label)
    ))
    && !["localhost", "local", "internal", "test", "invalid"].includes(
      labels.at(-1) ?? "",
    )
  );
}

function validatedSiteOrigin(rawValue: string | undefined): string {
  if (!rawValue) {
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        "NEXT_PUBLIC_SITE_URL is required for production builds and must be a public HTTPS origin.",
      );
    }
    return "http://localhost:3000";
  }

  let parsed: URL;
  try {
    parsed = new URL(rawValue);
  } catch {
    throw new Error(
      "NEXT_PUBLIC_SITE_URL must be a credential-free origin without a path, query or fragment.",
    );
  }

  if (
    parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
  ) {
    throw new Error(
      "NEXT_PUBLIC_SITE_URL must be a credential-free origin without a path, query or fragment.",
    );
  }

  if (process.env.NODE_ENV === "production") {
    const hasPublicSiteOrigin = (
      parsed.protocol === "https:"
      && isPublicHostname(parsed.hostname)
    );
    if (!hasPublicSiteOrigin) {
      throw new Error(
        "NEXT_PUBLIC_SITE_URL must be a credential-free public HTTPS origin.",
      );
    }
  } else if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("NEXT_PUBLIC_SITE_URL must use HTTP or HTTPS.");
  }

  return parsed.origin;
}

function validatedPublicApiUrl(rawValue: string | undefined): string {
  if (!rawValue) {
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        "NEXT_PUBLIC_API_BASE_URL is required for production builds and must be a public HTTPS URL.",
      );
    }
    return "http://localhost:8000/api";
  }

  let parsed: URL;
  try {
    parsed = new URL(rawValue);
  } catch {
    throw new Error("NEXT_PUBLIC_API_BASE_URL must be a valid URL.");
  }

  if (
    parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
  ) {
    throw new Error(
      "NEXT_PUBLIC_API_BASE_URL must be credential-free and must not contain a query or fragment.",
    );
  }
  if (
    process.env.NODE_ENV === "production"
    && (parsed.protocol !== "https:" || !isPublicHostname(parsed.hostname))
  ) {
    throw new Error(
      "NEXT_PUBLIC_API_BASE_URL must be a credential-free public HTTPS URL.",
    );
  }
  if (
    process.env.NODE_ENV !== "production"
    && parsed.protocol !== "http:"
    && parsed.protocol !== "https:"
  ) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL must use HTTP or HTTPS.");
  }

  return parsed.href.replace(/\/+$/, "");
}

const publicSiteOrigin = validatedSiteOrigin(process.env.NEXT_PUBLIC_SITE_URL);
const publicApiUrl = validatedPublicApiUrl(
  process.env.NEXT_PUBLIC_API_BASE_URL,
);

function validatedDevelopmentApiProxy(rawValue: string | undefined): string | undefined {
  if (process.env.NODE_ENV === "production" || !rawValue?.trim()) return undefined;

  const candidate = rawValue.trim();
  const match = /^http:\/\/(?:127\.0\.0\.1|localhost):([1-9]\d{0,4})\/?$/.exec(candidate);
  if (!match || Number(match[1]) > 65535) {
    throw new Error(
      "DEV_API_PROXY_URL must be an HTTP loopback origin with an explicit port, such as http://127.0.0.1:8000.",
    );
  }
  return new URL(candidate).origin;
}

const developmentApiProxy = validatedDevelopmentApiProxy(process.env.DEV_API_PROXY_URL);

const rawImageHost = process.env.NEXT_PUBLIC_IMAGE_HOSTNAME?.trim();
if (rawImageHost && !isPublicHostname(rawImageHost)) {
  throw new Error(
    "NEXT_PUBLIC_IMAGE_HOSTNAME must be one explicit public hostname without a scheme, port, path or wildcard.",
  );
}
const approvedImageHost = rawImageHost?.toLowerCase();
let apiImagePattern: { protocol: "http" | "https"; hostname: string; port?: string } | undefined;
try {
  const parsed = new URL(publicApiUrl);
  if (parsed.protocol === "http:" || parsed.protocol === "https:") {
    apiImagePattern = {
      protocol: parsed.protocol.slice(0, -1) as "http" | "https",
      hostname: parsed.hostname,
      port: parsed.port,
    };
  }
} catch {
  apiImagePattern = undefined;
}

const nextConfig: NextConfig = {
  reactStrictMode: true,
  ...(process.env.NEXT_OUTPUT_STANDALONE === "true" ? { output: "standalone" } : {}),
  ...(process.env.NODE_ENV !== "production" ? {
    allowedDevOrigins: ["localhost", "127.0.0.1", new URL(publicSiteOrigin).hostname],
  } : {}),
  ...(developmentApiProxy ? { skipTrailingSlashRedirect: true } : {}),
  env: {
    NEXT_PUBLIC_API_BASE_URL: publicApiUrl,
    NEXT_PUBLIC_SITE_URL: publicSiteOrigin,
  },
  images: {
    // Next does not re-check remotePatterns after a redirect. Image sources
    // must serve their bytes directly so approved hosts cannot proxy others.
    maximumRedirects: 0,
    remotePatterns: [
      ...(approvedImageHost ? [{ protocol: "https" as const, hostname: approvedImageHost }] : []),
      ...(apiImagePattern ? [apiImagePattern] : []),
      ...(process.env.NODE_ENV !== "production" ? [
        { protocol: "http" as const, hostname: "localhost", port: "8000" },
        { protocol: "http" as const, hostname: "127.0.0.1", port: "8000" },
      ] : []),
    ],
    dangerouslyAllowLocalIP: process.env.NODE_ENV !== "production",
  },
  async rewrites() {
    // Django API routes use trailing slashes. Forward them directly so the
    // temporary same-origin preview does not redirect browsers to loopback.
    return developmentApiProxy ? [{
      source: "/api/:path*",
      destination: `${developmentApiProxy}/api/:path*/`,
    }] : [];
  },
  async headers() {
    // CSP treats a path without its final slash as one exact resource.
    // Permit endpoints below the API base while retaining its path boundary.
    const connectSrc = ["'self'", `${publicApiUrl.replace(/\/+$/, "")}/`];
    if (process.env.NODE_ENV !== "production") {
      const developmentSocketOrigin = new URL(publicSiteOrigin);
      developmentSocketOrigin.protocol = developmentSocketOrigin.protocol === "https:" ? "wss:" : "ws:";
      connectSrc.push(developmentSocketOrigin.origin);
    }
    const imgSrc = ["'self'", "data:", "blob:"];
    if (apiImagePattern) {
      const apiOrigin = `${apiImagePattern.protocol}://${apiImagePattern.hostname}${apiImagePattern.port ? `:${apiImagePattern.port}` : ""}`;
      imgSrc.push(apiOrigin);
    }
    if (approvedImageHost) {
      imgSrc.push(`https://${approvedImageHost}`);
    }
    const csp = [
      "default-src 'self'",
      "base-uri 'self'",
      "object-src 'none'",
      "frame-ancestors 'none'",
      "form-action 'self'",
      // Turnstile / GA consent snippets inject inline scripts; JSON-LD is also
      // emitted inline (and already escapes `<`). script-src-attr 'none' still
      // blocks inline event handler attributes (onclick=...).
      `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV !== "production" ? " 'unsafe-eval'" : ""} https://challenges.cloudflare.com https://www.googletagmanager.com https://www.google-analytics.com`,
      "script-src-attr 'none'",
      "style-src 'self' 'unsafe-inline'",
      `img-src ${imgSrc.join(" ")}`,
      "font-src 'self' data:",
      `connect-src ${connectSrc.join(" ")} https://challenges.cloudflare.com https://www.google-analytics.com https://*.google-analytics.com https://www.googletagmanager.com`,
      "frame-src https://challenges.cloudflare.com https://www.googletagmanager.com",
      "worker-src 'self' blob:",
      "manifest-src 'self'",
    ].join("; ");
    return [{
      source: "/(.*)",
      headers: [
        { key: "Content-Security-Policy", value: csp },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "strict-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
        { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
        { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
        ...(process.env.NODE_ENV === "production"
          ? [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]
          : []),
      ],
    }];
  },
};

export default nextConfig;
