# ADR-0007: Server-Side Localisation via `?lang=` Query Parameter

## Status

Accepted

## Context

All controlled-vocabulary string values in the API response (part-of-speech labels, case names, syntactic roles, confidence levels) were defined as lowercase English strings so "the frontend can render them in any language without mapping tables on its side" (original Morphology docstring).

In practice the frontend needs to display these labels directly to end users in Hungarian.  Maintaining a complete translation table in the frontend introduces duplication and coupling: every new label added to the backend must also be updated in the frontend translation file.

Free-text content (`meaning`, `dictionary_form`) comes from external English/Latin dictionaries and cannot be translated server-side.

## Decision

Add a `?lang=` query parameter (default `hu`, supported values `en` | `hu`) to `POST /analyze/stream`.  Translation is applied in `app/i18n.py` to the plain dict produced by `model_dump()` before JSON serialisation, leaving all Pydantic models and internal logic untouched.

Translated fields:
- `morphology.pos`, `.case`, `.number`, `.gender`, `.tense`, `.mood`, `.voice`
- `syntactic_role`
- `confidence`

Untranslated fields (content, not labels):
- `meaning`, `dictionary_form` — English/Latin dictionary data
- `form`, `lemma` — Latin text
- `upos` — universal UDPipe code (technical, frontend may filter on it)
- `source`, `downstreams` — diagnostic data

## Consequences

- The frontend receives ready-to-display Hungarian labels with zero translation logic of its own.
- Adding a new language requires only a new dict in `app/i18n.py`; no other files change.
- The canonical English values remain the internal representation; `lang=en` always returns them unchanged.
- Clients that pass an unsupported language code silently receive English (graceful fallback).
- E2E tests that assert specific label strings must pass `?lang=en` to get stable English values.
