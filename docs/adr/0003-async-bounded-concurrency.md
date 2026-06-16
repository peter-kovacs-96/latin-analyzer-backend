# ADR-0003: Async Downstream Orchestration

## Status

Accepted

## Context

The main latency cost is waiting for external HTTP APIs. Calling them sequentially limits throughput and increases request latency.

## Decision

Use `httpx.AsyncClient`, `asyncio.gather`, and semaphores to bound downstream concurrency.

## Consequences

The service can execute independent WordNet and Latin-is-Simple calls concurrently while avoiding uncontrolled fan-out.
