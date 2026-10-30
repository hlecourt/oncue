// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangleIcon, CheckCircle2Icon, ScanSearchIcon, SquareIcon } from "lucide-react"
import { toast } from "sonner"

import { PageHeader, ToneDot } from "@/components/status"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { SYSTEM_KEY, useSystem } from "@/hooks/use-system"
import { api, unwrap } from "@/lib/api/client"
import { bytes, when } from "@/lib/format"
import { useT } from "@/lib/i18n"

const CHECK_WORDS: Record<string, string> = { ffmpeg: "ffmpeg", ffprobe: "ffprobe", ffsubsync: "ffsubsync (audio sync)", state_writable: "State folder",
  backups_writable: "Backup folder", bazarr: "Bazarr", allowed_folders: "Allowed folders" }
const WARNING_TITLES: Record<string, string> = { unreachable: "Bazarr cannot be reached", not_set_up: "Bazarr is not set up" }
const SCAN_WORDS: Record<string, string> = { listing: "Listing the library", checking: "Checking", finished: "Finished", stopped: "Stopped" }

export function SystemPage() {
  const t = useT()
  const queryClient = useQueryClient()
  const system = useSystem()
  const bazarr = useQuery({ queryKey: ["system", "bazarr"], queryFn: () => unwrap(api.GET("/api/system/bazarr")) })
  const scan = useQuery({
    queryKey: ["scan"],
    queryFn: () => unwrap(api.GET("/api/scans/latest")),
    refetchInterval: (query) => (query.state.data && ["listing", "checking"].includes(query.state.data.status) ? 2000 : false),
  })
  const start = useMutation({
    mutationFn: () => unwrap(api.POST("/api/scans")),
    onSuccess: (result) => queryClient.setQueryData(["scan"], result),
    onError: (error) => toast.error(error.message),
  })
  const stop = useMutation({
    mutationFn: (id: number) => unwrap(api.POST("/api/scans/{scan_id}/stop", { params: { path: { scan_id: id } } })),
    onSuccess: (result) => queryClient.setQueryData(["scan"], result),
  })
  const cleanUp = useMutation({
    mutationFn: () => unwrap(api.POST("/api/system/backups/clean-up")),
    onSuccess: (result) => {
      toast.success(t("{count} old backups removed.", { count: result.removed }))
      queryClient.invalidateQueries({ queryKey: SYSTEM_KEY })
    },
  })
  const running = scan.data && ["listing", "checking"].includes(scan.data.status)
  const workers = Object.entries(system.data?.workers ?? {})

  return (
    <>
      <PageHeader title={t("System")} description={t("The health of what OnCue depends on, the library scan, and the backups.")} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>{t("Health")}</CardTitle><CardDescription>{system.data ? t("OnCue {version}, mode {mode}", { version: system.data.version, mode: system.data.mode === "fix" ? t("Fix automatically") : t("Report only") }) : " "}</CardDescription></CardHeader>
          <CardContent className="space-y-2">
            {!system.data && <Skeleton className="h-40 w-full" />}
            {system.data && Object.entries(system.data.checks).map(([name, check]) => (
              <Row key={name} label={t(CHECK_WORDS[name] ?? name)} tone={check.ok ? "good" : "bad"} value={check.detail || (check.ok ? t("OK") : t("Not working"))} />
            ))}
            {system.data && (
              <Row label={t("Workers")} tone={workers.length && workers.every(([, alive]) => alive) ? "good" : "warn"}
                value={workers.length ? t("{running} of {total} running", { running: workers.filter(([, alive]) => alive).length, total: workers.length }) : t("None running: checks wait in the queue")} />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Bazarr</CardTitle><CardDescription>{t("Bazarr's own settings that decide what reaches OnCue.")}</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            {!bazarr.data && <Skeleton className="h-24 w-full" />}
            {bazarr.data?.warnings.length === 0 && <div className="flex items-center gap-2 text-sm"><CheckCircle2Icon className="size-4 text-success" />{t("Bazarr is set up for OnCue.")}</div>}
            {bazarr.data?.warnings.map((warning) => (
              <Alert key={warning.code} variant={["unreachable", "not_set_up"].includes(warning.code) ? "default" : "destructive"}>
                <AlertTriangleIcon />
                <AlertTitle>{t(WARNING_TITLES[warning.code] ?? "Change this in Bazarr")}</AlertTitle>
                <AlertDescription>{warning.message}</AlertDescription>
              </Alert>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("Library scan")}</CardTitle><CardDescription>{t("Check every subtitle of the library, through Bazarr or the watched folders.")}</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            {scan.data && (
              <div className="space-y-2">
                <div className="flex justify-between text-sm"><span>{t("{status}, started {when}", { status: t(SCAN_WORDS[scan.data.status] ?? scan.data.status), when: when(scan.data.started).toLowerCase() })}</span><span className="font-mono tabular-nums">{scan.data.done} / {scan.data.total}</span></div>
                <div className="h-2 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={scan.data.total} aria-valuenow={scan.data.done}>
                  <div className="h-full bg-primary transition-all" style={{ width: `${scan.data.total ? (100 * scan.data.done) / scan.data.total : 0}%` }} />
                </div>
                <p className="text-xs text-muted-foreground">{t("Through {source}. {waiting} waiting for a decision, {failed} failed.", { source: t(scan.data.source), waiting: scan.data.not_settled, failed: scan.data.failed })}</p>
              </div>
            )}
            <div className="flex gap-2">
              {running ? <Button variant="outline" onClick={() => stop.mutate(scan.data!.id)}><SquareIcon />{t("Stop the scan")}</Button>
                : <Button onClick={() => start.mutate()} disabled={start.isPending}><ScanSearchIcon />{t("Scan the library")}</Button>}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("Backups")}</CardTitle><CardDescription>{t("A copy is kept each time OnCue writes a subtitle.")}</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            <div className="font-mono text-2xl font-semibold tabular-nums">{system.data ? bytes(system.data.backups_bytes) : "-"}</div>
            <Button variant="outline" className="h-auto min-h-9 py-1.5 whitespace-normal" onClick={() => cleanUp.mutate()} disabled={cleanUp.isPending}>{t("Clean up old backups now")}</Button>
          </CardContent>
        </Card>
      </div>
    </>
  )
}

function Row({ label, tone, value }: { label: string; tone: "good" | "warn" | "bad"; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b pb-2 text-sm last:border-b-0">
      <span className="flex shrink-0 items-center gap-2"><ToneDot tone={tone} />{label}</span>
      <span className="min-w-0 truncate text-right text-muted-foreground" title={value}>{value}</span>
    </div>
  )
}
