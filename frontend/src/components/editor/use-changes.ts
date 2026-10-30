// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useBlocker } from "@tanstack/react-router"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import type { Block } from "@/components/editor/timeline"
import { STATUS_KEY } from "@/hooks/use-status"
import { ApiError, api, type Schemas, unwrap } from "@/lib/api/client"
import { useT } from "@/lib/i18n"
import { heldLines, lineOffsets, type Move, openRuns, type Scope, SCOPES, scopeRange, segments } from "@/lib/retime"
import { REVIEW_KEY } from "@/pages/review"

type Options = { detail: Schemas["SubtitleDetail"]; lines: Schemas["Lines"]; current: number; selection: [number, number] | null; onNext: () => void }

const blockNavigation = () => true

export function useChanges({ detail, lines, current, selection, onNext }: Options) {
  const t = useT()
  const queryClient = useQueryClient()
  const hasGuardFix = Boolean(lines.proposal)
  const [guardOn, setGuardOn] = useState(hasGuardFix)
  const [history, setHistory] = useState<Move[]>([])
  const [scope, setScope] = useState<Scope>("all")

  const base = useMemo(() => lines.lines.map((line, index) => (guardOn && lines.proposal ? { ...line, start: lines.proposal[index]![0]!, end: lines.proposal[index]![1]! } : line)), [guardOn, lines])
  const offsets = useMemo(() => lineOffsets(base.length, history), [base.length, history])
  const shown: Block[] = useMemo(() => base.map((line, index) => ({ start: line.start + offsets[index]!, end: line.end + offsets[index]!, text: line.text })), [base, offsets])
  const pending = useMemo(() => segments(offsets), [offsets])
  const held = useMemo(() => heldLines(base.length, history), [base.length, history])
  const moves = useMemo(() => shown.map((line, index) => ({ amount: line.start - lines.lines[index]!.start, byHand: offsets[index] !== 0 })), [shown, offsets, lines])
  const changeCount = (guardOn && hasGuardFix ? 1 : 0) + pending.length
  const negative = shown.some((line) => line.start < 0)
  const blocker = useBlocker({ shouldBlockFn: blockNavigation, disabled: pending.length === 0, withResolver: true })

  const save = useMutation({
    mutationFn: () => unwrap(api.POST("/api/subtitles/{subtitle_id}/retime", {
      params: { path: { subtitle_id: detail.id } },
      body: { version: lines.version, use_proposal: guardOn && hasGuardFix, changes: pending },
    })),
    onSuccess: () => {
      toast.success(t("Saved. The previous file was backed up."), { action: { label: t("Next in Review"), onClick: onNext } })
      setHistory([])
      for (const key of [["subtitle", detail.id], ["lines", detail.id], ["history"], REVIEW_KEY, STATUS_KEY]) queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => {
      const stale = error instanceof ApiError && error.code === "stale_version"
      toast.error(error.message, stale ? { action: { label: t("Reload"), onClick: () => location.reload() } } : undefined)
    },
  })

  const move = (offset: number, target?: [number, number]) => {
    const rounded = Math.round(offset * 100) / 100
    if (rounded === 0) return
    const pinned = target !== undefined || scope === "selected"
    const range = target ?? scopeRange(scope, current, selection, shown.length)
    const runs = pinned ? [range] : openRuns(range, held)
    const skipped = range[1] - range[0] + 1 - runs.reduce((sum, [first, last]) => sum + last - first + 1, 0)
    if (skipped > 0) toast(skipped === 1 ? t("1 line placed by hand stays where it is") : t("{count} lines placed by hand stay where they are", { count: skipped }))
    const group = (history.at(-1)?.group ?? 0) + 1
    setHistory((previous) => [...previous, ...runs.map(([first, last]) => ({ first, last, offset: rounded, group, held: pinned }))])
  }
  const undo = () => setHistory((previous) => previous.filter((entry) => entry.group !== previous.at(-1)?.group))
  const pickScope = (value: string) => {
    const picked = SCOPES.find((entry) => entry === value)
    if (picked) setScope(picked)
  }

  return { hasGuardFix, guardOn, setGuardOn, scope, setScope, pickScope, shown, pending, held, moves, changeCount, negative, move, undo, save, blocker }
}
