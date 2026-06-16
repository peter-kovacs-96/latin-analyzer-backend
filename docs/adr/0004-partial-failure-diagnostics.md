# ADR-0004: Return Partial Results With Diagnostics

## Status

Accepted

## Context

The frontend needs to know which downstream service was usable for each word and why a service was not used or failed.

## Decision

Return HTTP 200 for partial success and include structured per-word downstream diagnostics. Use HTTP 503 only when no meaningful analysis can be produced.

## Consequences

Clients can render partial results, surface actionable service status, and avoid losing all data because one downstream failed.
