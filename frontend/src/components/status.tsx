// SPDX-License-Identifier: GPL-3.0-or-later
import { cn } from "cn"
import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { useT } from "@/lib/i18n"

export type Tone = "good" | "warn" | "bad" | "neutral"

const DOT = { good: "bg-success", warn: "bg-warning", bad: "bg-destructive", neutral: "bg-muted-foreground" } as const
const TEXT = { good: "text-success", warn: "text-warning", bad: "text-destructive", neutral: "text-muted-foreground" } as const
const BAND_WORDS: Record<string, string> = { high: "High confidence", medium: "Medium confidence", low: "Low confidence", none: "None confidence" }

export function toneOf(value: string): Tone {
  return value in DOT ? (value as Tone) : "neutral"
}

export function ToneDot({ tone, label }: { tone: Tone; label?: string }) {
  return <span role={label ? "img" : undefined} aria-label={label} className={cn("inline-block size-2 shrink-0 rounded-full", DOT[tone])} />
}

export function ToneText({ tone, children, className }: { tone: Tone; children: ReactNode; className?: string }) {
  return <span className={cn(TEXT[tone], className)}>{children}</span>
}

export function ConfidenceBadge({ confidence, band }: { confidence: number | null | undefined; band: string }) {
  const t = useT()
  if (confidence == null) return <span className="text-xs text-muted-foreground">-</span>
  return (
    <Badge variant="outline" className="font-mono tabular-nums" title={t(BAND_WORDS[band] ?? "{band} confidence", { band })}>
      {confidence}
    </Badge>
  )
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function EmptyState({ icon, title, children, action }: { icon: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-12 text-center">
      <div className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">{icon}</div>
      <h2 className="text-base font-medium">{title}</h2>
      {children && <div className="max-w-md text-sm text-muted-foreground">{children}</div>}
      {action}
    </div>
  )
}

export function Stat({ label, value, tone = "neutral" }: { label: string; value: ReactNode; tone?: Tone }) {
  return (
    <div className="min-w-0 rounded-lg border bg-card px-3 py-2 sm:px-4 sm:py-3">
      <div className="flex items-center gap-1.5 text-xs leading-tight text-muted-foreground">{tone !== "neutral" && <ToneDot tone={tone} />}{label}</div>
      <div className="mt-1 font-mono text-xl font-semibold tabular-nums sm:text-2xl">{value}</div>
    </div>
  )
}
