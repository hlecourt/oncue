# An optional text check, off by default, has the last word on automatic writes

Timing alone cannot tell a line from its neighbour: a passage slid by one line, or a few lines of a translation that skips a short
exchange, pass every timing test. A line-by-line audit of a real library (48 French subtitles) found OnCue's timing-only decisions
fully right on 28, with confident keeps hiding passages 4 to 54 s off and fixes sliding passages onto neighbouring lines. So an
optional text check reads what the lines say: a multilingual sentence model (LaBSE, through ONNX Runtime without PyTorch) matches
each line with the reference line it translates, and only runs of consecutive matches agreeing on one offset count as evidence. It
runs after timing on every check, kept files included, and decides the automatic band: it confirms the current timing, writes a
text fix only when three or more agreeing matches put a passage more than 2.5 s away, and otherwise caps the result at 89. It
never confirms a timing proposal. It is off by default, its dependencies are a `text` extra, its model lives outside the image, and
the Bazarr hook stays numpy only.

## Measured (2026-10-31, `benchmarks/text_truth.py`, 61 library cases with 81 annotated bad passages, 1,047 synthetic cases)

| | Main | Timing-only design | + text check |
|---|---|---|---|
| Library results at 90+ that are wrong | 17 of 43 (5 writes) | 5 of 19 (1 write) | 0 of 3 |
| Library confident keeps with a bad passage | 12 | 4 | 0 |
| Library bad passages repaired (as a write or a proposal) | 19 | 3 | 19 |
| Synthetic results at 90+ that are wrong | 170 of 727 | 54 of 444 | 0 of 22 |
| Synthetic damaged files ending right | 477 of 810 | 625 of 810 | 654 of 810 |
| Synthetic lines right | 97.1 % | 99.1 % | 99.2 % |

The text check is the only variant with no wrong result at 90 or more, on both halves of the cases split by episode. The price is
automation: with it on, almost nothing reaches 90 (3 library cases, 20 synthetic fixes), so OnCue becomes a reviewer whose
proposals are mostly right rather than a tool that writes on its own. Its proposals also move a few lines that were right on
files that are otherwise improved (47 synthetic cases, 6 library lines), which is acceptable only because they wait for a person.

## Considered Options

- Let the text confirm a timing proposal: 2 wrong writes of 39 at 90+ on synthetic cases. Rejected.
- Accept an unread stretch of lines when timing backs it (half of its lines on a reference start or end): more confirmations, but
  2 wrong library results and 15 wrong synthetic ones at 90+. Rejected for a strict limit of 4 unread lines.
- A loose share of 3 % instead of 1.5 %: twice the confirmations, 3 wrong library results and 33 wrong synthetic ones. Rejected.
- paraphrase-multilingual-MiniLM-L12-v2 instead of LaBSE: a quarter of the cost, but 7 wrong of 9 synthetic results at 90+ in an
  earlier pass. Not safe as the matcher; documented only as a light option to measure first.
- The audio second opinion (speech activity) as a guard: it removes some confident keeps but cannot see one-line slides and fails
  on audio in another language; it adds nothing once the text check caps the result.

## Consequences

- Once switched on, the check fails closed: no reference text, no model, or too little readable text gives an unsettled verdict, so the
  result is capped at 89 and listed for review with its doubtful minutes (every minute when nothing could be read). Timing alone never
  reaches the automatic band while the check is on.
- A lone match far from its neighbours' offset is kept as a contradiction: too weak to move a line, it blocks confirmation when it is a
  near-exact match or when loose and contradicted lines together pass the loose limit. Coverage counts only lines a run of agreeing
  matches actually reads; a line the text cannot read moves with its passage only when it no longer fits between its moved neighbours.
- Every write, automatic or from the editor, goes through one output check: nothing before zero (refused, never clamped), no line ending
  before it starts, no neighbours swapped or newly overlapping (the seams with untouched lines included), and the exact bytes read back
  to the decided timings. An automatic fix that fails it stays a proposal.

- Picture references (PGS) need their text: Tesseract in the image built with `ONCUE_TEXT=1`, or an SRT made elsewhere in
  `<state>/reference-texts/`. The editor's picture reference now pairs each start with the next clear or the next picture, capped
  at 10 s, instead of the n-th start with the n-th end.
- Cost on the 4-core home server: about 60 s per 25-minute episode with LaBSE on 3 threads (about 1,600 texts embedded: the lines,
  the reference lines and their neighbouring pairs), about 3 GB of memory per worker.
- Thresholds were tuned on one library dominated by one series; before trusting 90 as a probability, other shows and languages
  must be added to the evaluation.
