import { createJSONStorage } from "zustand/middleware";

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem" | "clear">;

function createMemoryStorage(): StorageLike {
  const store = new Map<string, string>();

  return {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => {
      store.set(key, value);
    },
    removeItem: (key) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
  };
}

let fallbackStorage: StorageLike | null = null;

function getFallbackStorage(): StorageLike {
  if (!fallbackStorage) {
    fallbackStorage = createMemoryStorage();
  }
  return fallbackStorage;
}

export function getPersistentStorage(): StorageLike {
  if (typeof window !== "undefined" && window.localStorage) {
    return window.localStorage;
  }
  return getFallbackStorage();
}

export const persistentJsonStorage = createJSONStorage(getPersistentStorage);
