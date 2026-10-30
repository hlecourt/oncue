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

test("with an account, the API key shows only after the password is given again", async ({ page }) => {
  await page.goto("/settings")
  await page.getByLabel("Password").fill("e2e-password-long-enough")
  await page.getByRole("button", { name: "Log in" }).click()
  await page.getByRole("button", { name: "Show the key" }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByLabel("Password").fill("not the password")
  await dialog.getByRole("button", { name: "Show the key" }).click()
  await expect(page.getByText("That password is not right.")).toBeVisible()
  await dialog.getByLabel("Password").fill("e2e-password-long-enough")
  await dialog.getByRole("button", { name: "Show the key" }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByRole("textbox", { name: "API key", exact: true })).toHaveValue(/^[0-9a-f]{32}$/)
})
