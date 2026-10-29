// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from "@tanstack/react-query"

import { api, rememberCsrf, unwrap } from "@/lib/api/client"

export const SESSION_KEY = ["session"]

export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: async () => {
      const state = await unwrap(api.GET("/api/session"))
      rememberCsrf(state.csrf)
      return state
    },
    staleTime: Infinity,
  })
}
