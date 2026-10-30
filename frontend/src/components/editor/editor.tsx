// SPDX-License-Identifier: GPL-3.0-or-later
import { Link } from "@tanstack/react-router"
import { ChevronLeftIcon, ChevronsUpDownIcon, Loader2Icon, MoreHorizontalIcon, PauseIcon, PlayIcon } from "lucide-react"
import { type MouseEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react"

import { Preview } from "@/components/editor/preview"
import { ToneDot, toneOf } from "@/components/status"
import { SidePanel } from "@/components/editor/side-panel"
import { type Band, type Block, Timeline } from "@/components/editor/timeline"
import { useChanges } from "@/components/editor/use-changes"
import { usePlayback } from "@/components/editor/use-playback"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { Schemas } from "@/lib/api/client"
import { clock, languageName } from "@/lib/format"
import { type Locale, useLocale, useT } from "@/lib/i18n"
import type { Change } from "@/lib/retime"

type Detail = Schemas["SubtitleDetail"]
type Lines = Schemas["Lines"]

const SPANS = [10, 24, 60, 180]
const RATES = [0.75, 1, 1.25]
const NUDGES = [-1, -0.1, 0.1, 1]
const HEAR_BEFORE_SECONDS = 3
const VIDEO_TEXT_SECONDS = 1.5
const SKIP_SECONDS = 5
const KEYS: [string, string][] = [["Space", "Play or pause"], ["A", "The chosen line starts at the playhead"], ["R", "Hear 3 s before the line"],
  ["Left / Right", "0.1 s earlier or later, Shift for 1 s"], ["J / K, Down / Up", "Next or previous line"], ["1 / 2 / 3", "All lines, from this line, selected lines"],
  ["Ctrl Z", "Undo the last change"], ["Ctrl S", "Save"], ["N", "Next in Review"], ["?", "This help"]]

type Props = {
  detail: Detail; lines: Lines; actions: ReactNode; menuItems: ReactNode; notice: ReactNode
  onNext: () => void; onRestore: (version: Schemas["Version"]) => void; onEditing: (editing: boolean) => void
}

export function Editor({ detail, lines, actions, menuItems, notice, onNext, onRestore, onEditing }: Props) {
  const t = useT()
  const locale = useLocale()
  const wide = useWide()
  const [current, setCurrent] = useState(0)
  const [selection, setSelection] = useState<[number, number] | null>(null)
  const [span, setSpan] = useState(SPANS[1]!)
  const [tab, setTab] = useState("lines")
  const [helping, setHelping] = useState(false)
  const { hasGuardFix, guardOn, setGuardOn, scope, setScope, pickScope, shown, pending, held, moves, changeCount, negative, move, undo, save, blocker } =
    useChanges({ detail, lines, current, selection, onNext })
  const { media, peaks, time, playing, rate, setRate, setTrack, duration, sound, seek, start, play, audioProps } = usePlayback(detail.id, shown.at(-1)?.end ?? 0)

  const videoText = useMemo(() => shown.map((line) => nearestText(lines.reference, line.start)), [shown, lines.reference])
  const bands = useMemo(() => overviewBands(shown, pending, guardOn && hasGuardFix ? detail.parts : []), [shown, pending, guardOn, hasGuardFix, detail.parts])
  const playingLine = useMemo(() => shown.findIndex((line) => line.start <= time && time < line.end), [shown, time])
  const nearest = useMemo(() => nearestLine(shown, time), [shown, time])
  const unsaved = changeCount > 1 ? t("{count} changes, not saved", { count: changeCount }) : t("{count} change, not saved", { count: changeCount })
  const chosen = shown[current]
  const delta = chosen ? Math.round((time - chosen.start) * 100) / 100 : 0

  const pick = (index: number, extend: boolean) => {
    const ranged = extend || (touchScreen() && scope === "selected" && selection?.[0] === selection?.[1])
    setSelection(ranged ? [Math.min(current, index), Math.max(current, index)] : [index, index])
    if (ranged) setScope("selected")
    else setCurrent(index)
    seek(shown[index]!.start)
  }
  const anchor = () => {
    if (!chosen) return
    move(delta)
    seek(chosen.start + delta)
  }
  const hear = () => {
    if (!chosen) return
    seek(chosen.start - HEAR_BEFORE_SECONDS)
    start()
  }
  const step = (by: number) => {
    const next = Math.max(0, Math.min(shown.length - 1, current + by))
    setCurrent(next)
    setSelection([next, next])
    seek(shown[next]!.start)
  }
  const choosePart = (part: Schemas["Part"]) => {
    const first = lines.lines.findIndex((line) => line.start >= part.first - 0.01)
    const last = lines.lines.findLastIndex((line) => line.start <= part.last + 0.01)
    if (first < 0 || last < first) return
    setCurrent(first)
    setSelection([first, last])
    setScope("selected")
    seek(shown[first]!.start)
  }
  const saveAndLeave = (event: MouseEvent) => {
    event.preventDefault()
    save.mutate(undefined, { onSuccess: () => blocker.proceed?.(), onError: () => blocker.reset?.() })
  }
  const discardAndLeave = (event: MouseEvent) => {
    event.preventDefault()
    blocker.proceed?.()
  }
  const soundNote = () => {
    if (media.error || peaks.error) {
      return (
        <span className="pointer-events-auto flex items-center gap-2">
          {t("The sound could not be loaded.")}
          <Button size="sm" variant="outline" onClick={() => (media.error ? media.refetch() : peaks.refetch())}>{t("Try again")}</Button>
        </span>
      )
    }
    if (media.data && !media.data.tracks.length) return t("This video has no sound to listen to.")
    return <><Loader2Icon className="mr-2 size-4 animate-spin" />{t("Preparing the sound for listening. It takes a moment the first time only.")}</>
  }
  const keys = useRef<(event: KeyboardEvent) => void>(() => {})
  keys.current = (event) => {
    if (event.target instanceof HTMLElement && event.target.closest("input, textarea, select, [contenteditable], [role=dialog], [role=menu], [role=listbox]")) return
    const command = event.ctrlKey || event.metaKey
    const actions: Record<string, () => void> = {
      " ": play, a: anchor, r: hear, j: () => step(1), k: () => step(-1), ArrowDown: () => step(1), ArrowUp: () => step(-1), n: onNext, "?": () => setHelping((value) => !value),
      "1": () => setScope("all"), "2": () => setScope("from"), "3": () => setScope("selected"),
      ArrowLeft: () => move(event.shiftKey ? -1 : -0.1), ArrowRight: () => move(event.shiftKey ? 1 : 0.1),
    }
    if (command && event.key.toLowerCase() === "z") actions.undo = undo
    if (command && event.key.toLowerCase() === "s") actions.save = () => changeCount > 0 && !negative && save.mutate()
    const name = command ? { z: "undo", s: "save" }[event.key.toLowerCase()] : event.key.length === 1 ? event.key.toLowerCase() : event.key
    const action = event.altKey || !name ? undefined : actions[name]
    if (!action) return
    event.preventDefault()
    action()
  }

  useEffect(() => {
    const listener = (event: KeyboardEvent) => keys.current(event)
    window.addEventListener("keydown", listener)
    return () => window.removeEventListener("keydown", listener)
  }, [])

  const editing = pending.length > 0
  useEffect(() => onEditing(editing), [editing, onEditing])

  useEffect(() => {
    if (!playing || nearest < 0 || (selection && selection[0] !== selection[1])) return
    setCurrent(nearest)
    setSelection(null)
  }, [playing, nearest, selection])

  const saveButton = (
    <Button size="sm" disabled={!changeCount || negative || save.isPending} onClick={() => save.mutate()}>
      {save.isPending && <Loader2Icon className="animate-spin" />}{t("Save")}<kbd className="hidden rounded border border-current/30 px-1 font-mono text-[10px] opacity-80 lg:inline">Ctrl S</kbd>
    </Button>
  )

  return (
    <div className="flex h-svh min-h-0 flex-col bg-background">
      <header className="flex h-12 flex-none items-center gap-2 border-b px-2">
        <SidebarTrigger className="lg:hidden" />
        <Button asChild size="icon" variant="ghost" aria-label={t("Back to Review")}><Link to="/"><ChevronLeftIcon /></Link></Button>
        <div className="flex min-w-0 items-baseline gap-2.5">
          {detail.siblings.length === 0 && <h1 className="truncate text-sm font-semibold">{detail.name}</h1>}
          {detail.siblings.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="-mx-2 min-w-0 gap-1.5 px-2" aria-label={t("{name}: other subtitles of this video", { name: detail.name })}>
                  <h1 className="truncate text-sm font-semibold">{detail.name}</h1><ChevronsUpDownIcon className="text-muted-foreground" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-80">
                <DropdownMenuLabel>{t("Other subtitles of this video")}</DropdownMenuLabel>
                {detail.siblings.map((sibling) => (
                  <DropdownMenuItem key={sibling.id} asChild>
                    <Link to="/subtitles/$subtitleId" params={{ subtitleId: sibling.id }} className="flex items-center gap-2">
                      <ToneDot tone={toneOf(sibling.tone)} /><span className="truncate">{sibling.name}</span>
                      <span className="ml-auto truncate pl-2 text-xs text-muted-foreground">{sibling.words}</span>
                    </Link>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          <span className="hidden truncate text-[13px] text-muted-foreground xl:inline">{detail.folder}</span>
        </div>
        {hasGuardFix ? <span className="hidden flex-none rounded-full bg-guard-soft px-2 py-px text-xs font-medium text-guard-ink md:inline">{t("Fix found, confidence {confidence}", { confidence: detail.proposal?.confidence ?? "" })}</span>
          : <span className="hidden flex-none items-center gap-1.5 rounded-full bg-muted px-2 py-px text-xs font-medium md:inline-flex"><ToneDot tone={toneOf(detail.tone)} />{detail.words}</span>}
        {changeCount > 0 && (
          <span className="hidden flex-none items-center gap-1.5 text-xs font-medium text-unsaved-ink sm:inline-flex">
            <i className="size-1.5 rounded-full bg-unsaved shadow-[0_0_0_3px_var(--unsaved-soft)]" />{unsaved}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          {actions}
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label={t("More actions")}><MoreHorizontalIcon /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              <DropdownMenuGroup className="lg:hidden">
                {media.data && media.data.tracks.length > 1 && (
                  <>
                    <DropdownMenuLabel>{t("Sound track")}</DropdownMenuLabel>
                    <DropdownMenuRadioGroup value={String(media.data.track)} onValueChange={(value) => setTrack(Number(value))}>
                      {media.data.tracks.map((item) => <DropdownMenuRadioItem key={item.index} value={String(item.index)}>{[item.language || t("Unknown"), item.title, item.commentary ? t("commentary") : ""].filter(Boolean).join(", ")}</DropdownMenuRadioItem>)}
                    </DropdownMenuRadioGroup>
                  </>
                )}
                <DropdownMenuLabel>{t("Speed")}</DropdownMenuLabel>
                <DropdownMenuRadioGroup value={String(rate)} onValueChange={(value) => setRate(Number(value))}>
                  {RATES.map((value) => <DropdownMenuRadioItem key={value} value={String(value)}>{value}x</DropdownMenuRadioItem>)}
                </DropdownMenuRadioGroup>
                <DropdownMenuLabel>{t("Zoom")}</DropdownMenuLabel>
                <DropdownMenuRadioGroup value={String(span)} onValueChange={(value) => setSpan(Number(value))}>
                  {SPANS.map((value) => <DropdownMenuRadioItem key={value} value={String(value)}>{value < 60 ? `${value} s` : `${value / 60} min`}</DropdownMenuRadioItem>)}
                </DropdownMenuRadioGroup>
                <DropdownMenuSeparator />
              </DropdownMenuGroup>
              {menuItems}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button size="sm" variant="outline" className="hidden sm:inline-flex" onClick={onNext}>{t("Next")}<kbd className="hidden rounded border px-1 font-mono text-[10px] text-muted-foreground lg:inline">N</kbd></Button>
          <span className="hidden lg:contents">{saveButton}</span>
        </div>
      </header>
      {notice}

      <div className="min-h-0 flex-1 overflow-y-auto lg:grid lg:grid-cols-[minmax(0,1fr)_320px] lg:grid-rows-[minmax(0,1fr)_auto_auto] lg:overflow-hidden lg:[grid-template-areas:'stage_panel''toolbar_panel''timeline_timeline']">
        <div className="sticky top-0 z-10 border-b bg-background lg:contents">
          <div className="flex min-h-0 flex-col items-center justify-center gap-2 bg-stage lg:[grid-area:stage] lg:px-4 lg:pt-4 lg:pb-2">
            <Preview subtitleId={detail.id} time={time} playing={playing} rate={rate} caption={playingLine >= 0 ? shown[playingLine]!.text : ""}
              className="rounded-none lg:h-[calc(100%-3rem)] lg:w-auto lg:max-w-full lg:rounded-md" />
            <div className="grid w-full grid-cols-[1fr_auto_1fr] items-center px-3 py-1.5 lg:px-0 lg:py-0">
              <span className="font-mono text-[13px] whitespace-nowrap tabular-nums">{clock(time, 2)} <span className="text-muted-foreground">/ {clock(duration, 0)}</span></span>
              <div className="flex items-center gap-1">
                <Button variant="outline" size="sm" onClick={() => seek(time - SKIP_SECONDS)}>-5 s</Button>
                <Button size="icon" className="size-10 rounded-full" aria-label={playing ? t("Pause") : t("Play")} disabled={!peaks.data} onClick={play}>{playing ? <PauseIcon /> : <PlayIcon />}</Button>
                <Button variant="outline" size="sm" onClick={() => seek(time + SKIP_SECONDS)}>+5 s</Button>
              </div>
              <div className="hidden items-center justify-self-end gap-1 text-[13px] text-muted-foreground lg:flex">
                {media.data && media.data.tracks.length > 1 && (
                  <Select value={String(media.data.track)} onValueChange={(value) => setTrack(Number(value))}>
                    <SelectTrigger size="sm" className="w-40" aria-label={t("Sound track")}><SelectValue /></SelectTrigger>
                    <SelectContent>{media.data.tracks.map((item) => <SelectItem key={item.index} value={String(item.index)}>{[item.language || t("Unknown"), item.title, item.commentary ? t("commentary") : ""].filter(Boolean).join(", ")}</SelectItem>)}</SelectContent>
                  </Select>
                )}
                <span>{t("Speed")}</span>
                <Select value={String(rate)} onValueChange={(value) => setRate(Number(value))}>
                  <SelectTrigger size="sm" className="w-[4.5rem]" aria-label={t("Speed")}><SelectValue /></SelectTrigger>
                  <SelectContent>{RATES.map((value) => <SelectItem key={value} value={String(value)}>{value}x</SelectItem>)}</SelectContent>
                </Select>
                <span>{t("Zoom")}</span>
                <Select value={String(span)} onValueChange={(value) => setSpan(Number(value))}>
                  <SelectTrigger size="sm" className="w-[5.5rem]" aria-label={t("Zoom")}><SelectValue /></SelectTrigger>
                  <SelectContent>{SPANS.map((value) => <SelectItem key={value} value={String(value)}>{value < 60 ? `${value} s` : `${value / 60} min`}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <div className="lg:border-t lg:pt-1 lg:[grid-area:timeline]">
            <Timeline duration={duration} peaks={peaks.data ?? null} media={sound} soundNote={soundNote()}
              lines={shown} held={held} before={lines.lines[current] ?? null} reference={lines.reference} current={current} selection={selection} time={time} span={span}
              bands={bands} labels={trackLabels(detail, lines, t, locale)} compact={!wide} onSeek={seek} onPick={pick}
              onDrag={(index, newStart) => {
                const insideSelection = selection !== null && selection[0] !== selection[1] && index >= selection[0] && index <= selection[1]
                setCurrent(index)
                move(newStart - shown[index]!.start, insideSelection ? selection : [index, index])
              }} />
            <div className="h-2.5 lg:h-2" />
          </div>
        </div>

        <section aria-label={t("Fix the timing")} className="flex flex-wrap items-center gap-x-3 gap-y-2.5 border-b p-3 text-[13px] lg:justify-between lg:border-t lg:border-b-0 lg:px-4 lg:py-2.5 lg:[grid-area:toolbar]">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-2 whitespace-nowrap">
            <span className="font-semibold">{t("Line {number}", { number: current + 1 })}</span>
            <span className="text-muted-foreground">{t("starts")} <span className="font-mono text-foreground tabular-nums">{chosen ? clock(chosen.start, 2) : "-"}</span></span>
            <span className="text-muted-foreground">{t("playhead")} <span className="font-mono text-foreground tabular-nums">{clock(time, 2)}</span></span>
            <strong className="min-w-[4.5rem] font-mono text-lg font-semibold tracking-tight text-ours-ink tabular-nums">{delta > 0 ? "+" : ""}{delta.toFixed(2)} s</strong>
            <Button size="sm" variant="outline" className="border-ours-line bg-ours-soft text-ours-ink hover:bg-ours-soft/80 hover:text-ours-ink max-lg:flex-1" onClick={anchor} disabled={!chosen}>
              {t("It starts here")}<Key>A</Key>
            </Button>
            <Button size="sm" variant="outline" onClick={hear} disabled={!chosen || !peaks.data}>{t("Hear it")}<Key>R</Key></Button>
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 lg:ml-auto lg:w-auto lg:flex-nowrap">
            <ToggleGroup type="single" value={scope} onValueChange={pickScope} aria-label={t("Lines to move")}
              className="rounded-lg bg-muted p-0.5 max-lg:w-full">
              <ToggleGroupItem value="all" className="h-auto min-h-7 rounded-md px-2.5 py-1 text-center leading-tight whitespace-normal data-[state=on]:bg-background data-[state=on]:shadow-xs max-lg:flex-1">{t("All lines")}</ToggleGroupItem>
              <ToggleGroupItem value="from" className="h-auto min-h-7 rounded-md px-2.5 py-1 text-center leading-tight whitespace-normal data-[state=on]:bg-background data-[state=on]:shadow-xs max-lg:flex-1">{t("From line {number}", { number: current + 1 })}</ToggleGroupItem>
              <ToggleGroupItem value="selected" className="h-auto min-h-7 rounded-md px-2.5 py-1 text-center leading-tight whitespace-normal data-[state=on]:bg-background data-[state=on]:shadow-xs max-lg:flex-1">
                {selection && selection[0] !== selection[1] ? t("Lines {first} to {last}", { first: selection[0] + 1, last: selection[1] + 1 }) : t("Selected")}
              </ToggleGroupItem>
            </ToggleGroup>
            <div className="flex max-lg:w-full" role="group" aria-label={t("Move by")}>
              {NUDGES.map((amount, index) => (
                <Button key={amount} size="sm" variant="outline" title={nudgeTitle(amount, t)} onClick={() => move(amount)}
                  className={`min-w-12 rounded-none max-lg:flex-1 ${index ? "-ml-px" : "rounded-l-md"} ${index === NUDGES.length - 1 ? "rounded-r-md" : ""}`}>
                  {amount > 0 ? "+" : ""}{Math.abs(amount) === 1 ? `${amount} s` : amount}
                </Button>
              ))}
            </div>
          </div>
          {negative && <p className="w-full text-xs text-destructive">{t("Some lines would start before the video begins, so this cannot be saved. Undo the last change.")}</p>}
        </section>

        <aside className="flex min-h-[420px] flex-col lg:min-h-0 lg:border-l lg:[grid-area:panel]" aria-label={t("Lines and changes")}>
          <SidePanel tab={tab} onTab={setTab} lines={shown} videoText={videoText} moves={moves} held={held} current={current} selection={selection}
            onPick={pick} changeCount={changeCount} changes={pending}
            guard={hasGuardFix ? { confidence: detail.proposal?.confidence ?? null, parts: detail.parts, on: guardOn } : null} onGuard={setGuardOn} onPart={choosePart}
            onRemove={(change) => move(-change.offset, [change.first, change.last])} subtitleId={detail.id} versions={detail.versions} onRestore={onRestore} />
        </aside>
      </div>

      <div className="flex flex-none items-center justify-between border-t px-3 py-2 lg:hidden">
        <span className="text-xs font-medium text-unsaved-ink">{changeCount ? unsaved : t("Nothing to save")}</span>
        {saveButton}
      </div>

      {audioProps && <audio {...audioProps} preload="auto" className="hidden" />}

      <AlertDialog open={blocker.status === "blocked"} onOpenChange={(open) => !open && blocker.reset?.()}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("Leave without saving?")}</AlertDialogTitle>
            <AlertDialogDescription>{t("Your changes to the timing are not saved yet.")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("Cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="outline" onClick={discardAndLeave}>{t("Discard")}</AlertDialogAction>
            <AlertDialogAction disabled={negative || save.isPending} onClick={saveAndLeave}>{t("Save")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={helping} onOpenChange={setHelping}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>{t("Keyboard")}</DialogTitle><DialogDescription>{t("Everything on this screen also works with the mouse or a finger.")}</DialogDescription></DialogHeader>
          <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2 text-sm">
            {KEYS.map(([key, meaning]) => <div key={key} className="contents"><dt className="font-mono text-xs leading-5">{t(key)}</dt><dd>{t(meaning)}</dd></div>)}
          </dl>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function EditorSkeleton() {
  return <Skeleton className="h-svh w-full rounded-none" />
}

function Key({ children }: { children: ReactNode }) {
  return <kbd className="hidden rounded border border-current/25 px-1 font-mono text-[10px] opacity-75 lg:inline">{children}</kbd>
}

function useWide() {
  const query = "(min-width: 1024px)"
  const [wide, setWide] = useState(() => matchMedia(query).matches)
  useEffect(() => {
    const list = matchMedia(query)
    const changed = () => setWide(list.matches)
    list.addEventListener("change", changed)
    return () => list.removeEventListener("change", changed)
  }, [])
  return wide
}

function touchScreen() {
  return matchMedia("(pointer: coarse)").matches
}

function nudgeTitle(amount: number, t: ReturnType<typeof useT>) {
  if (Math.abs(amount) === 1) return amount < 0 ? t("Left with Shift") : t("Right with Shift")
  return amount < 0 ? t("Left") : t("Right")
}

function trackLabels(detail: Detail, lines: Lines, t: ReturnType<typeof useT>, locale: Locale) {
  const ours = languageName(detail.name.split(".").at(-2) ?? "", locale)
  const theirs = languageName((detail.reference ?? "").split("-")[0] ?? "", locale)
  return {
    ours: t("Your subtitle"), oursDetail: ours || t("the file"),
    theirs: t("Video's subtitle"), theirsDetail: lines.reference.length ? t("{language}, the reference", { language: theirs || t("in the video") }) : t("none, judge by the sound"),
  }
}

function overviewBands(shown: Block[], pending: Change[], guardParts: Schemas["Part"][]): Band[] {
  const guard = guardParts.map((part) => ({ start: part.first + part.shift, end: part.last + part.shift, tone: "guard" as const }))
  return [...guard, ...pending.map((change) => ({ start: shown[change.first]!.start, end: shown[change.last]!.end, tone: "ours" as const }))]
}

function nearestLine(shown: Block[], time: number) {
  let low = 0
  let high = shown.length
  while (low < high) {
    const middle = (low + high) >> 1
    if (shown[middle]!.start < time) low = middle + 1
    else high = middle
  }
  const candidates = [low - 1, low].filter((index) => index >= 0 && index < shown.length)
  return candidates.sort((first, second) => Math.abs(shown[first]!.start - time) - Math.abs(shown[second]!.start - time))[0] ?? -1
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
