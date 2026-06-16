# ADR-0005: Use Latin WordNet Lemma Endpoints After UDPipe

## Status

Accepted

## Context

The Latin WordNet documentation exposes two different concepts:

- `/lemmatize/<form>` for possible lemmas of an inflected form.
- `/api/lemmas/<lemma>/` for detailed information about known lemma headwords.

Live checks showed that `/api/lemmatize/<form>/` is not a valid route, while the documented `/lemmatize/<form>/`
route currently returns server errors for common forms. The `/api/lemmas/<lemma>/` endpoint is stable and returns
JSON for known lemma headwords.

## Decision

Use UDPipe as the first pass for tokenization, contextual lemma selection, and morphological analysis. Query Latin
WordNet with the resulting lemma via `/api/lemmas/<lemma>/`.

## Consequences

The application no longer depends on the currently unreliable Latin WordNet lemmatization route for its main path.
Latin WordNet still contributes lexical and morphological metadata for recognized lemmas. Inflected-form
lemmatization is delegated to UDPipe.
