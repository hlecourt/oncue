// SPDX-License-Identifier: GPL-3.0-or-later
import { defineConfig, devices } from "@playwright/test"

const SERVERS = { chromium: 8501, webkit: 8502, login: 8503 }

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  reporter: process.env.CI ? "github" : "list",
  use: { trace: "retain-on-failure" },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${SERVERS.chromium}` }, testIgnore: /login/ },
    { name: "webkit", use: { ...devices["Desktop Safari"], baseURL: `http://127.0.0.1:${SERVERS.webkit}` }, testIgnore: /login/ },
    { name: "login", use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${SERVERS.login}` }, testMatch: /login/ },
  ],
  webServer: [
    { command: `./e2e/serve.sh ${SERVERS.chromium}`, url: `http://127.0.0.1:${SERVERS.chromium}/health`, timeout: 120_000 },
    { command: `./e2e/serve.sh ${SERVERS.webkit}`, url: `http://127.0.0.1:${SERVERS.webkit}/health`, timeout: 120_000 },
    { command: `./e2e/serve.sh ${SERVERS.login} e2e-password-long-enough`, url: `http://127.0.0.1:${SERVERS.login}/health`, timeout: 120_000 },
  ],
})
