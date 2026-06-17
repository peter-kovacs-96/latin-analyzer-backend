# ADR-0012: Morpheus hyphen stripping and personal-pronoun paradigm matching

## Status

Accepted

## Context

A second sample poem (`test1.docx`) surfaced two more systematic gaps:

1. **Hyphenated Morpheus lemmata.** Morpheus marks morpheme boundaries with a
   hyphen for prefixed verbs, e.g. `obriguere` → `ob-rigesco`. The Morpheus
   cross-validation replaced UDPipe's lemma with this hyphenated string, which is
   not a dictionary headword — WordNet has `obrigesco`/`obrigeo` but not
   `ob-rigesco` — so the word fell through to `form_only` (lemma and morphology
   discarded) even though the lemma was essentially correct.

2. **Personal pronouns.** Latin is Simple files the personal pronoun as a single
   suppletive paradigm entry: `short_name = "ego"`, `full_name = "ego, tu, -"`.
   `find_lis_match` only compared the lemma against `short_name` and the *first*
   `full_name` token, so first-person forms (`mihi`, lemma `ego`) matched but
   second-person forms (`tibi`, `te` → lemma `tu`) did not, leaving common words
   like `tibi` as `form_only`.

## Decision

1. **Strip hyphens** from Morpheus lemmata in `_parse_morpheus_response`
   (`ob-rigesco` → `obrigesco`). Latin headwords contain no hyphens, so this is
   safe and yields the dictionary form.

2. **Match the pronoun paradigm**: in `find_lis_match`, for entries whose
   `intern_type` is `perspron`, also match the lemma against *any* principal part
   in `full_name` (so `tu`/`sui` match the `ego, tu, -` entry). This is the
   **weakest** scoring tier (3, below the surface-form tiers), so a pronoun that
   has its own specific LIS entry (e.g. `te` → "you (Nom./Acc.)") still wins;
   only forms without a usable specific entry (`tibi`, whose own LIS entry is
   "still in translation") fall back to the generic paradigm meaning.

## Consequences

- `obriguere` recovers its lemma (`obrigesco`) and morphology (`form_only` →
  `no_meaning`; LIS still lacks the rare `-ēre` perfect surface form, so no
  English meaning, but the analysis is now usable).
- `tibi`/`te` and other 2nd/3rd-person pronoun forms resolve to `full`.
- Measured across both sample poems (444 words): 3 improved, **0 regressions**,
  0 incidental changes — in particular `te` keeps its specific "you" gloss
  because the paradigm tier is weakest.
- Remaining unresolved words are genuine source limitations, left as-is: `Tete`
  (archaic reduplicated pronoun, absent everywhere), `Acidaliae` (Venus epithet,
  not in LIS), `Quodcumque` (LIS entry "still in translation"), `tigride` (LIS
  does not index the Greek ablative), `Ausonis`/`abi` (per ADR-0011).
- `ae`/`oe` spelling normalisation was considered (for `faeta` → `feta`) but
  rejected: it would help a single, UDPipe-mis-tagged word with a questionable
  result, against the risk of touching the many legitimate `ae` words.
