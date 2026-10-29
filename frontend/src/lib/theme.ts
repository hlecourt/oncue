// SPDX-License-Identifier: GPL-3.0-or-later
import { useSyncExternalStore } from "react"

export type Theme = "light" | "dark" | "system"
const STORAGE_KEY = "theme"
const SYSTEM_DARK = "(prefers-color-scheme: dark)"
const listeners = new Set<() => void>()

matchMedia(SYSTEM_DARK).addEventListener("change", () => {
  if (chosenTheme() === "system") chooseTheme("system")
})

export function chosenTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === "light" || stored === "dark" ? stored : "system"
  } catch {
    return "system"
  }
}

export function chooseTheme(theme: Theme) {
  try {
    if (theme === "system") localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // a blocked storage only loses the choice on reload
  }
  const dark = theme === "dark" || (theme === "system" && matchMedia(SYSTEM_DARK).matches)
  document.documentElement.classList.toggle("dark", dark)
  listeners.forEach((listener) => listener())
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    chosenTheme,
  )
}

export function useDark(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => document.documentElement.classList.contains("dark"),
  )
}
