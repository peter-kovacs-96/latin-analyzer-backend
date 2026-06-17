# ADR-0009: Recorded-cassette regression tests for word analysis

## Status

Accepted

## Context

Several real-world words exposed distinct bugs in the analysis pipeline
(enclitics like `gregesque`, punctuation glued to a word like `;surdis` /
`„accipe”`, LIS headwords differing from the UDPipe lemma like `faucibus` →
`faux`, and wrong UDPipe lemmas like `Venerem` → `venio`). Each fix needs a
regression test so the behaviour can't silently break again.

The natural place for these is the end-to-end suite, but the existing E2E tests
(`test_analyze_e2e.py`) start a real backend and call the live downstream APIs
(UDPipe/lindat, Latin WordNet/Exeter, Morpheus/Perseids, Latin is Simple). Run
repeatedly, those free services throttle our IP and the requests time out, so
the tests fail for reasons unrelated to our code. A regression test that is red
when the code is correct is worse than useless — it trains everyone to ignore it.

We need word-level regression tests that are deterministic, fast, runnable in CI
and offline, **and** that still exercise the real pipeline against real
downstream data (not hand-written mocks that drift from reality).

## Decision

Use a **record/replay cassette** for the word-level regression tests
(`tests/e2e/test_word_regression.py`).

- The four downstream clients are wrapped by a thin `_CassetteClient` that keys
  each call as `"{service_name}:{arg}"` and (de)serialises `DownstreamResult`
  via Pydantic.
- **Replay mode (default):** responses come only from the committed cassette
  `tests/e2e/fixtures/downstream_cassette.json`. The real `AnalyzerService` runs
  in-process over these recorded responses — no network. A cassette miss is a
  hard failure telling the developer to re-record. Runtime is < 1 s.
- **Record mode (`LATIN_ANALYZER_E2E_RECORD=1`):** each call is delegated to the
  real client and the result stored, refreshing the cassette. This is the only
  path that touches the live APIs and is run by a human on demand, with the
  worker/Upstash env vars set (see ADR-0008).

The cassette holds **real** captured API responses, so the test still validates
our parsing/alignment/matching/confidence logic against genuine downstream data;
it simply freezes that data so the test isn't at the mercy of API availability.

The live, network-dependent tests remain in `test_analyze_e2e.py` for what
genuinely needs the HTTP/streaming layer (endpoints, NDJSON ordering, blank
lines, localisation).

## Consequences

- Word regression tests are deterministic, offline, and fast — safe to run on
  every push / in CI without burning API quota or flaking.
- They catch regressions in **our** pipeline (the thing we own), which is the
  point of a regression test.
- They do **not** detect upstream drift (e.g. LIS changing a translation). That
  is intentional: re-record (record mode) to refresh and surface any change in a
  reviewable diff of the cassette JSON.
- The cassette must be re-recorded when the set of test sentences changes or
  when a fix deliberately changes downstream inputs. Re-recording requires the
  worker + Upstash env (ADR-0008) so the LIS path doesn't hit the Cloudflare
  challenge.
- The cassette is committed (~70 KB) and reviewable as plain JSON.
