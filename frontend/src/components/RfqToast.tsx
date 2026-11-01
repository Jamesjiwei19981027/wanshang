"use client";

import { useEffect, useState } from "react";

export function RfqToast() {
  const [message, setMessage] = useState("");

  useEffect(() => {
    let timer = 0;
    const show = (event: Event) => {
      const detail = event instanceof CustomEvent && event.detail && typeof event.detail === "object"
        ? event.detail as Record<string, unknown>
        : {};
      const title = typeof detail.title === "string"
        ? detail.title
        : "Product";
      const nextMessage = typeof detail.message === "string"
        ? detail.message.slice(0, 240)
        : `${title} added to your RFQ list.`;
      setMessage(nextMessage);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setMessage(""), 2600);
    };
    window.addEventListener("rfq-toast", show);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("rfq-toast", show);
    };
  }, []);

  if (!message) return null;
  return <div className="toast" role="status" aria-live="polite">{message}</div>;
}
