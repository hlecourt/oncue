// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useState } from "react"

import { mediaUrl } from "@/lib/api/client"

const FRAME_DELAY_MS = 250

export function Preview({ subtitleId, time, playing, caption }: { subtitleId: string; time: number; playing: boolean; caption: string }) {
  const [frame, setFrame] = useState<string | null>(null)

  useEffect(() => {
    if (playing) return
    let cancelled = false
    let shown: string | null = null
    const timer = setTimeout(async () => {
      const answer = await fetch(mediaUrl(`/api/subtitles/${subtitleId}/frame?time=${time.toFixed(2)}`), { credentials: "same-origin" })
      if (!answer.ok || cancelled) return
      shown = URL.createObjectURL(await answer.blob())
      setFrame((previous) => {
        if (previous) URL.revokeObjectURL(previous)
        return shown
      })
    }, FRAME_DELAY_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [subtitleId, time, playing])

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-lg border bg-black">
      {frame && <img src={frame} alt="The video at the current time" className="absolute inset-0 size-full object-contain" />}
      {!frame && <div className="absolute inset-0 flex items-center justify-center text-xs text-white/50">The picture shows when you pause.</div>}
      {caption && (
        <p className="absolute inset-x-[6%] bottom-[7%] text-center text-[clamp(14px,1.6vw,22px)] font-semibold whitespace-pre-line text-white [text-shadow:0_0_4px_#000,0_0_2px_#000]">
          {caption}
        </p>
      )}
    </div>
  )
}
