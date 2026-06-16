# ADR-0008: ZenRows + Upstash Redis for Latin is Simple Cloudflare bypass

## Status

Accepted

## Context

Latin is Simple is the only source of English word meanings in the pipeline. When the backend runs in a cloud datacenter (Render), LIS returns HTTP 403 with `cf-mitigated: challenge` — a Cloudflare JS challenge that requires a real browser to solve. Standard HTTP clients (httpx, curl-cffi) and browser-header spoofing all fail. The challenge cannot be solved server-side without executing JavaScript.

Additionally, the Render free tier spins the service down after 15 minutes of inactivity. On cold start the in-memory cache is empty, meaning every word would require a new LIS request.

## Decision

**ZenRows as Cloudflare bypass (fallback only):** When a direct LIS request returns HTTP 403, retry through the ZenRows scraping proxy API with `js_render=true`. ZenRows executes the Cloudflare JS challenge in a headless browser and returns the actual API response. This is only triggered on a 403 — if the direct request succeeds (e.g. in local development), ZenRows is never called.

**Upstash Redis as persistent L2 cache:** After a successful LIS response (whether direct or via ZenRows), store the result in Upstash Redis (REST API, no additional library). On subsequent requests — including after a cold start — the L2 cache is checked before making any network call to LIS or ZenRows.

The full lookup order is:

1. L1 in-memory TTLCache (fast, lost on restart)
2. L2 Upstash Redis (persistent, survives Render sleep/restart)
3. Direct LIS request
4. ZenRows fallback (only if LIS returned 403 and `LATIN_ANALYZER_ZENROWS_API_KEY` is set)

A cache hit at L2 also warms L1 for the remainder of the session.

## Consequences

- ZenRows free tier provides 1 000 credits/month; JS render costs 5 credits per request (~200 unique lemma lookups). Once a lemma is cached in Upstash it is never fetched again via ZenRows.
- Latin vocabulary is finite. A few hundred common lemmas cover most classical texts; once seen they are stored indefinitely in Upstash (eviction policy: LRU, only triggered at 256 MB which is not expected to be reached).
- Local development is unaffected: direct LIS requests succeed without Cloudflare interference, so ZenRows is never called and the ZenRows key is not required.
- If ZenRows credits are exhausted, LIS falls back to `no_meaning` gracefully (partial failure, HTTP 200, `confidence: no_meaning`).
- Future alternative: self-hosted FlareSolverr service returning a `cf_clearance` cookie. More complex to operate but eliminates the per-request credit cost.
