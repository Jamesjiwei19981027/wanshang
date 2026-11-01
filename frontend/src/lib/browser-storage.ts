type StorageKind = "localStorage" | "sessionStorage";

const memoryFallback: Record<StorageKind, Map<string, string>> = {
  localStorage: new Map<string, string>(),
  sessionStorage: new Map<string, string>(),
};
const blockedStorage = new Set<StorageKind>();

function browserStorage(kind: StorageKind): Storage | undefined {
  if (typeof window === "undefined" || blockedStorage.has(kind)) return undefined;
  try {
    return window[kind];
  } catch {
    blockedStorage.add(kind);
    return undefined;
  }
}

export function storageGet(kind: StorageKind, key: string): string | null {
  const storage = browserStorage(kind);
  if (storage) {
    try {
      return storage.getItem(key);
    } catch {
      blockedStorage.add(kind);
      // Fall back to in-memory state when browser privacy settings block storage.
    }
  }
  return memoryFallback[kind].get(key) ?? null;
}

export function storageSet(kind: StorageKind, key: string, value: string): boolean {
  memoryFallback[kind].set(key, value);
  const storage = browserStorage(kind);
  if (!storage) return false;
  try {
    storage.setItem(key, value);
    return true;
  } catch {
    blockedStorage.add(kind);
    return false;
  }
}

export function storageRemove(kind: StorageKind, key: string): boolean {
  memoryFallback[kind].delete(key);
  const storage = browserStorage(kind);
  if (!storage) return false;
  try {
    storage.removeItem(key);
    return true;
  } catch {
    blockedStorage.add(kind);
    return false;
  }
}
