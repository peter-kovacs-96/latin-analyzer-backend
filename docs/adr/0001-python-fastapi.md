# ADR-0001: Use Python FastAPI

## Status

Accepted

## Context

The service is a proxy/orchestrator around external HTTP APIs. It is I/O-bound, and the existing PoC is already in Python.

## Decision

Use Python with FastAPI and async HTTP clients.

## Consequences

The service can remain lightweight for free-tier hosting. Engineering discipline must be enforced through typed models, explicit downstream error handling, tests, timeouts, and observability hooks.
