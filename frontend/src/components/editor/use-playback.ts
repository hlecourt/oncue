// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from "@tanstack/react-query"
import { type SyntheticEvent, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { ApiError, api, mediaUrl, unwrap } from "@/lib/api/client"
import { useT } from "@/lib/i18n"

export function usePlayback(subtitleId: string, fallbackDuration: number, initialTime = 0) {
  const t = useT()
  const audio = useRef<HTMLAudioElement>(null)
  const [time, setTime] = useState(Math.max(0, initialTime))
  const [playing, setPlaying] = useState(false)
  const [rate, setRate] = useState(1)
  const [track, setTrack] = useState<number | null>(null)
  const [player, setPlayer] = useState<HTMLAudioElement | null>(null)
  const copyFailed = useRef(false)

  const media = useQuery({
    queryKey: ["audio", subtitleId, track],
    queryFn: async () => {
      const state = await unwrap(api.POST("/api/subtitles/{subtitle_id}/audio", { params: { path: { subtitle_id: subtitleId } }, body: { track, retry: copyFailed.current } }))
      copyFailed.current = state.failed
      if (state.failed) throw new ApiError(t("The sound could not be loaded."), "audio_copy_failed", 200)
      return state
    },
    // after a failed copy only the Try again button asks again: a poll or a focus would make ffmpeg fail in a loop
    refetchInterval: (query) => (!query.state.error && query.state.data && !query.state.data.ready && query.state.data.tracks.length ? 1500 : false),
    refetchOnWindowFocus: (query) => !query.state.error,
  })
  const peaks = useQuery({
    queryKey: ["peaks", subtitleId, media.data?.track],
    enabled: Boolean(media.data?.ready),
    staleTime: Infinity,
    queryFn: async () => {
      const answer = await fetch(mediaUrl(`/api/subtitles/${subtitleId}/audio/${media.data!.track}/peaks`), { credentials: "same-origin" })
      if (!answer.ok) throw new ApiError(t("The server answered {status}.", { status: answer.status }), "error", answer.status)
      return Float32Array.from(new Uint8Array(await answer.arrayBuffer()), (value) => value / 255)
    },
  })
  const duration = media.data?.duration ?? fallbackDuration
  const windowSeconds = media.data?.window_seconds ?? null
  const soundTrack = media.data?.track
  const sound = useMemo(() => (player && soundTrack !== undefined ? { element: player, url: mediaUrl(`/api/subtitles/${subtitleId}/audio/${soundTrack}`) } : null), [player, soundTrack, subtitleId])

  const seek = (seconds: number) => {
    if (audio.current) audio.current.currentTime = Math.max(0, Math.min(duration, seconds))
    setTime(Math.max(0, Math.min(duration, seconds)))
  }
  const start = () => audio.current?.play().catch((error: unknown) => {
    if (!(error instanceof DOMException && error.name === "AbortError")) toast.error(t("The sound could not be played."), { description: String(error) })
  })
  const play = () => (audio.current?.paused ? start() : audio.current?.pause())
  const audioProps = peaks.data && media.data
    ? {
      ref: (element: HTMLAudioElement | null) => {
        audio.current = element
        setPlayer(element)
      },
      src: mediaUrl(`/api/subtitles/${subtitleId}/audio/${media.data.track}`),
      onLoadedMetadata: (event: SyntheticEvent<HTMLAudioElement>) => { event.currentTarget.currentTime = time },
      onPlay: () => setPlaying(true),
      onPause: () => setPlaying(false),
      onEnded: () => setPlaying(false),
      onSeeked: (event: SyntheticEvent<HTMLAudioElement>) => setTime(event.currentTarget.currentTime),
    }
    : null

  useEffect(() => {
    if (audio.current) audio.current.playbackRate = rate
  }, [rate, peaks.data])

  useEffect(() => {
    if (!playing) return
    let frame = 0
    const follow = () => {
      if (audio.current) setTime(audio.current.currentTime)
      frame = requestAnimationFrame(follow)
    }
    frame = requestAnimationFrame(follow)
    return () => cancelAnimationFrame(frame)
  }, [playing])

  return { media, peaks, time, playing, rate, setRate, setTrack, duration, sound, seek, start, play, audioProps, windowSeconds }
}
