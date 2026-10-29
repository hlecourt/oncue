// SPDX-License-Identifier: GPL-3.0-or-later
import { expect, test } from "@playwright/test"

import { expectAccessible } from "./helpers"

test("a wrong password is refused, a right one opens the app, and an expired session goes back to the login", async ({ page, context }) => {
  await page.goto("/")
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible()
  await expectAccessible(page)
  await page.getByLabel("Password").fill("not the password")
  await page.getByRole("button", { name: "Log in" }).click()
  await expect(page.getByRole("alert")).toContainText("not right")
  await page.getByLabel("Password").fill("e2e-password-long-enough")
  await page.getByRole("button", { name: "Log in" }).click()
  await expect(page.getByRole("heading", { level: 1, name: "Review" })).toBeVisible()
  await context.clearCookies()
  await page.goto("/history")
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible()
})
