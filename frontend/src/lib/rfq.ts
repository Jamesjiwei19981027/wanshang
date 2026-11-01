import { useSyncExternalStore } from "react";
import type { Product, RfqItem } from "@/lib/types";
import { storageGet, storageSet } from "@/lib/browser-storage";

const KEY = "axleforge-rfq";
const event = "rfq-change";
const MAX_ITEMS = 20;
const MAX_QUANTITY = 100_000;
const EMPTY_ITEMS: RfqItem[] = [];
let cachedRaw: string | null = null;
let cachedItems: RfqItem[] = EMPTY_ITEMS;

const boundedText = (value: unknown, maximum: number) => (
  typeof value === "string" ? value.trim().slice(0, maximum) : ""
);

function normalizeRfqItems(value: unknown): RfqItem[] {
  if (!Array.isArray(value)) return EMPTY_ITEMS;
  const items: RfqItem[] = [];
  const seen = new Set<number>();
  for (const candidate of value.slice(0, 100)) {
    if (!candidate || typeof candidate !== "object") continue;
    const item = candidate as Record<string, unknown>;
    const id = item.id;
    const title = boundedText(item.title, 180);
    const model = boundedText(item.model, 80);
    const sku = boundedText(item.sku, 80);
    if (
      typeof id !== "number"
      || !Number.isSafeInteger(id)
      || id < 1
      || seen.has(id)
      || !title
      || !model
      || !sku
    ) {
      continue;
    }
    const rawQuantity = typeof item.quantity === "number" ? item.quantity : Number.NaN;
    const quantity = Number.isFinite(rawQuantity)
      ? Math.max(1, Math.min(MAX_QUANTITY, Math.trunc(rawQuantity)))
      : 1;
    const category = boundedText(item.category, 50);
    seen.add(id);
    items.push({
      id,
      title,
      model,
      sku,
      category: /^[a-z0-9_-]+$/i.test(category) ? category : undefined,
      quantity,
    });
    if (items.length === MAX_ITEMS) break;
  }
  return items.length ? items : EMPTY_ITEMS;
}

export const getRfqItems = (): RfqItem[] => {
  if (typeof window === "undefined") return EMPTY_ITEMS;
  const raw = storageGet("localStorage", KEY);
  if (raw === cachedRaw) return cachedItems;
  cachedRaw = raw;
  try {
    const items = JSON.parse(raw ?? "[]") as unknown;
    cachedItems = normalizeRfqItems(items);
  } catch { cachedItems = EMPTY_ITEMS; }
  return cachedItems;
};

const subscribe = (listener: () => void) => {
  window.addEventListener(event, listener);
  window.addEventListener("storage", listener);
  return () => { window.removeEventListener(event, listener); window.removeEventListener("storage", listener); };
};

export const useRfqItems = () => useSyncExternalStore(subscribe, getRfqItems, () => EMPTY_ITEMS);

const save = (items: RfqItem[]) => {
  const nextItems = normalizeRfqItems(items);
  const raw = JSON.stringify(nextItems);
  cachedRaw = raw; cachedItems = nextItems;
  storageSet("localStorage", KEY, raw);
  window.dispatchEvent(new Event(event));
};
export const addToRfq = (product: Product) => {
  const items = getRfqItems();
  const existing = items.find((item) => item.id === product.id);
  if (existing) {
    if (existing.quantity >= MAX_QUANTITY) {
      window.dispatchEvent(new CustomEvent("rfq-toast", {
        detail: { message: `${product.title} is already at the maximum quantity.` },
      }));
      return false;
    }
    save(items.map((item) => item.id === product.id
      ? { ...item, quantity: Math.min(MAX_QUANTITY, item.quantity + 1) }
      : item));
    window.dispatchEvent(new CustomEvent("rfq-toast", { detail: { title: product.title } }));
    return true;
  }
  if (items.length >= MAX_ITEMS) {
    window.dispatchEvent(new CustomEvent("rfq-toast", {
      detail: { message: `Your RFQ list can include up to ${MAX_ITEMS} products.` },
    }));
    return false;
  }
  save([...items, { id: product.id, title: product.title, model: product.model, sku: product.sku, category: product.category?.slug, quantity: 1 }]);
  window.dispatchEvent(new CustomEvent("rfq-toast", { detail: { title: product.title } }));
  return true;
};
export const removeFromRfq = (id: number) => save(getRfqItems().filter((item) => item.id !== id));
export const updateRfqQuantity = (id: number, quantity: number) => {
  if (!Number.isFinite(quantity)) return;
  const safeQuantity = Math.trunc(quantity);
  if (safeQuantity < 1 || safeQuantity > MAX_QUANTITY) return;
  save(getRfqItems().map((item) => item.id === id ? { ...item, quantity: safeQuantity } : item));
};
export const clearRfq = () => save([]);
