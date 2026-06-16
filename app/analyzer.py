import asyncio
import logging
import uuid
from typing import NamedTuple

from app.clients import LatinIsSimpleClient, LatinWordNetClient, MorpheusClient, UDPipeClient
from app import span as _span

_log = logging.getLogger(__name__)
from app.latin import (
    extract_lis_fullname,
    extract_lis_meaning,
    extract_lis_url,
    extract_wordnet_lemma,
    extract_wordnet_morpho,
    parse_conllu,
    tokenize,
    ud_to_morphology,
    wn_to_morphology,
)
from app.models import (
    AnalysisResponse,
    AnalysisSummary,
    DownstreamDiagnostic,
    DownstreamResult,
    DownstreamStatus,
    Morphology,
    WordAnalysis,
    WordConfidence,
)


class _Source:
    """Canonical values for the WordAnalysis.source field."""
    UDPIPE  = "UDPipe"
    WORDNET = "WordNet"
    NONE    = "-"


_SKIPPED_NO_LEMMA    = DownstreamDiagnostic(status=DownstreamStatus.SKIPPED, reason="NO_LEMMA")
_SKIPPED_NO_UD_TOKEN = DownstreamDiagnostic(status=DownstreamStatus.SKIPPED, reason="NO_UD_TOKEN")

_PARTIAL_FAILURE_STATUSES = {
    DownstreamStatus.OK,
    DownstreamStatus.SKIPPED,
    DownstreamStatus.NOT_FOUND,
}


class _TokenEntry(NamedTuple):
    """Intermediate per-token data gathered from UDPipe before dictionary lookups."""
    form: str
    internal_lemma: str          # UDPipe lemma, or raw form when UDPipe missed the token
    upos: str                    # UDPipe UPOS tag, or "" when no token
    morphology: Morphology
    syntactic_role: str | None
    source: str                  # _Source.*
    ud_diag: DownstreamDiagnostic


def _confidence(source: str, wn_status: DownstreamStatus, lis_status: DownstreamStatus) -> WordConfidence:
    """Derive word-level confidence from source, WordNet status, and LIS status.

    - source == _Source.NONE                 → FORM_ONLY (UDPipe produced no token)
    - neither WordNet nor LIS confirm it     → FORM_ONLY (could be non-Latin garbage)
    - at least one dictionary hit + LIS ok  → FULL
    - at least one dictionary hit, LIS miss → NO_MEANING
    """
    if source == _Source.NONE:
        return WordConfidence.FORM_ONLY
    if wn_status != DownstreamStatus.OK and lis_status != DownstreamStatus.OK:
        return WordConfidence.FORM_ONLY
    return WordConfidence.FULL if lis_status == DownstreamStatus.OK else WordConfidence.NO_MEANING


class AnalyzerService:
    def __init__(self, wordnet: LatinWordNetClient, udpipe: UDPipeClient, latin_is_simple: LatinIsSimpleClient, morpheus: MorpheusClient) -> None:
        self.wordnet = wordnet
        self.udpipe = udpipe
        self.latin_is_simple = latin_is_simple
        self.morpheus = morpheus

    async def analyze(self, text: str) -> AnalysisResponse:
        return (await self.analyze_group([text]))[0]

    async def analyze_group(self, lines: list[str]) -> list[AnalysisResponse]:
        """Analyze a group of lines as one UDPipe call for full sentence context.

        Tokenizes each line separately, joins them for a single UDPipe request,
        then slices the resulting token list back to per-line AnalysisResponse objects.
        """
        per_line_words = [tokenize(line) for line in lines]
        all_words = [w for words in per_line_words for w in words]

        if all_words:
            combined_text = " ".join(line for line in lines if line.strip())
            ud_result = await self.udpipe.process(combined_text)
            ud_tokens = self._align_udpipe(all_words, ud_result)
            udpipe_ok = ud_result.diagnostic.status == DownstreamStatus.OK
        else:
            ud_result = None
            ud_tokens = []
            udpipe_ok = False

        # Build per-token entries from UDPipe output.
        preliminary: list[_TokenEntry] = []
        for word, token in zip(all_words, ud_tokens, strict=True):
            if token is not None:
                morphology, syntactic_role = ud_to_morphology(token["upos"], token["feats"], token["deprel"])
                preliminary.append(_TokenEntry(
                    form=word,
                    internal_lemma=token["lemma"],
                    upos=token["upos"],
                    morphology=morphology,
                    syntactic_role=syntactic_role,
                    source=_Source.UDPIPE,
                    ud_diag=ud_result.diagnostic,
                ))
            else:
                # UDPipe either failed entirely or produced no token for this word.
                # Propagate the actual UDPipe diagnostic so partial_failure is set
                # correctly when UDPipe timed out rather than just missing the token.
                ud_diag = _SKIPPED_NO_UD_TOKEN if udpipe_ok else ud_result.diagnostic
                preliminary.append(_TokenEntry(
                    form=word,
                    internal_lemma=word,   # raw form used as fallback key for WordNet
                    upos="",
                    morphology=Morphology(),
                    syntactic_role=None,
                    source=_Source.NONE,
                    ud_diag=ud_diag,
                ))

        # Morpheus cross-validation: correct UDPipe lemmata where Morpheus disagrees.
        udpipe_forms = list(dict.fromkeys(e.form for e in preliminary if e.source == _Source.UDPIPE and e.upos != "PUNCT"))
        morpheus_results: dict[str, DownstreamResult] = {}
        if udpipe_forms:
            morpheus_results = await self._load_morpheus(udpipe_forms)
            preliminary = [
                (
                    entry._replace(internal_lemma=morph.data[0])
                    if (
                        entry.source == _Source.UDPIPE
                        and (morph := morpheus_results.get(entry.form)) is not None
                        and morph.diagnostic.status == DownstreamStatus.OK
                        and isinstance(morph.data, list)
                        and len(morph.data) == 1  # only correct when Morpheus is unambiguous
                        and entry.internal_lemma not in morph.data
                    )
                    else entry
                )
                for entry in preliminary
            ]

        # Look up WordNet (by lemma) and LIS (by form, forms_only=true) in parallel.
        # LIS is searched by the original surface form so that e.g. "ferit" finds
        # "ferio, feris, ferire" (to strike) rather than an unrelated "feriare" entry
        # that also starts with "ferio".
        internal_lemmas = list(dict.fromkeys(e.internal_lemma for e in preliminary if e.internal_lemma and e.upos != "PUNCT"))
        lis_forms = list(dict.fromkeys(e.form for e in preliminary if e.form and e.upos != "PUNCT"))
        if internal_lemmas or lis_forms:
            wordnet_results, meaning_results = await asyncio.gather(
                self._load_wordnet(internal_lemmas),
                self._load_meanings(lis_forms),
            )
        else:
            wordnet_results, meaning_results = {}, {}

        all_rows: list[WordAnalysis] = []
        for entry in preliminary:
            if entry.upos == "PUNCT" or not any(c.isalpha() for c in entry.form):
                all_rows.append(WordAnalysis(
                    form=entry.form,
                    upos="PUNCT",
                    confidence=WordConfidence.FULL,
                    source=_Source.NONE,
                    downstreams={},
                ))
                continue

            wn_result = wordnet_results.get(entry.internal_lemma)
            wn_diag = wn_result.diagnostic if wn_result else _SKIPPED_NO_LEMMA

            output_lemma: str | None = entry.internal_lemma if entry.source != _Source.NONE else None
            morphology = entry.morphology
            syntactic_role = entry.syntactic_role
            source = entry.source

            # WordNet fallback: if UDPipe missed this word but WordNet has exactly
            # one unambiguous entry for the raw form, use it for lemma and morphology.
            if source == _Source.NONE and wn_result and wn_result.diagnostic.status == DownstreamStatus.OK:
                cands = wn_result.data if isinstance(wn_result.data, list) else []
                if len(cands) == 1:
                    output_lemma = extract_wordnet_lemma(cands[0], entry.form)
                    morphology = wn_to_morphology(extract_wordnet_morpho(cands[0]))
                    source = _Source.WORDNET

            # LIS lookup is keyed by surface form.
            meaning_result = meaning_results.get(entry.form)
            lis_diag = meaning_result.diagnostic if meaning_result else _SKIPPED_NO_LEMMA
            lis_data = (
                meaning_result.data
                if meaning_result and meaning_result.diagnostic.status == DownstreamStatus.OK
                else None
            )

            confidence = _confidence(source, wn_diag.status, lis_diag.status)

            # When no dictionary confirms the word, discard UDPipe's morphology guess
            # (unreliable for non-Latin or unrecognised input).
            if confidence == WordConfidence.FORM_ONLY:
                output_lemma = None
                morphology = Morphology()
                syntactic_role = None

            all_rows.append(WordAnalysis(
                form=entry.form,
                lemma=output_lemma,
                upos=entry.upos,
                morphology=morphology,
                syntactic_role=syntactic_role,
                dictionary_form=extract_lis_fullname(lis_data, entry.internal_lemma, entry.upos),
                meaning=extract_lis_meaning(lis_data, entry.internal_lemma, entry.upos),
                lis_url=extract_lis_url(lis_data, entry.internal_lemma, entry.upos, entry.form),
                confidence=confidence,
                source=source,
                downstreams={
                    UDPipeClient.service_name:        entry.ud_diag,
                    LatinWordNetClient.service_name:  wn_diag,
                    LatinIsSimpleClient.service_name: lis_diag,
                    MorpheusClient.service_name:      morpheus_results[entry.form].diagnostic if entry.form in morpheus_results else _SKIPPED_NO_UD_TOKEN,
                },
            ))

        # Slice rows back to per-line groups and build one AnalysisResponse per line.
        responses: list[AnalysisResponse] = []
        word_offset = 0
        for line, words in zip(lines, per_line_words, strict=True):
            request_id = str(uuid.uuid4())
            count = len(words)
            line_rows = all_rows[word_offset:word_offset + count]
            word_offset += count

            if count == 0:
                responses.append(AnalysisResponse(
                    text=line,
                    request_id=request_id,
                    summary=AnalysisSummary(used_udpipe=False, word_count=0, partial_failure=False),
                    words=[],
                ))
            else:
                partial_failure = any(
                    diag.status not in _PARTIAL_FAILURE_STATUSES
                    for row in line_rows
                    for diag in row.downstreams.values()
                )
                responses.append(AnalysisResponse(
                    text=line,
                    request_id=request_id,
                    summary=AnalysisSummary(
                        used_udpipe=udpipe_ok,
                        word_count=count,
                        partial_failure=partial_failure,
                    ),
                    words=line_rows,
                ))

        sid = _span.current()
        for resp in responses:
            if resp.words:
                full = sum(1 for w in resp.words if w.confidence == WordConfidence.FULL)
                no_m = sum(1 for w in resp.words if w.confidence == WordConfidence.NO_MEANING)
                form_only = sum(1 for w in resp.words if w.confidence == WordConfidence.FORM_ONLY)
                _log.info(
                    "span=%s sentence words=%d full=%d no_meaning=%d form_only=%d partial=%s",
                    sid, len(resp.words), full, no_m, form_only, resp.summary.partial_failure,
                )

        return responses

    async def _load_morpheus(self, forms: list[str]) -> dict[str, DownstreamResult]:
        results = await asyncio.gather(*(self.morpheus.lemmatize(form) for form in forms))
        return dict(zip(forms, results, strict=True))

    async def _load_wordnet(self, words: list[str]) -> dict[str, DownstreamResult]:
        results = await asyncio.gather(*(self.wordnet.lemmatize(word) for word in words))
        return dict(zip(words, results, strict=True))

    async def _load_meanings(self, lemmas: list[str]) -> dict[str, DownstreamResult]:
        results = await asyncio.gather(*(self.latin_is_simple.search(lemma) for lemma in lemmas))
        return dict(zip(lemmas, results, strict=True))

    @staticmethod
    def _align_udpipe(words: list[str], result: DownstreamResult) -> list[dict[str, str] | None]:
        if result.diagnostic.status != DownstreamStatus.OK or not isinstance(result.data, str):
            return [None] * len(words)
        parsed = parse_conllu(result.data)
        if len(parsed) == len(words):
            return parsed
        by_form: dict[str, list[dict[str, str]]] = {}
        for token in parsed:
            by_form.setdefault(token["form"], []).append(token)
        aligned: list[dict[str, str] | None] = []
        for word in words:
            matches = by_form.get(word)
            aligned.append(matches.pop(0) if matches else None)
        return aligned
