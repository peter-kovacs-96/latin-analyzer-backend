# ADR-0002: Use In-Memory Cache

## Status

Partially superseded by ADR-0008 (Latin is Simple only)

## Context

The PoC must run on a free platform without Redis, a database, or other managed infrastructure.

## Decision

Use process-local TTL cache for WordNet, Latin-is-Simple, and optional UDPipe results.

## Consequences

Cache is lost on restart and is not shared across workers. This is acceptable for WordNet and UDPipe (free services, fast to re-query).

For Latin is Simple the tradeoff is not acceptable: LIS is behind a Cloudflare JS challenge from cloud datacenters and requires a paid scraping proxy (ZenRows) to bypass it. Losing the cache on restart would waste ZenRows credits. Latin is Simple therefore uses an additional Upstash Redis L2 cache that survives restarts. See ADR-0008.
