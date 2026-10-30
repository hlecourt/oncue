// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from "vitest"

import fr from "@/locales/fr.json"
import zh from "@/locales/zh.json"

import { translate } from "./i18n"

describe("translate", () => {
  it("uses the catalog of the language and fills the values in", () => {
    expect(translate("Review", undefined, "fr")).toBe("À revoir")
    expect(translate("{count} selected", { count: 3 }, "fr")).toBe("3 sélectionnés")
  })

  it("falls back to the English text when a translation is missing", () => {
    expect(translate("A text nobody translated", undefined, "zh")).toBe("A text nobody translated")
  })

  it("translates the same texts in every language", () => {
    expect(Object.keys(zh).sort()).toEqual(Object.keys(fr).sort())
  })

  it("keeps the same placeholders in every translation", () => {
    for (const catalog of [fr, zh] as Record<string, string>[]) {
      for (const [source, translated] of Object.entries(catalog)) {
        expect(translated.match(/\{\w+\}/g)?.sort() ?? [], source).toEqual(source.match(/\{\w+\}/g)?.sort() ?? [])
      }
    }
  })
})
