# ADR-0013: Morpheus part-of-speech correction for UDPipe guesses

## Status

Accepted

## Context

UDPipe's tagger is trained on `latin-ittb` (the Index Thomisticus — medieval
scholastic prose). On the Renaissance/early-modern verse we analyse it mis-tags
rare, poetic, Greek-declension or enclitic forms, dumping them into `ADV` (its
catch-all) with **no** morphological features: `tigride` (Greek ablative of
*tigris*) → ADV, `telumque`/`telaque` (unsplit enclitic) → ADV, `gravique` → ADV,
`proterve`/`hic` → ADV. The lemma is sometimes still right, but the part of
speech (and the features the prompt/UI rely on) is wrong.

Testing seven UDPipe Latin models showed none is uniformly better, and a model
swap risks regressions across the other ~400 words; `gravique`/`eam` aren't fixed
by any model. Morpheus, however, knows the correct POS for these forms
(`tigris`=noun, `gravis`=adjective, `telum`=noun).

## Decision

Carry Morpheus's POS tags alongside its lemmata (the Morpheus result `data`
becomes `{"lemmata": [...], "upos": [...]}`), and add a **narrow** POS correction
in the analyzer (`_morpheus_pos_fix`): replace UDPipe's POS with Morpheus's only
when **all** of these hold —

- UDPipe put the word in `ADV`/`X` (its dumping ground for unplaceable forms),
- UDPipe gave **no** morphological features (so it was only guessing the POS —
  confidently-tagged words always keep their analysis),
- Morpheus assigns a **single** distinct POS, and
- that POS is a **content** POS (`NOUN`/`PROPN`/`VERB`/`ADJ`/`PRON`/`NUM`).

The asymmetry is deliberate: we only ever move ADV/X → content word, never touch
function words, so UDPipe's reliable function-word tags and its fine-grained
distinctions (e.g. SCONJ vs CCONJ) are preserved.

## Consequences

- Measured over both sample poems (444 words): an earlier naive rule (any
  POS, override on no-features) changed 33 words and **regressed** function words
  (`si`/`dum`/`de`/`ante`/`non` mis-tagged). The narrowed rule changes **8**
  words — `tigride`/`telumque`/`telaque`→NOUN, `gravique`/`proterve`/`Stulte`→ADJ,
  `hic`→PRON — all correct or defensible, with **0 regressions** and **0
  meaning/confidence changes**.
- The benefit is POS-label accuracy only (it adds no new meanings and changes no
  confidence): cleaner data for the AI-translation prompt and the morphology
  display. It does not help words Morpheus can't analyse (`faeta`) or where the
  POS is genuinely ambiguous and Morpheus offers several (`sculptus`, `eam`).
- Regression-tested via the cassette suite (`telumque`→NOUN); the recorded
  Morpheus payloads now include `upos`, so the correction is exercised offline.
