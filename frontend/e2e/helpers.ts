// SPDX-License-Identifier: GPL-3.0-or-later
import AxeBuilder from "@axe-core/playwright"
import { expect, type Page } from "@playwright/test"

export async function watchProblems(page: Page) {
  const problems: string[] = []
  page.on("pageerror", (error) => problems.push(`page error: ${error.message}`))
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("401")) problems.push(`console: ${message.text()}`)
  })
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => console.error(`CSP violation: ${event.violatedDirective} ${event.blockedURI}`))
  })
  return problems
}

export async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze()
  const serious = results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical")
  expect(serious.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([])
}
