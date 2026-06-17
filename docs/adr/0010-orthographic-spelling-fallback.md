# ADR-0010: Orthographic-spelling fallback for dictionary lookups

## Status

Accepted

## Context

Early-modern / medieval Latin texts (e.g. the test poem `progihoz.docx`) use
spelling conventions that differ from the classical headwords in our
dictionaries:

- `y` for `i` — `hyems` (classical `hiems`), `syluas` (`silvas`)
- consonantal `u` for `v` — `mouet` (`movet`), `syluas` (`silvas`)
- `j` for `i`

Latin WordNet already tolerates u/v variants, but not y/j. Latin is Simple's
`forms_only=true` search is keyed by the exact surface form, so a non-classical
spelling returns zero hits even when the classical form matches. The result:
words like `syluas` (→ `no_meaning`), `mouet` (→ `no_meaning`) and `hyems` (→
`form_only`) despite being ordinary, dictionary-present words.

A measurement over the whole document (218 analysed words) showed exactly three
such words affected.

We considered, and rejected, retrying LIS **by lemma**: a wrong UDPipe lemma
would then inject a confidently-wrong meaning (e.g. `Venerem` → lemma `venio`
→ "to come"). The surface form is always correct, so lookups must stay
form-based.

## Decision

Add `normalize_spelling()` (in `app/latin.py`): given a surface form or lemma it
returns a classical-spelling variant — `j→i`, `y→i`, and consonantal `u→v` (a
`u` before a vowel, except after `q` so `que`/`aqua` are untouched) — or `None`
when already classical.

Use it as a **fallback only**, in the analyzer's lookup loaders:

- `_meaning_one` (LIS): if the surface-form search returns no entries, retry
  once with the normalised **form**. Still a form lookup, so a wrong lemma can
  never select a wrong meaning.
- `_wordnet_one` (WordNet): if the lemma lookup is `NOT_FOUND`, retry once with
  the normalised **lemma** (WordNet handles u/v itself, so this mainly fixes
  y/j).

Both retries call the same client methods, so the normalised key passes through
the existing cache hierarchy (LIS: L1 in-memory → L2 Upstash → live; WordNet:
L1 → live) and is itself cached. The displayed surface form and lemma are never
rewritten — normalisation only ever produces an alternative lookup key.

## Consequences

- The fallback fires only on a dictionary miss, so words whose spelling already
  resolves (incl. legitimate `y` words like `lyra`, which LIS finds directly)
  are never normalised and cannot regress. Measured impact on the test
  document: 3 words improved, 0 changed otherwise.
- No extra downstream calls in the common case — the second lookup happens only
  for words that missed, and is served from cache on repeat.
- It does not address part-of-speech mismatches (e.g. `sculptus`, which UDPipe
  tags NOUN but LIS only has under the verb `sculpo`); that is a separate
  concern.
- Regression coverage: `syluas`, `hyems`, `mouet` are pinned in the cassette
  word-regression suite (ADR-0009).
