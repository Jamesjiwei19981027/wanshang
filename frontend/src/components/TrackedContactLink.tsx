"use client";

import type { ReactNode } from "react";
import { trackEvent } from "@/lib/events";

export function TrackedContactLink({
  href,
  children,
  placement,
  className,
  channel,
}: {
  href: string;
  children: ReactNode;
  placement: string;
  className?: string;
  channel?: "email" | "phone" | "whatsapp";
}) {
  const eventName = channel === "whatsapp"
    ? "click_whatsapp"
    : channel === "email" || href.startsWith("mailto:")
      ? "click_email"
      : "click_phone";
  return <a className={className} href={href} onClick={() => trackEvent(eventName, { placement })}>{children}</a>;
}
