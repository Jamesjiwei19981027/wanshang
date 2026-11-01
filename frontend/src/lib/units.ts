"use client";

import { useSyncExternalStore } from "react";
import { storageGet, storageSet } from "@/lib/browser-storage";
import { DEFAULT_UNIT_SYSTEM, isUnitSystem } from "@/lib/unit-format";
import type { UnitSystem } from "@/lib/types";

export { cardSpecs, specText } from "@/lib/unit-format";

const KEY = "wanshang-unit-system";
const EVENT = "unit-system-change";

function readSystem(): UnitSystem | null {
  const stored = storageGet("localStorage", KEY);
  return isUnitSystem(stored) ? stored : null;
}

const subscribe = (listener: () => void) => {
  window.addEventListener(EVENT, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(EVENT, listener);
    window.removeEventListener("storage", listener);
  };
};

/** The visitor's chosen unit system, falling back to the site default. */
export function useUnitSystem(siteDefault: UnitSystem = DEFAULT_UNIT_SYSTEM): UnitSystem {
  const chosen = useSyncExternalStore(subscribe, readSystem, () => null);
  return chosen ?? siteDefault;
}

export function setUnitSystem(system: UnitSystem) {
  storageSet("localStorage", KEY, system);
  window.dispatchEvent(new Event(EVENT));
}
