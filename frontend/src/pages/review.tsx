// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { type ColumnDef, flexRender, getCoreRowModel, getFilteredRowModel, type RowSelectionState, useReactTable } from "@tanstack/react-table"
import { cn } from "cn"
import { CheckCircle2Icon, InfoIcon, ScanSearchIcon } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { ConfidenceBadge, EmptyState, LoadError, PageHeader, Stat, ToneDot, toneOf } from "@/components/status"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { STATUS_KEY, useStatus } from "@/hooks/use-status"
import { REPORT_MODE, useSystem } from "@/hooks/use-system"
import { api, type Schemas, unwrap } from "@/lib/api/client"
import { when } from "@/lib/format"
import { useT } from "@/lib/i18n"

type Item = Schemas["ReviewItem"]
const SHOWS = ["all", "proposals", "other"] as const
type Show = (typeof SHOWS)[number]
type BulkAction = Schemas["BulkAction"]["action"]
export const REVIEW_KEY = ["review"]
const WIDE_ONLY = new Set(["reason", "confidence", "checked"])

const columns = (t: ReturnType<typeof useT>): ColumnDef<Item>[] => [
  {
    id: "select",
    header: ({ table }) => (
      <Checkbox aria-label={t("Select every subtitle")} checked={table.getIsAllRowsSelected() || (table.getIsSomeRowsSelected() && "indeterminate")}
        onCheckedChange={(value) => table.toggleAllRowsSelected(Boolean(value))} />
    ),
    cell: ({ row }) => (
      <Checkbox aria-label={t("Select {name}", { name: row.original.name })} checked={row.getIsSelected()} onCheckedChange={(value) => row.toggleSelected(Boolean(value))}
        onClick={(event) => event.stopPropagation()} />
    ),
  },
  {
    id: "subtitle",
    header: t("Subtitle"),
    accessorFn: (item) => `${item.name} ${item.folder} ${item.reason}`,
    cell: ({ row }) => (
      <div className="flex min-w-0 items-start gap-3">
        <span className="mt-1.5"><ToneDot tone={toneOf(row.original.tone)} /></span>
        <div className="min-w-0 flex-1">
          <Link to="/subtitles/$subtitleId" params={{ subtitleId: row.original.id }} className="block truncate font-medium hover:underline">{row.original.name}</Link>
          <div className="truncate text-xs text-muted-foreground">{row.original.folder}</div>
          <div className="mt-1.5 flex items-start justify-between gap-3 md:hidden">
            <span className="text-sm">{row.original.reason}</span>
            <ConfidenceBadge confidence={row.original.proposal?.confidence ?? row.original.confidence} band={row.original.proposal?.band ?? row.original.band} />
          </div>
        </div>
      </div>
    ),
  },
  { id: "reason", header: t("Why it is here"), cell: ({ row }) => <span className="text-sm">{row.original.reason}</span> },
  {
    id: "confidence",
    header: t("Confidence"),
    cell: ({ row }) => <ConfidenceBadge confidence={row.original.proposal?.confidence ?? row.original.confidence} band={row.original.proposal?.band ?? row.original.band} />,
  },
  { id: "checked", header: t("Checked"), cell: ({ row }) => <span className="whitespace-nowrap text-xs text-muted-foreground">{when(row.original.checked)}</span> },
]

export function ReviewPage() {
  const t = useT()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const status = useStatus()
  const system = useSystem()
  const review = useQuery({ queryKey: REVIEW_KEY, queryFn: () => unwrap(api.GET("/api/review")), refetchInterval: 15_000 })
  const history = useQuery({ queryKey: ["history", "groups"], queryFn: () => unwrap(api.GET("/api/history", { params: { query: { limit: 5, result: "fixed" } } })) })
  const [show, setShow] = useState<Show>("all")
  const [search, setSearch] = useState("")
  const [selection, setSelection] = useState<RowSelectionState>({})
  const items = useMemo(() => (review.data ?? []).filter((item) => show === "all" || (show === "proposals") === Boolean(item.proposal)), [review.data, show])
  const table = useReactTable({
    data: items, columns: useMemo(() => columns(t), [t]), getRowId: (item) => item.id, state: { rowSelection: selection, globalFilter: search },
    onRowSelectionChange: setSelection, onGlobalFilterChange: setSearch, getCoreRowModel: getCoreRowModel(), getFilteredRowModel: getFilteredRowModel(),
  })
  const bulk = useMutation({
    mutationFn: (action: BulkAction) => unwrap(api.POST("/api/review/bulk", { body: { action, ids: Object.keys(selection) } })),
    onSuccess: (result) => {
      const skipped = Object.keys(result.skipped).length
      toast.success(skipped ? t("{done} done, {skipped} skipped.", { done: result.done.length, skipped }) : t("{done} done.", { done: result.done.length }), { description: Object.values(result.skipped)[0] })
      setSelection({})
      queryClient.invalidateQueries({ queryKey: REVIEW_KEY })
      queryClient.invalidateQueries({ queryKey: STATUS_KEY })
    },
    onError: (error) => toast.error(error.message),
  })
  const scan = useMutation({
    mutationFn: () => unwrap(api.POST("/api/scans")),
    onSuccess: () => toast.success(t("The library scan started."), { description: t("Follow it on the System page.") }),
    onError: (error) => toast.error(error.message),
  })
  const checkCount = (history.data?.groups ?? []).reduce((sum, group) => sum + group.count, 0)
  const latestFix = history.data?.rows[0]
  const reportMode = system.data?.mode === REPORT_MODE
  const proposals = (review.data ?? []).filter((item) => item.proposal).length
  const selected = Object.keys(selection).length
  const jobs = status.data?.jobs ?? {}

  function pickShow(value: string) {
    const picked = SHOWS.find((entry) => entry === value)
    if (picked) setShow(picked)
  }

  function emptyNote() {
    if (!history.data) return ""
    if (latestFix) return t("The latest automatic fix: {name}, {when}.", { name: latestFix.name, when: when(latestFix.time) })
    if (checkCount > 0) return t("Every subtitle checked so far is settled.")
    return t("Nothing has been checked yet. Scan the library to start.")
  }

  return (
    <>
      <PageHeader title={t("Review")} description={reportMode
        ? t("Subtitles waiting for your decision. In report mode every fix waits here, even one OnCue is sure about.")
        : t("Subtitles waiting for your decision. A fix OnCue is sure about is written on its own; everything else waits here.")} />
      {reportMode && (
        <Alert className="mb-6">
          <InfoIcon />
          <AlertTitle>{t("Report mode: nothing is written.")}</AlertTitle>
          <AlertDescription>
            <p>{t("Sure fixes wait here with the other proposals.")} <Link to="/settings" className="font-medium text-foreground underline underline-offset-4">{t("Change the mode in Settings")}</Link></p>
          </AlertDescription>
        </Alert>
      )}
      <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4 sm:gap-3">
        <Stat label={t("To review")} value={review.data?.length ?? "-"} tone={review.data?.length ? "warn" : "neutral"} />
        <Stat label={t("Proposals")} value={review.data ? proposals : "-"} />
        <Stat label={t("Checks waiting")} value={(jobs.queued ?? 0) + (jobs.running ?? 0)} />
        <Stat label={t("Errors")} value={jobs.failed ?? 0} tone={jobs.failed ? "bad" : "neutral"} />
      </div>
      {review.isPending && <Skeleton className="h-64 w-full" />}
      {review.error && <LoadError error={review.error} onRetry={() => review.refetch()} title={t("Review could not be loaded")} />}
      {review.data?.length === 0 && (
        <EmptyState icon={<CheckCircle2Icon className="text-success" />} title={t("Nothing to review")}
          action={<Button variant="outline" onClick={() => scan.mutate()} disabled={scan.isPending}><ScanSearchIcon />{t("Scan the library")}</Button>}>
          {emptyNote()}
        </EmptyState>
      )}
      {review.data && review.data.length > 0 && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input placeholder={t("Filter by name, folder or reason")} value={search} onChange={(event) => setSearch(event.target.value)} className="h-8 w-full sm:w-72" aria-label={t("Filter")} />
            <ToggleGroup type="single" variant="outline" size="sm" value={show} onValueChange={pickShow} aria-label={t("Show")}>
              <ToggleGroupItem value="all">{t("All")}</ToggleGroupItem>
              <ToggleGroupItem value="proposals">{t("Proposals")}</ToggleGroupItem>
              <ToggleGroupItem value="other">{t("Other")}</ToggleGroupItem>
            </ToggleGroup>
            {selected > 0 && (
              <div className="ml-auto flex flex-wrap items-center gap-2" role="toolbar" aria-label={t("Selected subtitles")}>
                <span className="text-sm text-muted-foreground">{t("{count} selected", { count: selected })}</span>
                <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => bulk.mutate("replacement")}>{t("Ask Bazarr for replacements")}</Button>
                <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => bulk.mutate("recheck")}>{t("Check again")}</Button>
                <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => bulk.mutate("keep")}>{t("Keep as is")}</Button>
              </div>
            )}
          </div>
          <div className="overflow-hidden rounded-lg border">
            <Table>
              <TableHeader className="bg-muted/50">
                {table.getHeaderGroups().map((group) => (
                  <TableRow key={group.id}>
                    {group.headers.map((header) => (
                      <TableHead key={header.id} className={cn(header.id === "select" && "w-10", WIDE_ONLY.has(header.id) && "hidden md:table-cell")}>
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </TableHead>
                    ))}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {table.getRowModel().rows.map((row) => (
                  <TableRow key={row.id} data-state={row.getIsSelected() ? "selected" : undefined} className="cursor-pointer"
                    onClick={() => navigate({ to: "/subtitles/$subtitleId", params: { subtitleId: row.original.id } })}>
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id} className={cn("py-3 align-top", cell.column.id === "subtitle" && "max-md:whitespace-normal", WIDE_ONLY.has(cell.column.id) && "hidden md:table-cell")}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
                {table.getRowModel().rows.length === 0 && (
                  <TableRow><TableCell colSpan={5} className="py-8 text-center text-sm text-muted-foreground">{t("No subtitle matches this filter.")}</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </>
  )
}
