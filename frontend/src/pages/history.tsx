// SPDX-License-Identifier: GPL-3.0-or-later
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { HistoryIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { ConfidenceBadge, EmptyState, PageHeader, ToneDot, toneOf } from "@/components/status"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { api, unwrap } from "@/lib/api/client"
import { when } from "@/lib/format"

const PAGE_SIZE = 50
const JOB_WORDS: Record<string, string> = { hook: "Check a download from Bazarr", check: "Check", blacklist: "Ask Bazarr for another subtitle",
  "retry-check": "Wait for the other subtitle", scan: "List the library", "audio-copy": "Prepare the sound for listening" }

export function HistoryPage() {
  return (
    <>
      <PageHeader title="History" description="Every check the guard made, newest first: what started it, what it decided, how sure it was and what it changed." />
      <Tabs defaultValue="checks">
        <TabsList>
          <TabsTrigger value="checks">Checks</TabsTrigger>
          <TabsTrigger value="tasks">Tasks</TabsTrigger>
        </TabsList>
        <TabsContent value="checks" className="mt-4"><Checks /></TabsContent>
        <TabsContent value="tasks" className="mt-4"><Tasks /></TabsContent>
      </Tabs>
    </>
  )
}

function Checks() {
  const [result, setResult] = useState("all")
  const history = useInfiniteQuery({
    queryKey: ["history", result],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => unwrap(api.GET("/api/history", { params: { query: { limit: PAGE_SIZE, before: pageParam, result: result === "all" ? undefined : result } } })),
    getNextPageParam: (page) => page.next_before ?? undefined,
  })
  const groups = history.data?.pages[0]?.groups ?? []
  const rows = history.data?.pages.flatMap((page) => page.rows) ?? []
  const total = groups.reduce((sum, group) => sum + group.count, 0)

  return (
    <div className="space-y-3">
      <ToggleGroup type="single" variant="outline" size="sm" value={result} onValueChange={(value) => value && setResult(value)} aria-label="Filter by result" className="flex-wrap">
        <ToggleGroupItem value="all">All <Badge variant="secondary" className="ml-1 font-mono">{total}</Badge></ToggleGroupItem>
        {groups.map((group) => (
          <ToggleGroupItem key={group.key} value={group.key}>{group.label} <Badge variant="secondary" className="ml-1 font-mono">{group.count}</Badge></ToggleGroupItem>
        ))}
      </ToggleGroup>
      {history.isPending && <Skeleton className="h-64 w-full" />}
      {history.data && rows.length === 0 && <EmptyState icon={<HistoryIcon />} title={result === "all" ? "Nothing checked yet" : "No check with this result"} />}
      {rows.length > 0 && (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader className="bg-muted/50">
              <TableRow><TableHead>Subtitle</TableHead><TableHead>Result</TableHead><TableHead>What changed</TableHead><TableHead>Confidence</TableHead><TableHead>Checked</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="max-w-72 align-top">
                    {row.subtitle_id ? <Link to="/subtitles/$subtitleId" params={{ subtitleId: row.subtitle_id }} className="block truncate font-medium hover:underline">{row.name}</Link>
                      : <span className="block truncate font-medium text-muted-foreground">{row.name}</span>}
                    <span className="block truncate text-xs text-muted-foreground">{row.folder}</span>
                  </TableCell>
                  <TableCell className="align-top">
                    <div className="flex items-center gap-2"><ToneDot tone={toneOf(row.tone)} />{row.words}</div>
                  </TableCell>
                  <TableCell className="align-top text-sm">{row.changes.map((sentence) => <div key={sentence}>{sentence}</div>)}</TableCell>
                  <TableCell className="align-top"><ConfidenceBadge confidence={row.confidence} band={row.band} /></TableCell>
                  <TableCell className="whitespace-nowrap align-top text-xs text-muted-foreground">
                    <div>{when(row.time)}</div>
                    {row.started_by && <div className="mt-1">{row.started_by}</div>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {history.hasNextPage && <Button variant="outline" onClick={() => history.fetchNextPage()} disabled={history.isFetchingNextPage}>Show older checks</Button>}
    </div>
  )
}

function Tasks() {
  const queryClient = useQueryClient()
  const tasks = useQuery({ queryKey: ["jobs"], queryFn: () => unwrap(api.GET("/api/jobs", { params: { query: { limit: 100 } } })), refetchInterval: 5000 })
  const retry = useMutation({
    mutationFn: (id: number) => unwrap(api.POST("/api/jobs/{job_id}/retry", { params: { path: { job_id: id } } })),
    onSuccess: (result) => {
      toast.success(result.message)
      queryClient.invalidateQueries({ queryKey: ["jobs"] })
    },
    onError: (error) => toast.error(error.message),
  })
  if (tasks.isPending) return <Skeleton className="h-64 w-full" />
  if (!tasks.data?.length) return <EmptyState icon={<HistoryIcon />} title="No task yet" />
  return (
    <div className="overflow-hidden rounded-lg border">
      <Table>
        <TableHeader className="bg-muted/50">
          <TableRow><TableHead>Task</TableHead><TableHead>Subtitle</TableHead><TableHead>Status</TableHead><TableHead>Updated</TableHead><TableHead><span className="sr-only">Action</span></TableHead></TableRow>
        </TableHeader>
        <TableBody>
          {tasks.data.map((task) => (
            <TableRow key={task.id}>
              <TableCell>{JOB_WORDS[task.kind] ?? task.kind}</TableCell>
              <TableCell className="max-w-64 truncate text-sm">{task.subtitle ?? "-"}</TableCell>
              <TableCell>
                <div className="flex items-center gap-2"><ToneDot tone={task.status === "failed" ? "bad" : task.status === "done" ? "good" : "neutral"} />{taskWords(task.status)}</div>
                {task.error && <div className="mt-1 max-w-md truncate text-xs text-destructive" title={task.error}>{task.error}</div>}
              </TableCell>
              <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{when(task.finished ?? task.started ?? task.created)}</TableCell>
              <TableCell className="text-right">
                {(task.status === "failed" || task.status === "done") && <Button size="sm" variant="ghost" disabled={retry.isPending} onClick={() => retry.mutate(task.id)}>Run again</Button>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function taskWords(status: string) {
  return ({ queued: "Waiting", running: "Working on it", done: "Done", failed: "Error" } as Record<string, string>)[status] ?? status
}
