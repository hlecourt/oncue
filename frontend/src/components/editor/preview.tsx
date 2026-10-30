// SPDX-License-Identifier: GPL-3.0-or-later
import { cn } from "cn"
import { useEffect, useRef, useState } from "react"

import { mediaUrl } from "@/lib/api/client"
import { useT } from "@/lib/i18n"

const FRAME_DELAY_MS = 250
const WINDOW_SECONDS = 30
const PREPARE_NEXT_SECONDS = 8
const DRIFT_SECONDS = 0.25
const PAUSED_DRIFT_SECONDS = 0.02

type Props = { subtitleId: string; time: number; playing: boolean; rate: number; caption: string; className?: string }

export function Preview({ subtitleId, time, playing, rate, caption, className }: Props) {
  const t = useT()
  const [frame, setFrame] = useState<string | null>(null)
  const [playable, setPlayable] = useState<number | null>(null)
  const [broken, setBroken] = useState(false)
  const previousRequest = useRef<Promise<void>>(Promise.resolve())
  const prepared = useRef(new Set<number>())
  const player = useRef<HTMLVideoElement>(null)
  const index = Math.floor(time / WINDOW_SECONDS)
  const offset = time - index * WINDOW_SECONDS
  const windowUrl = (number: number) => mediaUrl(`/api/subtitles/${subtitleId}/video/${number}`)
  const showsVideo = !broken && playable !== null

  useEffect(() => {
    if (playing || (!broken && playable === index)) return
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
  }, [subtitleId, time, playing, broken, playable, index])

  useEffect(() => {
    const element = player.current
    if (!element) return
    element.playbackRate = rate
    if (!playing) {
      element.pause()
      return
    }
    element.play().catch((error: unknown) => {
      if (!(error instanceof DOMException && error.name === "AbortError")) throw error
    })
  }, [playing, rate, index])

  // the sound leads: the picture only jumps when it drifts away from the playhead
  useEffect(() => {
    const element = player.current
    if (!element || playable !== index) return
    if (Math.abs(element.currentTime - offset) > (playing ? DRIFT_SECONDS : PAUSED_DRIFT_SECONDS)) element.currentTime = offset
  }, [offset, playing, playable, index])

  // asking for the next window before it is needed lets the server make it while this one plays
  useEffect(() => {
    if (broken || !playing || offset < WINDOW_SECONDS - PREPARE_NEXT_SECONDS || prepared.current.has(index + 1)) return
    prepared.current.add(index + 1)
    fetch(windowUrl(index + 1), { credentials: "same-origin", headers: { Range: "bytes=0-0" } }).catch(() => prepared.current.delete(index + 1))
  })

  return (
    <div className={cn("relative aspect-video w-full overflow-hidden rounded-md bg-black", className)}>
      {frame && !showsVideo && <img src={frame} alt={t("The video at the current time")} className="absolute inset-0 size-full object-contain" />}
      {!frame && !showsVideo && <div className="absolute inset-0 flex items-center justify-center text-xs text-white/50">{t("The picture shows when you pause.")}</div>}
      {!broken && (
        <video ref={player} src={windowUrl(index)} muted playsInline preload="auto" aria-label={t("The video at the current time")}
          className={cn("absolute inset-0 size-full object-contain", !showsVideo && "opacity-0")}
          onLoadedMetadata={(event) => { event.currentTarget.currentTime = offset }}
          onCanPlay={() => setPlayable(index)} onError={() => setBroken(true)} />
      )}
      {caption && (
        <p className="absolute inset-x-[6%] bottom-[7%] text-center text-[clamp(14px,1.6vw,22px)] font-semibold whitespace-pre-line text-white [text-shadow:0_0_4px_#000,0_0_2px_#000]">
          {caption}
        </p>
      )}
    </div>
  )
}
