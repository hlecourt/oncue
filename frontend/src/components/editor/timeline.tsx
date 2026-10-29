// SPDX-License-Identifier: GPL-3.0-or-later
import { cn } from "cn"
import { type PointerEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react"
import WaveSurfer from "wavesurfer.js"

import { clock } from "@/lib/format"
import { useDark } from "@/lib/theme"

export type Block = { start: number; end: number; text: string }
export type Band = { start: number; end: number; tone: "guard" | "ours" }

type Props = {
  duration: number
  peaks: Float32Array | null
  media: { element: HTMLAudioElement; url: string } | null
  soundNote: ReactNode
  lines: Block[]
  before: Block | null
  reference: Block[]
  current: number
  selection: [number, number] | null
  time: number
  span: number
  bands: Band[]
  labels: { ours: string; oursDetail: string; theirs: string; theirsDetail: string }
  compact?: boolean
  onSeek: (seconds: number) => void
  onPick: (index: number, extend: boolean) => void
  onDrag: (index: number, newStart: number) => void
}

const RULER_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
const LABEL_SPACING = 56
const PLAYHEAD_CLEARANCE = 34
const FOLLOW_MARGIN = 0.9
const FOLLOW_LEAD = 0.2
const DRAG_THRESHOLD = 8

export function Timeline(props: Props) {
  const { duration, span, time, compact = false } = props
  const tracks = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [windowStart, setWindowStart] = useState(0)
  const [drag, setDrag] = useState<{ index: number; pointerX: number; shift: number } | null>(null)
  const heights = compact ? { ruler: 20, ours: 32, sound: 72, theirs: 26 } : { ruler: 22, ours: 36, sound: 112, theirs: 30 }
  const lanesHeight = heights.ours + heights.sound + heights.theirs
  const scale = width / span
  const xOf = (seconds: number) => (seconds - windowStart) * scale
  const visible = (block: Block) => block.end >= windowStart && block.start <= windowStart + span

  const ticks = useMemo(() => rulerTicks(windowStart, span, scale), [windowStart, span, scale])

  useEffect(() => {
    const element = tracks.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry!.contentRect.width))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (time >= windowStart && time <= windowStart + span * FOLLOW_MARGIN) return
    setWindowStart(Math.max(0, Math.min(Math.max(0, duration - span), time - span * FOLLOW_LEAD)))
  }, [time, span, duration, windowStart])

  const timeAt = (event: PointerEvent<HTMLElement>) => windowStart + (event.clientX - tracks.current!.getBoundingClientRect().left) / scale

  const startDrag = (index: number) => (event: PointerEvent<HTMLButtonElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    setDrag({ index, pointerX: event.clientX, shift: 0 })
  }
  const moveDrag = (event: PointerEvent<HTMLButtonElement>) => {
    if (drag) setDrag({ ...drag, shift: (event.clientX - drag.pointerX) / scale })
  }
  const endDrag = (index: number) => (event: PointerEvent<HTMLButtonElement>) => {
    const moved = drag && Math.abs(drag.shift * scale) > DRAG_THRESHOLD
    if (moved) props.onDrag(index, props.lines[index]!.start + drag.shift)
    else props.onPick(index, event.shiftKey)
    setDrag(null)
  }

  const current = props.lines[props.current]
  const gap = current && Math.abs(time - current.start) < span ? [Math.min(time, current.start), Math.max(time, current.start)] : null
  const inSelection = (index: number) => props.selection !== null && index >= props.selection[0] && index <= props.selection[1]

  return (
    <section className="flex bg-background" aria-label="Timeline">
      {!compact && (
        <div className="w-[150px] flex-none pl-4 text-xs" aria-hidden="true">
          <div style={{ height: heights.ruler }} />
          <TrackLabel height={heights.ours} tone="bg-ours" title={props.labels.ours} detail={props.labels.oursDetail} />
          <TrackLabel height={heights.sound} tone="bg-wave" title="Sound" detail="voices" />
          <TrackLabel height={heights.theirs} tone="bg-theirs" title={props.labels.theirs} detail={props.labels.theirsDetail} />
          <div className="pt-1 text-[11px] text-muted-foreground">Whole file</div>
        </div>
      )}
      <div ref={tracks} className={cn("relative min-w-0 flex-1 select-none", compact ? "mx-3" : "mr-3")}>
        <div className="relative border-b" style={{ height: heights.ruler }} aria-hidden="true">
          {ticks.filter((second) => Math.abs(xOf(second) - xOf(time)) > PLAYHEAD_CLEARANCE).map((second) => (
            <span key={second} className="absolute bottom-1 -translate-x-1/2 font-mono text-[10px] text-muted-foreground tabular-nums" style={{ left: xOf(second) }}>
              {clock(second, second % 1 ? 1 : 0)}
            </span>
          ))}
        </div>
        <div className="relative overflow-hidden" style={{ height: lanesHeight }} onPointerDown={(event) => event.target === event.currentTarget && props.onSeek(timeAt(event))}>
          {props.before && current && visible(props.before) && Math.abs(props.before.start - current.start) > 0.05 && (
            <div className="pointer-events-none absolute flex items-center overflow-hidden rounded-[5px] border border-dashed border-ours-line px-1.5 font-mono text-[10px] whitespace-nowrap text-ours-ink"
              style={{ left: xOf(props.before.start), width: (props.before.end - props.before.start) * scale, top: 5, height: heights.ours - 10 }}>
              was {clock(props.before.start, 2)}
            </div>
          )}
          {current && visible(current) && (
            <div className="pointer-events-none absolute rounded bg-ours-soft/80" style={{ left: xOf(current.start), width: (current.end - current.start) * scale, top: heights.ours + 4, height: heights.sound - 8 }} />
          )}
          {props.peaks && props.media && (
            <SoundTrack media={props.media} peaks={props.peaks} duration={duration} windowStart={windowStart} span={span} width={width} height={heights.sound} top={heights.ours} />
          )}
          {!(props.peaks && props.media) && <div className="pointer-events-none absolute inset-x-0 flex items-center justify-center text-xs text-muted-foreground" style={{ top: heights.ours, height: heights.sound }}>{props.soundNote}</div>}
          {props.lines.map((line, index) => {
            if (!visible(line)) return null
            const shift = drag?.index === index ? drag.shift : 0
            const left = xOf(line.start + shift)
            return (
              <div key={index} className="contents">
                <i className={cn("pointer-events-none absolute w-px", index === props.current ? "bg-ours" : "bg-ours/30")} style={{ left, top: 4, height: heights.ours + heights.sound - 4 }} />
                <button type="button" aria-label={`Line ${index + 1}: ${line.text}`} title={line.text}
                  onPointerDown={startDrag(index)} onPointerMove={moveDrag} onPointerUp={endDrag(index)}
                  className={cn("absolute flex cursor-grab items-center overflow-hidden rounded-[5px] px-1.5 text-[11px] whitespace-nowrap active:cursor-grabbing",
                    index === props.current ? "bg-ours-strong font-medium text-white shadow-sm" : "bg-ours-soft text-ours-ink shadow-[inset_2px_0_0_var(--ours)]",
                    inSelection(index) && index !== props.current && "ring-1 ring-ours ring-inset")}
                  style={{ left, width: Math.max(4, (line.end - line.start) * scale), top: 5, height: heights.ours - 10 }}>
                  {line.text}
                </button>
              </div>
            )
          })}
          {props.reference.map((line, index) => visible(line) && (
            <div key={index} title={line.text} className="pointer-events-none absolute flex items-center overflow-hidden rounded-[5px] bg-theirs-soft px-1.5 text-[11px] whitespace-nowrap text-theirs-ink shadow-[inset_2px_0_0_var(--theirs)]"
              style={{ left: xOf(line.start), width: (line.end - line.start) * scale, top: heights.ours + heights.sound + 4, height: heights.theirs - 8 }}>
              {line.text}
            </div>
          ))}
          {gap && <div className="pointer-events-none absolute border-b-2 border-ours" style={{ left: xOf(gap[0]!), width: Math.max(2, (gap[1]! - gap[0]!) * scale), top: heights.ours + heights.sound * 0.72, height: 8 }} />}
        </div>
        <div className="pointer-events-none absolute top-0 w-[1.5px] bg-playhead" style={{ left: xOf(time), height: heights.ruler + lanesHeight }}>
          <span className="absolute top-0.5 left-1.5 rounded bg-playhead px-1.5 py-px font-mono text-[10px] font-semibold text-white tabular-nums">{clock(time, 2)}</span>
        </div>
        <Overview duration={duration} bands={props.bands} windowStart={windowStart} span={span} onSeek={props.onSeek} />
      </div>
    </section>
  )
}

function TrackLabel({ height, tone, title, detail }: { height: number; tone: string; title: string; detail: string }) {
  return (
    <div className="flex items-center gap-2" style={{ height }}>
      <i className={cn("size-2 flex-none rounded-[2px]", tone)} />
      <span className="flex flex-col leading-tight"><span className="font-medium">{title}</span><span className="text-[11px] text-muted-foreground">{detail}</span></span>
    </div>
  )
}

function Overview({ duration, bands, windowStart, span, onSeek }: { duration: number; bands: Band[]; windowStart: number; span: number; onSeek: (seconds: number) => void }) {
  const percent = (seconds: number) => `${(100 * seconds) / Math.max(duration, 1)}%`
  return (
    <button type="button" aria-label="Whole file: click to go there" className="relative mt-1 block h-3.5 w-full overflow-hidden rounded bg-muted"
      onClick={(event) => {
        const box = event.currentTarget.getBoundingClientRect()
        onSeek(((event.clientX - box.left) / box.width) * duration)
      }}>
      {bands.map((band, index) => (
        <i key={index} className={cn("absolute inset-y-[3px] rounded-[3px] border", band.tone === "guard" ? "border-guard bg-guard-line" : "border-ours bg-ours-line")}
          style={{ left: percent(band.start), width: percent(band.end - band.start) }} />
      ))}
      <b className="absolute inset-y-0 rounded border-[1.5px] border-foreground" style={{ left: percent(windowStart), width: percent(span) }} />
    </button>
  )
}

type SoundProps = { media: { element: HTMLAudioElement; url: string }; peaks: Float32Array; duration: number; windowStart: number; span: number; width: number; height: number; top: number }

function SoundTrack({ media, peaks, duration, windowStart, span, width, height, top }: SoundProps) {
  const container = useRef<HTMLDivElement>(null)
  const surfer = useRef<WaveSurfer | null>(null)
  const [loads, setLoads] = useState(0)
  const dark = useDark()

  useEffect(() => {
    if (!container.current) return
    const wave = WaveSurfer.create({
      container: container.current, media: media.element, url: media.url, peaks: [peaks], duration, height, ...waveColors(),
      cursorWidth: 0, barWidth: 2, barGap: 1, barRadius: 2, normalize: true, interact: false, hideScrollbar: true, autoScroll: false, autoCenter: false,
    })
    surfer.current = wave
    wave.on("ready", () => setLoads((count) => count + 1))
    return () => {
      surfer.current = null
      wave.destroy()
    }
  }, [media, peaks, duration, height])

  useEffect(() => {
    surfer.current?.setOptions(waveColors())
  }, [dark])

  useEffect(() => {
    if (!loads || !surfer.current || width <= 0) return
    surfer.current.zoom(width / span)
    surfer.current.setScrollTime(windowStart)
  }, [loads, width, span, windowStart])

  return <div ref={container} className="pointer-events-none absolute inset-x-0" style={{ top, height }} aria-hidden="true" />
}

function waveColors() {
  const colors = getComputedStyle(document.documentElement)
  return { waveColor: colors.getPropertyValue("--wave").trim(), progressColor: colors.getPropertyValue("--ours-line").trim() }
}

function rulerTicks(start: number, span: number, scale: number) {
  const step = RULER_STEPS.find((candidate) => candidate * scale >= LABEL_SPACING) ?? RULER_STEPS.at(-1)!
  const ticks: number[] = []
  for (let second = Math.ceil(start / step) * step; second <= start + span; second += step) ticks.push(Math.round(second * 10) / 10)
  return ticks
}
