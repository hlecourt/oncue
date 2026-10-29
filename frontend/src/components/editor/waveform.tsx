// SPDX-License-Identifier: GPL-3.0-or-later
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react"
import WaveSurfer from "wavesurfer.js"
import Minimap from "wavesurfer.js/plugins/minimap"
import Regions, { type Region } from "wavesurfer.js/plugins/regions"
import Timeline from "wavesurfer.js/plugins/timeline"

export type Timing = { start: number; end: number; text: string; moved: boolean; selected: boolean }
export type WaveformHandle = { play: () => Promise<void>; pause: () => void; seek: (seconds: number) => void; playing: () => boolean }

type Props = {
  audioUrl: string
  peaks: Float32Array
  duration: number
  lines: Timing[]
  reference: { start: number; end: number; text: string }[]
  zoom: number
  rate: number
  onTime: (seconds: number) => void
  onPlaying: (playing: boolean) => void
  onLineDragged: (index: number, newStart: number) => void
  onLineClicked: (index: number, extend: boolean) => void
}

const LINE_PREFIX = "line-"

export const Waveform = forwardRef<WaveformHandle, Props>(function Waveform(props, handle) {
  const container = useRef<HTMLDivElement>(null)
  const surfer = useRef<WaveSurfer | null>(null)
  const regions = useRef<ReturnType<typeof Regions.create> | null>(null)
  const latest = useRef(props)
  const [ready, setReady] = useState(false)
  latest.current = props

  useImperativeHandle(handle, () => ({
    play: async () => {
      await surfer.current?.play()
    },
    pause: () => surfer.current?.pause(),
    seek: (seconds) => surfer.current?.setTime(Math.max(0, seconds)),
    playing: () => surfer.current?.isPlaying() ?? false,
  }))

  useEffect(() => {
    const styles = getComputedStyle(document.documentElement)
    const color = (name: string) => styles.getPropertyValue(name).trim()
    const plugin = Regions.create()
    const wave = WaveSurfer.create({
      container: container.current!, url: props.audioUrl, peaks: [props.peaks], duration: props.duration, height: 150, minPxPerSec: props.zoom,
      waveColor: color("--wave"), progressColor: color("--wave"), cursorColor: color("--foreground"), cursorWidth: 2, autoScroll: true, autoCenter: true,
      dragToSeek: false, plugins: [plugin, Timeline.create({ height: 16, style: { fontSize: "10px", color: color("--muted-foreground") } }),
        Minimap.create({ height: 24, waveColor: color("--muted-foreground"), progressColor: color("--muted-foreground"), overlayColor: color("--accent") })],
    })
    wave.on("ready", () => {
      const scroller = wave.getWrapper().parentElement
      scroller?.setAttribute("tabindex", "0")
      scroller?.setAttribute("aria-label", "Waveform of the sound with the lines of the subtitle")
      setReady(true)
    })
    wave.on("timeupdate", (seconds) => latest.current.onTime(seconds))
    wave.on("play", () => latest.current.onPlaying(true))
    wave.on("pause", () => latest.current.onPlaying(false))
    plugin.on("region-updated", (region: Region) => {
      if (region.id.startsWith(LINE_PREFIX)) latest.current.onLineDragged(Number(region.id.slice(LINE_PREFIX.length)), region.start)
    })
    plugin.on("region-clicked", (region: Region, event: MouseEvent) => {
      if (!region.id.startsWith(LINE_PREFIX)) return
      event.stopPropagation()
      latest.current.onLineClicked(Number(region.id.slice(LINE_PREFIX.length)), event.shiftKey)
    })
    surfer.current = wave
    regions.current = plugin
    return () => {
      setReady(false)
      wave.destroy()
      surfer.current = null
      regions.current = null
    }
  }, [props.audioUrl, props.peaks, props.duration])

  useEffect(() => {
    if (ready) surfer.current?.zoom(props.zoom)
  }, [props.zoom, ready])

  useEffect(() => {
    if (ready) surfer.current?.setPlaybackRate(props.rate)
  }, [props.rate, ready])

  useEffect(() => {
    const plugin = regions.current
    if (!plugin || !ready) return
    const styles = getComputedStyle(document.documentElement)
    const color = (name: string) => styles.getPropertyValue(name).trim()
    plugin.clearRegions()
    for (const line of props.reference) {
      const region = plugin.addRegion({ start: line.start, end: line.end, drag: false, resize: false, color: color("--reference"), content: line.text })
      Object.assign(region.element!.style, { top: "70%", height: "26%", fontSize: "11px", color: color("--muted-foreground"), overflow: "hidden" })
    }
    props.lines.forEach((line, index) => {
      const region = plugin.addRegion({ id: `${LINE_PREFIX}${index}`, start: line.start, end: line.end, drag: true, resize: false,
        color: color(line.moved ? "--cue-moved" : "--cue"), content: line.text })
      Object.assign(region.element!.style, { top: "4%", height: "60%", fontSize: "12px", overflow: "hidden", cursor: "grab",
        borderLeft: `2px solid ${color(line.moved ? "--cue-moved-edge" : "--cue-edge")}`, outline: line.selected ? `1px solid ${color("--foreground")}` : "none" })
      region.element!.setAttribute("aria-label", `Line ${index + 1}: ${line.text}`)
    })
  }, [props.lines, props.reference, ready])

  return <div ref={container} className="w-full" />
})
