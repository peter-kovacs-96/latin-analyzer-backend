# ADR-0011: Morpheus lemmata as extra LIS name-match candidates

## Status

Accepted

## Context

UDPipe sometimes assigns the wrong part of speech to a word and, with it, a
lemma that Latin is Simple files under a different headword. The clearest case
is the perfect participle `sculptus` ("carved"): UDPipe tags it `NOUN` with
lemma `sculptus`, but LIS only has it under the **verb** `sculpo` (`sculpere`).

`find_lis_match` matched LIS entries by the UDPipe lemma (and surface form), so
`sculptus`/`sculpo` never matched by name, and the POS fallback (ADR-0009) also
refused it — the single LIS hit is a verb while UDPipe said noun. Result:
`no_meaning`, even though the meaning is plainly available.

Morpheus, however, already returns the correct analyses for the surface form:
for `sculptus` it gives `sculpo` (verb participle) and `sculptus` (adjective).
The pipeline only used these to *correct the lemma* when UDPipe's lemma was
absent from Morpheus's list — it did not use the *other* Morpheus lemmata when
matching LIS.

## Decision

Pass all Morpheus lemmata for a word into `find_lis_match` as additional
**name-match candidates** (`extra_lemmas`). An LIS entry whose `short_name` or
first `full_name` token equals any Morpheus lemma now scores as a lemma match,
exactly like the UDPipe lemma does.

This recovers `sculptus` (matches the LIS verb `sculpo`) without relaxing the
part-of-speech guard: it is still a precise *name* match, not a POS bypass. The
existing UPOS-based scoring bonus and the single-POS fallback are unchanged.

## Consequences

- Words UDPipe mis-tags but Morpheus analyses correctly resolve when LIS has the
  entry under a Morpheus-supplied headword (e.g. `sculptus` → "carve, engrave").
- Measured over the test poem (218 words): 1 word improved (`sculptus`), 0
  regressions. Three other words shifted to a different—still correct—LIS sense
  because a Morpheus lemma matched a better entry (e.g. `friget` moved from the
  wrong `frigo` "roast" to the correct `frigeo` "be cold").
- Does not help when LIS simply lacks the word (e.g. `abi`/`abeo`, where LIS's
  forms_only search returns only noise) or when no source has it at all (the
  rare proper-noun adjective `Ausonis`). Those remain `no_meaning`/`form_only`.
- Morpheus lemmata are morphologically valid analyses of the actual surface
  form, so they are safe name candidates; the surface form itself is never
  bypassed, and a wrong UDPipe lemma still cannot inject an unrelated meaning.
