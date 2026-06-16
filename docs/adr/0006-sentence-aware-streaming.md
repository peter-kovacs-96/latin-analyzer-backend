# ADR-0006: Sentence-Aware Streaming

## Status

Accepted

## Context

Latin poetry uses heavy enjambment — subjects appear on one line, predicates on the next, objects further still. The previous line-by-line UDPipe calls meant each line was parsed in isolation, so cross-line dependency relationships (subject↔predicate, modifier↔head) could not be resolved. UDPipe's dependency parser requires the full sentence to assign reliable syntactic roles.

The streaming NDJSON format was also line-granular, which provided no way to signal to the frontend that adjacent lines belong to the same syntactic unit.

## Decision

Group input lines into sentence groups before calling UDPipe. Send each group as a single UDPipe request to get full sentence context, then slice the token list back to per-line `AnalysisResponse` objects. Emit one NDJSON object per sentence group (not per line).

Two grouping modes are supported via `?mode=`:

- `sentence` (default) — boundary at blank line **or** line ending `.` `!` `?`
- `stanza` — boundary at blank line only (for poetry without consistent punctuation)

Semicolons are excluded from boundaries; they are commonly used mid-sentence in Latin prose.

The NDJSON output format changes from per-line objects to:
- `{"sentence_number": N, "lines": [...]}` — one object per sentence group; each element of `lines` is the full `AnalysisResponse` for that input line
- `{"line_number": N, "empty": true}` — unchanged, emitted immediately for blank lines

## Consequences

- Cross-line syntactic roles (subject on line 1, predicate on line 2) are correctly resolved by UDPipe.
- The number of UDPipe calls equals the number of sentence groups, not the number of lines — fewer round-trips for multi-line sentences.
- WordNet and LIS lookups are deduplicated across all lines in a group, reducing downstream call volume.
- Clients must handle the new `{"sentence_number", "lines"}` envelope instead of flat per-line objects.
- Streaming latency per chunk increases: the client must wait for all lines in a group before receiving any of them.
