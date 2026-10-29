// SPDX-License-Identifier: GPL-3.0-or-later
import createClient, { type Middleware } from "openapi-fetch"

import { chosenLocale, translate } from "@/lib/i18n"

import type { components, paths } from "./schema"

export type Schemas = components["schemas"]

export const apiRoot = new URL(".", document.baseURI).href.replace(/\/$/, "")
const session = { csrf: "" }

export function rememberCsrf(token: string | null | undefined) {
  session.csrf = token ?? ""
}

const security: Middleware = {
  onRequest({ request }) {
    request.headers.set("Accept-Language", chosenLocale())
    if (request.method !== "GET" && session.csrf) request.headers.set("X-CSRF-Token", session.csrf)
    return request
  },
  onResponse({ response }) {
    if (response.status === 401) window.dispatchEvent(new Event(LOGGED_OUT_EVENT))
    return response
  },
}

export const LOGGED_OUT_EVENT = "subtitle-guard:logged-out"
export const api = createClient<paths>({ baseUrl: apiRoot, credentials: "same-origin" })
api.use(security)

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message)
  }
}

type Answer<T> = { data?: T; error?: unknown; response: Response }

export async function unwrap<T>(pending: Promise<Answer<T>>): Promise<T> {
  const { data, error, response } = await pending
  if (response.ok) return data as T
  const problem = (error ?? {}) as { code?: string; message?: string }
  throw new ApiError(problem.message ?? translate("The server answered {status}.", { status: response.status }), problem.code ?? "error", response.status)
}

export function mediaUrl(path: string) {
  return `${apiRoot}${path}`
}
