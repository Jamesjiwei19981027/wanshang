"use client";

import Link from "next/link";
import { useState } from "react";
import { removeFromRfq, updateRfqQuantity, useRfqItems } from "@/lib/rfq";
import { trackEvent } from "@/lib/events";

const MAX_QUANTITY = 100_000;

function RfqQuantityInput({ id, title, quantity }: { id: number; title: string; quantity: number }) {
  const [draft, setDraft] = useState(() => String(quantity));
  const [lastQuantity, setLastQuantity] = useState(quantity);

  // React recommended pattern: adjust state during render when props change,
  // instead of a cascading setState inside an effect.
  if (quantity !== lastQuantity) {
    setLastQuantity(quantity);
    setDraft(String(quantity));
  }

  const commit = () => {
    const trimmed = draft.trim();
    const parsed = trimmed === "" ? Number.NaN : Number(trimmed);
    if (Number.isFinite(parsed) && parsed >= 1 && parsed <= MAX_QUANTITY) {
      updateRfqQuantity(id, parsed);
      setDraft(String(Math.trunc(parsed)));
    } else {
      setDraft(String(quantity));
    }
  };

  return (
    <label>
      Quantity
      <input
        aria-label={`Quantity for ${title}`}
        type="number"
        min="1"
        max={MAX_QUANTITY}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
      />
    </label>
  );
}

export function RfqSummary() {
  const items = useRfqItems();
  if (!items.length) return <p>There are no selected standard products yet. You can still submit a drawing-led or custom enquiry.</p>;
  return (
    <>
      {items.map((item) => (
        <div className="summary-item" key={item.id}>
          <b>{item.title}</b>
          <span>{item.model} · {item.rated_capacity?.toLocaleString() ?? "—"} {item.capacity_unit || "lb"}</span>
          <RfqQuantityInput id={item.id} title={item.title} quantity={item.quantity} />
          <button
            className="remove-button"
            type="button"
            onClick={() => {
              removeFromRfq(item.id);
              trackEvent("remove_from_inquiry_list", { product_id: item.id, model: item.model });
            }}
          >
            Remove
          </button>
        </div>
      ))}
      <Link className="button-outline light" style={{ width: "100%", marginTop: 14 }} href="/products/trailer-axles">Add another product</Link>
    </>
  );
}
