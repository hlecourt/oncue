// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from "@tanstack/react-query"

import { api, unwrap } from "@/lib/api/client"

export const STATUS_KEY = ["status"]
const BUSY_SECONDS = 2
const IDLE_SECONDS = 15

export function useStatus() {
  return useQuery({
    queryKey: STATUS_KEY,
    queryFn: () => unwrap(api.GET("/api/status")),
    refetchInterval: (query) => ((query.state.data?.jobs.running ?? 0) + (query.state.data?.jobs.queued ?? 0) > 0 ? BUSY_SECONDS : IDLE_SECONDS) * 1000,
    refetchIntervalInBackground: false,
  })
}
