// SPDX-License-Identifier: GPL-3.0-or-later
import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

import { expect, test } from "@playwright/test"

import { expectAccessible, watchProblems } from "./helpers"

const LATE = "Movie.2020.fr.srt"
const WRONG_EPISODE = "Movie.2020.de.srt"

function libraryFile(baseURL: string, name: string) {
  const folder = readFileSync(`test-results/library-${new URL(baseURL).port}.txt`, "utf8").trim()
  return path.join(folder, "library", "Movie (2020)", name)
}

test.describe.serial("from Review to a fixed subtitle", () => {
  for (const scheme of ["dark", "light"] as const) {
    test(`every page is accessible in the ${scheme} theme`, async ({ page }) => {
      const problems = await watchProblems(page)
      await page.emulateMedia({ colorScheme: scheme })
      for (const [url, heading] of [["/", "Review"], ["/history", "History"], ["/log", "Log"], ["/system", "System"], ["/settings", "Settings"]]) {
        await page.goto(url!)
        await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible()
        await expectAccessible(page)
      }
      expect(problems).toEqual([])
    })
  }

  test("Review lists both subtitles with plain reasons", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByRole("link", { name: LATE })).toBeVisible()
    await expect(page.getByText("Belongs to S01E03")).toBeVisible()
    await expect(page).toHaveTitle(/^\(2\) Review/)
  })

  test("a file changed behind the page is never overwritten", async ({ page, baseURL }) => {
    const problems = await watchProblems(page)
    await page.goto("/")
    await page.getByRole("link", { name: LATE }).click()
    await expect(page.getByRole("heading", { level: 1, name: LATE })).toBeVisible()
    await expect(page.getByLabel("Line 1:", { exact: false }).first()).toBeVisible({ timeout: 60_000 })
    const file = libraryFile(baseURL!, LATE)
    const before = readFileSync(file, "utf8")
    writeFileSync(file, before.replace("ligne 1\n", "LIGNE 1\n"))
    await page.getByRole("button", { name: "Save to the file" }).click()
    await expect(page.getByText(/changed since this page was opened/)).toBeVisible()
    expect(readFileSync(file, "utf8")).toContain("LIGNE 1")
    writeFileSync(file, before)
    expect(problems.filter((problem) => !problem.includes("409"))).toEqual([])
  })

  test("listening, then saving the guard's fix, writes the file and leaves Review", async ({ page, baseURL }) => {
    const problems = await watchProblems(page)
    await page.goto("/")
    await page.getByRole("link", { name: LATE }).click()
    await expect(page.getByLabel("Line 1:", { exact: false }).first()).toBeVisible({ timeout: 60_000 })
    await expectAccessible(page)
    await page.getByRole("button", { name: "Play" }).click()
    await expect(page.getByRole("button", { name: "Pause" })).toBeVisible()
    await page.getByRole("button", { name: "Pause" }).click()
    await page.getByRole("button", { name: "Later 1 s" }).click()
    await expect(page.getByText(/lines 1.00 s later/)).toBeVisible()
    await page.getByRole("button", { name: "Reset" }).click()
    const before = readFileSync(libraryFile(baseURL!, LATE), "utf8")
    await page.getByRole("button", { name: "Save to the file" }).click()
    await expect(page.getByText("Saved. The previous file was backed up.")).toBeVisible()
    await expect(page.getByText("Proposal applied")).toBeVisible()
    expect(readFileSync(libraryFile(baseURL!, LATE), "utf8")).not.toEqual(before)
    await page.getByRole("button", { name: "More actions" }).click()
    await expect(page.getByRole("menuitem", { name: /Before a fix/ })).toBeVisible()
    await page.keyboard.press("Escape")
    expect(problems).toEqual([])
  })

  test("keeping the other subtitle as is empties Review", async ({ page }) => {
    await page.goto("/")
    await page.getByRole("link", { name: WRONG_EPISODE }).click()
    await page.getByRole("button", { name: "Keep as is" }).click()
    await expect(page.getByText("It leaves Review.", { exact: false })).toBeVisible()
    await page.goto("/")
    await expect(page.getByRole("heading", { name: "Nothing to review" })).toBeVisible()
  })

  test("History shows the fix and Settings saves a change", async ({ page }) => {
    await page.goto("/history")
    await expect(page.getByText("Proposal applied").first()).toBeVisible()
    await page.goto("/settings")
    await page.getByLabel("Keep every version for (days)").fill("12")
    await page.getByRole("button", { name: "Save 1 change" }).click()
    await expect(page.getByText("Settings saved.")).toBeVisible()
    await page.reload()
    await expect(page.getByLabel("Keep every version for (days)")).toHaveValue("12")
  })
})
