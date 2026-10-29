// SPDX-License-Identifier: GPL-3.0-or-later
import { useCallback, useSyncExternalStore } from "react"

import fr from "@/locales/fr.json"
import zh from "@/locales/zh.json"

export type Locale = "en" | "fr" | "zh"
type Values = Record<string, string | number>

export const LOCALES: { value: Locale; label: string; tag: string }[] = [
  { value: "en", label: "English", tag: "en" },
  { value: "fr", label: "Français", tag: "fr" },
  { value: "zh", label: "中文", tag: "zh-Hans" },
]
const CATALOGS: Record<Locale, Record<string, string>> = { en: {}, fr, zh }
const STORAGE_KEY = "locale"
const listeners = new Set<() => void>()

export function chosenLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === "en" || stored === "fr" || stored === "zh") return stored
  } catch {
    // a blocked storage falls back to the browser's language
  }
  const browser = navigator.language.toLowerCase()
  if (browser.startsWith("fr")) return "fr"
  if (browser.startsWith("zh")) return "zh"
  return "en"
}

export function chooseLocale(locale: Locale) {
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // a blocked storage only loses the choice on reload
  }
  markDocument(locale)
  listeners.forEach((listener) => listener())
}

export function markDocument(locale = chosenLocale()) {
  document.documentElement.lang = LOCALES.find((item) => item.value === locale)!.tag
}

export function translate(text: string, values?: Values, locale = chosenLocale()) {
  const template = CATALOGS[locale][text] ?? text
  return values ? template.replace(/\{(\w+)\}/g, (whole, name: string) => (name in values ? String(values[name]) : whole)) : template
}

export function useLocale(): Locale {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    chosenLocale,
  )
}

export function useT() {
  const locale = useLocale()
  return useCallback((text: string, values?: Values) => translate(text, values, locale), [locale])
}
