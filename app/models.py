import enum
from typing import Any

from pydantic import BaseModel, Field


class DownstreamStatus(str, enum.Enum):
    """All possible outcomes of a single downstream API call."""
    OK               = "ok"
    NOT_FOUND        = "not_found"
    SKIPPED          = "skipped"
    TIMEOUT          = "timeout"
    HTTP_ERROR       = "http_error"
    NETWORK_ERROR    = "network_error"
    INVALID_RESPONSE = "invalid_response"
    RATE_LIMITED     = "rate_limited"
    UNAVAILABLE      = "unavailable"
    UNKNOWN_ERROR    = "unknown_error"


class WordConfidence(str, enum.Enum):
    """How much of the word analysis we were able to determine reliably.

    FULL       – UDPipe succeeded AND LIS returned an English meaning.
    NO_MEANING – UDPipe succeeded (lemma + morphology + syntactic role reliable)
                 but LIS did not return a meaning.
    FORM_ONLY  – UDPipe produced no token; only the surface form is certain.
                 Lemma is null and all morphology fields are null.
    """
    FULL       = "full"
    NO_MEANING = "no_meaning"
    FORM_ONLY  = "form_only"


class DownstreamDiagnostic(BaseModel):
    status: DownstreamStatus
    reason: str | None = None
    message: str | None = None
    retryable: bool = False
    http_status: int | None = None
    latency_ms: int | None = None
    cached: bool = False


class Morphology(BaseModel):
    """Structured morphological features for a single Latin token.

    All fields are None when the information is not applicable to this part of
    speech (e.g. verbs have no `case`) or when it could not be determined.

    Values use lowercase English strings as canonical keys.  The endpoint
    translates these to the requested language via the ?lang= query parameter
    (see app/i18n.py).
    """

    # Part of speech
    # noun | verb | adjective | adverb | pronoun | preposition | conjunction |
    # auxiliary | proper_noun | numeral | determiner | particle | interjection |
    # infinitive | participle | gerund | gerundive
    pos: str | None = None

    # Nominal / adjectival features
    case: str | None = None     # nominative | genitive | dative | accusative | ablative | vocative
    number: str | None = None   # singular | plural
    gender: str | None = None   # masculine | feminine | neuter

    # Verbal features
    person: str | None = None   # "1" | "2" | "3"
    tense: str | None = None    # present | imperfect | future | perfect | pluperfect | future_perfect
    mood: str | None = None     # indicative | subjunctive | imperative | infinitive | participle | gerund | gerundive
    voice: str | None = None    # active | passive


class WordAnalysis(BaseModel):
    """Full analysis for one Latin token."""

    # The surface form exactly as it appears in the input text — always present.
    form: str

    # Dictionary headword (lemma).  None when UDPipe failed and WordNet had
    # no unambiguous match for this form.
    lemma: str | None = None

    # Raw UDPipe UPOS tag (e.g. "NOUN", "VERB").  Empty string when UDPipe
    # did not produce a token for this word.
    upos: str = ""

    # Structured morphological features.  All fields are None for "form_only"
    # words.
    morphology: Morphology = Field(default_factory=Morphology)

    # Syntactic role of this token in its clause (from UDPipe dependency parse).
    # None when UDPipe did not produce a token.
    # Values: subject | predicate | object | indirect_object | adverbial |
    #         modifier | genitive_modifier | coordinator | copula | conjoined |
    #         determiner_role | case_marker | predicative_complement | ...
    syntactic_role: str | None = None

    # Full dictionary entry from Latin is Simple, e.g. "amor, amoris [m.] C".
    # Shows the declension / conjugation class.  Empty when LIS failed.
    dictionary_form: str = ""

    # English meaning from Latin is Simple.  Empty when LIS failed or the
    # lemma was not found in the dictionary.
    meaning: str = ""

    # Direct URL to the Latin is Simple vocabulary page for this entry.
    # Empty when the LIS entry id/type could not be determined.
    lis_url: str = ""

    # Overall reliability of this word's analysis (see WordConfidence enum).
    confidence: WordConfidence

    # Which service supplied the lemma and morphology.
    # "UDPipe"  – full contextual analysis (most reliable)
    # "WordNet" – unambiguous dictionary lookup without sentence context
    # "-"       – could not be determined
    source: str

    # Per-service call diagnostics for debugging / monitoring.
    downstreams: dict[str, DownstreamDiagnostic] = Field(default_factory=dict)


class AnalysisSummary(BaseModel):
    used_udpipe: bool       # True when UDPipe processed this line successfully
    word_count: int
    partial_failure: bool   # True when any downstream had a non-recoverable error


class AnalysisResponse(BaseModel):
    text: str
    request_id: str
    summary: AnalysisSummary
    words: list[WordAnalysis]


class DownstreamResult(BaseModel):
    data: Any = None
    diagnostic: DownstreamDiagnostic
