// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link, Outlet, useMatches } from "@tanstack/react-router"
import { ActivityIcon, HistoryIcon, ListChecksIcon, LogOutIcon, MonitorIcon, MoonIcon, ScrollTextIcon, SearchIcon, SettingsIcon, ShieldCheckIcon, SunIcon } from "lucide-react"
import { useEffect, useState } from "react"

import { CommandMenu } from "@/components/command-menu"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Separator } from "@/components/ui/separator"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { SESSION_KEY, useSession } from "@/hooks/use-session"
import { useStatus } from "@/hooks/use-status"
import { api, unwrap } from "@/lib/api/client"
import { chooseTheme, type Theme, useTheme } from "@/lib/theme"

const PAGES = [
  { to: "/", label: "Review", icon: ListChecksIcon },
  { to: "/history", label: "History", icon: HistoryIcon },
  { to: "/log", label: "Log", icon: ScrollTextIcon },
  { to: "/system", label: "System", icon: ActivityIcon },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
] as const
const THEMES: { value: Theme; label: string; icon: typeof SunIcon }[] = [
  { value: "light", label: "Light", icon: SunIcon },
  { value: "dark", label: "Dark", icon: MoonIcon },
  { value: "system", label: "System", icon: MonitorIcon },
]

export function AppShell() {
  const status = useStatus()
  const session = useSession()
  const [searching, setSearching] = useState(false)
  const toReview = status.data?.to_review ?? 0
  const title = useMatches().at(-1)?.staticData.title ?? ""

  useEffect(() => {
    document.title = `${toReview ? `(${toReview}) ` : ""}${title ? `${title} - ` : ""}subtitle-guard`
  }, [toReview, title])

  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild size="lg">
                <Link to="/">
                  <span className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
                    <ShieldCheckIcon className="size-4" />
                  </span>
                  <span className="font-semibold">subtitle-guard</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarMenu>
              {PAGES.map((page) => (
                <SidebarMenuItem key={page.to}>
                  <SidebarMenuButton asChild tooltip={page.label}>
                    <Link to={page.to} activeOptions={{ exact: page.to === "/" }} activeProps={{ "data-active": true, "aria-current": "page" }}>
                      <page.icon />
                      <span>{page.label}</span>
                    </Link>
                  </SidebarMenuButton>
                  {page.to === "/" && toReview > 0 && <SidebarMenuBadge>{toReview}</SidebarMenuBadge>}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <SidebarMenu>
            <ThemeMenu />
            {session.data?.user && <LogOut />}
          </SidebarMenu>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset className="min-w-0">
        <header className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
          <span className="text-sm font-medium">{title}</span>
          <Button variant="outline" size="sm" className="ml-auto h-8 gap-2 text-muted-foreground" onClick={() => setSearching(true)}>
            <SearchIcon className="size-3.5" />
            <span className="hidden sm:inline">Search</span>
            <kbd className="hidden rounded border bg-muted px-1 font-mono text-[10px] sm:inline">Ctrl K</kbd>
          </Button>
        </header>
        <div id="content" className="mx-auto w-full min-w-0 max-w-[1400px] flex-1 p-4 md:p-6">
          <Outlet />
        </div>
      </SidebarInset>
      <CommandMenu open={searching} onOpenChange={setSearching} />
    </SidebarProvider>
  )
}

function ThemeMenu() {
  const theme = useTheme()
  const current = THEMES.find((item) => item.value === theme) ?? THEMES[2]!
  return (
    <SidebarMenuItem>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <SidebarMenuButton tooltip="Theme">
            <current.icon />
            <span>Theme: {current.label}</span>
          </SidebarMenuButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="end">
          {THEMES.map((item) => (
            <DropdownMenuItem key={item.value} onSelect={() => chooseTheme(item.value)}>
              <item.icon />
              {item.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </SidebarMenuItem>
  )
}

function LogOut() {
  const queryClient = useQueryClient()
  const leave = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/session")),
    onSettled: () => queryClient.invalidateQueries({ queryKey: SESSION_KEY }),
  })
  return (
    <SidebarMenuItem>
      <SidebarMenuButton tooltip="Log out" onClick={() => leave.mutate()}>
        <LogOutIcon />
        <span>Log out</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
}
