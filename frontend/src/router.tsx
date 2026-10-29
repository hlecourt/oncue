// SPDX-License-Identifier: GPL-3.0-or-later
import { useQueryClient } from "@tanstack/react-query"
import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from "@tanstack/react-router"
import { useEffect } from "react"

import { AppShell } from "@/components/app-shell"
import { Skeleton } from "@/components/ui/skeleton"
import { SESSION_KEY, useSession } from "@/hooks/use-session"
import { LOGGED_OUT_EVENT } from "@/lib/api/client"
import { LoginPage } from "@/pages/login"
import { ReviewPage } from "@/pages/review"

declare module "@tanstack/react-router" {
  interface StaticDataRouteOption {
    title?: string
    editor?: boolean
  }
  interface Register {
    router: typeof router
  }
}

function Gate() {
  const session = useSession()
  const queryClient = useQueryClient()

  useEffect(() => {
    const expired = () => queryClient.invalidateQueries({ queryKey: SESSION_KEY })
    window.addEventListener(LOGGED_OUT_EVENT, expired)
    return () => window.removeEventListener(LOGGED_OUT_EVENT, expired)
  }, [queryClient])

  if (session.isPending) return <Skeleton className="m-6 h-8 w-48" />
  if (session.data?.login_required && !session.data.user) return <LoginPage />
  return <AppShell />
}

const root = createRootRoute({ component: Gate })
const routes = [
  createRoute({ getParentRoute: () => root, path: "/", component: ReviewPage, staticData: { title: "Review" } }),
  createRoute({ getParentRoute: () => root, path: "/subtitles", component: lazyRouteComponent(() => import("@/pages/subtitles"), "SubtitlesPage"), staticData: { title: "Subtitles" } }),
  createRoute({ getParentRoute: () => root, path: "/subtitles/$subtitleId", component: lazyRouteComponent(() => import("@/pages/subtitle"), "SubtitlePage"), staticData: { title: "Subtitle", editor: true } }),
  createRoute({ getParentRoute: () => root, path: "/history", component: lazyRouteComponent(() => import("@/pages/history"), "HistoryPage"), staticData: { title: "History" } }),
  createRoute({ getParentRoute: () => root, path: "/log", component: lazyRouteComponent(() => import("@/pages/log"), "LogPage"), staticData: { title: "Log" } }),
  createRoute({ getParentRoute: () => root, path: "/system", component: lazyRouteComponent(() => import("@/pages/system"), "SystemPage"), staticData: { title: "System" } }),
  createRoute({ getParentRoute: () => root, path: "/settings", component: lazyRouteComponent(() => import("@/pages/settings"), "SettingsPage"), staticData: { title: "Settings" } }),
]

export const router = createRouter({
  routeTree: root.addChildren(routes),
  basepath: new URL(".", document.baseURI).pathname.replace(/\/$/, "") || "/",
  defaultPreload: "intent",
})
