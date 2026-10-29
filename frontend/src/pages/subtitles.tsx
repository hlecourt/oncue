// SPDX-License-Identifier: GPL-3.0-or-later
import { useInfiniteQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { FilesIcon } from "lucide-react"
import { useDeferredValue, useState } from "react"

import { ConfidenceBadge, EmptyState, PageHeader, ToneDot, toneOf } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api, unwrap } from "@/lib/api/client"
import { when } from "@/lib/format"

const PAGE_SIZE = 100

export function SubtitlesPage() {
  const [search, setSearch] = useState("")
  const query = useDeferredValue(search)
  const listing = useInfiniteQuery({
    queryKey: ["subtitles", query],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => unwrap(api.GET("/api/subtitles", { params: { query: { q: query, offset: pageParam, limit: PAGE_SIZE } } })),
    getNextPageParam: (page, pages) => (pages.length * PAGE_SIZE < page.total ? pages.length * PAGE_SIZE : undefined),
  })
  const rows = listing.data?.pages.flatMap((page) => page.rows) ?? []
  const total = listing.data?.pages[0]?.total ?? 0

  return (
    <>
      <PageHeader title="Subtitles" description="Every subtitle the guard has checked. Open one to listen to it and fix its timing by hand, even when it is not in Review." />
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Input placeholder="Search a file or a folder" value={search} onChange={(event) => setSearch(event.target.value)} className="h-8 w-full sm:w-80" aria-label="Search the subtitles" />
        {listing.data && <span className="text-sm text-muted-foreground">{total} subtitle{total === 1 ? "" : "s"}</span>}
      </div>
      {listing.isPending && <Skeleton className="h-64 w-full" />}
      {listing.data && rows.length === 0 && <EmptyState icon={<FilesIcon />} title={query ? "No subtitle matches" : "No subtitle checked yet"}>{query ? "Try a shorter search." : "Scan the library from the System page."}</EmptyState>}
      {rows.length > 0 && (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader className="bg-muted/50">
              <TableRow><TableHead>Subtitle</TableHead><TableHead className="hidden md:table-cell">Result</TableHead><TableHead className="hidden md:table-cell">Confidence</TableHead><TableHead className="hidden md:table-cell">Checked</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="max-w-96 align-top">
                    <Link to="/subtitles/$subtitleId" params={{ subtitleId: row.id }} className="block truncate font-medium hover:underline">{row.name}</Link>
                    <span className="block truncate text-xs text-muted-foreground">{row.folder}</span>
                    <span className="mt-1 flex items-center gap-2 text-sm md:hidden"><ToneDot tone={toneOf(row.tone)} />{row.words}</span>
                  </TableCell>
                  <TableCell className="hidden align-top md:table-cell"><div className="flex items-center gap-2"><ToneDot tone={toneOf(row.tone)} />{row.words}</div></TableCell>
                  <TableCell className="hidden align-top md:table-cell"><ConfidenceBadge confidence={row.confidence} band={row.band} /></TableCell>
                  <TableCell className="hidden align-top text-xs whitespace-nowrap text-muted-foreground md:table-cell">{when(row.checked)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {listing.hasNextPage && <Button variant="outline" className="mt-3" onClick={() => listing.fetchNextPage()} disabled={listing.isFetchingNextPage}>Show more</Button>}
    </>
  )
}
