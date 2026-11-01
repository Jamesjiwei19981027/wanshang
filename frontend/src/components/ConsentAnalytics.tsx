"use client";

import Script from "next/script";
import { useEffect, useRef, useSyncExternalStore } from "react";
import { useSiteConfig } from "@/components/SiteConfigProvider";
import { clearAnalyticsCookies, CONSENT_EVENT, CONSENT_KEY, updateGoogleConsent } from "@/lib/consent";
import { storageGet } from "@/lib/browser-storage";
import { trackingPageUrl, trackingReferrerOrigin } from "@/lib/tracking-url";

const subscribe = (listener: () => void) => {
  window.addEventListener(CONSENT_EVENT, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(CONSENT_EVENT, listener);
    window.removeEventListener("storage", listener);
  };
};

const getAnalyticsConsent = () => storageGet("localStorage", CONSENT_KEY) === "accepted";

export function ConsentAnalytics() {
  const consent = useSyncExternalStore(subscribe, getAnalyticsConsent, () => false);
  const previouslyGranted = useRef(false);
  const site = useSiteConfig();
  const gaId = /^G-[A-Z0-9]+$/i.test(site.googleAnalyticsId) ? site.googleAnalyticsId : "";
  const gtmId = /^GTM-[A-Z0-9]+$/i.test(site.googleTagManagerId) ? site.googleTagManagerId : "";

  useEffect(() => {
    updateGoogleConsent(consent);
    if (!consent) {
      clearAnalyticsCookies();
      // Storage events also revoke consent in other open tabs. A reload tears
      // down already-running tags, just as the preferences button does.
      if (previouslyGranted.current) window.location.reload();
    }
    previouslyGranted.current = consent;
  }, [consent]);

  if (!consent) return null;
  // GA otherwise defaults to the complete document URL and referrer, including
  // query values that may contain personal information or access tokens.
  const analyticsPageConfig = JSON.stringify({
    anonymize_ip: true,
    page_location: trackingPageUrl(window.location.href),
    page_referrer: trackingReferrerOrigin(document.referrer),
  }).replaceAll("<", "\\u003c");
  return <>
    {gaId && <>
      <Script src={`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(gaId)}`} strategy="afterInteractive" />
      <Script id="ga-consent-config" strategy="afterInteractive">{`
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('consent', 'default', { analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' });
        gtag('consent', 'update', { analytics_storage: 'granted', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' });
        gtag('js', new Date());
        gtag('config', '${gaId}', ${analyticsPageConfig});
      `}</Script>
    </>}
    {gtmId && <Script id="gtm-consent-config" strategy="afterInteractive">{`
      (function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
      new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
      j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
      'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
      })(window,document,'script','dataLayer','${gtmId}');
    `}</Script>}
  </>;
}
