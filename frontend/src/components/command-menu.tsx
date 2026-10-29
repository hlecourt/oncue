// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { FileTextIcon, MoonIcon, SunIcon } from "lucide-react"
import { useEffect } from "react"

import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command"
import { api, unwrap } from "@/lib/api/client"
import { chooseTheme } from "@/lib/theme"

const PAGES = [
  { to: "/", label: "Review" },
  { to: "/history", label: "History" },
  { to: "/log", label: "Log" },
  { to: "/system", label: "System" },
  { to: "/settings", label: "Settings" },
] as const

export function CommandMenu({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate()
  const review = useQuery({ queryKey: ["review"], queryFn: () => unwrap(api.GET("/api/review")), enabled: open })

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        onOpenChange(!open)
      }
    }
    window.addEventListener("keydown", shortcut)
    return () => window.removeEventListener("keydown", shortcut)
  }, [open, onOpenChange])

  const go = (action: () => void) => {
    onOpenChange(false)
    action()
  }

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Search" description="Go to a page or a subtitle">
      <CommandInput placeholder="Type a page or a subtitle name..." />
      <CommandList>
        <CommandEmpty>Nothing matches.</CommandEmpty>
        <CommandGroup heading="Pages">
          {PAGES.map((page) => (
            <CommandItem key={page.to} onSelect={() => go(() => navigate({ to: page.to }))}>
              {page.label}
            </CommandItem>
          ))}
        </CommandGroup>
        {review.data && review.data.length > 0 && (
          <CommandGroup heading="In Review">
            {review.data.map((item) => (
              <CommandItem key={item.id} value={`${item.name} ${item.folder}`} onSelect={() => go(() => navigate({ to: "/subtitles/$subtitleId", params: { subtitleId: item.id } }))}>
                <FileTextIcon />
                <span className="truncate">{item.name}</span>
                <span className="ml-auto truncate text-xs text-muted-foreground">{item.folder}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        <CommandGroup heading="Theme">
          <CommandItem onSelect={() => go(() => chooseTheme("light"))}>
            <SunIcon />
            Light theme
          </CommandItem>
          <CommandItem onSelect={() => go(() => chooseTheme("dark"))}>
            <MoonIcon />
            Dark theme
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
