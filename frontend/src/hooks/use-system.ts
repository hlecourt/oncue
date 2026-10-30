// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from "@tanstack/react-query"

import { api, unwrap } from "@/lib/api/client"

export const SYSTEM_KEY = ["system"]
export const REPORT_MODE = "report"

export function useSystem() {
  return useQuery({ queryKey: SYSTEM_KEY, queryFn: () => unwrap(api.GET("/api/system")) })
}
