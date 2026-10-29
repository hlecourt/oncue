// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate, useParams } from "@tanstack/react-router"
import { MoreHorizontalIcon, RefreshCwIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Editor, EditorSkeleton } from "@/components/editor/editor"
import { EmptyState } from "@/components/status"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { STATUS_KEY } from "@/hooks/use-status"
import { api, type Schemas, unwrap } from "@/lib/api/client"
import { when } from "@/lib/format"
import { REVIEW_KEY } from "@/pages/review"

type Version = Schemas["Version"]
type Confirm = { kind: "delete" } | { kind: "restore"; version: Version } | null

export function SubtitlePage() {
  const { subtitleId } = useParams({ strict: false }) as { subtitleId: string }
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [confirm, setConfirm] = useState<Confirm>(null)
  const path = { params: { path: { subtitle_id: subtitleId } } }
  const detail = useQuery({ queryKey: ["subtitle", subtitleId], queryFn: () => unwrap(api.GET("/api/subtitles/{subtitle_id}", path)) })
  const lines = useQuery({ queryKey: ["lines", subtitleId], queryFn: () => unwrap(api.GET("/api/subtitles/{subtitle_id}/lines", path)) })

  const refresh = () => {
    for (const key of [["subtitle", subtitleId], ["lines", subtitleId], ["history"], REVIEW_KEY, STATUS_KEY]) queryClient.invalidateQueries({ queryKey: key })
  }
  const nextInReview = () => {
    const items = queryClient.getQueryData<Schemas["ReviewItem"][]>(REVIEW_KEY) ?? []
    const position = items.findIndex((item) => item.id === subtitleId)
    const next = items[position + 1] ?? items.find((item) => item.id !== subtitleId)
    if (next) navigate({ to: "/subtitles/$subtitleId", params: { subtitleId: next.id } })
    else navigate({ to: "/" })
  }
  const command = useMutation({
    mutationFn: async (action: "keep" | "recheck" | "replacement" | "delete" | { restore: Version }) => {
      const version = detail.data!.version
      if (action === "keep") return unwrap(api.POST("/api/subtitles/{subtitle_id}/keep", path))
      if (action === "recheck") return unwrap(api.POST("/api/subtitles/{subtitle_id}/recheck", path))
      if (action === "replacement") return unwrap(api.POST("/api/subtitles/{subtitle_id}/replacement", path))
      if (action === "delete") return unwrap(api.POST("/api/subtitles/{subtitle_id}/delete", { ...path, body: { version } }))
      return unwrap(api.POST("/api/subtitles/{subtitle_id}/restore", { ...path, body: { version, saved: action.restore.id } }))
    },
    onSuccess: (result, action) => {
      toast.success(result.message)
      refresh()
      if (action === "keep" || action === "delete") nextInReview()
    },
    onError: (error) => toast.error(error.message),
  })

  if (detail.error) {
    return (
      <div className="p-6">
        <EmptyState icon={<RefreshCwIcon />} title="This subtitle cannot be shown" action={<Button asChild variant="outline"><Link to="/">Back to Review</Link></Button>}>{detail.error.message}</EmptyState>
      </div>
    )
  }
  if (!detail.data || !lines.data) return <EditorSkeleton />
  const subtitle = detail.data

  const actions = (
    <>
      <Button size="sm" variant="outline" className="hidden md:inline-flex" disabled={command.isPending} onClick={() => command.mutate("keep")}>Keep as is</Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label="More actions"><MoreHorizontalIcon /></Button></DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuItem className="md:hidden" onSelect={() => command.mutate("keep")}>Keep as is</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => command.mutate("recheck")}>Check again</DropdownMenuItem>
          {subtitle.from_bazarr && <DropdownMenuItem onSelect={() => command.mutate("replacement")}>Ask Bazarr for another</DropdownMenuItem>}
          <DropdownMenuSeparator />
          <DropdownMenuLabel>Restore a saved version</DropdownMenuLabel>
          {subtitle.versions.length === 0 && <DropdownMenuItem disabled>No saved version yet</DropdownMenuItem>}
          {subtitle.versions.slice(0, 8).map((version) => (
            <DropdownMenuItem key={version.id} onSelect={() => setConfirm({ kind: "restore", version })}>
              <span className="truncate">{versionLabel(version.label)}</span>
              <span className="ml-auto text-xs text-muted-foreground">{when(version.time)}</span>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => setConfirm({ kind: "delete" })}>Delete this subtitle</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  )

  return (
    <>
      <Editor key={`${subtitle.version}-${lines.data.version}`} detail={subtitle} lines={lines.data} actions={actions} onNext={nextInReview} onRestore={(version) => setConfirm({ kind: "restore", version })} />
      <AlertDialog open={confirm !== null} onOpenChange={(open) => !open && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm?.kind === "delete" ? "Delete this subtitle?" : "Restore this version?"}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirm?.kind === "delete" ? "The file is removed from the video's folder. A copy is kept in the backups." : `The file goes back to "${confirm && confirm.kind === "restore" ? versionLabel(confirm.version.label) : ""}". The current one is backed up first.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => confirm && command.mutate(confirm.kind === "delete" ? "delete" : { restore: confirm.version })}>
              {confirm?.kind === "delete" ? "Delete" : "Restore"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function versionLabel(label: string) {
  const words: Record<string, string> = { original: "Before a fix", "before-manual": "Before a change by hand", "before-restore": "Before a restore",
    "before-revert": "Before going back to the original", deleted: "Deleted copy" }
  return words[label] ?? label
}
