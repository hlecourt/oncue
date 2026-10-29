// SPDX-License-Identifier: GPL-3.0-or-later
import { useVirtualizer } from "@tanstack/react-virtual"
import { cn } from "cn"
import { useEffect, useRef } from "react"

import type { Timing } from "@/components/editor/waveform"
import { clock } from "@/lib/format"

const ROW_HEIGHT = 36

type Props = {
  lines: Timing[]
  videoText: (string | null)[]
  current: number
  following: boolean
  onPick: (index: number, extend: boolean) => void
}

export function LinesList({ lines, videoText, current, following, onPick }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const virtual = useVirtualizer({ count: lines.length, getScrollElement: () => scroller.current, estimateSize: () => ROW_HEIGHT, overscan: 12 })

  useEffect(() => {
    if (following && current >= 0) virtual.scrollToIndex(current, { align: "auto" })
  }, [current, following, virtual])

  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border bg-card">
      <div className="grid grid-cols-[3rem_5.5rem_3.5rem_1fr_1fr] gap-2 border-b bg-muted/50 px-3 py-2 text-xs font-medium text-muted-foreground" aria-hidden="true">
        <span>#</span>
        <span>Start</span>
        <span>Length</span>
        <span>Your subtitle</span>
        <span>In the video</span>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-auto" tabIndex={-1}>
        <ul className="relative w-full" style={{ height: virtual.getTotalSize() }} aria-label="Lines of the subtitle">
          {virtual.getVirtualItems().map((item) => {
            const line = lines[item.index]!
            return (
              <li key={item.key} className="absolute left-0 w-full" style={{ top: item.start, height: ROW_HEIGHT }}>
              <button type="button" onClick={(event) => onPick(item.index, event.shiftKey)}
                aria-current={item.index === current ? "true" : undefined} aria-pressed={line.selected}
                className={cn("grid size-full grid-cols-[3rem_5.5rem_3.5rem_1fr_1fr] items-center gap-2 border-b px-3 text-left text-sm select-none hover:bg-accent/60",
                  line.selected && "bg-info/10", item.index === current && "shadow-[inset_3px_0_0_var(--foreground)]")}>
                <span className="font-mono text-xs text-muted-foreground tabular-nums">{item.index + 1}</span>
                <span className={cn("font-mono text-xs tabular-nums", line.moved ? "text-warning" : "text-foreground")}>{clock(line.start, 2)}</span>
                <span className="font-mono text-xs text-muted-foreground tabular-nums">{(line.end - line.start).toFixed(1)}s</span>
                <span className="truncate">{line.text}</span>
                <span className="truncate text-muted-foreground">{videoText[item.index] ?? ""}</span>
              </button>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
