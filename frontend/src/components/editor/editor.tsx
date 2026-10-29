// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { FastForwardIcon, Loader2Icon, PauseIcon, PlayIcon, RewindIcon, ZoomInIcon, ZoomOutIcon } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { LinesList } from "@/components/editor/lines-list"
import { Preview } from "@/components/editor/preview"
import { type Timing, Waveform, type WaveformHandle } from "@/components/editor/waveform"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { STATUS_KEY } from "@/hooks/use-status"
import { ApiError, api, mediaUrl, type Schemas, unwrap } from "@/lib/api/client"
import { clock } from "@/lib/format"
import { REVIEW_KEY } from "@/pages/review"

type Detail = Schemas["SubtitleDetail"]
type Lines = Schemas["Lines"]
type Mode = "proposal" | "current"
type Scope = "all" | "selected"

const ZOOMS = [10, 25, 50, 100, 200]
const NUDGES = [-1, -0.1, 0.1, 1]
const MOVED_SECONDS = 0.05
const VIDEO_TEXT_SECONDS = 1.5
const MAX_SHIFT = 3600

export function Editor({ detail, lines }: { detail: Detail; lines: Lines }) {
  const queryClient = useQueryClient()
  const waveform = useRef<WaveformHandle>(null)
  const hasProposal = Boolean(lines.proposal)
  const [mode, setMode] = useState<Mode>(hasProposal ? "proposal" : "current")
  const [shift, setShift] = useState(0)
  const [scope, setScope] = useState<Scope>("all")
  const [selection, setSelection] = useState<[number, number] | null>(null)
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [zoom, setZoom] = useState(2)
  const [rate, setRate] = useState(1)
  const [track, setTrack] = useState<number | null>(null)

  const audio = useQuery({
    queryKey: ["audio", detail.id, track],
    queryFn: () => unwrap(api.POST("/api/subtitles/{subtitle_id}/audio", { params: { path: { subtitle_id: detail.id } }, body: { track } })),
    refetchInterval: (query) => (query.state.data && !query.state.data.ready && query.state.data.tracks.length ? 1500 : false),
  })
  const peaks = useQuery({
    queryKey: ["peaks", detail.id, audio.data?.track],
    enabled: Boolean(audio.data?.ready),
    staleTime: Infinity,
    queryFn: async () => {
      const answer = await fetch(mediaUrl(`/api/subtitles/${detail.id}/audio/${audio.data!.track}/peaks`), { credentials: "same-origin" })
      return Float32Array.from(new Uint8Array(await answer.arrayBuffer()), (value) => value / 255)
    },
  })

  const base = useMemo(() => (mode === "proposal" && lines.proposal ? lines.lines.map((line, index) => ({ ...line, start: lines.proposal![index]![0]!, end: lines.proposal![index]![1]! })) : lines.lines),
    [mode, lines])
  const inScope = (index: number) => scope === "all" || (selection !== null && index >= selection[0] && index <= selection[1])
  const shown: Timing[] = useMemo(() => base.map((line, index) => {
    const offset = inScope(index) ? shift : 0
    const start = line.start + offset
    return { start, end: line.end + offset, text: line.text, moved: Math.abs(start - lines.lines[index]!.start) > MOVED_SECONDS,
      selected: selection !== null && index >= selection[0] && index <= selection[1] }
  }), [base, shift, scope, selection, lines])
  const videoText = useMemo(() => shown.map((line) => nearestText(lines.reference, line.start)), [shown, lines.reference])
  const current = useMemo(() => shown.findIndex((line) => line.start <= time && time < line.end), [shown, time])
  const negative = shown.some((line) => line.start < 0)
  const dirty = shift !== 0 || (mode === "proposal" && hasProposal)
  const moving = shown.filter((_, index) => inScope(index)).length

  useEffect(() => {
    if (shift === 0) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [shift])

  const apply = useMutation({
    mutationFn: async () => {
      let version = detail.version
      let starts = lines.lines.map((line) => line.start)
      if (mode === "proposal" && hasProposal) {
        await unwrap(api.POST("/api/subtitles/{subtitle_id}/apply", { params: { path: { subtitle_id: detail.id } }, body: { version } }))
        version = (await unwrap(api.GET("/api/subtitles/{subtitle_id}", { params: { path: { subtitle_id: detail.id } } }))).version
        starts = base.map((line) => line.start)
      }
      if (shift !== 0) {
        const range = scope === "selected" && selection ? selection : [0, starts.length - 1]
        await unwrap(api.POST("/api/subtitles/{subtitle_id}/shift", {
          params: { path: { subtitle_id: detail.id } },
          body: { version, first: Math.max(0, starts[range[0]!]!), last: starts[range[1]!]!, offset: Math.round(shift * 100) / 100 },
        }))
      }
    },
    onSuccess: () => {
      toast.success("Saved. The previous file was backed up.")
      setShift(0)
      setMode("current")
      for (const key of [["subtitle", detail.id], ["lines", detail.id], REVIEW_KEY, STATUS_KEY]) queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => {
      const stale = error instanceof ApiError && error.code === "stale_version"
      toast.error(error.message, stale ? { action: { label: "Reload", onClick: () => location.reload() } } : undefined)
    },
  })

  const nudge = (amount: number) => setShift((value) => clamp(Math.round((value + amount) * 100) / 100))
  const pick = (index: number, extend: boolean) => {
    setSelection((previous) => (extend && previous ? [Math.min(previous[0], index), Math.max(previous[1], index)] : [index, index]))
    if (extend) setScope("selected")
    waveform.current?.seek(Math.max(0, shown[index]!.start - 0.3))
  }
  const dragged = (index: number, newStart: number) => {
    if (!inScope(index)) {
      setSelection([index, index])
      setScope("selected")
    }
    setShift(clamp(Math.round((newStart - base[index]!.start) * 100) / 100))
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="h-[360px] lg:h-[400px]">
          <LinesList lines={shown} videoText={videoText} current={current} following={playing} onPick={pick} />
        </div>
        <Preview subtitleId={detail.id} time={time} playing={playing} caption={current >= 0 ? shown[current]!.text : ""} />
      </div>

      <section className="min-w-0 overflow-hidden rounded-lg border bg-card" aria-label="Playback and waveform">
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          <Button size="sm" variant="outline" aria-label="Back 5 seconds" onClick={() => waveform.current?.seek(time - 5)}><RewindIcon /></Button>
          <Button size="sm" className="min-w-24" disabled={!peaks.data} onClick={() => (playing ? waveform.current?.pause() : waveform.current?.play())}>
            {playing ? <PauseIcon /> : <PlayIcon />}{playing ? "Pause" : "Play"}
          </Button>
          <Button size="sm" variant="outline" aria-label="Forward 5 seconds" onClick={() => waveform.current?.seek(time + 5)}><FastForwardIcon /></Button>
          <span className="ml-1 font-mono text-sm tabular-nums">{clock(time, 1)} <span className="text-muted-foreground">/ {clock(audio.data?.duration ?? 0)}</span></span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {audio.data && audio.data.tracks.length > 1 && (
              <Select value={String(audio.data.track)} onValueChange={(value) => setTrack(Number(value))}>
                <SelectTrigger size="sm" className="w-44" aria-label="Audio track"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {audio.data.tracks.map((item) => <SelectItem key={item.index} value={String(item.index)}>{[item.language || "Unknown", item.title, item.commentary ? "commentary" : ""].filter(Boolean).join(", ")}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
            <Select value={String(rate)} onValueChange={(value) => setRate(Number(value))}>
              <SelectTrigger size="sm" className="w-24" aria-label="Speed"><SelectValue /></SelectTrigger>
              <SelectContent>{[0.75, 1, 1.25].map((value) => <SelectItem key={value} value={String(value)}>{value}x</SelectItem>)}</SelectContent>
            </Select>
            <Button size="sm" variant="ghost" aria-label="Zoom out" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}><ZoomOutIcon /></Button>
            <Button size="sm" variant="ghost" aria-label="Zoom in" disabled={zoom === ZOOMS.length - 1} onClick={() => setZoom(zoom + 1)}><ZoomInIcon /></Button>
          </div>
        </div>
        <div className="px-2 py-3">
          {peaks.data && audio.data ? (
            <Waveform ref={waveform} audioUrl={mediaUrl(`/api/subtitles/${detail.id}/audio/${audio.data.track}`)} peaks={peaks.data} duration={audio.data.duration}
              lines={shown} reference={lines.reference} zoom={ZOOMS[zoom]!} rate={rate} onTime={setTime} onPlaying={setPlaying} onLineDragged={dragged} onLineClicked={pick} />
          ) : (
            <div className="flex h-[190px] flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
              {audio.data && !audio.data.tracks.length ? "This video has no sound to listen to." : (
                <><Loader2Icon className="size-4 animate-spin" />Preparing the sound for listening. It takes a moment the first time only.</>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-4 py-2 text-xs text-muted-foreground">
          <Legend color="var(--cue)" edge="var(--cue-edge)" label="Your subtitle" />
          <Legend color="var(--cue-moved)" edge="var(--cue-moved-edge)" label="Moved" />
          {lines.reference.length > 0 && <Legend color="var(--reference)" label="In the video" />}
          <span className="ml-auto">Click to go there. Drag a line sideways to move the chosen lines.</span>
        </div>
      </section>

      <section className="space-y-4 rounded-lg border bg-card p-4" aria-label="Adjust the timing">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-medium">Adjust the timing</h2>
            <p className="text-sm text-muted-foreground" aria-live="polite">
              {summary(mode, hasProposal, shift, moving, negative)}
            </p>
          </div>
          {hasProposal && (
            <ToggleGroup type="single" variant="outline" size="sm" value={mode} onValueChange={(value) => value && setMode(value as Mode)} aria-label="Start from">
              <ToggleGroupItem value="proposal">With the fix</ToggleGroupItem>
              <ToggleGroupItem value="current">Current file</ToggleGroupItem>
            </ToggleGroup>
          )}
        </div>
        <RadioGroup value={scope} onValueChange={(value) => setScope(value as Scope)} className="flex flex-wrap gap-4" aria-label="Lines to move">
          <div className="flex items-center gap-2"><RadioGroupItem value="all" id="scope-all" /><Label htmlFor="scope-all">Every line</Label></div>
          <div className="flex items-center gap-2">
            <RadioGroupItem value="selected" id="scope-selected" disabled={!selection} />
            <Label htmlFor="scope-selected" className={!selection ? "text-muted-foreground" : undefined}>
              {selection ? `Lines ${selection[0] + 1} to ${selection[1] + 1}` : "Selected lines (click a line, Shift-click another)"}
            </Label>
          </div>
        </RadioGroup>
        <div className="flex flex-wrap items-center gap-2">
          {NUDGES.slice(0, 2).map((amount) => <Button key={amount} size="sm" variant="outline" onClick={() => nudge(amount)}>Earlier {Math.abs(amount)} s</Button>)}
          <Label htmlFor="shift" className="sr-only">Shift in seconds</Label>
          <Input id="shift" inputMode="decimal" className="h-8 w-24 text-center font-mono tabular-nums" value={shift.toFixed(2)} aria-invalid={negative}
            onChange={(event) => {
              const value = Number(event.target.value.replace(",", "."))
              if (Number.isFinite(value)) setShift(clamp(value))
            }} />
          {NUDGES.slice(2).map((amount) => <Button key={amount} size="sm" variant="outline" onClick={() => nudge(amount)}>Later {amount} s</Button>)}
          <Button size="sm" variant="ghost" disabled={shift === 0} onClick={() => setShift(0)}>Reset</Button>
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t pt-4">
          <Button disabled={!dirty || negative || apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending && <Loader2Icon className="animate-spin" />}Save to the file
          </Button>
          <p className="text-sm text-muted-foreground">Nothing is written until you save. The file is backed up first and can be restored from its versions.</p>
        </div>
      </section>
    </div>
  )
}

export function EditorSkeleton() {
  return <Skeleton className="h-[640px] w-full" />
}

function Legend({ color, edge, label }: { color: string; edge?: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: color, borderLeft: edge ? `2px solid ${edge}` : undefined }} />
      {label}
    </span>
  )
}

function summary(mode: Mode, hasProposal: boolean, shift: number, moving: number, negative: boolean) {
  if (negative) return "Some lines would start before the video begins, so this cannot be saved."
  const base = mode === "proposal" && hasProposal ? "The guard's fix is shown" : "The current file is shown"
  if (shift === 0) return `${base}. Listen, then save it or move lines by hand.`
  return `${base}, with ${moving} lines ${Math.abs(shift).toFixed(2)} s ${shift < 0 ? "earlier" : "later"}.`
}

function nearestText(reference: Lines["reference"], start: number) {
  let low = 0
  let high = reference.length
  while (low < high) {
    const middle = (low + high) >> 1
    if (reference[middle]!.start < start) low = middle + 1
    else high = middle
  }
  const near = [reference[low - 1], reference[low]].filter((line) => line && Math.abs(line.start - start) <= VIDEO_TEXT_SECONDS)
  near.sort((first, second) => Math.abs(first!.start - start) - Math.abs(second!.start - start))
  return near[0]?.text ?? null
}

function clamp(value: number) {
  return Math.max(-MAX_SHIFT, Math.min(MAX_SHIFT, value))
}
