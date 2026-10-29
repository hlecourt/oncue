// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from "@tanstack/react-query"
import { useVirtualizer } from "@tanstack/react-virtual"
import { cn } from "cn"
import { XIcon } from "lucide-react"
import { useEffect, useRef } from "react"

import type { Block } from "@/components/editor/timeline"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { Change } from "@/lib/retime"
import { ToneDot, toneOf } from "@/components/status"
import { api, type Schemas, unwrap } from "@/lib/api/client"
import { clock, when } from "@/lib/format"

type Props = {
  tab: string
  onTab: (tab: string) => void
  lines: Block[]
  videoText: (string | null)[]
  moves: { amount: number; byHand: boolean }[]
  current: number
  selection: [number, number] | null
  following: boolean
  onPick: (index: number, extend: boolean) => void
  guard: { confidence: number | null; parts: Schemas["Part"][]; on: boolean } | null
  onGuard: (on: boolean) => void
  onPart: (part: Schemas["Part"]) => void
  changes: Change[]
  onRemove: (change: Change) => void
  changeCount: number
  subtitleId: string
  versions: Schemas["Version"][]
  onRestore: (version: Schemas["Version"]) => void
}

const VERSION_WORDS: Record<string, string> = { original: "Before a fix", "before-manual": "Before a change by hand", "before-restore": "Before a restore",
  "before-revert": "Before going back to the original", deleted: "Deleted copy" }

const ROW_ESTIMATE = 58

export function SidePanel(props: Props) {
  return (
    <Tabs value={props.tab} onValueChange={props.onTab} className="flex min-h-0 flex-1 flex-col gap-0">
      <TabsList className="h-11 w-full justify-start gap-1 rounded-none border-b bg-transparent px-3 pb-0">
        <PanelTab value="lines">Lines <span className="text-muted-foreground">{props.lines.length}</span></PanelTab>
        <PanelTab value="changes">Changes {props.changeCount > 0 && <span className="rounded-full bg-unsaved-soft px-1.5 font-mono text-[11px] text-unsaved-ink">{props.changeCount}</span>}</PanelTab>
        <PanelTab value="history">History</PanelTab>
      </TabsList>
      <TabsContent value="lines" className="min-h-0 flex-1"><Script {...props} /></TabsContent>
      <TabsContent value="changes" className="min-h-0 flex-1 overflow-auto"><Changes {...props} /></TabsContent>
      <TabsContent value="history" className="min-h-0 flex-1 overflow-auto"><History {...props} /></TabsContent>
    </Tabs>
  )
}

function PanelTab({ value, children }: { value: string; children: React.ReactNode }) {
  return (
    <TabsTrigger value={value} className="h-full flex-none gap-1.5 rounded-none border-0 border-b-2 border-transparent px-2 text-muted-foreground shadow-none data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none dark:data-[state=active]:bg-transparent">
      {children}
    </TabsTrigger>
  )
}

function Script({ lines, videoText, moves, current, selection, following, onPick }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const virtual = useVirtualizer({ count: lines.length, getScrollElement: () => scroller.current, estimateSize: () => ROW_ESTIMATE, overscan: 10 })

  useEffect(() => {
    if (following && current >= 0) virtual.scrollToIndex(current, { align: "center" })
  }, [current, following, virtual])

  return (
    <div ref={scroller} className="h-full overflow-y-auto" tabIndex={-1}>
      <ol className="relative w-full" style={{ height: virtual.getTotalSize() }} aria-label="Lines of the subtitle">
        {virtual.getVirtualItems().map((item) => {
          const line = lines[item.index]!
          const move = moves[item.index]!
          const chosen = selection !== null && item.index >= selection[0] && item.index <= selection[1]
          return (
            <li key={item.key} ref={virtual.measureElement} data-index={item.index} className="absolute left-0 w-full" style={{ top: item.start }}>
              <button type="button" onClick={(event) => onPick(item.index, event.shiftKey)} aria-current={item.index === current ? "true" : undefined} aria-pressed={chosen}
                className={cn("grid w-full grid-cols-[3.75rem_minmax(0,1fr)_auto] gap-2.5 border-l-2 border-transparent py-2 pr-3 pl-3 text-left text-[13px] hover:bg-accent/60",
                  chosen && "border-ours-line bg-ours-soft/50", item.index === current && "border-ours bg-ours-soft")}>
                <span className={cn("pt-px font-mono text-xs tabular-nums", item.index === current ? "font-medium text-ours-ink" : "text-muted-foreground")}>{clock(line.start, 2)}</span>
                <span className="min-w-0">
                  <span className="line-clamp-2">{line.text}</span>
                  {videoText[item.index] && (
                    <span className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground"><i className="size-1.5 flex-none rounded-[2px] bg-theirs" /><span className="truncate">{videoText[item.index]}</span></span>
                  )}
                </span>
                {Math.abs(move.amount) >= 0.01 && (
                  <span className={cn("h-[18px] rounded px-1 font-mono text-[11px] leading-[18px] tabular-nums", move.byHand ? "bg-ours-soft text-ours-ink" : "bg-guard-soft text-guard-ink")}>
                    {move.amount > 0 ? "+" : ""}{move.amount.toFixed(1)}
                  </span>
                )}
              </button>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function Changes({ guard, onGuard, onPart, changes, onRemove, onPick, lines }: Props) {
  const empty = !guard && changes.length === 0
  return (
    <div className="px-4 py-2 text-[13px]">
      {empty && <p className="py-6 text-center text-muted-foreground">No change yet. Pick a line, listen, then press It starts here.</p>}
      <ul className="divide-y">
        {guard && (
          <li className="py-3">
            <div className="flex items-center gap-2.5">
              <i className="size-2 flex-none rounded-full bg-guard" />
              <div className="min-w-0 flex-1"><div>The guard's fix</div><div className="text-xs text-muted-foreground">confidence {guard.confidence ?? "-"}, nothing written yet</div></div>
              <Switch checked={guard.on} onCheckedChange={onGuard} aria-label="Use the guard's fix" className="data-[state=checked]:bg-guard" />
            </div>
            {guard.parts.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5 pl-[18px]">
                {guard.parts.map((part) => (
                  <button key={part.first} type="button" onClick={() => onPart(part)} className="rounded-full border px-2 py-0.5 text-xs hover:bg-accent">
                    {clock(part.first, 0)} to {clock(part.last, 0)} <span className="font-mono text-guard-ink">{part.shift > 0 ? "+" : ""}{part.shift.toFixed(2)} s</span>
                  </button>
                ))}
              </div>
            )}
          </li>
        )}
        {changes.map((change) => (
          <li key={`${change.first}-${change.last}`} className="flex items-center gap-2.5 py-3">
            <i className="size-2 flex-none rounded-full bg-ours" />
            <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onPick(change.first, false)}>
              <div className="underline decoration-border underline-offset-4">Lines {change.first + 1} to {change.last + 1}</div>
              <div className="text-xs text-muted-foreground">from {clock(lines[change.first]?.start ?? 0, 0)}, your change</div>
            </button>
            <span className="font-mono tabular-nums">{change.offset > 0 ? "+" : ""}{change.offset.toFixed(2)} s</span>
            <Button size="icon" variant="ghost" className="size-7" aria-label={`Remove the change on lines ${change.first + 1} to ${change.last + 1}`} onClick={() => onRemove(change)}><XIcon /></Button>
          </li>
        ))}
      </ul>
      {changes.length > 0 && <p className="py-3 text-xs text-muted-foreground">Ctrl Z undoes the last change.</p>}
    </div>
  )
}

function History({ subtitleId, versions, onRestore }: Props) {
  const checks = useQuery({ queryKey: ["history", "subtitle", subtitleId], queryFn: () => unwrap(api.GET("/api/history", { params: { query: { subtitle: subtitleId, limit: 50 } } })) })
  return (
    <div className="px-4 py-2 text-[13px]">
      <h2 className="pt-2 pb-1 text-xs font-medium text-muted-foreground">Checks</h2>
      {checks.data?.rows.length === 0 && <p className="py-3 text-muted-foreground">Not checked yet.</p>}
      <ol className="divide-y">
        {checks.data?.rows.map((row) => (
          <li key={row.id} className="py-2.5">
            <div className="flex items-center gap-2"><ToneDot tone={toneOf(row.tone)} /><span className="min-w-0 flex-1 truncate">{row.words}</span>
              {row.confidence !== null && <span className="font-mono text-xs text-muted-foreground tabular-nums">{row.confidence}</span>}</div>
            <div className="mt-0.5 pl-4 text-xs text-muted-foreground">{when(row.time)}{row.started_by ? `, ${row.started_by}` : ""}</div>
            {row.changes.filter((sentence) => sentence !== "Nothing moved").map((sentence) => <div key={sentence} className="pl-4 text-xs">{sentence}</div>)}
          </li>
        ))}
      </ol>
      <h2 className="pt-4 pb-1 text-xs font-medium text-muted-foreground">Saved versions</h2>
      {versions.length === 0 && <p className="py-3 text-muted-foreground">No saved version yet. One is kept each time the file is written.</p>}
      <ul className="divide-y">
        {versions.map((version) => (
          <li key={version.id} className="flex items-center gap-2 py-2.5">
            <div className="min-w-0 flex-1"><div className="truncate">{VERSION_WORDS[version.label] ?? version.label}</div><div className="text-xs text-muted-foreground">{when(version.time)}</div></div>
            <Button size="sm" variant="outline" onClick={() => onRestore(version)}>Restore</Button>
          </li>
        ))}
      </ul>
    </div>
  )
}
