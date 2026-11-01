"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";
import { clearAnalyticsCookies, CONSENT_EVENT, CONSENT_KEY, setConsent, updateGoogleConsent } from "@/lib/consent";
import { storageGet, storageRemove } from "@/lib/browser-storage";

const subscribe = (listener: () => void) => {
  window.addEventListener(CONSENT_EVENT, listener);
  window.addEventListener("storage", listener);
  return () => { window.removeEventListener(CONSENT_EVENT, listener); window.removeEventListener("storage", listener); };
};

const getConsentPromptVisibility = () => !storageGet("localStorage", CONSENT_KEY);

export function CookieBanner() {
  const visible = useSyncExternalStore(subscribe, getConsentPromptVisibility, () => false);

  const choose = (value: "essential" | "accepted") => {
    const previous = setConsent(value);
    // A reload tears down any third-party code that ran under an earlier grant.
    if (value === "essential" && previous === "accepted") window.location.reload();
  };

  if (!visible) return null;
  return <aside className="cookie-banner" role="dialog" aria-live="polite" aria-label="Cookie preferences" aria-describedby="cookie-description"><div><strong>Privacy choices</strong><p id="cookie-description">Essential storage keeps your RFQ list and draft. Accept enables optional analytics. <Link href="/cookie-policy">Cookie policy</Link></p></div><div className="cookie-actions"><button className="button-outline" type="button" onClick={() => choose("essential")}>Essential only</button><button className="button" type="button" onClick={() => choose("accepted")}>Accept</button></div></aside>;
}

export function CookiePreferencesButton({ className = "footer-cookie-button" }: { className?: string } = {}) {
  return <button className={className} type="button" onClick={() => {
    const hadAnalyticsConsent = storageGet("localStorage", CONSENT_KEY) === "accepted";
    storageRemove("localStorage", CONSENT_KEY);
    if (hadAnalyticsConsent) {
      updateGoogleConsent(false);
      clearAnalyticsCookies();
    }
    window.dispatchEvent(new Event(CONSENT_EVENT));
    // Reload only after a prior grant so already-running third-party code is torn down.
    if (hadAnalyticsConsent) window.location.reload();
  }}>Cookie preferences</button>;
}
