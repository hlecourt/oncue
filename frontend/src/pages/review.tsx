// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { type ColumnDef, flexRender, getCoreRowModel, getFilteredRowModel, type RowSelectionState, useReactTable } from "@tanstack/react-table"
import { cn } from "cn"
import { CheckCircle2Icon, RefreshCwIcon, ScanSearchIcon } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { ConfidenceBadge, EmptyState, PageHeader, Stat, ToneDot, toneOf } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { STATUS_KEY, useStatus } from "@/hooks/use-status"
import { api, type Schemas, unwrap } from "@/lib/api/client"
import { when } from "@/lib/format"

type Item = Schemas["ReviewItem"]
type Show = "all" | "proposals" | "other"
type BulkAction = Schemas["BulkAction"]["action"]
export const REVIEW_KEY = ["review"]
const WIDE_ONLY = new Set(["reason", "confidence", "checked"])

const COLUMNS: ColumnDef<Item>[] = [
  {
    id: "select",
    header: ({ table }) => (
      <Checkbox aria-label="Select every subtitle" checked={table.getIsAllRowsSelected() || (table.getIsSomeRowsSelected() && "indeterminate")}
        onCheckedChange={(value) => table.toggleAllRowsSelected(Boolean(value))} />
    ),
    cell: ({ row }) => (
      <Checkbox aria-label={`Select ${row.original.name}`} checked={row.getIsSelected()} onCheckedChange={(value) => row.toggleSelected(Boolean(value))}
        onClick={(event) => event.stopPropagation()} />
    ),
  },
  {
    id: "subtitle",
    header: "Subtitle",
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
  { id: "reason", header: "Why it is here", cell: ({ row }) => <span className="text-sm">{row.original.reason}</span> },
  {
    id: "confidence",
    header: "Confidence",
    cell: ({ row }) => <ConfidenceBadge confidence={row.original.proposal?.confidence ?? row.original.confidence} band={row.original.proposal?.band ?? row.original.band} />,
  },
  { id: "checked", header: "Checked", cell: ({ row }) => <span className="whitespace-nowrap text-xs text-muted-foreground">{when(row.original.checked)}</span> },
]

export function ReviewPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const status = useStatus()
  const review = useQuery({ queryKey: REVIEW_KEY, queryFn: () => unwrap(api.GET("/api/review")), refetchInterval: 15_000 })
  const history = useQuery({ queryKey: ["history", "groups"], queryFn: () => unwrap(api.GET("/api/history", { params: { query: { limit: 5, result: "fixed" } } })) })
  const [show, setShow] = useState<Show>("all")
  const [search, setSearch] = useState("")
  const [selection, setSelection] = useState<RowSelectionState>({})
  const items = useMemo(() => (review.data ?? []).filter((item) => show === "all" || (show === "proposals") === Boolean(item.proposal)), [review.data, show])
  const table = useReactTable({
    data: items, columns: COLUMNS, getRowId: (item) => item.id, state: { rowSelection: selection, globalFilter: search },
    onRowSelectionChange: setSelection, onGlobalFilterChange: setSearch, getCoreRowModel: getCoreRowModel(), getFilteredRowModel: getFilteredRowModel(),
  })
  const bulk = useMutation({
    mutationFn: (action: BulkAction) => unwrap(api.POST("/api/review/bulk", { body: { action, ids: Object.keys(selection) } })),
    onSuccess: (result) => {
      const skipped = Object.keys(result.skipped).length
      toast.success(`${result.done.length} done${skipped ? `, ${skipped} skipped` : ""}.`, { description: Object.values(result.skipped)[0] })
      setSelection({})
      queryClient.invalidateQueries({ queryKey: REVIEW_KEY })
      queryClient.invalidateQueries({ queryKey: STATUS_KEY })
    },
    onError: (error) => toast.error(error.message),
  })
  const scan = useMutation({
    mutationFn: () => unwrap(api.POST("/api/scans")),
    onSuccess: () => toast.success("The library scan started.", { description: "Follow it on the System page." }),
    onError: (error) => toast.error(error.message),
  })
  const groups = Object.fromEntries((history.data?.groups ?? []).map((group) => [group.key, group.count]))
  const proposals = (review.data ?? []).filter((item) => item.proposal).length
  const selected = Object.keys(selection).length
  const jobs = status.data?.jobs ?? {}

  return (
    <>
      <PageHeader title="Review" description="Subtitles waiting for your decision. A fix the guard is sure about is written on its own; everything else waits here." />
      <div className="mb-6 grid grid-cols-3 gap-2 sm:gap-3 xl:grid-cols-6">
        <Stat label="To review" value={review.data?.length ?? "-"} tone={review.data?.length ? "warn" : "neutral"} />
        <Stat label="Proposals" value={review.data ? proposals : "-"} />
        <Stat label="Fixed automatically" value={groups.fixed ?? "-"} tone={groups.fixed ? "good" : "neutral"} />
        <Stat label="In sync" value={groups["in-sync"] ?? "-"} />
        <Stat label="Checks waiting" value={(jobs.queued ?? 0) + (jobs.running ?? 0)} />
        <Stat label="Errors" value={jobs.failed ?? 0} tone={jobs.failed ? "bad" : "neutral"} />
      </div>
      {review.isPending && <Skeleton className="h-64 w-full" />}
      {review.error && <EmptyState icon={<RefreshCwIcon />} title="Review could not be loaded" action={<Button variant="outline" onClick={() => review.refetch()}>Try again</Button>}>{review.error.message}</EmptyState>}
      {review.data?.length === 0 && (
        <EmptyState icon={<CheckCircle2Icon className="text-success" />} title="Nothing to review"
          action={<Button variant="outline" onClick={() => scan.mutate()} disabled={scan.isPending}><ScanSearchIcon />Scan the library</Button>}>
          {history.data?.rows.length ? `The latest automatic fix: ${history.data.rows[0]!.name}, ${when(history.data.rows[0]!.time)}.` : "Nothing has been checked yet. Scan the library to start."}
        </EmptyState>
      )}
      {review.data && review.data.length > 0 && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input placeholder="Filter by name, folder or reason" value={search} onChange={(event) => setSearch(event.target.value)} className="h-8 w-full sm:w-72" aria-label="Filter" />
            <ToggleGroup type="single" variant="outline" size="sm" value={show} onValueChange={(value) => value && setShow(value as Show)} aria-label="Show">
              <ToggleGroupItem value="all">All</ToggleGroupItem>
              <ToggleGroupItem value="proposals">Proposals</ToggleGroupItem>
              <ToggleGroupItem value="other">Other</ToggleGroupItem>
            </ToggleGroup>
            {selected > 0 && (
              <div className="ml-auto flex flex-wrap items-center gap-2" role="toolbar" aria-label="Selected subtitles">
                <span className="text-sm text-muted-foreground">{selected} selected</span>
                <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => bulk.mutate("replacement")}>Ask Bazarr for new versions</Button>
                <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => bulk.mutate("recheck")}>Check again</Button>
                <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => bulk.mutate("keep")}>Keep as is</Button>
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
                  <TableRow><TableCell colSpan={COLUMNS.length} className="py-8 text-center text-sm text-muted-foreground">No subtitle matches this filter.</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </>
  )
}
