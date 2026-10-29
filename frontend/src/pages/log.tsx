// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from "@tanstack/react-query"
import { cn } from "cn"
import { ChevronRightIcon, CopyIcon, ScrollTextIcon } from "lucide-react"
import { useDeferredValue, useState } from "react"
import { toast } from "sonner"

import { EmptyState, PageHeader } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type Schemas, unwrap } from "@/lib/api/client"

const LEVEL_TONE: Record<string, string> = { error: "text-destructive", warning: "text-warning", debug: "text-muted-foreground" }

export function LogPage() {
  const [search, setSearch] = useState("")
  const [level, setLevel] = useState("all")
  const query = useDeferredValue(search)
  const log = useQuery({
    queryKey: ["log", query, level],
    queryFn: () => unwrap(api.GET("/api/log", { params: { query: { q: query, level: level === "all" ? undefined : level, limit: 300 } } })),
    refetchInterval: query ? false : 15_000,
  })

  return (
    <>
      <PageHeader title="Log" description="The technical log, newest first. Open a line for its details." />
      <div className="mb-3 flex flex-wrap gap-2">
        <Input placeholder="Search a file, an event, a word" value={search} onChange={(event) => setSearch(event.target.value)} className="h-8 w-full sm:w-80" aria-label="Search the log" />
        <Select value={level} onValueChange={setLevel}>
          <SelectTrigger size="sm" className="w-36" aria-label="Level"><SelectValue /></SelectTrigger>
          <SelectContent>
            {["all", "error", "warning", "info", "debug"].map((value) => <SelectItem key={value} value={value}>{value === "all" ? "Every level" : value}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      {log.isPending && <Skeleton className="h-96 w-full" />}
      {log.data?.length === 0 && <EmptyState icon={<ScrollTextIcon />} title={query || level !== "all" ? "No log entry matches" : "Nothing logged yet"} />}
      {log.data && log.data.length > 0 && (
        <div className="overflow-hidden rounded-lg border bg-card font-mono text-xs">
          {log.data.map((event, index) => <LogLine key={`${event.time}-${index}`} event={event} />)}
        </div>
      )}
    </>
  )
}

function LogLine({ event }: { event: Schemas["LogEvent"] }) {
  const details = JSON.stringify(event.details, null, 2)
  return (
    <Collapsible className="border-b last:border-b-0">
      <CollapsibleTrigger className="group grid w-full grid-cols-[1rem_9.5rem_4.5rem_6rem_1fr] items-start gap-2 px-3 py-1.5 text-left hover:bg-accent/50 max-md:grid-cols-[1rem_1fr]">
        <ChevronRightIcon className="mt-0.5 size-3 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
        <span className="text-muted-foreground tabular-nums max-md:col-start-2">{event.time.replace("T", " ")}</span>
        <span className={cn("uppercase max-md:col-start-2", LEVEL_TONE[event.level])}>{event.level}</span>
        <span className="truncate text-muted-foreground max-md:col-start-2">{event.source}</span>
        <span className="min-w-0 break-words max-md:col-start-2"><span className="font-semibold">{event.event}</span> {event.message}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="px-3 pb-3">
        <div className="flex justify-end pb-1">
          <Button size="sm" variant="ghost" onClick={() => navigator.clipboard.writeText(details).then(() => toast.success("Copied."))}><CopyIcon />Copy</Button>
        </div>
        <pre className="overflow-x-auto rounded-md border bg-background p-3">{details}</pre>
      </CollapsibleContent>
    </Collapsible>
  )
}
