// SPDX-License-Identifier: GPL-3.0-or-later
import "./index.css"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ApiError } from "@/lib/api/client"
import { markDocument } from "@/lib/i18n"
import { router } from "@/router"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2, refetchOnWindowFocus: true },
    mutations: { retry: false },
  },
})

markDocument()
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster richColors position="bottom-right" />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
