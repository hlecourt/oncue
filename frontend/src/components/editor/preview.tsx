// SPDX-License-Identifier: GPL-3.0-or-later
import { cn } from "cn"
import { useEffect, useRef, useState } from "react"

import { mediaUrl } from "@/lib/api/client"
import { useT } from "@/lib/i18n"

const FRAME_DELAY_MS = 250

export function Preview({ subtitleId, time, playing, caption, className }: { subtitleId: string; time: number; playing: boolean; caption: string; className?: string }) {
  const t = useT()
  const [frame, setFrame] = useState<string | null>(null)
  const previousRequest = useRef<Promise<void>>(Promise.resolve())

  useEffect(() => {
    if (playing) return
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
  }, [subtitleId, time, playing])

  return (
    <div className={cn("relative aspect-video w-full overflow-hidden rounded-md bg-black", className)}>
      {frame && <img src={frame} alt={t("The video at the current time")} className="absolute inset-0 size-full object-contain" />}
      {!frame && <div className="absolute inset-0 flex items-center justify-center text-xs text-white/50">{t("The picture shows when you pause.")}</div>}
      {caption && (
        <p className="absolute inset-x-[6%] bottom-[7%] text-center text-[clamp(14px,1.6vw,22px)] font-semibold whitespace-pre-line text-white [text-shadow:0_0_4px_#000,0_0_2px_#000]">
          {caption}
        </p>
      )}
    </div>
  )
}
