# Latin Analyzer Backend

FastAPI proxy/orchestrator for context-aware Latin morphological analysis and translation.

## What it does

Translates Latin text **sentence by sentence**, streaming results to the frontend as each sentence group finishes.
Analysis is contextual: lines belonging to the same sentence are sent to UDPipe together so syntactic roles (subject, predicate, object…) can be resolved across line boundaries — critical for Latin's heavy use of enjambment.

## Pipeline

For each sentence group:

1. **UDPipe** (`latin-ittb-ud-2.17-251125`) — tokenises the combined text of the group and assigns each token:
   - Lemma (dictionary headword)
   - UPOS part-of-speech tag
   - Full morphological features (case, number, gender, tense, mood, voice…)
   - Syntactic dependency role (subject, object, modifier…)
   - Context resolves ambiguous forms: same surface form → different lemmas based on sentence structure.

2. **Morpheus** (`morph.perseids.org` — Perseids/Tufts University) — cross-validates UDPipe's lemma against the full set of morphological candidates for the surface form. If UDPipe's lemma is not among Morpheus's candidates, Morpheus's first candidate is used instead.
   Example: *ferit* → UDPipe gives *fero* (wrong), Morpheus corrects to *ferio* (to strike).

3. **Latin WordNet** (`/api/lemmas/{lemma}/`) — validates the lemma and provides the morpho code.
   Returns `not_found` when a lemma is absent from the lexicon (non-Latin or very rare word).

4. **Latin is Simple** (`/api/vocabulary/search/?forms_only=true`) — looks up the English meaning.
   - `forms_only=true` matches by actual Latin form, not by full-text search (the default `false` is unreliable — it returns results whose *German* translation contains the query word).
   - Smart matching: among all hits for a lemma, picks the entry where `short_name == lemma` **or** the first token of `full_name == lemma`, then breaks ties by UDPipe POS (`NOUN→noun`, `VERB→verb`, `ADJ→adjective`…).

## API

### `POST /analyze/stream`

Analyse a multi-line Latin text (file contents) and stream results sentence by sentence.

**Request:** `Content-Type: text/plain; charset=utf-8`, body = raw Latin text.

**Query params:**

| param | values | notes |
|---|---|---|
| `?mode=sentence` | default | sentence boundary at blank line **or** line ending `.` `!` `?` |
| `?mode=stanza` | — | boundary at blank line only (for poetry without consistent punctuation) |
| `?lang=hu` | default | morphological labels, syntactic roles, and confidence in Hungarian |
| `?lang=en` | — | all labels in English |

Semicolons are intentionally not treated as boundaries (often used mid-sentence in Latin).

Translated fields: `morphology.pos`, `morphology.case`, `morphology.number`, `morphology.gender`,
`morphology.tense`, `morphology.mood`, `morphology.voice`, `syntactic_role`.
Free-text fields (`meaning`, `dictionary_form`, `form`, `lemma`) are always in English/Latin.
`confidence` is a stable machine code (`full` / `no_meaning` / `form_only`) and is **never**
translated — the client branches on it (warning markers), so the frontend localises its label.

**Response:** `Content-Type: application/x-ndjson` — one JSON object per sentence group or blank line, in input order.

Blank lines emit:
```json
{"line_number": 3, "empty": true}
```

Non-empty sentence groups emit one object containing all lines in the group:
```json
{
  "sentence_number": 1,
  "lines": [
    {
      "line_number": 1,
      "text": "Arma virumque cano,",
      "request_id": "...",
      "summary": {"used_udpipe": true, "word_count": 3, "partial_failure": false},
      "words": [...]
    },
    {
      "line_number": 2,
      "text": "Troiae qui primus ab oris",
      "request_id": "...",
      "summary": {"used_udpipe": true, "word_count": 5, "partial_failure": false},
      "words": [...]
    }
  ]
}
```

Sentences are emitted **only after all API calls for the group complete** (accuracy over latency).

### `GET /health`

Returns `{"ok": true}`.

## WordAnalysis fields

| Field | Description |
|---|---|
| `form` | Surface form as it appears in the text |
| `lemma` | Dictionary headword (from UDPipe, or WordNet if UDPipe missed the word; `null` if unrecognized) |
| `upos` | Raw UDPipe UPOS tag (`NOUN`, `VERB`, `ADJ`, …) |
| `morphology` | Structured morphological features: `pos`, `case`, `number`, `gender`, `person`, `tense`, `mood`, `voice` |
| `syntactic_role` | Dependency role in sentence (`subject`, `predicate`, `object`, `modifier`…); `null` if unrecognized |
| `dictionary_form` | LIS `full_name` e.g. `"amor, amoris [m.] C"` — shows declension/conjugation class |
| `meaning` | English meaning from Latin is Simple |
| `confidence` | `full` (dictionary confirmed + meaning found) / `no_meaning` (WordNet ok, LIS missing) / `form_only` (unrecognized word) |
| `source` | Which service provided the lemma (`UDPipe`, `WordNet`, or `-`) |
| `downstreams` | Per-service diagnostic (`ok` / `not_found` / `timeout` / …) |

## Downstream API findings

| Service | Status | Notes |
|---|---|---|
| UDPipe | ✅ Reliable | Always processes text; best model for classical Latin: `latin-ittb-ud-2.17-251125`. Disambiguates context-dependent forms within a sentence group. |
| Morpheus (`morph.perseids.org`) | ✅ Reliable | Free JSON API. Returns candidate lemmata for a surface form. Used to correct UDPipe lemmatisation errors where the form is unambiguous. |
| Latin WordNet `/api/lemmas/{lemma}/` | ✅ Reliable | Stable endpoint; returns `count:0` for unknown lemmas. Handles v/u orthographic variants (`vinco`→`uinco`). The `/lemmatize/` endpoint is broken. |
| Latin is Simple (forms_only=true) | ✅ Good | Returns form-based matches. Must filter by exact lemma in `short_name` or first token of `full_name`. Cloudflare blocks direct server-to-server requests with a JS challenge (HTTP 403); see ADR-0008 for the ZenRows workaround. |
| Latin is Simple (forms_only=false) | ❌ Unreliable | Full-text search: returns entries whose translations *contain* the query word as a substring (e.g. querying `malum` returns `morbus` because German for morbus includes "Malum"). |

## Downstream statuses

| Status | Meaning |
|---|---|
| `ok` | Service responded with data |
| `not_found` | Service responded but has no entry for this lemma |
| `skipped` | Service was not called (e.g. no lemma available) |
| `timeout` / `network_error` | Transient failure; retried once |
| `http_error` | Unexpected HTTP error code |
| `rate_limited` | HTTP 429 |
| `invalid_response` | Non-JSON or unexpected shape |

## Local run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Interactive API docs: `http://127.0.0.1:8000/docs`

Streaming — sentence mode (default):
```powershell
Invoke-WebRequest -Method POST -Uri http://127.0.0.1:8000/analyze/stream `
  -ContentType "text/plain; charset=utf-8" `
  -Body "Amor vincit omnia`net nos cedamus Amori"
```

Streaming — stanza mode (no punctuation boundaries):
```powershell
Invoke-WebRequest -Method POST -Uri "http://127.0.0.1:8000/analyze/stream?mode=stanza" `
  -ContentType "text/plain; charset=utf-8" `
  -Body "Arma virumque cano`nTroiae qui primus ab oris"
```

## Tests

```bash
# API probing (direct calls to live external services)
pytest tests/e2e/test_api_probing.py -v

# Full E2E (starts local backend, tests all endpoints — hits live downstreams)
pytest tests/e2e/test_analyze_e2e.py -v

# Word-level regression tests (deterministic, offline — no live API calls)
pytest tests/e2e/test_word_regression.py -v
```

### Word regression tests (cassette-based)

`test_word_regression.py` pins the analysis of words that previously exposed
pipeline bugs (`gregesque`, `surdis`, `ferit`, `accipe`, `Venerem`, `faucibus`,
`eiusdem`). To stay reliable it does **not** call the live downstream APIs on
every run — those throttle our IP and make the suite flaky (see ADR-0009).

Instead it runs the real `AnalyzerService` in-process against a **cassette** of
recorded *real* downstream responses, committed at
`tests/e2e/fixtures/downstream_cassette.json`:

- **Replay (default):** responses come only from the cassette → fast (< 1 s),
  deterministic, offline. This is what runs in CI.
- **Record:** delegates to the real clients and refreshes the cassette. Run this
  by hand when you add test sentences or want to re-capture upstream data:

  ```bash
  LATIN_ANALYZER_E2E_RECORD=1 \
  LATIN_ANALYZER_LATIN_IS_SIMPLE_BASE_URL=https://latin-is-simple-proxy.gondir96.workers.dev \
  LATIN_ANALYZER_UPSTASH_REDIS_URL=... LATIN_ANALYZER_UPSTASH_REDIS_TOKEN=... \
  pytest tests/e2e/test_word_regression.py
  ```

  Then review and commit the updated cassette JSON.

The cassette holds genuine API output, so the tests still validate our
parsing/alignment/matching/confidence logic against real data; they just freeze
that data so the test isn't at the mercy of API availability. They intentionally
do not detect upstream changes — re-record to surface those.

## Deployment

`render.yaml` and `.github/workflows/docker-publish.yml` included.
Push to `main` → GitHub Actions builds and pushes the Docker image to `ghcr.io` → Render deploys automatically via deploy hook.

Required environment variables on Render:

| Variable | Description |
|---|---|
| `LATIN_ANALYZER_ZENROWS_API_KEY` | ZenRows API key — bypasses Cloudflare JS challenge on LIS requests |
| `LATIN_ANALYZER_UPSTASH_REDIS_URL` | Upstash Redis REST URL — persistent L2 cache for LIS results |
| `LATIN_ANALYZER_UPSTASH_REDIS_TOKEN` | Upstash Redis REST token |
| `RENDER_DEPLOY_HOOK_URL` | Render deploy hook URL (set as GitHub secret) |

## Notes

- Two-layer cache for Latin is Simple: L1 in-memory TTL cache (6 h, 5 000 items), L2 Upstash Redis (persistent across restarts). ZenRows is only called on a true miss in both layers.
- WordNet, UDPipe, and Morpheus use in-memory TTL cache only (no paid external services involved).
- Async downstream calls with bounded concurrency (default 10).
- TLS verification intentionally disabled (`verify_tls: false`).
- Per-word downstream diagnostics included in every response.
- Orthographic-spelling fallback: on a dictionary miss, WordNet/LIS lookups are
  retried once with a classical-spelling variant of the key (`j→i`, `y→i`,
  consonantal `u→v`) so medieval spellings like `hyems`/`syluas`/`mouet` resolve.
  Fallback only — the displayed form/lemma are unchanged. See ADR-0010.
- Morpheus lemmata are also used as LIS name-match candidates, so words UDPipe
  mis-tags (e.g. the participle `sculptus`, tagged NOUN, found in LIS under the
  verb `sculpo`) still resolve. See ADR-0011.
- Morpheus morpheme hyphens are stripped (`ob-rigesco` → `obrigesco`), and the
  personal-pronoun paradigm (`ego, tu, -`) is matched on any principal part so
  2nd/3rd-person forms (`tibi`, `te`) resolve. See ADR-0012.
