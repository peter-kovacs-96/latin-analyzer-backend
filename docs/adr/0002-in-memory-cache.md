# ADR-0002: Use In-Memory Cache

## Status

Accepted

## Context

The PoC must run on a free platform without Redis, a database, or other managed infrastructure.

## Decision

Use process-local TTL cache for WordNet, Latin-is-Simple, and optional UDPipe results.

## Consequences

Cache is lost on restart and is not shared across workers. This is acceptable for the current PoC deployment model.
