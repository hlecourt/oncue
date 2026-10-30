// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from "@tanstack/react-query"
import { ChartNoAxesColumnIcon } from "lucide-react"
import { type FocusEvent, type KeyboardEvent, type PointerEvent, type ReactNode, useState } from "react"

import { EmptyState, LoadError, PageHeader } from "@/components/status"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { REPORT_MODE, useSystem } from "@/hooks/use-system"
import { api, type Schemas, unwrap } from "@/lib/api/client"
import { bytes, languageName, when } from "@/lib/format"
import { LOCALES, useLocale, useT } from "@/lib/i18n"
import { cn } from "cn"

type Stats = Schemas["StatsView"]
type Translate = ReturnType<typeof useT>

const RANGES = [{ value: "30", label: "30 days", days: 30 }, { value: "182", label: "6 months", days: 182 }, { value: "all", label: "All time", days: undefined }] as const
const AUTOMATIC_FROM = 80
const CONFIDENCE_STEP = 5
const CHECK_TIME_STEP = 0.5
const REFRESH_MS = 15_000
const HEAT = ["bg-[var(--heat-0)]", "bg-[var(--heat-1)]", "bg-[var(--heat-2)]", "bg-[var(--heat-3)]", "bg-[var(--heat-4)]"]
const VERDICTS = [
  { key: "in_sync", label: "in sync", tone: "bg-in-sync" },
  { key: "sure_fix", label: "sure fix", tone: "bg-guard" },
  { key: "review", label: "Review", tone: "bg-to-review" },
  { key: "rejected", label: "rejected", tone: "bg-rejected" },
] as const
const REFERENCES: Record<string, { label: string; tone: string }> = {
  text: { label: "Text track", tone: "bg-bar" }, picture: { label: "Picture track", tone: "bg-bar-soft" }, sound: { label: "Sound", tone: "bg-bar-faint" },
  none: { label: "None", tone: "bg-muted" },
}
const FOCUS_RING = "focus-visible:outline-2 focus-visible:outline-solid focus-visible:-outline-offset-2 focus-visible:outline-foreground"
const CHART_KEYS: Record<string, (rows: number) => number> = {
  ArrowRight: (rows) => rows, ArrowLeft: (rows) => -rows, ArrowDown: () => 1, ArrowUp: () => -1, Home: () => -Infinity, End: () => Infinity,
}
const CARD = "min-w-0 rounded-xl border bg-card px-4 pt-3.5 pb-4"

export function DashboardPage() {
  const t = useT()
  const [range, setRange] = useState<(typeof RANGES)[number]["value"]>("182")
  const [tip, setTip] = useState<{ text: string; x: number; y: number } | null>(null)
  const days = RANGES.find((entry) => entry.value === range)?.days
  const system = useSystem()
  const stats = useQuery({
    queryKey: ["stats", range],
    queryFn: () => unwrap(api.GET("/api/stats", { params: { query: { days } } })),
    refetchInterval: REFRESH_MS,
  })

  function pickRange(value: string) {
    const picked = RANGES.find((entry) => entry.value === value)
    if (picked) setRange(picked.value)
  }

  function followTip(event: PointerEvent) {
    const target = event.target instanceof Element ? event.target.closest("[role=img][aria-label]") : null
    setTip(target ? { text: target.getAttribute("aria-label") ?? "", x: event.clientX, y: event.clientY } : null)
  }

  function showFocusedTip(event: FocusEvent) {
    if (!(event.target instanceof HTMLElement) || !event.target.matches("[data-chart] [role=img]")) return
    const box = event.target.getBoundingClientRect()
    setTip({ text: event.target.getAttribute("aria-label") ?? "", x: box.left + box.width / 2, y: box.top })
  }

  const rangePicker = (
    <ToggleGroup type="single" value={range} onValueChange={(value) => pickRange(value)} aria-label={t("Period")} className="rounded-lg bg-muted p-0.5">
      {RANGES.map((entry) => (
        <ToggleGroupItem key={entry.value} value={entry.value} className="h-7 rounded-md px-2.5 data-[state=on]:bg-background data-[state=on]:shadow-xs">{t(entry.label)}</ToggleGroupItem>
      ))}
    </ToggleGroup>
  )

  return (
    <div onPointerMove={followTip} onPointerLeave={() => setTip(null)} onFocus={showFocusedTip} onBlur={() => setTip(null)} onKeyDown={moveInChart}>
      <PageHeader title={t("Dashboard")} actions={rangePicker} />
      {stats.isPending && <Skeleton className="h-96 w-full" />}
      {stats.error && <LoadError error={stats.error} onRetry={() => stats.refetch()} />}
      {stats.data && stats.data.checks === 0 && <EmptyState icon={<ChartNoAxesColumnIcon />} title={t("No check in this period")}>{t("Scan the library from the System page.")}</EmptyState>}
      {stats.data && stats.data.checks > 0 && <Board stats={stats.data} reportMode={system.data?.mode === REPORT_MODE} t={t} />}
      {tip && <div role="tooltip" className="pointer-events-none fixed z-50 rounded-md bg-foreground px-2 py-1 text-xs whitespace-nowrap text-background" style={{ left: tip.x + 12, top: tip.y - 32 }}>{tip.text}</div>}
    </div>
  )
}

function Board({ stats, reportMode, t }: { stats: Stats; reportMode: boolean; t: Translate }) {
  const locale = useLocale()
  const tag = LOCALES.find((entry) => entry.value === locale)?.tag ?? "en"
  const number = (value: number) => value.toLocaleString(tag)
  const share = (part: number, whole: number) => (whole ? `${Math.round((part / whole) * 100)}%` : "0%")

  return (
    <div className="flex flex-col gap-3">
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Hero label={t("Subtitles checked")} value={number(stats.subtitles)} detail={plural(t, stats.checks, "{count} check", "{count} checks", number)} spark={stats.weekly} />
        {reportMode
          ? <Hero label={t("Sure fixes found")} tone="bg-guard" value={number(stats.found_not_written)} detail={t("Report mode: not written")} />
          : <Hero label={t("Fixed on their own")} tone="bg-guard" value={number(stats.fixed_on_their_own)} detail={t("{count} found, not written", { count: number(stats.found_not_written) })} />}
        <Hero label={t("Lines realigned")} value={number(stats.lines_realigned)} detail={stats.median_offset === null ? "" : t("median offset {value}", { value: seconds(stats.median_offset) })} />
        <Hero label={t("Fixes you undid")} tone="bg-in-sync" value={number(stats.undone)} detail={t("{share} of automatic fixes", { share: share(stats.undone, stats.fixed_on_their_own) })} />
      </section>

      <section className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className={CARD}>
          <CardHead title={t("Checks per day")} detail={t("busiest: {day}", { day: busiestDay(stats.per_day, tag) })} />
          <Heatmap days={stats.per_day} tag={tag} t={t} />
        </div>
        <RightNow now={stats.now} t={t} />
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <div className={CARD}>
          <CardHead title={t("Confidence of the fixes found")} />
          <Bars rule={AUTOMATIC_FROM / 100} bars={stats.confidence.map((count, index) => ({
            count, tone: index * CONFIDENCE_STEP >= AUTOMATIC_FROM ? "bg-guard" : "bg-to-review",
            tip: t("confidence {low} to {high}: {count}", { low: index * CONFIDENCE_STEP, high: (index + 1) * CONFIDENCE_STEP, count: fixes(t, count) }),
          }))} />
          <Axis labels={["0", "50", "80", "100"]} positions={[0, 50, 80, 100]} />
          <Key items={[{ label: t("written on its own"), tone: "bg-guard" }, { label: t("Review"), tone: "bg-to-review" }]} />
        </div>
        <div className={CARD}>
          <CardHead title={t("Offsets found")} />
          <Bars bars={stats.offsets.map((entry) => ({ count: entry.count, tone: (entry.high ?? 1) <= 0 ? "bg-bar-soft" : "bg-bar", tip: `${offsetRange(t, entry)}: ${fixes(t, entry.count)}` }))} />
          <Axis labels={[t("early"), "0", t("late")]} positions={[0, 50, 100]} />
          <Key items={[{ label: t("early"), tone: "bg-bar-soft" }, { label: t("late"), tone: "bg-bar" }]} />
        </div>
      </section>

      <Providers providers={stats.providers} number={number} t={t} />

      <section className="grid gap-3 lg:grid-cols-3">
        <div className={CARD}>
          <CardHead title={t("Languages")} />
          <Meters rows={stats.languages.map((entry) => ({ label: languageName(entry.name, tag) || t("Unknown"), count: entry.count }))} tone="bg-bar" number={number} empty={t("None yet")} />
        </div>
        <div className={CARD}>
          <CardHead title={t("Compared with")} />
          <References references={stats.references} share={share} t={t} />
        </div>
        <div className={CARD}>
          <CardHead title={t("Speed fixes")} />
          <Meters rows={stats.speed_fixes.map((entry) => ({ label: entry.name, count: entry.count }))} tone="bg-guard" number={number} mono empty={t("None found")} />
        </div>
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <div className={CARD}>
          <CardHead title={t("Check time")} detail={stats.check_time.median === null ? "" : t("median {median}, p95 {p95}", { median: seconds(stats.check_time.median), p95: seconds(stats.check_time.p95 ?? 0) })} />
          {stats.check_time.median === null
            ? <p className="py-10 text-center text-sm text-muted-foreground">{t("Measured from this version on")}</p>
            : <Bars height="h-28" bars={stats.check_time.bins.map((count, index) => ({
              count, tone: "bg-bar", tip: `${checkTimeRange(t, index, stats.check_time.bins.length)}: ${plural(t, count, "{count} check", "{count} checks", number)}`,
            }))} />}
          <Axis labels={["0 s", `${(stats.check_time.bins.length * CHECK_TIME_STEP) / 2} s`, `${stats.check_time.bins.length * CHECK_TIME_STEP} s`]} positions={[0, 50, 100]} />
        </div>
        <div className={CARD}>
          <CardHead title={t("Records")} />
          <Facts rows={[
            [t("Largest offset fixed"), stats.records.largest_offset === null ? "-" : seconds(stats.records.largest_offset)],
            [t("Most parts in one fix"), number(stats.records.most_parts)],
            [t("Fastest check"), stats.records.fastest_check === null ? "-" : seconds(stats.records.fastest_check)],
            [t("Replacements asked"), stats.records.replacements_checked
              ? t("{count}, {share} in sync", { count: number(stats.records.replacements), share: share(stats.records.replacements_in_sync, stats.records.replacements_checked) })
              : number(stats.records.replacements)],
            [t("Edits by hand"), number(stats.records.edits_by_hand)],
          ]} />
        </div>
      </section>
    </div>
  )
}

function Hero({ label, value, detail, tone, spark }: { label: string; value: string; detail: string; tone?: string; spark?: number[] }) {
  return (
    <div className={cn(CARD, "relative flex flex-col gap-1 overflow-hidden pb-3.5")}>
      <span className="text-sm text-muted-foreground">{tone && <i className={cn("mr-1.5 inline-block size-2 rounded-full align-middle", tone)} />}{label}</span>
      <span className="mt-auto font-mono text-3xl font-semibold tracking-tight tabular-nums">{value}</span>
      <span className="line-clamp-2 min-h-8 text-xs text-muted-foreground lg:min-h-0">{detail || " "}</span>
      {spark && <Sparkline values={spark} />}
    </div>
  )
}

function Sparkline({ values }: { values: number[] }) {
  const top = Math.max(...values, 1)
  const points = values.map((value, index) => `${(index / Math.max(values.length - 1, 1)) * 120},${28 - (value / top) * 26}`).join(" ")
  return (
    <svg viewBox="0 0 120 30" preserveAspectRatio="none" aria-hidden="true" className="absolute right-4 bottom-4 hidden h-7 w-28 xl:block">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" className="opacity-70" />
    </svg>
  )
}

function CardHead({ title, detail }: { title: string; detail?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
      <h2 className="truncate text-sm font-semibold">{title}</h2>
      {detail && <span className="truncate font-mono text-xs text-muted-foreground">{detail}</span>}
    </div>
  )
}

function Heatmap({ days, tag, t }: { days: Stats["per_day"]; tag: string; t: Translate }) {
  const top = Math.max(...days.map((day) => day.checks), 1)
  const months = days.filter((_, index) => index % 7 === 0).map((day) => new Date(`${day.day}T12:00:00`).toLocaleDateString(tag, { month: "short" }))
  const columns = { gridTemplateColumns: `repeat(${months.length}, minmax(0, 1fr))` }
  return (
    <>
      <div className="mb-1 grid gap-[3px] font-mono text-[10px] text-muted-foreground" style={columns} aria-hidden="true">
        {months.map((month, index) => <span key={index} className="whitespace-nowrap">{month !== months[index - 1] ? month : ""}</span>)}
      </div>
      <div className="grid grid-flow-col grid-rows-7 gap-[3px]" style={columns} data-chart data-chart-rows={7}>
        {days.map((day, index) => (
          <i key={day.day} className={cn("aspect-square rounded-[3px]", FOCUS_RING, HEAT[day.checks ? Math.ceil((day.checks / top) * 4) : 0])}
            tabIndex={index === 0 ? 0 : -1} role="img" aria-label={`${new Date(`${day.day}T12:00:00`).toLocaleDateString(tag, { weekday: "short", day: "numeric", month: "short" })}: ${plural(t, day.checks, "{count} check", "{count} checks", String)}`} />
        ))}
      </div>
      <div className="mt-2 flex items-center justify-end gap-1 text-xs text-muted-foreground">
        {t("Fewer")}{HEAT.map((tone) => <i key={tone} className={cn("size-[11px] rounded-[3px]", tone)} />)}{t("More")}
      </div>
    </>
  )
}

function RightNow({ now, t }: { now: Stats["now"]; t: Translate }) {
  const cacheShare = now.audio_cache_limit ? Math.min(now.audio_cache_bytes / now.audio_cache_limit, 1) : 0
  return (
    <div className={CARD}>
      <CardHead title={t("Right now")} detail={now.workers ? <span className="flex items-center gap-1.5 font-sans"><i className="size-1.5 rounded-full bg-in-sync" />{t("live")}</span> : undefined} />
      <Facts rows={[
        [t("Queue"), t("{queued} waiting, {running} running", { queued: now.queued, running: now.running })],
        [t("Workers"), now.workers_expected ? t("{count} of {total}", { count: now.workers, total: now.workers_expected }) : t("stopped")],
        [t("Last check"), when(now.last_check) || "-"],
        [t("Audio cache"), <><span className="h-2 w-20 overflow-hidden rounded bg-muted"><i className="block h-full rounded bg-bar" style={{ width: `${cacheShare * 100}%` }} /></span>{`${bytes(now.audio_cache_bytes)} / ${bytes(now.audio_cache_limit)}`}</>],
        [t("Backups"), bytes(now.backups_bytes)],
        [t("Uptime"), now.started ? uptime(t, now.started) : "-"],
      ]} />
    </div>
  )
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-2.5 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="truncate text-muted-foreground">{label}</dt>
          <dd className="flex min-w-0 flex-wrap items-center justify-end gap-x-2 gap-y-1 text-right font-mono text-[13px] tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Bars({ bars, rule, height = "h-36" }: { bars: { count: number; tone: string; tip: string }[]; rule?: number; height?: string }) {
  const top = Math.max(...bars.map((bar) => bar.count), 1)
  return (
    <div className={cn("relative flex items-end gap-[2px] border-b", height)} data-chart>
      {bars.map((bar, index) => (
        <div key={bar.tip} className={cn("flex h-full flex-1 items-end", FOCUS_RING)} tabIndex={index === 0 ? 0 : -1} role="img" aria-label={bar.tip}>
          <i className={cn("block w-full rounded-t-[4px]", bar.tone)} style={{ height: bar.count ? `max(2px, ${(bar.count / top) * 100}%)` : 0 }} />
        </div>
      ))}
      {rule !== undefined && <span aria-hidden="true" className="pointer-events-none absolute inset-y-0 border-l border-dashed border-foreground" style={{ left: `${rule * 100}%` }} />}
    </div>
  )
}

function Axis({ labels, positions }: { labels: string[]; positions: number[] }) {
  return (
    <div className="relative mt-1 h-4 font-mono text-[10px] text-muted-foreground" aria-hidden="true">
      {labels.map((label, index) => (
        <span key={label} className="absolute whitespace-nowrap"
          style={{ left: `${positions[index]}%`, transform: index === 0 ? "none" : index === labels.length - 1 ? "translateX(-100%)" : "translateX(-50%)" }}>{label}</span>
      ))}
    </div>
  )
}

function Key({ items }: { items: { label: string; tone: string }[] }) {
  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
      {items.map((item) => <span key={item.label} className="flex items-center gap-1.5 whitespace-nowrap"><i className={cn("size-2.5 rounded-[3px]", item.tone)} />{item.label}</span>)}
    </div>
  )
}

function Providers({ providers, number, t }: { providers: Stats["providers"]; number: (value: number) => string; t: Translate }) {
  return (
    <section className={CARD}>
      <CardHead title={t("Subtitle providers")} detail={t("verdict when the subtitle arrived")} />
      {providers.length === 0 && <p className="py-6 text-center text-sm text-muted-foreground">{t("No subtitle downloaded by Bazarr in this period")}</p>}
      {providers.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm whitespace-nowrap">
            <thead className="text-xs text-muted-foreground">
              <tr className="border-b">
                <th className="px-2.5 py-1.5 text-left font-medium">{t("Provider")}</th>
                <th className="px-2.5 py-1.5 text-right font-medium">{t("Downloads")}</th>
                <th className="w-1/2 px-2.5 py-1.5 text-left font-medium">{t("Verdict")}</th>
                <th className="px-2.5 py-1.5 text-right font-medium">{t("In sync")}</th>
                <th className="px-2.5 py-1.5 text-right font-medium">{t("Median offset")}</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((provider) => (
                <tr key={provider.provider} className="border-b last:border-0">
                  <td className="px-2.5 py-2 font-medium">{provider.provider}</td>
                  <td className="px-2.5 py-2 text-right font-mono tabular-nums">{number(provider.downloads)}</td>
                  <td className="px-2.5 py-2">
                    <div className="flex h-2.5 gap-[2px] overflow-hidden rounded bg-muted" data-chart>
                      {VERDICTS.map((verdict) => provider[verdict.key] > 0 && (
                        <i key={verdict.key} className={cn("block h-full", FOCUS_RING, verdict.tone)} style={{ width: `${(provider[verdict.key] / provider.downloads) * 100}%` }}
                          tabIndex={verdict.key === VERDICTS.find((first) => provider[first.key] > 0)?.key ? 0 : -1} role="img" aria-label={`${t(verdict.label)}: ${number(provider[verdict.key])}`} />
                      ))}
                    </div>
                  </td>
                  <td className="px-2.5 py-2 text-right font-mono tabular-nums">{Math.round((provider.in_sync / provider.downloads) * 100)}%</td>
                  <td className="px-2.5 py-2 text-right font-mono tabular-nums">{provider.median_offset === null ? "-" : seconds(provider.median_offset)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Key items={VERDICTS.map((verdict) => ({ label: t(verdict.label), tone: verdict.tone }))} />
    </section>
  )
}

function Meters({ rows, tone, number, empty, mono }: { rows: { label: string; count: number }[]; tone: string; number: (value: number) => string; empty: string; mono?: boolean }) {
  const top = Math.max(...rows.map((row) => row.count), 1)
  if (rows.length === 0) return <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>
  return (
    <ul className="flex flex-col gap-2.5 text-sm">
      {rows.slice(0, 6).map((row) => (
        <li key={row.label} className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_44px] items-center gap-2.5 whitespace-nowrap">
          <span className={cn("truncate", mono && "font-mono")}>{row.label}</span>
          <span className="h-2 overflow-hidden rounded bg-muted"><i className={cn("block h-full rounded", tone)} style={{ width: `${(row.count / top) * 100}%` }} /></span>
          <span className="text-right font-mono tabular-nums">{number(row.count)}</span>
        </li>
      ))}
    </ul>
  )
}

function References({ references, share, t }: { references: Stats["references"]; share: (part: number, whole: number) => string; t: Translate }) {
  const total = references.reduce((sum, entry) => sum + entry.count, 0)
  return (
    <>
      <div className="mb-3 flex h-3.5 gap-[2px] overflow-hidden rounded" data-chart>
        {references.map((entry, index) => <i key={entry.name} className={cn("block h-full", FOCUS_RING, REFERENCES[entry.name]?.tone)} style={{ width: `${(entry.count / total) * 100}%` }}
          tabIndex={index === 0 ? 0 : -1} role="img" aria-label={`${t(REFERENCES[entry.name]?.label ?? entry.name)}: ${share(entry.count, total)}`} />)}
      </div>
      <ul className="flex flex-col gap-2.5 text-sm">
        {references.map((entry) => (
          <li key={entry.name} className="flex items-center justify-between gap-2.5 whitespace-nowrap">
            <span className="flex items-center gap-2 truncate"><i className={cn("size-2.5 flex-none rounded-[3px]", REFERENCES[entry.name]?.tone)} />{t(REFERENCES[entry.name]?.label ?? entry.name)}</span>
            <span className="font-mono tabular-nums">{share(entry.count, total)}</span>
          </li>
        ))}
      </ul>
    </>
  )
}

function plural(t: Translate, count: number, one: string, many: string, number: (value: number) => string) {
  return t(count === 1 ? one : many, { count: number(count) })
}

function fixes(t: Translate, count: number) {
  return plural(t, count, "{count} fix", "{count} fixes", String)
}

function seconds(value: number) {
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} s`
  return `${Math.floor(value / 60)} min ${String(Math.round(value % 60)).padStart(2, "0")} s`
}

function offsetRange(t: Translate, entry: Stats["offsets"][number]) {
  if (entry.low === null) return t("more than {value} s early", { value: -(entry.high ?? 0) })
  if (entry.high === null) return t("more than {value} s late", { value: entry.low })
  if (entry.high <= 0) return t("{low} to {high} s early", { low: -entry.high, high: -entry.low })
  return t("{low} to {high} s late", { low: entry.low, high: entry.high })
}

function checkTimeRange(t: Translate, index: number, count: number) {
  if (index === count - 1) return t("more than {value} s", { value: index * CHECK_TIME_STEP })
  return t("{low} to {high} s", { low: index * CHECK_TIME_STEP, high: (index + 1) * CHECK_TIME_STEP })
}

function busiestDay(days: Stats["per_day"], tag: string) {
  const totals = Array.from({ length: 7 }, () => 0)
  for (const day of days) {
    const weekday = new Date(`${day.day}T12:00:00`).getDay()
    totals[weekday] = (totals[weekday] ?? 0) + day.checks
  }
  const busiest = totals.indexOf(Math.max(...totals))
  return new Date(2024, 0, 7 + busiest).toLocaleDateString(tag, { weekday: "long" })
}

function uptime(t: Translate, started: string) {
  const hours = Math.max(0, Math.floor((Date.now() - new Date(started).getTime()) / 3_600_000))
  return t("{days} d {hours} h", { days: Math.floor(hours / 24), hours: hours % 24 })
}

// one tab stop per chart, arrows move between its values (the roving tabindex pattern)
function moveInChart(event: KeyboardEvent) {
  const step = CHART_KEYS[event.key]
  const item = event.target instanceof HTMLElement && event.target.matches("[data-chart] [role=img]") ? event.target : null
  const chart = item?.closest("[data-chart]")
  if (!step || !item || !chart) return
  event.preventDefault()
  const items = [...chart.querySelectorAll<HTMLElement>("[role=img]")]
  const next = items[Math.min(items.length - 1, Math.max(0, items.indexOf(item) + step(Number(chart.getAttribute("data-chart-rows")) || 1)))]
  if (!next || next === item) return
  item.tabIndex = -1
  next.tabIndex = 0
  next.focus()
}
