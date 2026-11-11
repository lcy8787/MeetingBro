import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import messages from "./locales/zh-Hant.json";

export type UiLanguage = "en" | "zh-Hant";
const STORAGE_KEY = "meetingbro.uiLanguage";
const dictionary: Record<string, string> = messages;
let currentLanguage: UiLanguage = (() => {
  try { return localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "zh-Hant"; }
  catch { return "zh-Hant"; }
})();

export function getUiLanguage(): UiLanguage { return currentLanguage; }
export function uiLocale(): string { return currentLanguage === "en" ? "en-HK" : "zh-HK"; }

const patterns = Object.entries(dictionary).filter(([key]) => /\{\d+\}/.test(key)).map(([key, value]) => {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\\\{\d+\\\}/g, "(.*?)");
  return { expression: new RegExp(`^${escaped}$`, "s"), value };
});

/** Translate UI copy only. Transcript, saved notes and model output stay untouched. */
export function t(source: string, ...values: Array<string | number | undefined>): string {
  let text = currentLanguage === "en" ? source : dictionary[source];
  if (text === undefined) {
    for (const pattern of patterns) {
      const match = source.match(pattern.expression);
      if (match) return pattern.value.replace(/\{(\d+)\}/g, (_, index) => match[Number(index) + 1] ?? "");
    }
    text = source;
  }
  return text.replace(/\{(\d+)\}/g, (_, index) => String(values[Number(index)] ?? `{${index}}`));
}

const I18nContext = createContext<{ language: UiLanguage; setLanguage: (language: UiLanguage) => void }>({ language: currentLanguage, setLanguage: () => {} });

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, updateLanguage] = useState<UiLanguage>(currentLanguage);
  const setLanguage = (next: UiLanguage) => {
    currentLanguage = next;
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* usable without persistent storage */ }
    updateLanguage(next);
  };
  useEffect(() => { document.documentElement.lang = language; }, [language]);
  return <I18nContext.Provider value={{ language, setLanguage }}>{children}</I18nContext.Provider>;
}

export function useI18n() { return useContext(I18nContext); }
