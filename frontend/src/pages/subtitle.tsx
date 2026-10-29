// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate, useParams } from "@tanstack/react-router"
import { ArrowLeftIcon, HeadphonesIcon, MoreHorizontalIcon, RefreshCwIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Editor, EditorSkeleton } from "@/components/editor/editor"
import { ConfidenceBadge, EmptyState, ToneDot, toneOf } from "@/components/status"
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
import { Card, CardContent } from "@/components/ui/card"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import { STATUS_KEY } from "@/hooks/use-status"
import { api, type Schemas, unwrap } from "@/lib/api/client"
import { clock, when } from "@/lib/format"
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
    for (const key of [["subtitle", subtitleId], ["lines", subtitleId], REVIEW_KEY, STATUS_KEY]) queryClient.invalidateQueries({ queryKey: key })
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
    return <EmptyState icon={<RefreshCwIcon />} title="This subtitle cannot be shown" action={<Button asChild variant="outline"><Link to="/">Back to Review</Link></Button>}>{detail.error.message}</EmptyState>
  }
  if (!detail.data) return <Skeleton className="h-40 w-full" />
  const subtitle = detail.data

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <Link to="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeftIcon className="size-3.5" />Review</Link>
          <h1 className="truncate text-2xl font-semibold tracking-tight">{subtitle.name}</h1>
          <p className="truncate text-sm text-muted-foreground">{subtitle.folder}{subtitle.video ? ` / ${subtitle.video}` : ""}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" disabled={command.isPending} onClick={() => command.mutate("keep")}>Keep as is</Button>
          {subtitle.from_bazarr && <Button variant="outline" disabled={command.isPending} onClick={() => command.mutate("replacement")}>Ask Bazarr for another</Button>}
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="outline" size="icon" aria-label="More actions"><MoreHorizontalIcon /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              <DropdownMenuItem onSelect={() => command.mutate("recheck")}>Check again</DropdownMenuItem>
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
        </div>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-start gap-x-8 gap-y-4">
          <div className="min-w-56 space-y-1">
            <div className="flex items-center gap-2 font-medium"><ToneDot tone={toneOf(subtitle.tone)} />{subtitle.words}</div>
            <p className="text-sm text-muted-foreground">{subtitle.checked ? `Checked ${when(subtitle.checked).toLowerCase()}` : "Not checked yet"}{subtitle.reference ? `, against ${referenceWords(subtitle.reference)}` : ""}</p>
          </div>
          <div className="space-y-1">
            <div className="text-xs text-muted-foreground">Confidence</div>
            <ConfidenceBadge confidence={subtitle.proposal?.confidence ?? subtitle.confidence} band={subtitle.proposal?.band ?? subtitle.band} />
          </div>
          {subtitle.proposal && (
            <div className="min-w-64 flex-1 space-y-2">
              <div className="text-xs text-muted-foreground">The fix the guard found, not written yet</div>
              <ul className="space-y-1 text-sm">{subtitle.proposal.changes.map((sentence) => <li key={sentence}>{sentence}</li>)}</ul>
            </div>
          )}
          {subtitle.parts.length > 0 && (
            <div className="w-full space-y-2">
              <div className="text-xs text-muted-foreground">Parts that move</div>
              <div className="flex flex-wrap gap-2">
                {subtitle.parts.map((part) => (
                  <span key={part.first} className="inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-sm">
                    <HeadphonesIcon className="size-3.5 text-muted-foreground" />
                    <span className="font-mono tabular-nums">{clock(part.first)} to {clock(part.last)}</span>
                    <span className="text-muted-foreground">{part.lines} lines {Math.abs(part.shift).toFixed(1)} s {part.shift < 0 ? "earlier" : "later"}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {lines.data ? <Editor key={`${subtitle.version}-${lines.data.version}`} detail={subtitle} lines={lines.data} /> : <EditorSkeleton />}

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
    </div>
  )
}

function versionLabel(label: string) {
  const words: Record<string, string> = { original: "Before a fix", "before-manual": "Before a change by hand", "before-restore": "Before a restore",
    "before-revert": "Before going back to the original", deleted: "Deleted copy" }
  return words[label] ?? label
}

function referenceWords(reference: string) {
  if (reference === "audio") return "the sound"
  return `the video's ${reference.replace("-text", " subtitles").replace("-image", " picture subtitles").replace("-sdh", " subtitles for the deaf")}`
}
