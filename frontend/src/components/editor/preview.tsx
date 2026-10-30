// SPDX-License-Identifier: GPL-3.0-or-later
import { cn } from "cn"
import { Loader2Icon } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import { mediaUrl } from "@/lib/api/client"
import { useT } from "@/lib/i18n"

const FRAME_DELAY_MS = 250
const LOADING_DELAY_MS = 400
const DRIFT_SECONDS = 0.25
const PAUSED_DRIFT_SECONDS = 0.02

type Props = {
  subtitleId: string
  time: number
  duration: number
  playing: boolean
  rate: number
  windowSeconds: number | null
  caption: string
  className?: string
}
type Slots = [number, number]

// two players take turns: one shows the window under the playhead while the other loads the next one, so the switch is instant
export function Preview({ subtitleId, time, duration, playing, rate, windowSeconds, caption, className }: Props) {
  const t = useT()
  const [frame, setFrame] = useState<string | null>(null)
  const [broken, setBroken] = useState(false)
  const [loaded, setLoaded] = useState<(number | null)[]>([null, null])
  const [requested, setRequested] = useState<ReadonlySet<number>>(new Set())
  const [slow, setSlow] = useState(false)
  const previousRequest = useRef<Promise<void>>(Promise.resolve())
  const players = useRef<(HTMLVideoElement | null)[]>([null, null])
  const index = windowSeconds ? Math.floor(time / windowSeconds) : 0
  const offset = windowSeconds ? time - index * windowSeconds : 0
  const [slots, setSlots] = useState<Slots>([index, index + 1])
  const arranged = arrange(slots, index)
  if (arranged !== slots) setSlots(arranged)
  const current = arranged.indexOf(index)
  const showsVideo = windowSeconds !== null && !broken && loaded[current] === index
  const nextExists = windowSeconds !== null && (index + 1) * windowSeconds < duration
  const waiting = windowSeconds !== null && !broken && !showsVideo
  const windowUrl = (number: number) => mediaUrl(`/api/subtitles/${subtitleId}/video/${number}`)
  const mark = (slot: number, number: number | null) =>
    setLoaded((previous) => (previous[slot] === number ? previous : previous.map((value, position) => (position === slot ? number : value))))

  useEffect(() => {
    if (playing || showsVideo) return
    let cancelled = false
    const showFrame = async () => {
      if (cancelled) return
      const answer = await fetch(mediaUrl(`/api/subtitles/${subtitleId}/frame?time=${time.toFixed(2)}`), { credentials: "same-origin" })
      if (!answer.ok || cancelled) return
      const shown = URL.createObjectURL(await answer.blob())
      setFrame((previous) => {
        if (previous) URL.revokeObjectURL(previous)
        return shown
      })
    }
    // the server extracts one frame at a time and answers 503 to a second request
    const timer = setTimeout(() => {
      previousRequest.current = previousRequest.current.then(showFrame).catch(() => setFrame(null))
    }, FRAME_DELAY_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [subtitleId, time, playing, showsVideo])

  useEffect(() => {
    players.current.forEach((element, slot) => {
      if (!element) return
      element.playbackRate = rate
      if (playing && slot === current) {
        element.play().catch(ignoreAbort)
        return
      }
      element.pause()
    })
  }, [playing, rate, current, index])

  // the sound leads: the picture only jumps when it drifts away from the playhead
  useEffect(() => {
    const element = players.current[current]
    if (!element || loaded[current] !== index) return
    if (Math.abs(element.currentTime - offset) > (playing ? DRIFT_SECONDS : PAUSED_DRIFT_SECONDS)) element.currentTime = offset
  }, [offset, playing, loaded, index, current])

  // a copied window arrives within a blink: the loading sign only shows when the wait lasts
  useEffect(() => {
    if (!waiting) return
    const timer = setTimeout(() => setSlow(true), LOADING_DELAY_MS)
    return () => {
      clearTimeout(timer)
      setSlow(false)
    }
  }, [waiting, index])

  // as soon as a window plays, the next one is asked for, so the server makes it while this one lasts
  useEffect(() => {
    if (!playing || !nextExists || requested.has(index + 1)) return
    setRequested((previous) => new Set(previous).add(index + 1))
  }, [playing, nextExists, index, requested])

  return (
    <div className={cn("relative aspect-video w-full overflow-hidden rounded-md bg-black", className)}>
      {frame && !showsVideo && <img src={frame} alt={t("The video at the current time")} className="absolute inset-0 size-full object-contain" />}
      {!frame && !showsVideo && !waiting && <div className="absolute inset-0 flex items-center justify-center text-xs text-white/50">{t("The picture shows when you pause.")}</div>}
      {windowSeconds !== null && !broken && arranged.map((number, slot) => {
        const visible = slot === current && showsVideo
        return (
          <video key={slot} ref={(element) => { players.current[slot] = element }} data-window={number} muted playsInline preload="auto"
            src={number === index || requested.has(number) ? windowUrl(number) : undefined}
            aria-hidden={!visible} aria-label={visible ? t("The video at the current time") : undefined}
            className={cn("absolute inset-0 size-full object-contain", !visible && "opacity-0")}
            onLoadStart={() => mark(slot, null)}
            onLoadedMetadata={(event) => { event.currentTarget.currentTime = slot === current ? offset : 0 }}
            onCanPlay={(event) => mark(slot, Number(event.currentTarget.dataset.window))}
            onError={(event) => { if (Number(event.currentTarget.dataset.window) === index) setBroken(true) }} />
        )
      })}
      {slow && (
        <div role="status" className="absolute top-3 left-3 flex items-center gap-2 rounded-full bg-black/70 px-3 py-1.5 text-xs text-white">
          <Loader2Icon className="size-3.5 animate-spin" />{t("Preparing the video")}
        </div>
      )}
      {caption && (
        <p className="absolute inset-x-[6%] bottom-[7%] text-center text-[clamp(14px,1.6vw,22px)] font-semibold whitespace-pre-line text-white [text-shadow:0_0_4px_#000,0_0_2px_#000]">
          {caption}
        </p>
      )}
    </div>
  )
}

function arrange(slots: Slots, index: number): Slots {
  const kept = slots.indexOf(index)
  if (kept === -1) return [index, index + 1]
  if (slots[1 - kept] === index + 1) return slots
  return kept === 0 ? [index, index + 1] : [index + 1, index]
}

function ignoreAbort(error: unknown) {
  if (!(error instanceof DOMException && error.name === "AbortError")) throw error
}
